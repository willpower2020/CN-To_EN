"""Main entry point for the video dubbing system."""

import os
import re
import sys
import logging
import argparse
import traceback
import warnings
from pathlib import Path

warnings.filterwarnings('ignore', category=UserWarning)


def check_dependencies():
    from src.config.config import Config

    try:
        from dotenv import load_dotenv
        load_dotenv()
    except Exception:
        pass

    cfg = Config()
    engine = cfg.get('tts_engine', 'engine')

    missing: list[str] = []
    notes: list[str] = []

    for cmd in ('ffmpeg', 'ffprobe'):
        r = os.system(f'{cmd} -version >nul 2>&1')
        if r != 0:
            missing.append(cmd)

    required = {
        'dotenv': 'pip install python-dotenv',
        'librosa': 'pip install librosa',
        'soundfile': 'pip install soundfile',
        'pydub': 'pip install pydub',
        'numpy': 'pip install numpy',
    }

    if engine == 'elevenlabs':
        required['elevenlabs'] = 'pip install elevenlabs'
        if not os.environ.get('ELEVENLABS_API_KEY'):
            missing.append('ELEVENLABS_API_KEY')
        else:
            notes.append("  ElevenLabs API Key configured [OK]")

    elif engine == 'xtts':
        required['TTS'] = 'pip install TTS'
        try:
            import torch  # noqa: F401
        except ImportError:
            missing.append('torch')

    elif engine == 'fishspeech':
        required['fish-speech'] = 'pip install fish-speech'
        try:
            import torch  # noqa: F401
        except ImportError:
            missing.append('torch')

    for pkg, install_cmd in required.items():
        try:
            __import__(pkg)
        except ImportError:
            missing.append(pkg)

    if not missing:
        print(f"  [OK] Dependencies passed (engine: {engine})")
        for n in notes:
            print(n)
        return True

    print(f"  [FAIL] Missing: {', '.join(missing)}")
    return False


def setup_logging(verbose: bool = True):
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

    level = logging.DEBUG if verbose else logging.INFO
    for h in logging.root.handlers[:]:
        logging.root.removeHandler(h)

    logging.basicConfig(
        level=level,
        format='%(asctime)s  %(levelname)-8s  %(message)s',
        handlers=[
            logging.FileHandler('processing.log', encoding='utf-8'),
            logging.StreamHandler(sys.stdout),
        ],
    )

    for noisy in ('numba', 'fsspec', 'aiohttp', 'urllib3', 'matplotlib'):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def find_matching_video(srt_path: str, video_dir: str) -> str | None:
    srt_stem = Path(srt_path).stem
    srt_digits = ''.join(re.findall(r'\d+', srt_stem))

    video_extensions = {'.mp4', '.mov', '.avi', '.mkv', '.flv'}
    video_dir_path = Path(video_dir)

    if not video_dir_path.exists():
        return None

    candidates = [
        f for f in video_dir_path.iterdir()
        if f.is_file() and f.suffix.lower() in video_extensions
    ]

    for v in candidates:
        if v.stem == srt_stem:
            return str(v)

    for v in candidates:
        norm_srt = re.sub(r'[\s·\-\(\)（）]', '', srt_stem)
        norm_v = re.sub(r'[\s·\-\(\)（）]', '', v.stem)
        if norm_srt == norm_v:
            return str(v)

    for v in candidates:
        v_digits = ''.join(re.findall(r'\d+', v.stem))
        if srt_digits and srt_digits == v_digits:
            return str(v)

    return None


def process_single(
    video_path: str,
    srt_path: str,
    output_path: str,
    cfg,
    max_subtitles: int | None = None,
    sample_window_padding: float | None = None,
):
    from src.config.config import Config
    from src.models.models import SubtitleEntry
    from src.functions.subtitle_parser import SubtitleParser
    from src.functions.tts_engine import create_tts_engine
    from src.functions.audio_processor import AudioProcessor
    from src.functions.video_processor import VideoProcessor
    from src.functions.sync_manager import SyncManager

    log = logging.getLogger(__name__)
    log.info("")
    log.info("Video Dubbing System")
    log.info(f"  Video: {video_path}")
    log.info(f"  Subtitle: {srt_path}")
    log.info(f"  Output: {output_path}")

    log.info("[1/5] Parsing subtitles ...")
    parser = SubtitleParser()
    subtitles = parser.parse(srt_path)

    if not subtitles:
        log.error("  Subtitle empty, exiting")
        return False

    target_subs = [s for s in subtitles if s.english_text.strip()]
    if max_subtitles is not None and max_subtitles > 0:
        target_subs = target_subs[:max_subtitles]

    window_start = None
    window_end = None
    if sample_window_padding is not None and sample_window_padding >= 0 and target_subs:
        window_start = max(0.0, target_subs[0].start_time - sample_window_padding)
        window_end = target_subs[-1].end_time + sample_window_padding
        target_subs = [
            s for s in subtitles
            if s.english_text.strip() and s.start_time >= window_start and s.end_time <= window_end
        ]

    log.info(f"  {len(subtitles)} subtitles, {len(target_subs)} with English")
    if window_start is not None and window_end is not None:
        log.info(f"  Window: {window_start:.1f}s ~ {window_end:.1f}s")

    log.info("[2/5] Initializing TTS engine ...")
    tts_engine = create_tts_engine(cfg)
    audio_processor = AudioProcessor(cfg)
    video_processor = VideoProcessor(cfg)
    sync_manager = SyncManager(cfg, tts_engine, audio_processor, video_processor)

    log.info("[3/5] Processing ...")
    try:
        sync_manager.process_video(
            video_path,
            target_subs,
            output_path,
            window_start=window_start,
            window_end=window_end,
        )
    except Exception as e:
        log.error(f"  Processing failed: {e}")
        log.debug(traceback.format_exc())
        return False

    log.info("[4/5] Checking output ...")
    if os.path.exists(output_path):
        size_mb = os.path.getsize(output_path) / 1024 / 1024
        log.info(f"  [OK] Generated: {size_mb:.1f} MB")
    else:
        log.error("  [FAIL] Output not generated")
        return False

    log.info("[5/5] Done [OK]")
    return True


def main():
    parser = argparse.ArgumentParser(
        description='Video Dubbing System - Replace Chinese audio with English TTS',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python main.py                      # Process all videos
  python main.py --check-deps          # Check dependencies
  python main.py --verbose             # Verbose logging
  python main.py --video X.mp4 --subtitle X.srt --max-subtitles 5
        """,
    )
    parser.add_argument('--check-deps', action='store_true', help='Check dependencies only')
    parser.add_argument('--verbose', action='store_true', help='Verbose output')
    parser.add_argument('--video', type=str, help='Single video file path')
    parser.add_argument('--subtitle', type=str, help='Single subtitle file path (use with --video)')
    parser.add_argument('--output', type=str, help='Output file path')
    parser.add_argument('--max-subtitles', type=int, default=None,
                        help='Debug: process only first N English subtitles')
    parser.add_argument('--sample-window-padding', type=float, default=None,
                        help='Sample mode: generate window around first N subtitles (seconds)')

    args = parser.parse_args()

    if args.check_deps:
        ok = check_dependencies()
        sys.exit(0 if ok else 1)

    from src.config.config import Config
    cfg = Config()
    setup_logging(verbose=args.verbose)
    log = logging.getLogger(__name__)

    if args.video:
        if not args.subtitle:
            log.error("--subtitle is required with --video")
            sys.exit(1)

        output = args.output or os.path.join(
            cfg.get('directories', 'output'),
            Path(args.video).stem + '_EN.mp4'
        )
        Path(output).parent.mkdir(parents=True, exist_ok=True)

        ok = process_single(
            args.video, args.subtitle, output, cfg,
            max_subtitles=args.max_subtitles,
            sample_window_padding=args.sample_window_padding,
        )
        sys.exit(0 if ok else 1)

    log.info("Starting video dubbing system ...")

    try:
        os.system('ffmpeg -version >nul 2>&1')
    except Exception:
        log.error("FFmpeg not installed: https://ffmpeg.org/download.html")
        sys.exit(1)

    srt_dir = cfg.get('directories', 'subtitle_source')
    video_dir = cfg.get('directories', 'video_source')
    out_dir = cfg.get('directories', 'output')

    Path(out_dir).mkdir(parents=True, exist_ok=True)

    srt_files = sorted([
        f for f in Path(srt_dir).rglob('*')
        if f.is_file() and f.suffix.lower() == '.srt'
    ])

    if not srt_files:
        log.error(f"Subtitle directory is empty: {srt_dir}")
        sys.exit(1)

    log.info(f"Found {len(srt_files)} subtitle files, processing ...")

    success = 0
    failed = 0

    for srt_file in srt_files:
        video_path = find_matching_video(str(srt_file), video_dir)
        if not video_path:
            log.warning(f"No matching video found, skipping: {srt_file.stem}")
            failed += 1
            continue

        video_stem = Path(video_path).stem
        output_path = os.path.join(out_dir, video_stem + '_EN.mp4')

        ok = process_single(
            video_path, str(srt_file), output_path, cfg,
            max_subtitles=args.max_subtitles,
            sample_window_padding=args.sample_window_padding,
        )
        if ok:
            success += 1
        else:
            failed += 1

        if not cfg.get('debug', 'save_intermediate_files'):
            cache_dir = Path(cfg.get('directories', 'cache'))
            for f in cache_dir.glob('_vid_*'):
                try:
                    f.unlink()
                except:
                    pass
            for f in cache_dir.glob('_part_*'):
                try:
                    f.unlink()
                except:
                    pass
            for f in cache_dir.glob('_concat_*'):
                try:
                    f.unlink()
                except:
                    pass

    total = success + failed
    log.info("")
    log.info(f"All done! Total {total}, success {success}, failed {failed}")
    sys.exit(0 if failed == 0 else 1)


if __name__ == '__main__':
    main()
