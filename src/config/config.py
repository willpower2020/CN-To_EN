# -*- coding: utf-8 -*-
"""视频配音系统配置文件"""

import os
from pathlib import Path


_CONFIG = {
    # ── 目录设置 ──────────────────────────────────────────────────
    'directories': {
        'video_source':    './data/videos',      # 视频源文件目录
        'subtitle_source': './data/subtitles',   # 字幕文件目录
        'output':          './data/output',       # 输出文件目录
        'cache':           './data/cache',        # 缓存目录（临时文件）
    },


    # ── TTS 语音合成引擎 ─────────────────────────────────────────
    'tts_engine': {
        # 引擎选择: 'elevenlabs'（付费，高质量，需 API Key）
        #           'xtts'（免费，本地运行，需 GPU）
        'engine': 'xtts',

        'xtts': {
            'model_name': 'tts_models/multilingual/multi-dataset/xtts_v2',  # XTTS v2 模型名称
            'device':     'cuda',     # 运行设备: 'cuda'(GPU) 或 'cpu'
            'language':   'en',       # 合成语言: 英语
        },

        'elevenlabs': {
            'model_id':           'eleven_v3',    # ElevenLabs 模型版本
            'output_format':      'mp3_44100_128', # 输出音频格式
            'language':           'en',            # 合成语言
            'stability':          0.3,             # 语音稳定性 (0~1, 越低越有变化，更自然)
            'similarity_boost':   0.95,            # 音色相似度 (0~1, 越高越接近原声，建议 0.9~1.0)
            'style':              0.0,             # 风格表现力 (0~1, 越高越有表现力)
            'use_speaker_boost':  True,            # 增强说话人音色特征
        },
    },

    # ── 音频参数 ──────────────────────────────────────────────────
    'tts': {
        'sample_rate': 44100,  # 音频采样率 (Hz), CD 品质
    },

    # ── 音频后处理 ────────────────────────────────────────────────
    'audio_post': {
        'trim_leading_silence_ms':   180,   # 裁剪开头静音 (毫秒)
        'trim_trailing_silence_ms':  260,   # 裁剪尾部静音 (毫秒)
        'leading_pad_ms':            25,    # 开头填充静音 (毫秒)
        'trailing_pad_ms':           35,    # 尾部填充静音 (毫秒)
        'silence_threshold_db':      -40,   # 静音判定阈值 (dB)
        'merge_gap_ms':              70,    # 合并相邻语音段的最大间隔 (毫秒)
        'edge_noise_max_ms':         120,   # 边缘噪声处理最大时长 (毫秒)
        'edge_noise_gap_ms':         60,    # 边缘噪声间隔 (毫秒)
        'target_tts_dbfs':           -18.0, # 目标音量 (dBFS)
        'max_gain_db':               4.0,   # 最大增益 (dB)
        'fade_in_ms':                30,    # 淡入时长 (毫秒)
        'fade_out_ms':               80,    # 淡出时长 (毫秒)
        'enable_lowpass_filter':     True,  # 启用低通滤波去高频噪声
        'lowpass_cutoff_hz':        16000, # 低通截止频率 (Hz) — 人声保真
        'enable_eq':                 True,  # 启用人声均衡器
        'eq_bass_gain_db':           1.5,   # 低频增益 (100-300Hz)
        'eq_presence_gain_db':       2.0,   # 临场感增益 (2-4kHz)
        'eq_air_gain_db':            1.0,   # 空气感增益 (8-12kHz)
    },

    # ── 音色克隆 ──────────────────────────────────────────────────
    'voice_cloning': {
        'min_audio_length': 15.0,  # 参考音频最短时长 (秒)
        'max_audio_length': 30.0,  # 参考音频最长时长 (秒)
    },

    # ── 情感迁移 ──────────────────────────────────────────────────
    'emotion': {
        'enable_emotion_transfer': False,  # 是否启用情感迁移（音调/能量/语速）
        'pitch_shift_range':  [-3, 3],     # 音调偏移范围 (半音)
        'energy_scale_range': [0.75, 1.25],  # 能量缩放范围
    },

    # ── 音画同步 ──────────────────────────────────────────────────
    'sync': {
        'enable_video_speed_adjust': True,   # 是否允许调整视频片段速度以匹配 TTS
        'min_speed_ratio':           0.50,   # 最小播放倍速
        'max_speed_ratio':           1.80,   # 最大播放倍速
        'mute_padding_seconds':      2.5,    # 静音填充时长 (秒)
        'replace_start_shift_seconds': 0.0,  # 替换起始时间偏移 (秒)
        'replace_end_shift_seconds':   0.0,  # 替换结束时间偏移 (秒)
    },

    # ── 字幕叠加 ──────────────────────────────────────────────────
    'subtitle_overlay': {
        'enable':                    True,       # 是否叠加英文字幕到视频
        'bottom_region_height_ratio': 0.18,      # 底部模糊区域高度比例
        'bottom_margin_ratio':        0.04,      # 底部边距比例
        'blur_luma_radius':          8,          # 亮度模糊半径
        'blur_luma_power':           1,          # 亮度模糊强度
        'box_color':                 'black@0.45', # 字幕背景色及透明度
        'font_size':                 22,          # 字体大小
        'font_name':                 'Microsoft YaHei',  # 字体名称
        'font_color':                'white',     # 字体颜色
        'border_color':              'black',     # 描边颜色
        'border_width':              2,           # 描边宽度
        'alignment':                 2,           # 对齐方式 (2=居中)
        'margin_v':                  28,          # 垂直边距
    },

    # ── 视频编码输出 ──────────────────────────────────────────────
    'video': {
        'codec':        'libx264',   # 视频编码器
        'preset':       'fast',      # 编码预设: ultrafast/fast/medium/slow
        'crf':          23,          # 质量因子 (18~28, 越小质量越高)
        'audio_codec':  'aac',       # 音频编码器
        'audio_bitrate': '320k',     # 音频码率
        'pix_fmt':      'yuv420p',  # 像素格式
    },

    # ── 调试 ──────────────────────────────────────────────────────
    'debug': {
        'save_intermediate_files': False,  # 是否保存中间文件（用于调试）
    },
}


class Config:
    """配置管理类"""

    def __init__(self):
        self._cfg = _CONFIG
        self._setup_directories()

    def get(self, *keys):
        """获取配置值，支持多级键，如 config.get('tts_engine', 'engine')"""
        val = self._cfg
        for k in keys:
            val = val[k]
        return val

    def _setup_directories(self):
        """自动创建所需目录"""
        for path in self._cfg['directories'].values():
            Path(path).mkdir(parents=True, exist_ok=True)
