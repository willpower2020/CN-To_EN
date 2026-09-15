"""Data models for the video dubbing system."""

from dataclasses import dataclass


@dataclass
class SubtitleEntry:
    index: int
    start_time: float  # seconds
    end_time: float    # seconds
    chinese_text: str
    english_text: str

    @property
    def duration(self) -> float:
        return self.end_time - self.start_time


@dataclass
class EmotionFeatures:
    pitch_mean: float
    pitch_std: float
    energy_mean: float
    energy_std: float
    speaking_rate: float
    pause_ratio: float