"""TTS engine supporting ElevenLabs and XTTS v2."""

import os
import io
import logging
from pathlib import Path
from typing import Optional, Dict
from abc import ABC, abstractmethod


class TTSBase(ABC):
    def __init__(self, config):
        self.config = config
        self.logger = logging.getLogger(self.__class__.__name__)
        self._voice_id: Optional[str] = None
    @abstractmethod
    def clone_voice(self, reference_audio_path: str, voice_name: str = "speaker") -> str:
        pass

    @abstractmethod
    def synthesize(self, text: str, output_path: str) -> str:
        pass

    def get_voice_id(self) -> Optional[str]:
        return self._voice_id


class XTTSEngine(TTSBase):
    """XTTS v2 local model (Coqui TTS). Free but slower, GPU recommended."""

    def __init__(self, config):
        super().__init__(config)
        xtts_cfg = config.get('tts_engine', 'xtts')
        self.model_name = xtts_cfg.get('model_name', 'tts_models/multilingual/multi-dataset/xtts_v2')
        self.device = xtts_cfg.get('device', 'cuda')
        self.language = xtts_cfg.get('language', 'en')
        self._model = None
        self._speaker_wav: Optional[str] = None

    def _load_model(self):
        if self._model is None:
            from TTS.api import TTS
            self._model = TTS(self.model_name)
            self._model.to(self.device)

    def clone_voice(self, reference_audio_path: str, voice_name: str = "speaker") -> str:
        if not os.path.exists(reference_audio_path):
            raise FileNotFoundError(f"Reference audio not found: {reference_audio_path}")
        self._speaker_wav = reference_audio_path
        self._voice_id = reference_audio_path
        return self._voice_id

    def synthesize(self, text: str, output_path: str, language: str = None) -> str:
        if not text or not text.strip():
            raise ValueError("Text cannot be empty")
        if not self._speaker_wav:
            raise ValueError("Call clone_voice() first")

        self._load_model()
        lang = language or self.language
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)

        self._model.tts_to_file(
            text=text,
            speaker_wav=self._speaker_wav,
            language=lang,
            file_path=output_path,
            split_sentences=False,
        )
        return output_path


class ElevenLabsEngine(TTSBase):
    """ElevenLabs cloud API. Higher quality, requires API key."""

    def __init__(self, config):
        super().__init__(config)
        try:
            from dotenv import load_dotenv
            load_dotenv()
        except ImportError:
            pass

        raw_key: Optional[str] = os.environ.get('ELEVENLABS_API_KEY')
        if not raw_key:
            raise RuntimeError(
                "ELEVENLABS_API_KEY not set. Create .env file:\n"
                "    ELEVENLABS_API_KEY=sk_xxx\n"
                "Get key at: https://elevenlabs.io"
            )

        self._api_key = raw_key
        from elevenlabs.client import ElevenLabs
        self._client = ElevenLabs(api_key=self._api_key)

        el_cfg = config.get('tts_engine', 'elevenlabs')
        self.model_id = el_cfg.get('model_id', 'eleven_v3')
        self.stability = el_cfg.get('stability', 0.5)
        self.similarity_boost = el_cfg.get('similarity_boost', 0.85)
        self.style = el_cfg.get('style', 0.0)
        self.use_speaker_boost = el_cfg.get('use_speaker_boost', True)

    def clone_voice(self, reference_audio_path: str, voice_name: str = "speaker") -> str:
        if not os.path.exists(reference_audio_path):
            raise FileNotFoundError(f"Reference audio not found: {reference_audio_path}")

        audio_bytes = self._audio_to_bytesio(reference_audio_path)
        voice = self._client.voices.ivc.create(
            name=voice_name,
            files=[('reference.mp3', audio_bytes, 'audio/mpeg')],
        )
        self._voice_id = voice.voice_id
        return self._voice_id

    def synthesize(self, text: str, output_path: str, voice_id: str = None) -> str:
        vid = voice_id or self._voice_id
        if not vid:
            raise ValueError("Call clone_voice() first")
        if not text or not text.strip():
            raise ValueError("Text cannot be empty")

        Path(output_path).parent.mkdir(parents=True, exist_ok=True)

        from elevenlabs.types import VoiceSettings
        voice_settings = VoiceSettings(
            stability=self.stability,
            similarity_boost=self.similarity_boost,
            style=self.style,
            use_speaker_boost=self.use_speaker_boost,
        )

        audio_stream = self._client.text_to_speech.convert(
            text=text,
            voice_id=vid,
            model_id=self.model_id,
            voice_settings=voice_settings,
            output_format="mp3_44100_128",
        )

        with open(output_path, "wb") as f:
            for chunk in audio_stream:
                if isinstance(chunk, bytes):
                    f.write(chunk)

        return output_path

    def _audio_to_bytesio(self, audio_path: str) -> bytes:
        from pydub import AudioSegment
        audio = AudioSegment.from_file(audio_path)
        buf = io.BytesIO()
        audio.export(buf, format='mp3', bitrate='192k')
        buf.seek(0)
        return buf.read()


def create_tts_engine(config) -> TTSBase:
    engine_type = config.get('tts_engine', 'engine')
    if engine_type == 'elevenlabs':
        return ElevenLabsEngine(config)
    elif engine_type == 'xtts':
        return XTTSEngine(config)
    else:
        raise ValueError(f"Unknown engine: {engine_type}")