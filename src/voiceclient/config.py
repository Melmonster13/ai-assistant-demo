"""Voice client config. Backends are config, not architecture (the model-adapter
pattern again): wake word and STT/TTS engines are swappable leaves.

Dev defaults keep first run light (push-to-talk, macOS `say`); the decided target
stack is openwakeword + faster-whisper + piper, all local — selected via env."""

import os
from dataclasses import dataclass

from dotenv import load_dotenv

SAMPLE_RATE = 16000  # what openwakeword and whisper expect; the recorder honors it


@dataclass(frozen=True)
class VoiceConfig:
    assistant_url: str
    wake_backend: str  # pushtotalk | openwakeword
    wakeword_model: str
    stt_backend: str  # whisper
    whisper_model: str
    tts_backend: str  # say | piper
    piper_voice: str  # path to a piper .onnx voice (piper backend only)
    silence_threshold: float
    trailing_silence_seconds: float
    max_utterance_seconds: float


def load_config() -> VoiceConfig:
    load_dotenv()
    return VoiceConfig(
        assistant_url=os.environ.get("ASSISTANT_URL", "http://127.0.0.1:8080"),
        wake_backend=os.environ.get("WAKE_BACKEND", "pushtotalk"),
        wakeword_model=os.environ.get("WAKEWORD_MODEL", "hey_jarvis_v0.1"),
        stt_backend=os.environ.get("STT_BACKEND", "whisper"),
        whisper_model=os.environ.get("WHISPER_MODEL", "base.en"),
        tts_backend=os.environ.get("TTS_BACKEND", "say"),
        piper_voice=os.environ.get("PIPER_VOICE", ""),
        silence_threshold=float(os.environ.get("SILENCE_THRESHOLD", "0.015")),
        trailing_silence_seconds=float(os.environ.get("TRAILING_SILENCE_SECONDS", "0.9")),
        max_utterance_seconds=float(os.environ.get("MAX_UTTERANCE_SECONDS", "20")),
    )
