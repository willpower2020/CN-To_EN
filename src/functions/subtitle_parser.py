# -*- coding: utf-8 -*-
"""
字幕解析模块（subtitle_parser.py）
===================================
解析 SRT 格式字幕文件，提取时间轴、中文原文和英文翻译。

字幕文件格式（每条）：
    1
    00:01:12,000 --> 00:01:14,000
    你的境界是
    Your meditative state is

说明：
    - 每条字幕第1行：序号
    - 第2行：时间轴
    - 第3行：中文原文
    - 第4行：英文翻译
    - 编码：自动识别 UTF-8 / GB18030 / GBK
"""

import re
import logging
from typing import List
from src.models import SubtitleEntry


logger = logging.getLogger(__name__)


class SubtitleParser:
    """
    SRT 字幕解析器

    使用示例：
        parser = SubtitleParser()
        subtitles = parser.parse('video.srt')
        for sub in subtitles:
            print(sub.start_time, sub.english_text)
    """

    # SRT 时间轴正则：00:01:12,000 --> 00:01:14,000
    TIME_PATTERN = re.compile(
        r'(\d{2}):(\d{2}):(\d{2})[,.](\d{3})\s*-->\s*(\d{2}):(\d{2}):(\d{2})[,.](\d{3})'
    )

    @staticmethod
    def _time_to_seconds(h: str, m: str, s: str, ms: str) -> float:
        """
        将 SRT 时间格式转换为秒。

        示例：'01', '12', '00', '000' → 3720.0
        """
        return int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000.0

    @staticmethod
    def _is_chinese(text: str) -> bool:
        """判断文本是否包含中文字符"""
        return bool(re.search(r'[\u4e00-\u9fff]', text))

    def _parse_time_match(self, m) -> tuple:
        """从正则匹配对象中提取并计算（开始时间, 结束时间）"""
        g = m.groups()
        start = self._time_to_seconds(g[0], g[1], g[2], g[3])
        end   = self._time_to_seconds(g[4], g[5], g[6], g[7])
        return start, end

    def parse(self, srt_path: str) -> List[SubtitleEntry]:
        """
        解析 SRT 文件。

        参数：
            srt_path —— SRT 文件路径

        返回：
            List[SubtitleEntry]，按时间顺序排列
        """
        content = self._read_with_auto_encoding(srt_path)
        # 统一换行符为 \n（处理 Windows \r\n）
        content = content.replace('\r\n', '\n').replace('\r', '\n')
        return self._parse_content(content)

    def _read_with_auto_encoding(self, path: str) -> str:
        """
        自动识别文件编码并读取内容。

        尝试顺序：UTF-8-sig → UTF-8 → GB18030 → GBK
        """
        for enc in ('utf-8-sig', 'utf-8', 'gb18030', 'gbk'):
            try:
                with open(path, encoding=enc) as f:
                    return f.read()
            except UnicodeDecodeError:
                continue

        raise UnicodeDecodeError(
            'unknown', b'', 0, 1,
            f'无法识别文件编码: {path}'
        )

    def _parse_content(self, content: str) -> List[SubtitleEntry]:
        """
        核心解析逻辑：将 SRT 文本内容转为字幕条目列表。

        文件格式确认（两种均支持）：
            有空行版：  "1\n00:00:01 --> 00:00:03\n中文\n英文\n\n2\n..."
            无空行版：  "1\n00:00:01 --> 00:00:03\n中文\n英文\n2\n..."

        解析策略：
            1. 找出所有时间轴行的位置（字节/字符偏移量）
            2. 每个时间轴对应一条字幕
            3. 取该时间轴到下一时间轴之间的所有文本行
            4. 第1文本行 = 中文，第2文本行 = 英文
        """
        lines = content.split('\n')
        entries: List[SubtitleEntry] = []

        # 找出所有时间轴行的行索引
        time_line_indices: List[int] = []
        for i, line in enumerate(lines):
            if self.TIME_PATTERN.match(line.strip()):
                time_line_indices.append(i)

        if not time_line_indices:
            logger.warning("[字幕解析] 未找到任何时间轴行")
            return []

        logger.debug(f"[字幕解析] 找到 {len(time_line_indices)} 个时间轴")

        for idx, time_line_idx in enumerate(time_line_indices):
            # 解析时间轴
            match = self.TIME_PATTERN.match(lines[time_line_idx].strip())
            if not match:
                continue
            start_time, end_time = self._parse_time_match(match)

            # 确定字幕文本范围：从时间轴行之后到下一时间轴行之前
            next_time_line_idx = time_line_indices[idx + 1] if idx + 1 < len(time_line_indices) else len(lines)

            # 收集所有非空文本行（跳过时间轴行和序号行）
            # 序号行 = 纯数字（如 "1", "2", "108"），不是字幕文本
            text_lines: List[str] = []
            for line in lines[time_line_idx + 1: next_time_line_idx]:
                stripped = line.strip()
                # 排除：空行、仅数字的行（字幕序号）、纯时间轴残留
                if stripped and not stripped.isdigit():
                    text_lines.append(stripped)

            # 提取中文和英文
            chinese = ''
            english = ''
            for line in text_lines:
                if self._is_chinese(line):
                    chinese += line + ' '
                else:
                    english += line + ' '

            chinese = chinese.strip()
            english = english.strip()

            # 过滤：必须有中文和英文
            if chinese and english:
                entries.append(SubtitleEntry(
                    index        = len(entries) + 1,
                    start_time   = start_time,
                    end_time     = end_time,
                    chinese_text = chinese,
                    english_text = english,
                ))

        if entries:
            logger.info(
                f"[字幕解析] 共解析 {len(entries)} 条字幕，"
                f"时间范围 {entries[0].start_time:.1f}s ~ {entries[-1].end_time:.1f}s"
            )
        return entries
