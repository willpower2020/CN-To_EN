"""Audio processing: reference extraction, TTS polish, and final track assembly."""

import os
import shutil
import logging
import subprocess
from pathlib import Path
from typing import List

import numpy as np
from pydub import AudioSegment
from pydub.silence import detect_nonsilent


class AudioProcessor:
    def __init__(self, config):
        self.config = config
        self.logger = logging.getLogger(__name__)

    # ── Reference audio extraction ─────────────────────────────────

    def extract_reference_audio(self, video_path: str, subtitles, output_path: str) -> str:
        """Extract the best-quality audio segment from video for voice cloning.

        Selection criteria:
        1. Duration within [min_len, max_len]
        2. High energy (loud, clear speech)
        3. Low silence ratio
        4. High SNR estimate (ratio of speech energy to background noise floor)
        """
        import librosa

        min_len = self.config.get('voice_cloning', 'min_audio_length')
        max_len = self.config.get('voice_cloning', 'max_audio_length')
        cache = self.config.get('directories', 'cache')
        Path(cache).mkdir(parents=True, exist_ok=True)

        temp_audio = os.path.join(cache, '_temp_full_audio.wav')
        self._ffmpeg_extract_audio(video_path, temp_audio)
        audio = AudioSegment.from_wav(temp_audio)

        best_segment = None
        best_score = -999.0

        for i, sub in enumerate(subtitles):
            seg_start_ms = int(sub.start_time * 1000)
            seg_end_ms = int(sub.end_time * 1000)

            while (seg_end_ms - seg_start_ms) / 1000 < min_len and i + 1 < len(subtitles):
                seg_end_ms = int(subtitles[i + 1].end_time * 1000)
                i += 1

            duration = (seg_end_ms - seg_start_ms) / 1000
            if duration < min_len or duration > max_len:
                continue

            segment = audio[seg_start_ms:seg_end_ms]
            energy = segment.dBFS

            nonsilent = detect_nonsilent(segment, min_silence_len=200, silence_thresh=-40)
            total_ms = len(segment)
            silent_ms = sum(e - s for s, e in nonsilent)
            silence_ratio = silent_ms / total_ms if total_ms > 0 else 1.0

            # Estimate SNR: compare speech level to noise floor (lower 10th percentile)
            y_seg, sr = librosa.load(
                temp_audio, sr=22050,
                offset=seg_start_ms / 1000.0,
                duration=duration,
            )
            if y_seg is None or len(y_seg) == 0:
                continue

            # Noise floor = bottom 15% of RMS values (represents background noise)
            rms_frame = librosa.feature.rms(y=y_seg, frame_length=1024, hop_length=512)[0]
            noise_floor = float(np.percentile(rms_frame, 15)) + 1e-10
            speech_rms = float(np.mean(rms_frame))
            snr = speech_rms / noise_floor if noise_floor > 0 else 0.0

            # Combined score: energy * (1 - silence_ratio) * log(SNR + 1)
            import math
            score = energy * (1 - silence_ratio) * math.log(snr + 1)
            if score > best_score:
                best_score = score
                best_segment = segment

        if best_segment is None:
            best_segment = audio[:10000]

        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        best_segment.export(output_path, format='wav')

        # Apply noise reduction to the extracted reference audio
        self._reduce_noise_ffmpeg(output_path, output_path)

        if os.path.exists(temp_audio):
            os.remove(temp_audio)

        return output_path

    def _ffmpeg_extract_audio(
        self,
        video_path: str,
        output_path: str,
        sample_rate: int = 44100,
    ) -> str:
        cmd = [
            'ffmpeg', '-y',
            '-i', video_path,
            '-vn',
            '-acodec', 'pcm_s16le',
            '-ar', str(sample_rate),
            '-ac', '1',
            output_path,
        ]
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
        return output_path

    def _reduce_noise_ffmpeg(self, input_path: str, output_path: str) -> str:
        """Apply ffmpeg's afftdn (adaptive FFT denoise) filter to reduce noise."""
        tmp_path = input_path + '.denoise_tmp.wav'
        cmd = [
            'ffmpeg', '-y',
            '-i', input_path,
            '-af', 'afftdn=nf=-25:tn=1:tr=1',
            '-ar', '44100',
            '-ac', '1',
            tmp_path,
        ]
        result = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, check=False)
        if result.returncode == 0 and os.path.exists(tmp_path):
            shutil.move(tmp_path, output_path)
        else:
            # Fallback: if denoise fails, keep original
            if os.path.exists(tmp_path):
                try:
                    os.remove(tmp_path)
                except OSError:
                    pass
        return output_path

    def extract_full_audio(self, video_path: str, output_path: str, sample_rate: int = 44100) -> str:
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        return self._ffmpeg_extract_audio(video_path, output_path, sample_rate=sample_rate)

    # ── TTS silence compression ────────────────────────────────────

    def _compress_tts_silence(self, tts_audio: AudioSegment, idx: int) -> AudioSegment:
        MAX_SILENCE_MS = 200
        CHUNK_MS = 100
        SPEECH_DB = -40.0

        sample_rate = tts_audio.frame_rate
        total_ms = len(tts_audio)
        original_len = total_ms

        num_chunks = total_ms // CHUNK_MS
        speech_chunks = [
            tts_audio[j * CHUNK_MS:(j + 1) * CHUNK_MS].dBFS > SPEECH_DB
            for j in range(num_chunks)
        ]

        needs_compress = False
        in_sil = False
        for j, is_sp in enumerate(speech_chunks):
            if not is_sp and not in_sil:
                in_sil, sil_start = True, j
            elif is_sp and in_sil:
                in_sil = False
                if (j - sil_start) * CHUNK_MS > MAX_SILENCE_MS:
                    needs_compress = True

        if not needs_compress:
            return tts_audio

        result = AudioSegment.empty()
        in_sil = False
        i = 0
        while i < num_chunks:
            is_sp = speech_chunks[i]
            chunk_audio = tts_audio[i * CHUNK_MS:(i + 1) * CHUNK_MS]
            if is_sp:
                if in_sil:
                    sil_len_ms = (i - sil_start) * CHUNK_MS
                    result += AudioSegment.silent(
                        duration=min(sil_len_ms, MAX_SILENCE_MS),
                        frame_rate=sample_rate
                    )
                    in_sil = False
                result += chunk_audio
            else:
                if not in_sil:
                    in_sil, sil_start = True, i
            i += 1

        if in_sil:
            sil_len_ms = (num_chunks - sil_start) * CHUNK_MS
            result += AudioSegment.silent(
                duration=min(sil_len_ms, MAX_SILENCE_MS),
                frame_rate=sample_rate
            )
        remainder = total_ms % CHUNK_MS
        if remainder > 0:
            tail = tts_audio[num_chunks * CHUNK_MS:total_ms]
            if tail.dBFS > SPEECH_DB:
                result += tail

        new_len = len(result)
        if new_len < original_len * 0.8:
            self.logger.info(f"  [{idx}] Silence compressed: {original_len/1000:.2f}s -> {new_len/1000:.2f}s")

        return result

    def _compress_tts_silence_to_file(self, input_wav: str, output_wav: str, idx: int) -> None:
        tts_audio = AudioSegment.from_file(input_wav)
        compressed = self._compress_tts_silence(tts_audio, idx)
        compressed = compressed.set_frame_rate(tts_audio.frame_rate)
        compressed.export(output_wav, format='wav')

    def _merge_nonsilent_ranges(self, ranges: List[List[int]], max_gap_ms: int) -> List[List[int]]:
        if not ranges:
            return []
        merged = [list(ranges[0])]
        for start, end in ranges[1:]:
            last = merged[-1]
            if start - last[1] <= max_gap_ms:
                last[1] = max(last[1], end)
            else:
                merged.append([start, end])
        return merged

    # ── TTS audio polishing ─────────────────────────────────────────

    def _apply_lowpass_filter(self, audio: AudioSegment, cutoff_hz: int) -> AudioSegment:
        """Apply low-pass filter via ffmpeg to remove high-frequency noise."""
        cache = self.config.get('directories', 'cache')
        tmp_in = os.path.join(cache, '_lpf_in.wav')
        tmp_out = os.path.join(cache, '_lpf_out.wav')
        audio.export(tmp_in, format='wav')
        cmd = [
            'ffmpeg', '-y', '-i', tmp_in,
            '-af', f'lowpass=f={cutoff_hz}:p=2',
            '-ar', str(audio.frame_rate),
            tmp_out,
        ]
        result = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
        if result.returncode == 0 and os.path.exists(tmp_out):
            filtered = AudioSegment.from_wav(tmp_out)
            for p in (tmp_in, tmp_out):
                try:
                    os.remove(p)
                except OSError:
                    pass
            return filtered
        for p in (tmp_in, tmp_out):
            try:
                os.remove(p)
            except OSError:
                pass
        return audio

    def _apply_eq_filter(self, audio: AudioSegment, post_cfg: dict) -> AudioSegment:
        """Apply voice-optimized EQ via ffmpeg: bass warmth + presence + air."""
        cache = self.config.get('directories', 'cache')
        tmp_in = os.path.join(cache, '_eq_in.wav')
        tmp_out = os.path.join(cache, '_eq_out.wav')
        audio.export(tmp_in, format='wav')

        bass_gain = float(post_cfg.get('eq_bass_gain_db', 1.5))
        presence_gain = float(post_cfg.get('eq_presence_gain_db', 2.0))
        air_gain = float(post_cfg.get('eq_air_gain_db', 1.0))

        filters = [
            f'equalizer=f=200:t=q:w=1.5:g={bass_gain}',
            f'equalizer=f=3000:t=q:w=1.5:g={presence_gain}',
            f'equalizer=f=10000:t=q:w=2.0:g={air_gain}',
        ]
        cmd = [
            'ffmpeg', '-y', '-i', tmp_in,
            '-af', ','.join(filters),
            '-ar', str(audio.frame_rate),
            tmp_out,
        ]
        result = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
        if result.returncode == 0 and os.path.exists(tmp_out):
            filtered = AudioSegment.from_wav(tmp_out)
            for p in (tmp_in, tmp_out):
                try:
                    os.remove(p)
                except OSError:
                    pass
            return filtered
        for p in (tmp_in, tmp_out):
            try:
                os.remove(p)
            except OSError:
                pass
        return audio

    def _polish_tts_audio(self, tts_audio: AudioSegment) -> AudioSegment:
        post_cfg = self.config.get('audio_post') or {}
        trim_limit_ms = int(post_cfg.get('trim_leading_silence_ms', 180))
        trim_trailing_ms = int(post_cfg.get('trim_trailing_silence_ms', 260))
        leading_pad_ms = int(post_cfg.get('leading_pad_ms', 25))
        trailing_pad_ms = int(post_cfg.get('trailing_pad_ms', 35))
        silence_threshold_db = float(post_cfg.get('silence_threshold_db', -38))
        merge_gap_ms = int(post_cfg.get('merge_gap_ms', 70))
        target_dbfs = float(post_cfg.get('target_tts_dbfs', -18.0))
        max_gain_db = float(post_cfg.get('max_gain_db', 4.0))
        fade_in_ms = int(post_cfg.get('fade_in_ms', 20))
        fade_out_ms = int(post_cfg.get('fade_out_ms', 60))

        # Detect non-silent speech ranges
        raw_nonsilent = detect_nonsilent(
            tts_audio,
            min_silence_len=40,
            silence_thresh=silence_threshold_db,
        )
        if raw_nonsilent:
            # Merge nearby speech chunks separated by short gaps
            nonsilent = self._merge_nonsilent_ranges(raw_nonsilent, merge_gap_ms)

            if len(nonsilent) >= 2:
                # Clean edge noise at the START of the audio
                first_start = nonsilent[0][0]
                if first_start <= trim_limit_ms:
                    first_start = max(0, first_start - min(leading_pad_ms, first_start))

                # Clean edge noise at the END of the audio
                last_end = nonsilent[-1][1]
                trailing_ms = max(0, len(tts_audio) - last_end)
                if trailing_ms > trim_trailing_ms:
                    last_end = min(len(tts_audio), last_end + trailing_pad_ms)

                if first_start > 0 or last_end < len(tts_audio):
                    tts_audio = tts_audio[first_start:last_end]

        # Normalize loudness
        if tts_audio.dBFS > -45:
            gain_db = max(-max_gain_db, min(target_dbfs - tts_audio.dBFS, max_gain_db))
            if abs(gain_db) > 0.1:
                tts_audio = tts_audio.apply_gain(gain_db)

        # Low-pass filter to remove high-frequency artifacts
        if post_cfg.get('enable_lowpass_filter', False):
            cutoff_hz = int(post_cfg.get('lowpass_cutoff_hz', 16000))
            tts_audio = self._apply_lowpass_filter(tts_audio, cutoff_hz)

        # Voice EQ: bass warmth + presence + air
        if post_cfg.get('enable_eq', False):
            tts_audio = self._apply_eq_filter(tts_audio, post_cfg)

        # Gentle fade in/out
        if len(tts_audio) > 80:
            tts_audio = tts_audio.fade_in(min(fade_in_ms, len(tts_audio) // 3))
            tts_audio = tts_audio.fade_out(min(fade_out_ms, len(tts_audio) // 2))

        return tts_audio

    # ── Final audio track assembly ─────────────────────────────────

    def build_final_audio_track(
        self,
        original_audio_path: str,
        subtitles,
        tts_files,
        output_path: str,
    ) -> str:
        """
        Build final audio track: sequential splice replacing original audio
        with English TTS at subtitle positions. Mute padding around subtitles.
        """
        sample_rate = self.config.get('tts', 'sample_rate') or 24000

        base_audio = AudioSegment.from_wav(original_audio_path)
        if base_audio.frame_rate != sample_rate:
            base_audio = base_audio.set_frame_rate(sample_rate)
        base_audio = base_audio.set_channels(1)

        # Prepare each TTS segment
        prepared = []
        for idx, info in enumerate(tts_files):
            start_ms = int(info['start'] * 1000)
            end_ms = int(info['end'] * 1000)

            wav_for_audio = info.get('wav_compressed', info['wav'])
            if not (os.path.exists(wav_for_audio) and os.path.getsize(wav_for_audio) > 1024):
                AudioSegment.silent(duration=2000, frame_rate=sample_rate).export(wav_for_audio, format='wav')

            tts_audio = AudioSegment.from_file(wav_for_audio)
            tts_audio = tts_audio.set_frame_rate(sample_rate).set_channels(1)

            if tts_audio.dBFS < -50:
                tts_audio = AudioSegment.silent(duration=2000, frame_rate=sample_rate)
            else:
                tts_audio = self._polish_tts_audio(tts_audio)

            if idx + 1 < len(tts_files):
                boundary_ms = int(tts_files[idx + 1]['end'] * 1000)
            else:
                boundary_ms = int(info['end'] * 1000) + 500

            available_ms = boundary_ms - start_ms
            is_long_phrase = (len(tts_audio) > available_ms * 1.20)

            if is_long_phrase:
                self.logger.info(
                    f"  [{idx}] Long phrase {len(tts_audio)/1000:.2f}s > slot {available_ms/1000:.2f}s, keeping full"
                )
            elif len(tts_audio) > available_ms:
                speed_ratio = min(len(tts_audio) / available_ms, 1.15)
                if speed_ratio > 1.01:
                    sped_path = Path(output_path).with_name(f'_tts_fit_{idx:03d}.wav')
                    cmd = [
                        'ffmpeg', '-y',
                        '-i', info['wav'],
                        '-filter:a', f'atempo={speed_ratio:.4f}',
                        str(sped_path),
                    ]
                    subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
                    if sped_path.exists() and sped_path.stat().st_size > 1024:
                        tts_audio = AudioSegment.from_file(sped_path)
                        tts_audio = tts_audio.set_frame_rate(sample_rate).set_channels(1)
                        tts_audio = self._polish_tts_audio(tts_audio)

            prepared.append({
                'start': start_ms,
                'end': end_ms,
                'tts': tts_audio,
                'text': info.get('text', ''),
                'adjusted_dur': info.get('adjusted_dur'),
            })

        if not prepared:
            Path(output_path).parent.mkdir(parents=True, exist_ok=True)
            base_audio.export(output_path, format='wav')
            return output_path

        # Sequential splice with crossfade transitions
        mute_padding_ms = int(self.config.get('sync', 'mute_padding_seconds') * 1000)
        crossfade_ms = 30  # Short crossfade for smooth transitions
        base_len_ms = len(base_audio)
        result_parts = []
        cursor_ms = 0

        for i, item in enumerate(prepared):
            start_ms = item['start']
            tts_audio = item['tts']
            adjusted_dur_ms = int(item.get('adjusted_dur', len(tts_audio) / 1000.0) * 1000)
            gap_ms = start_ms - cursor_ms

            if gap_ms > mute_padding_ms:
                result_parts.append(base_audio[cursor_ms:start_ms - mute_padding_ms])
                result_parts.append(AudioSegment.silent(duration=mute_padding_ms, frame_rate=base_audio.frame_rate))
            elif gap_ms > 0:
                # Apply short crossfade from previous segment into silence gap
                if result_parts and len(result_parts[-1]) > crossfade_ms:
                    prev = result_parts[-1]
                    fade_tail = prev[-crossfade_ms:].fade_out(crossfade_ms)
                    silence_chunk = AudioSegment.silent(duration=gap_ms, frame_rate=base_audio.frame_rate)
                    silence_chunk = silence_chunk.fade_in(crossfade_ms)
                    result_parts[-1] = prev[:-crossfade_ms] + fade_tail
                    result_parts.append(silence_chunk)
                else:
                    result_parts.append(AudioSegment.silent(duration=gap_ms, frame_rate=base_audio.frame_rate))
            elif gap_ms < 0:
                # Overlap: crossfade previous tail into current TTS
                fade_ms = min(-gap_ms, 400)
                if result_parts and len(result_parts[-1]) > fade_ms:
                    prev_tts = result_parts[-1]
                    faded_tail = prev_tts[-fade_ms:].fade_out(fade_ms)
                    tts_audio = tts_audio.fade_in(min(fade_ms, 50))
                    result_parts[-1] = prev_tts[:-fade_ms] + faded_tail
                else:
                    fade_ms = min(-gap_ms, 200)

            cursor_ms = max(cursor_ms, start_ms)

            tts_len_ms = len(tts_audio)
            if tts_len_ms > adjusted_dur_ms:
                fade_ms = min(tts_len_ms - adjusted_dur_ms, 400)
                tts_audio = tts_audio[:adjusted_dur_ms + fade_ms].fade_out(fade_ms)
                if len(tts_audio) > adjusted_dur_ms:
                    tts_audio = tts_audio[:adjusted_dur_ms]
            elif tts_len_ms < adjusted_dur_ms:
                # Pad with silence that crossfades out smoothly
                silence_pad = AudioSegment.silent(
                    duration=adjusted_dur_ms - tts_len_ms,
                    frame_rate=base_audio.frame_rate
                )
                fade_out_len = min(50, len(tts_audio) // 4)
                if fade_out_len > 5:
                    tts_audio = tts_audio.fade_out(fade_out_len) if tts_len_ms < adjusted_dur_ms - 50 else tts_audio
                tts_audio = tts_audio + silence_pad

            result_parts.append(tts_audio)
            cursor_ms = start_ms + adjusted_dur_ms

        if cursor_ms < base_len_ms:
            # Crossfade from last TTS back to original audio
            tail_audio = base_audio[cursor_ms:base_len_ms]
            if result_parts and len(result_parts[-1]) > crossfade_ms:
                tail_audio = tail_audio.fade_in(crossfade_ms)
            result_parts.append(tail_audio)

        final_audio = AudioSegment.empty()
        for part in result_parts:
            final_audio += part

        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        final_audio.export(output_path, format='wav')
        return output_path

    # ── Emotion analysis ────────────────────────────────────────────

    def analyze_emotion_features(self, audio_path: str):
        """Extract emotion-relevant features from an audio file.

        Features extracted:
        - pitch_mean/pitch_std: fundamental frequency statistics (Hz)
        - energy_mean/energy_std: RMS energy statistics
        - speaking_rate: estimated syllables per second
        - pause_ratio: fraction of frames below energy threshold
        - spectral_centroid_mean: brightness/timbre indicator
        - zero_crossing_rate_mean: voice quality indicator
        """
        from src.models.models import EmotionFeatures
        import librosa

        DEFAULT = EmotionFeatures(
            pitch_mean=150.0, pitch_std=20.0,
            energy_mean=0.1, energy_std=0.05,
            speaking_rate=3.0, pause_ratio=0.2
        )

        try:
            y, sr = librosa.load(audio_path, sr=22050)
        except Exception:
            return DEFAULT

        if y is None or len(y) == 0:
            return DEFAULT

        # Pitch (fundamental frequency) analysis
        try:
            f0, _, _ = librosa.pyin(
                y,
                fmin=librosa.note_to_hz('C2'),
                fmax=librosa.note_to_hz('C7')
            )
            voiced = f0[~np.isnan(f0)]
        except Exception:
            voiced = np.array([])

        pitch_mean = float(np.mean(voiced)) if len(voiced) > 0 else DEFAULT.pitch_mean
        pitch_std = float(np.std(voiced)) if len(voiced) > 0 else DEFAULT.pitch_std

        # Energy (RMS) analysis
        try:
            rms = librosa.feature.rms(y=y, frame_length=1024, hop_length=512)[0]
        except Exception:
            rms = np.array([])

        if len(rms) > 0:
            energy_mean = float(np.mean(rms))
            energy_std = float(np.std(rms))
            silence_thresh = float(np.percentile(rms, 15))
            pause_ratio = float(np.sum(rms < silence_thresh) / len(rms))
        else:
            energy_mean, energy_std, pause_ratio = DEFAULT.energy_mean, DEFAULT.energy_std, DEFAULT.pause_ratio

        # Speaking rate via onset detection
        try:
            onset_env = librosa.onset.onset_strength(y=y, sr=sr)
            onset_frames = librosa.onset.onset_detect(onset_envelope=onset_env, sr=sr)
            syllable_count = len(onset_frames)
            duration = len(y) / sr
            speaking_rate = float(syllable_count / duration) if duration > 0 else 0.0
        except Exception:
            speaking_rate = DEFAULT.speaking_rate

        # Spectral centroid (brightness/timbre)
        try:
            spec_cent = librosa.feature.spectral_centroid(y=y, sr=sr)[0]
            spectral_centroid_mean = float(np.mean(spec_cent))
        except Exception:
            spectral_centroid_mean = 2500.0

        # Zero crossing rate (voice quality/breathiness)
        try:
            zcr = librosa.feature.zero_crossing_rate(y, frame_length=1024, hop_length=512)[0]
            zcr_mean = float(np.mean(zcr))
        except Exception:
            zcr_mean = 0.05

        return EmotionFeatures(
            pitch_mean=pitch_mean, pitch_std=pitch_std,
            energy_mean=energy_mean, energy_std=energy_std,
            speaking_rate=speaking_rate, pause_ratio=pause_ratio,
        )

    def apply_emotion_transfer(
        self,
        input_path: str,
        reference_features: object,
        output_path: str,
    ) -> str:
        """Transfer emotion profile from reference audio features to TTS output.

        Applies three adjustments:
        1. Pitch shift (semitones) — matches average pitch of reference
        2. Energy scaling — matches loudness level of reference
        3. Tempo adjustment — matches speaking rate of reference
        """
        import librosa
        import soundfile as sf

        # Analyze TTS output features
        tts_features = self.analyze_emotion_features(input_path)
        ref = reference_features

        # 1. Pitch shift
        semitones = 0.0
        if tts_features.pitch_mean > 0 and ref.pitch_mean > 0:
            ratio = ref.pitch_mean / tts_features.pitch_mean
            semitones = float(12 * np.log2(ratio + 1e-10))
            range_min, range_max = self.config.get('emotion', 'pitch_shift_range')
            semitones = float(np.clip(semitones, range_min, range_max))
            if abs(semitones) <= 0.5:
                semitones = 0.0

        # 2. Energy (volume) scaling
        energy_ratio = 1.0
        if tts_features.energy_mean > 0 and ref.energy_mean > 0:
            energy_ratio = ref.energy_mean / tts_features.energy_mean
            e_min, e_max = self.config.get('emotion', 'energy_scale_range')
            energy_ratio = float(np.clip(energy_ratio, e_min, e_max))

        # 3. Tempo (speaking rate) adjustment
        speed_ratio = 1.0
        if tts_features.speaking_rate > 0 and ref.speaking_rate > 0:
            rate_ratio = ref.speaking_rate / tts_features.speaking_rate
            rate_ratio = float(np.clip(rate_ratio, 0.80, 1.20))
            if abs(rate_ratio - 1.0) > 0.03:
                speed_ratio = rate_ratio

        if abs(energy_ratio - 1.0) > 0.01:
            y_in, sr_in = librosa.load(input_path, sr=None)
            y_out = np.clip(y_in * energy_ratio, -1.0, 1.0)
            base_path = input_path + '_base.wav'
            sf.write(base_path, y_out, sr_in)
        else:
            base_path = input_path

        self._apply_ffmpeg_pitch_speed(
            base_path, output_path,
            sample_rate=22050,
            semitones=semitones, speed_ratio=speed_ratio,
        )

        for p in (base_path, input_path):
            if p != output_path and os.path.exists(p) and p.endswith('_base.wav'):
                try:
                    os.remove(p)
                except OSError:
                    pass

        return output_path

    def _apply_ffmpeg_pitch_speed(
        self,
        input_path: str,
        output_path: str,
        sample_rate: int,
        semitones: float = 0.0,
        speed_ratio: float = 1.0,
    ) -> str:
        filters: List[str] = []
        pitch_factor = 1.0

        if abs(semitones) > 1e-3:
            pitch_factor = float(2 ** (semitones / 12.0))
            new_rate = sample_rate * pitch_factor
            filters.append(f'asetrate={new_rate}')
            filters.append(f'aresample={sample_rate}')

        tempo_after_pitch = float(speed_ratio) / pitch_factor
        filters.extend(self._build_atempo_filters(tempo_after_pitch))

        if not filters:
            shutil.copyfile(input_path, output_path)
            return output_path

        cmd = [
            'ffmpeg', '-y',
            '-i', input_path,
            '-af', ','.join(filters),
            '-ar', str(sample_rate),
            output_path,
        ]
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
        return output_path

    @staticmethod
    def _build_atempo_filters(ratio: float) -> List[str]:
        filters: List[str] = []
        r = float(ratio)
        while r > 2.0 + 1e-6:
            filters.append('atempo=2.0')
            r /= 2.0
        while r < 0.5 - 1e-6:
            filters.append('atempo=0.5')
            r /= 0.5
        if abs(r - 1.0) > 1e-4:
            filters.append(f'atempo={r:.6f}')
        return filters