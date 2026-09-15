"""Video processing: segment extraction, speed adjustment, concatenation, and subtitle overlay."""

import os
import json
import re
import shutil
import logging
import subprocess
from pathlib import Path
from typing import List


class VideoProcessor:
    def __init__(self, config):
        self.config = config
        self.logger = logging.getLogger(__name__)

    def get_duration(self, video_path: str) -> float:
        cmd = [
            'ffprobe', '-v', 'error',
            '-show_entries', 'format=duration',
            '-of', 'default=noprint_wrappers=1:nokey=1',
            video_path,
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        return float(result.stdout.strip())

    def _probe_streams(self, video_path: str) -> dict:
        cmd = [
            'ffprobe', '-v', 'error',
            '-show_entries', 'stream=codec_type',
            '-of', 'json', video_path,
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        payload = json.loads(result.stdout or '{}')
        streams = payload.get('streams', [])
        return {
            'has_video': any(s.get('codec_type') == 'video' for s in streams),
            'has_audio': any(s.get('codec_type') == 'audio' for s in streams),
        }

    def _run_ffmpeg(self, cmd: List[str], label: str = '') -> subprocess.CompletedProcess:
        label = label or cmd[0]
        result = subprocess.run(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            check=False,
        )
        if result.returncode != 0:
            stderr = (result.stderr or b'').decode('utf-8', errors='replace')[-800:]
            self.logger.error(f"[FFmpeg {label}] Failed (code {result.returncode}):\n{stderr}")
            raise subprocess.CalledProcessError(result.returncode, cmd)
        return result

    def extract_segment(
        self,
        video_path: str,
        start_time: float,
        end_time: float,
        output_path: str,
        include_audio: bool = True,
    ) -> str:
        duration = end_time - start_time
        if duration <= 0:
            raise ValueError(f"Invalid segment: start={start_time}, end={end_time}")

        cmd_base = [
            'ffmpeg', '-y',
            '-i', video_path,
            '-ss', str(start_time),
            '-t', str(duration),
            '-map', '0:v:0',
        ]
        if include_audio:
            cmd_base.extend(['-map', '0:a:0?'])
        else:
            cmd_base.append('-an')

        encode_cmd = cmd_base + [
            '-c:v', self.config.get('video', 'codec'),
            '-preset', 'fast',
            '-crf', str(self.config.get('video', 'crf')),
            '-pix_fmt', self.config.get('video', 'pix_fmt'),
            '-c:a', self.config.get('video', 'audio_codec'),
            '-b:a', self.config.get('video', 'audio_bitrate'),
            '-movflags', '+faststart',
            output_path,
        ]
        self._run_ffmpeg(encode_cmd, 'extract_segment')
        return output_path

    def adjust_speed(self, video_path: str, speed_ratio: float, output_path: str) -> str:
        if speed_ratio <= 0:
            raise ValueError(f"Speed ratio must be > 0, got {speed_ratio}")

        pts_ratio = 1.0 / speed_ratio

        cmd = [
            'ffmpeg', '-y',
            '-i', video_path,
            '-filter:v', f'setpts={pts_ratio:.6f}*PTS',
            '-an',
            '-c:v', self.config.get('video', 'codec'),
            '-preset', self.config.get('video', 'preset'),
            '-crf', str(self.config.get('video', 'crf')),
            '-pix_fmt', self.config.get('video', 'pix_fmt'),
            '-movflags', '+faststart',
            output_path,
        ]
        self._run_ffmpeg(cmd, f'adjust_speed({speed_ratio:.3f}x)')
        return output_path

    def adjust_speed_in_range(
        self,
        video_path: str,
        range_start: float,
        range_end: float,
        speed_ratio: float,
        output_path: str,
    ) -> str:
        if speed_ratio <= 0:
            raise ValueError("Speed ratio must be > 0")

        total = self.get_duration(video_path)
        rs = max(0.0, min(float(range_start), total))
        re = max(rs, min(float(range_end), total))
        if re - rs <= 1e-4:
            shutil.copyfile(video_path, output_path)
            return output_path

        cache = self.config.get('directories', 'cache')
        Path(cache).mkdir(parents=True, exist_ok=True)
        base = output_path.replace('.mp4', '')
        part_files = []

        if rs > 0.01:
            pre_path = f'{base}_pre.mp4'
            self.extract_segment(video_path, 0.0, rs, pre_path, include_audio=False)
            part_files.append(pre_path)

        core_path = f'{base}_core.mp4'
        self.extract_segment(video_path, rs, re, core_path, include_audio=False)

        if abs(speed_ratio - 1.0) > 1e-4:
            core_adj = f'{base}_core_adj.mp4'
            self.adjust_speed(core_path, speed_ratio, core_adj)
        else:
            core_adj = core_path

        part_files.append(core_adj)

        if total - re > 0.01:
            post_path = f'{base}_post.mp4'
            self.extract_segment(video_path, re, total, post_path, include_audio=False)
            part_files.append(post_path)

        self._concatenate_videos(part_files, output_path, delete_parts=True)
        return output_path

    def merge_audio_video(
        self,
        video_path: str,
        audio_path: str,
        output_path: str,
    ) -> str:
        video_duration = self.get_duration(video_path)
        audio_duration = self._get_audio_duration(audio_path)

        self.logger.debug(
            f"[merge] video={video_duration:.2f}s audio={audio_duration:.2f}s"
        )

        cmd = [
            'ffmpeg', '-y',
            '-i', video_path,
            '-i', audio_path,
            '-map', '0:v:0',
            '-map', '1:a:0',
            '-c:v', self.config.get('video', 'codec'),
            '-preset', self.config.get('video', 'preset'),
            '-crf', str(self.config.get('video', 'crf')),
            '-pix_fmt', self.config.get('video', 'pix_fmt'),
            '-c:a', self.config.get('video', 'audio_codec'),
            '-b:a', self.config.get('video', 'audio_bitrate'),
            '-movflags', '+faststart',
            output_path,
        ]
        self._run_ffmpeg(cmd, 'merge')
        return output_path

    def write_english_srt(
        self,
        subtitles,
        output_path: str,
        time_offset: float = 0.0,
        segment_info: list = None,
    ) -> str:
        def fmt_srt_time(seconds: float) -> str:
            total_ms = int(round(seconds * 1000))
            h = total_ms // 3600000
            total_ms %= 3600000
            m = total_ms // 60000
            total_ms %= 60000
            s = total_ms // 1000
            ms = total_ms % 1000
            return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"

        Path(output_path).parent.mkdir(parents=True, exist_ok=True)

        if segment_info:
            seg_lookup = {s['index']: s for s in segment_info}
            cursor_ms = 0.0
            subtitle_positions = {}

            for sub_idx, sub in enumerate(subtitles):
                seg = seg_lookup.get(sub_idx)
                if not seg:
                    continue
                gap_dur_ms = seg['gap_dur'] * 1000
                adjusted_dur_ms = seg['adjusted_dur'] * 1000

                cursor_ms += max(0, gap_dur_ms)
                tts_start_ms = cursor_ms
                tts_end_ms = tts_start_ms + adjusted_dur_ms
                subtitle_positions[sub_idx] = (
                    tts_start_ms / 1000.0,
                    tts_end_ms / 1000.0,
                )
                cursor_ms += adjusted_dur_ms

            with open(output_path, 'w', encoding='utf-8') as f:
                out_index = 1
                for sub_idx, sub in enumerate(subtitles):
                    english = (sub.english_text or '').strip()
                    if english:
                        english = re.sub(r'[\u2018\u2019]', "'", english)
                        english = re.sub(r'[\u201c\u201d]', '"', english)
                        english = re.sub(r'[\u2014\u2013]', '-', english)
                        english = re.sub(r'[\u2026\u2025]', '...', english)
                    if not english:
                        continue
                    pos = subtitle_positions.get(sub_idx)
                    if pos:
                        start_time, end_time = pos
                    else:
                        start_time = max(0.0, sub.start_time - time_offset)
                        end_time = max(start_time, sub.end_time - time_offset)
                    f.write(f"{out_index}\n")
                    f.write(f"{fmt_srt_time(start_time)} --> {fmt_srt_time(end_time)}\n")
                    f.write(f"{english}\n\n")
                    out_index += 1
        else:
            with open(output_path, 'w', encoding='utf-8') as f:
                out_index = 1
                for sub in subtitles:
                    english = (sub.english_text or '').strip()
                    if english:
                        english = re.sub(r'[\u2018\u2019]', "'", english)
                        english = re.sub(r'[\u201c\u201d]', '"', english)
                        english = re.sub(r'[\u2014\u2013]', '-', english)
                        english = re.sub(r'[\u2026\u2025]', '...', english)
                    if not english:
                        continue
                    start_time = max(0.0, sub.start_time - time_offset)
                    end_time = max(start_time, sub.end_time - time_offset)
                    f.write(f"{out_index}\n")
                    f.write(f"{fmt_srt_time(start_time)} --> {fmt_srt_time(end_time)}\n")
                    f.write(f"{english}\n\n")
                    out_index += 1

        return output_path

    def apply_subtitle_overlay(
        self,
        video_path: str,
        english_srt_path: str,
        output_path: str,
    ) -> str:
        ov = self.config.get('subtitle_overlay')
        h_ratio = ov['bottom_region_height_ratio']
        bottom_margin_ratio = ov['bottom_margin_ratio']
        blur_r = ov['blur_luma_radius']
        blur_p = ov['blur_luma_power']
        box_color = ov['box_color']
        font_name = ov['font_name']
        font_size = ov['font_size']
        font_color = ov['font_color']
        border_color = ov['border_color']
        border_width = ov['border_width']
        alignment = ov['alignment']
        margin_v = ov['margin_v']

        esc_srt = english_srt_path.replace('\\', '/').replace(':', '\\:').replace("'", "\\'")
        style = (
            f"FontName={font_name},FontSize={font_size},PrimaryColour=&HFFFFFF&,"
            f"OutlineColour=&H000000&,BorderStyle=1,Outline={border_width},Shadow=0,"
            f"Alignment={alignment},MarginV={margin_v}"
        )
        filter_complex = (
            f"[0:v]split=2[main][blur];"
            f"[blur]crop=w=iw:h=ih*{h_ratio}:x=0:y=ih*(1-{h_ratio}-{bottom_margin_ratio}),"
            f"boxblur=luma_radius={blur_r}:luma_power={blur_p}[blurred];"
            f"[main][blurred]overlay=x=0:y=H-h-h*{bottom_margin_ratio}[tmp1];"
            f"[tmp1]drawbox=x=0:y=ih*(1-{h_ratio}-{bottom_margin_ratio}):w=iw:h=ih*{h_ratio}:"
            f"color={box_color}:t=fill[tmp2];"
            f"[tmp2]subtitles='{esc_srt}':force_style='{style}'[vout]"
        )

        cmd = [
            'ffmpeg', '-y',
            '-i', video_path,
            '-filter_complex', filter_complex,
            '-map', '[vout]',
            '-an',
            '-c:v', self.config.get('video', 'codec'),
            '-preset', self.config.get('video', 'preset'),
            '-crf', str(self.config.get('video', 'crf')),
            '-pix_fmt', self.config.get('video', 'pix_fmt'),
            '-movflags', '+faststart',
            output_path,
        ]
        self._run_ffmpeg(cmd, 'subtitle_overlay')
        return output_path

    def _get_audio_duration(self, audio_path: str) -> float:
        cmd = [
            'ffprobe', '-v', 'error',
            '-show_entries', 'format=duration',
            '-of', 'default=noprint_wrappers=1:nokey=1',
            audio_path,
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        return float(result.stdout.strip())

    def concatenate_videos(self, video_paths: List[str], output_path: str) -> str:
        if not video_paths:
            raise ValueError("No video paths to concatenate")
        if len(video_paths) == 1:
            shutil.copyfile(video_paths[0], output_path)
            return output_path

        return self._concatenate_videos(video_paths, output_path, delete_parts=False, include_audio=False)

    def _concatenate_videos(
        self,
        video_paths: List[str],
        output_path: str,
        delete_parts: bool = False,
        include_audio: bool = True,
    ) -> str:
        cache = self.config.get('directories', 'cache')
        Path(cache).mkdir(parents=True, exist_ok=True)

        def concat_batch(paths: List[str], out_path: str, label: str) -> None:
            if not paths:
                raise ValueError("Empty batch")
            if len(paths) == 1:
                shutil.copyfile(paths[0], out_path)
                return

            cmd = ['ffmpeg', '-y']
            for p in paths:
                cmd.extend(['-i', p])

            if include_audio:
                concat_inputs = ''.join(f'[{i}:v:0][{i}:a:0]' for i in range(len(paths)))
                filter_complex = f'{concat_inputs}concat=n={len(paths)}:v=1:a=1[outv][outa]'
                cmd.extend([
                    '-filter_complex', filter_complex,
                    '-map', '[outv]', '-map', '[outa]',
                    '-c:v', self.config.get('video', 'codec'),
                    '-preset', self.config.get('video', 'preset'),
                    '-crf', str(self.config.get('video', 'crf')),
                    '-pix_fmt', self.config.get('video', 'pix_fmt'),
                    '-c:a', self.config.get('video', 'audio_codec'),
                    '-b:a', self.config.get('video', 'audio_bitrate'),
                    '-movflags', '+faststart',
                    out_path,
                ])
            else:
                concat_inputs = ''.join(f'[{i}:v:0]' for i in range(len(paths)))
                filter_complex = f'{concat_inputs}concat=n={len(paths)}:v=1:a=0[outv]'
                cmd.extend([
                    '-filter_complex', filter_complex,
                    '-map', '[outv]', '-an',
                    '-c:v', self.config.get('video', 'codec'),
                    '-preset', self.config.get('video', 'preset'),
                    '-crf', str(self.config.get('video', 'crf')),
                    '-pix_fmt', self.config.get('video', 'pix_fmt'),
                    '-movflags', '+faststart',
                    out_path,
                ])
            self._run_ffmpeg(cmd, label)

        batch_size = 12
        batches = [video_paths[i:i + batch_size] for i in range(0, len(video_paths), batch_size)]

        self.logger.info(f"Concatenating: {len(video_paths)} clips -> {len(batches)} batches")

        batch_outputs: List[str] = []
        for batch_idx, batch in enumerate(batches):
            batch_output = os.path.join(cache, f'_batch_{batch_idx:03d}.mp4')
            batch_outputs.append(batch_output)
            concat_batch(batch, batch_output, f'batch_{batch_idx:03d}')

        if len(batch_outputs) == 1:
            shutil.move(batch_outputs[0], output_path)
        else:
            self.logger.info(f"Final concat: {len(batch_outputs)} batches")
            concat_batch(batch_outputs, output_path, 'final_concat')
            for p in batch_outputs:
                if os.path.exists(p):
                    try:
                        os.remove(p)
                    except OSError:
                        pass

        if delete_parts:
            for p in video_paths:
                if os.path.exists(p) and p != output_path:
                    try:
                        os.remove(p)
                    except OSError:
                        pass

        return output_path