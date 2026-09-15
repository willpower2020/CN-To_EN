# Video Dubbing System

Replace Chinese audio in videos with English TTS while preserving the original speaker's voice.

## Features

- **Voice cloning** from original video speaker
- **Bilingual subtitle support** (Chinese original + English translation)
- **Video speed adjustment** to match English audio duration
- **Subtitle overlay** on the original Chinese subtitle region
- **Dual TTS engines**: ElevenLabs (paid, high quality) or XTTS v2 (free, local)

## Project Structure

```
D:\PycharmProjects\music\
├── main.py
├── src/
│   ├── config/              # Configuration
│   │   └── config.py
│   ├── models/              # Data structures
│   │   └── models.py
│   └── functions/           # Core functions
│       ├── subtitle_parser.py
│       ├── tts_engine.py
│       ├── audio_processor.py
│       ├── video_processor.py
│       └── sync_manager.py
├── data/
│   ├── videos/           # Input videos (MP4)
│   ├── subtitles/       # Input subtitles (SRT)
│   ├── output/          # Output dubbed videos
│   └── cache/          # Temporary files (auto-cleaned)
├── .env                 # API Key
└── requirements.txt
```

## Data Directories

| Directory | Description | Example |
|-----------|-------------|---------|
| `data/videos/` | Input MP4 videos | `220104 №51578 continue闻思修 H265-高清-720P.mp4` |
| `data/subtitles/` | Input SRT subtitles | `220104 №51578 continue闻思修 H265-高清-720P.srt` |
| `data/output/` | Output dubbed videos | `220104 №51578 continue闻思修 H265-高清-720P_EN.mp4` |
| `data/cache/` | Temp files, auto-cleaned | `_tts_000.wav` etc. |

## Quick Start

### 1. Install dependencies

```bash
pip install -r requirements.txt
```

### 2. Configure API Key (for ElevenLabs)

```bash
cp .env.example .env
# Edit .env and add ELEVENLABS_API_KEY
```

### 3. Add files

Place MP4 videos in `data/videos/` and SRT subtitles in `data/subtitles/`. File names should match for auto-pairing.

### 4. Run

```bash
python main.py
```

## SRT Subtitle Format

Each subtitle entry has Chinese on line 3 and English on line 4:

```
1
00:01:12,000 --> 00:01:14,000
你的境界是
Your meditative state is
```

## Configuration

Edit `src/config/config.py`:

| Setting | Description | Default |
|---------|-------------|---------|
| `tts_engine.engine` | `'elevenlabs'` or `'xtts'` | `xtts` |
| `sync.min/max_speed_ratio` | Video speed bounds | `0.50` ~ `1.80` |
| `sync.mute_padding_seconds` | Silence buffer around subtitles | `2.5` |

## CLI Options

```bash
# Batch process (auto-pair videos and subtitles)
python main.py

# Check dependencies
python main.py --check-deps

# Verbose logging
python main.py --verbose

# Single video
python main.py --video data/videos/X.mp4 --subtitle data/subtitles/X.srt

# Debug: first N subtitles only
python main.py --max-subtitles 5

# Sample window mode
python main.py --sample-window-padding 5
```

## TTS Engine Comparison

| Engine | Cost | Quality | Speed |
|--------|------|---------|-------|
| ElevenLabs | Paid | High | Fast (cloud) |
| XTTS v2 | Free | Good | Slower (local GPU) |
