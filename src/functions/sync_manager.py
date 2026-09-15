import os
import re
import json
import time
import hashlib
import logging
import subprocess
import shutil
from pathlib import Path
from typing import List, Dict

from pydub import AudioSegment


class SyncManager:
    TTS_METADATA_VERSION = 2

    def __init__(self, config, tts_engine, audio_processor, video_processor):
        self.config = config
        self.tts_engine = tts_engine
        self.audio_processor = audio_processor
        self.video_processor = video_processor
        self.logger = logging.getLogger(__name__)

    def process_video(
        self,
        video_path: str,
        subtitles: List,
        output_path: str,
        window_start: float | None = None,
        window_end: float | None = None,
    ) -> str:
        self.logger.info("=" * 60)
        self.logger.info(f"[START] {os.path.basename(video_path)} | {len(subtitles)} subtitles")
        self.logger.info("=" * 60)

        cache_dir = self.config.get('directories', 'cache')
        Path(cache_dir).mkdir(parents=True, exist_ok=True)

        video_duration = self.video_processor.get_duration(video_path)
        self.logger.info(f"[Video] {video_duration:.1f}s")

        local_window_start = 0.0 if window_start is None else max(0.0, float(window_start))
        local_window_end = video_duration if window_end is None else min(video_duration, float(window_end))
        if local_window_end <= local_window_start:
            raise ValueError("Invalid window")

        # Step 1: Clone voice
        self.logger.info("[Step 1/5] Cloning voice ...")
        ref_path = os.path.join(cache_dir, '_reference.wav')
        self.audio_processor.extract_reference_audio(video_path, subtitles, ref_path)
        cloned_id = self.tts_engine.clone_voice(ref_path)
        self.logger.info(f"  voice_id: {cloned_id}")

        # Extract emotion features from reference audio for emotion transfer
        self._ref_emotion_features = None
        if self.config.get('emotion', 'enable_emotion_transfer'):
            self.logger.info("  Extracting reference emotion features ...")
            self._ref_emotion_features = self.audio_processor.analyze_emotion_features(ref_path)
            self.logger.info(
                f"    pitch={self._ref_emotion_features.pitch_mean:.1f}Hz "
                f"energy={self._ref_emotion_features.energy_mean:.3f} "
                f"rate={self._ref_emotion_features.speaking_rate:.1f}/s"
            )

        # Step 2: Synthesize English audio for each subtitle
        self.logger.info(f"[Step 2/5] Synthesizing {len(subtitles)} TTS segments ...")

        tts_files = []
        for i, sub in enumerate(subtitles):
            tts_wav = os.path.join(cache_dir, f'_tts_{i:03d}.wav')
            subtitle_duration = max(0.0, sub.end_time - sub.start_time)

            regenerate_reason = self._get_tts_regen_reason(
                sub.english_text, tts_wav, subtitle_duration,
            )
            if regenerate_reason:
                self.logger.info(
                    f"  [{i}] Regenerate TTS: {regenerate_reason} | '{sub.english_text[:60]}'"
                )
                self._synthesize_wav_with_validation(
                    sub.english_text, tts_wav, subtitle_duration,
                )

            if os.path.exists(tts_wav) and os.path.getsize(tts_wav) > 1024:
                self._write_tts_metadata(tts_wav, sub.english_text)
                compressed_wav = os.path.join(cache_dir, f'_tts_comp_{i:03d}.wav')
                self.audio_processor._compress_tts_silence_to_file(tts_wav, compressed_wav, i)

                # Apply emotion transfer if enabled
                if self._ref_emotion_features is not None:
                    emotion_wav = os.path.join(cache_dir, f'_tts_emotion_{i:03d}.wav')
                    try:
                        self.audio_processor.apply_emotion_transfer(
                            compressed_wav, self._ref_emotion_features, emotion_wav
                        )
                        final_wav = emotion_wav
                    except Exception as e:
                        self.logger.debug(f"  [{i}] Emotion transfer failed: {e}, using compressed")
                        final_wav = compressed_wav
                else:
                    final_wav = compressed_wav

                tts_files.append({
                    'index': i,
                    'start': sub.start_time,
                    'end': sub.end_time,
                    'wav': tts_wav,
                    'wav_compressed': final_wav,
                    'text': sub.english_text,
                })

        self.logger.info(f"  Done: {len(tts_files)} segments")

        # Step 3: Calculate speed adjustment parameters
        self.logger.info("[Step 3/5] Calculating speed parameters ...")

        min_speed = self.config.get('sync', 'min_speed_ratio') or 0.50
        max_speed = self.config.get('sync', 'max_speed_ratio') or 1.80

        segment_info = []
        cursor = local_window_start

        for info in tts_files:
            start = info['start']
            end = info['end']
            tts_wav = info['wav']

            if start < local_window_start or end > local_window_end:
                continue

            chinese_dur = end - start
            english_dur = self._get_audio_duration(tts_wav)
            speed_ratio = chinese_dur / english_dur if english_dur > 0 else 1.0
            speed_ratio = max(min_speed, min(max_speed, speed_ratio))
            adjusted_dur = chinese_dur / speed_ratio
            gap_dur = start - cursor

            segment_info.append({
                'index': info['index'],
                'start': start,
                'end': end,
                'gap_dur': gap_dur,
                'speed_ratio': speed_ratio,
                'adjusted_dur': adjusted_dur,
                'english_dur': english_dur,
                'tts_wav': tts_wav,
            })

            cursor = start + adjusted_dur

        tail_dur = local_window_end - cursor
        self.logger.info(f"  {len(segment_info)} segments, tail={tail_dur:.1f}s")

        # Step 4: Generate video clips (video only, no audio)
        self.logger.info("[Step 4/6] Generating video clips ...")

        segment_files = []
        segment_idx = 0

        for info in segment_info:
            if info['gap_dur'] > 0.05:
                gap_start = info['start'] - info['gap_dur']
                gap_end = info['start']
                gap_file = os.path.join(cache_dir, f'_part_{segment_idx:03d}.mp4')
                self.video_processor.extract_segment(
                    video_path, gap_start, gap_end, gap_file, include_audio=False
                )
                if os.path.exists(gap_file) and os.path.getsize(gap_file) > 1024:
                    segment_files.append(gap_file)
                    segment_idx += 1

            sub_file = os.path.join(cache_dir, f'_part_{segment_idx:03d}.mp4')
            raw_sub = os.path.join(cache_dir, f'_raw_{info["index"]:03d}.mp4')

            self.video_processor.extract_segment(
                video_path, info['start'], info['end'], raw_sub, include_audio=False
            )

            if abs(info['speed_ratio'] - 1.0) > 0.01:
                self.video_processor.adjust_speed(raw_sub, info['speed_ratio'], sub_file)
            else:
                shutil.copyfile(raw_sub, sub_file)

            if os.path.exists(sub_file) and os.path.getsize(sub_file) > 1024:
                segment_files.append(sub_file)
                segment_idx += 1

        if tail_dur > 0.05:
            tail_file = os.path.join(cache_dir, f'_part_{segment_idx:03d}.mp4')
            self.video_processor.extract_segment(
                video_path, cursor, video_duration, tail_file, include_audio=False
            )
            if os.path.exists(tail_file) and os.path.getsize(tail_file) > 1024:
                segment_files.append(tail_file)

        self.logger.info(f"  Generated {len(segment_files)} clips")

        # Step 5: Concatenate video track
        self.logger.info("[Step 5/7] Concatenating video track ...")
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        final_video_only = os.path.join(cache_dir, '_final_video_silent.mp4')
        self.video_processor.concatenate_videos(segment_files, final_video_only)

        # Step 6: Subtitle overlay
        self.logger.info("[Step 6/7] Applying subtitle overlay ...")
        english_srt = os.path.join(cache_dir, '_english_overlay.srt')
        subtitled_video = os.path.join(cache_dir, '_final_video_subtitled.mp4')
        self.video_processor.write_english_srt(
            subtitles, english_srt,
            time_offset=local_window_start,
            segment_info=segment_info
        )

        if local_window_start > 0.0 or local_window_end < video_duration:
            windowed_video = os.path.join(cache_dir, '_final_video_windowed.mp4')
            self.video_processor.extract_segment(
                video_path, local_window_start, local_window_end, windowed_video, include_audio=False
            )
            self.video_processor.apply_subtitle_overlay(windowed_video, english_srt, subtitled_video)
        else:
            self.video_processor.apply_subtitle_overlay(final_video_only, english_srt, subtitled_video)

        # Step 7: Build final audio and merge
        self.logger.info("[Step 7/7] Building final audio and merging ...")
        original_audio = os.path.join(cache_dir, '_full_original_audio.wav')
        final_audio = os.path.join(cache_dir, '_final_audio_mix.wav')
        self.audio_processor.extract_full_audio(video_path, original_audio)

        seg_lookup = {s['index']: s for s in segment_info}
        for tf in tts_files:
            seg = seg_lookup.get(tf['index'])
            if seg:
                tf['adjusted_dur'] = seg['adjusted_dur']
                tf['gap_dur'] = seg['gap_dur']

        self.audio_processor.build_final_audio_track(original_audio, subtitles, tts_files, final_audio)

        if local_window_start > 0.0 or local_window_end < video_duration:
            sampled_audio = os.path.join(cache_dir, '_final_audio_mix_window.wav')
            window_audio = AudioSegment.from_wav(final_audio)
            window_audio = window_audio[int(local_window_start * 1000):int(local_window_end * 1000)]
            window_audio.export(sampled_audio, format='wav')
            final_audio = sampled_audio

        self.video_processor.merge_audio_video(subtitled_video, final_audio, output_path)

        out_dur = self.video_processor.get_duration(output_path)
        out_size = os.path.getsize(output_path) / 1024 / 1024
        self.logger.info(f"[OK] {output_path}")
        self.logger.info(f"  {video_duration:.1f}s -> {out_dur:.1f}s ({out_size:.1f}MB)")

        return output_path

    # ── TTS helpers ──────────────────────────────────────────────

    def _normalize_tts_text(self, text: str) -> str:
        text = (text or '').strip()
        text = text.replace('\u2018', "'").replace('\u2019', "'")
        text = text.replace('\u201c', '"').replace('\u201d', '"')
        text = text.replace('\u2014', '-').replace('\u2013', '-')
        text = text.replace('\u2026', ',').replace('...', ',')

        replacements = {
            "you're": "you are", "it's": "it is", "that's": "that is",
            "don't": "do not", "can't": "can not", "I've": "I have",
            "you've": "you have", "they've": "they have", "he've": "he have",
            "they're": "they are", "we're": "we are", "there's": "there is",
            "here's": "here is", "what's": "what is", "who's": "who is",
            "where's": "where is", "how's": "how is", "let's": "let us",
            "ain't": "is not", "gonna": "going to", "wanna": "want to",
            "gotta": "got to", "kinda": "kind of", "sorta": "sort of",
            "outta": "out of", "lotta": "lot of", "lotsa": "lots of",
        }
        for old, new in replacements.items():
            text = text.replace(old, new)

        text = ' '.join(text.split())
        return re.sub(r"^([^A-Za-z]*)([a-z])", lambda m: m.group(1) + m.group(2).upper(), text, count=1)

    def _tts_metadata_path(self, wav_path: str) -> str:
        return f"{wav_path}.json"

    def _tts_text_signature(self, text: str) -> str:
        normalized = self._normalize_tts_text(text)
        return hashlib.sha1(normalized.encode('utf-8')).hexdigest()

    def _read_tts_metadata(self, wav_path: str) -> Dict | None:
        meta_path = self._tts_metadata_path(wav_path)
        if not os.path.exists(meta_path):
            return None
        try:
            with open(meta_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            return None

    def _write_tts_metadata(self, wav_path: str, text: str) -> None:
        duration = self._safe_get_audio_duration(wav_path)
        meta = {
            'validation_version': self.TTS_METADATA_VERSION,
            'signature': self._tts_text_signature(text),
            'normalized_text': self._normalize_tts_text(text),
            'duration': duration,
        }
        with open(self._tts_metadata_path(wav_path), 'w', encoding='utf-8') as f:
            json.dump(meta, f, ensure_ascii=True, indent=2)

    def _safe_get_audio_duration(self, audio_path: str) -> float | None:
        try:
            return self._get_audio_duration(audio_path)
        except Exception:
            return None

    def _get_tts_words(self, text: str) -> list[str]:
        return re.findall(r"[A-Za-z0-9']+", self._normalize_tts_text(text))

    def _should_collect_multiple_tts_candidates(self, text: str, subtitle_duration: float) -> bool:
        words = self._get_tts_words(text)
        if not words:
            return False
        if len(words) == 1 and len(words[0]) <= 5:
            return True
        return len(words) <= 12 or subtitle_duration <= 4.0

    def _get_tts_duration_window(self, text: str, subtitle_duration: float) -> tuple[float, float, float]:
        words = self._get_tts_words(text)
        word_count = len(words)

        if word_count == 0:
            return 0.6, max(2.4, subtitle_duration * 1.6), max(4.0, subtitle_duration * 2.4)

        lower_bound = max(0.7, word_count * 0.16 + 0.30)
        preferred_upper = max(
            lower_bound + 0.45,
            subtitle_duration * 1.35,
            word_count * 0.26 + 0.55,
        )
        hard_upper = max(
            preferred_upper + 1.0,
            subtitle_duration * 2.2,
            word_count * 0.42 + 0.75,
        )
        return lower_bound, preferred_upper, hard_upper

    def _score_tts_candidate(self, text: str, duration: float | None, subtitle_duration: float) -> tuple[float, bool]:
        if duration is None:
            return float('inf'), True

        lower_bound, preferred_upper, hard_upper = self._get_tts_duration_window(text, subtitle_duration)
        hard_fail = duration < lower_bound or duration > hard_upper

        if duration < lower_bound:
            score = (lower_bound - duration) * 3.5
        elif duration <= preferred_upper:
            score = 0.0
        else:
            score = duration - preferred_upper

        if hard_fail:
            score += 5.0

        return score, hard_fail

    def _is_suspicious_tts_duration(self, text: str, duration: float | None, subtitle_duration: float) -> bool:
        _, hard_fail = self._score_tts_candidate(text, duration, subtitle_duration)
        return hard_fail

    def _get_tts_regen_reason(self, text: str, wav_path: str, subtitle_duration: float) -> str | None:
        if not (os.path.exists(wav_path) and os.path.getsize(wav_path) > 1024):
            return 'cache miss'

        metadata = self._read_tts_metadata(wav_path)
        if metadata:
            if metadata.get('validation_version') != self.TTS_METADATA_VERSION:
                return 'validation version changed'
            if metadata.get('signature') != self._tts_text_signature(text):
                return 'text changed'

        duration = self._safe_get_audio_duration(wav_path)
        if self._is_suspicious_tts_duration(text, duration, subtitle_duration):
            if duration is None:
                return 'bad audio'
            return f'suspicious duration {duration:.2f}s'

        return None

    def _synthesize_wav_with_validation(
        self,
        text: str,
        output: str,
        subtitle_duration: float,
        attempts: int = 3,
    ) -> None:
        output_path = Path(output)
        candidates: list[tuple[float, str]] = []
        scored_candidates: list[tuple[float, float, str]] = []
        attempt_paths: list[str] = []
        chosen_path: str | None = None
        collect_multiple = self._should_collect_multiple_tts_candidates(text, subtitle_duration)
        total_attempts = attempts if collect_multiple else 1

        for attempt in range(1, total_attempts + 1):
            candidate = str(output_path.with_name(f'{output_path.stem}.attempt{attempt}{output_path.suffix}'))
            attempt_paths.append(candidate)
            self._synthesize_wav(text, candidate)

            duration = self._safe_get_audio_duration(candidate)
            if duration is None:
                continue

            candidates.append((duration, candidate))
            score, hard_fail = self._score_tts_candidate(text, duration, subtitle_duration)
            scored_candidates.append((score, duration, candidate))

            if hard_fail:
                self.logger.info(
                    f"  TTS candidate duration abnormal, retry {attempt}/{total_attempts}: "
                    f"{duration:.2f}s '{text[:60]}'"
                )
            elif not collect_multiple:
                chosen_path = candidate
                break

        if chosen_path is None and scored_candidates:
            best_score, best_duration, chosen_path = min(
                scored_candidates, key=lambda item: (item[0], item[1]),
            )

        if chosen_path is None and candidates:
            best_duration, chosen_path = min(candidates, key=lambda item: item[0])

        if chosen_path is None:
            self._synthesize_wav(text, output)
        else:
            os.replace(chosen_path, output)

        for path in attempt_paths:
            if path != chosen_path and os.path.exists(path):
                try:
                    os.remove(path)
                except OSError:
                    pass

    def _synthesize_wav(self, text: str, output: str) -> None:
        engine_type = self.config.get('tts_engine', 'engine')
        text = self._normalize_tts_text(text)

        text = text.replace(''', "'").replace(''', "'")
        text = text.replace('"', '"').replace('"', '"')
        text = text.replace('\u2014', '-').replace('\u2013', '-')
        text = text.replace('...', ',').replace('\u2026', ',')

        replacements = {
            "you're": "you are", "it's": "it is", "that's": "that is",
            "don't": "do not", "can't": "can not", "I've": "I have",
            "you've": "you have", "they've": "they have", "he've": "he have",
            "they're": "they are", "we're": "we are", "there's": "there is",
            "here's": "here is", "what's": "what is", "who's": "who is",
            "where's": "where is", "how's": "how is", "let's": "let us",
            "ain't": "is not", "gonna": "going to", "wanna": "want to",
            "gotta": "got to", "kinda": "kind of", "sorta": "sort of",
            "outta": "out of", "lotta": "lot of", "lotsa": "lots of",
        }
        for old, new in replacements.items():
            text = text.replace(old, new)

        for attempt in range(1, 4):
            try:
                if engine_type == 'xtts':
                    self.tts_engine.synthesize(text, output)
                else:
                    mp3 = output.replace('.wav', '.mp3')
                    self.tts_engine.synthesize(text, mp3)
                    if os.path.exists(mp3) and os.path.getsize(mp3) > 1024:
                        audio = AudioSegment.from_file(mp3)
                        audio.export(output, format='wav')

                if os.path.exists(output) and os.path.getsize(output) > 1024:
                    return
                raise RuntimeError("Empty output file")

            except Exception:
                if attempt < 3:
                    time.sleep(2 ** attempt)
                else:
                    sr = self.config.get('tts', 'sample_rate') or 24000
                    AudioSegment.silent(duration=3000, frame_rate=sr).export(output, format='wav')

    def _get_audio_duration(self, audio_path: str) -> float:
        cmd = [
            'ffprobe', '-v', 'error',
            '-show_entries', 'stream=duration',
            '-of', 'default=noprint_wrappers=1:nokey=1',
            audio_path,
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        return float(result.stdout.strip())