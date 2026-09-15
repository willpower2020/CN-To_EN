# 视频配音系统

将视频中的中文音频替换为英文配音，保留原说话人的音色。

## 功能特点

- **音色克隆** — 从原视频中提取说话人音色
- **双语字幕支持** — 中文原文 + 英文翻译
- **视频变速匹配** — 自动调整视频速度以匹配英文配音时长
- **字幕叠加** — 在原中文字幕区域覆盖英文字幕
- **双 TTS 引擎** — ElevenLabs（付费，高质量）或 XTTS v2（免费，本地）

## 项目结构

```
D:\PycharmProjects\music\
├── main.py                   # 入口程序
├── src/
│   ├── config/              # 配置
│   │   └── config.py
│   ├── models/              # 数据结构
│   │   └── models.py
│   └── functions/           # 功能函数
│       ├── subtitle_parser.py   # 字幕解析
│       ├── tts_engine.py      # TTS 引擎
│       ├── audio_processor.py  # 音频处理
│       ├── video_processor.py  # 视频处理
│       └── sync_manager.py    # 音画同步
├── data/
│   ├── videos/            # 【输入】视频文件（MP4）
│   ├── subtitles/        # 【输入】字幕文件（SRT）
│   ├── output/           # 【输出】配音后的视频
│   └── cache/           # 临时缓存（自动清理）
├── .env                 # API Key 配置
└── requirements.txt
```

## 数据目录说明

| 目录 | 说明 | 内容示例 |
|------|------|----------|
| `data/videos/` | 输入视频，放入 MP4 文件 | `220104 №51578 继续闻思修 H265-高清-720P.mp4` |
| `data/subtitles/` | 输入字幕，放入 SRT 文件 | `220104 №51578 继续闻思修 H265-高清-720P.srt` |
| `data/output/` | 输出视频，自动生成 | `220104 №51578 继续闻思修 H265-高清-720P_EN.mp4` |
| `data/cache/` | 临时文件，无需手动管理 | TTS 音频、片段等，处理后自动清理 |

## 快速开始

### 1. 安装依赖

```bash
pip install -r requirements.txt
```

### 2. 配置 API Key（使用 ElevenLabs 时需要）

```bash
cp .env.example .env
# 编辑 .env 文件，填入 ELEVENLABS_API_KEY=你的密钥
```

### 3. 放入视频和字幕

将 MP4 视频放入 `data/videos/`，将 SRT 字幕放入 `data/subtitles/`。文件名尽量保持一致，系统会自动匹配。

### 4. 运行

```bash
python main.py
```

## 字幕格式要求

SRT 文件每条字幕包含中文原文和英文翻译，第三行中文、第四行英文：

```
1
00:01:12,000 --> 00:01:14,000
你的境界是
Your meditative state is

2
00:01:15,000 --> 00:01:18,000
这是空性
This is emptiness
```

## 配置说明

编辑 `src/config/config.py`：

| 配置项 | 说明 | 默认值 |
|--------|------|--------|
| `tts_engine.engine` | TTS 引擎：`'elevenlabs'` 或 `'xtts'` | `xtts` |
| `sync.min_speed_ratio` | 视频最慢速度比例 | `0.50` |
| `sync.max_speed_ratio` | 视频最快速度比例 | `1.80` |
| `sync.mute_padding_seconds` | 字幕前后静音缓冲区（秒） | `2.5` |

切换引擎只需修改一行：

```python
'tts_engine': {
    'engine': 'xtts',        # 免费，本地（需 GPU）
    # 'engine': 'elevenlabs', # 付费，云端（需配置 API Key）
}
```

## 命令行选项

```bash
# 批量处理（自动匹配 data/videos/ 中的视频与 data/subtitles/ 中的字幕）
python main.py

# 检查依赖是否安装完整
python main.py --check-deps

# 详细日志输出
python main.py --verbose

# 处理单个视频（跳过自动匹配）
python main.py --video data/videos/你的视频.mp4 --subtitle data/subtitles/你的字幕.srt

# 调试模式：只处理前5条字幕（快速测试）
python main.py --max-subtitles 5

# 样片模式：处理前N条字幕的时间窗口
python main.py --sample-window-padding 5
```

## TTS 引擎对比

| 引擎 | 费用 | 质量 | 速度 | 需要 |
|------|------|------|------|------|
| XTTS v2 | 免费 | 较好 | 较慢（本地 GPU） | GPU |
| ElevenLabs | 付费（Starter 有免费额度） | 高 | 快（云端） | API Key |
