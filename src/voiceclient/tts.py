"""Text-to-speech backends — the decided swappable leaf. Piper is the target
(local, runs anywhere); `say` is the zero-setup macOS dev fallback."""

import subprocess
import sys
from typing import Protocol

import numpy as np


class Tts(Protocol):
    def speak(self, text: str) -> None: ...


class SayTts:
    def __init__(self) -> None:
        if sys.platform != "darwin":
            raise SystemExit("TTS_BACKEND=say only works on macOS — use piper")

    def speak(self, text: str) -> None:
        if text:
            subprocess.run(["say", text], check=False)


class PiperTts:
    def __init__(self, voice_path: str) -> None:
        if not voice_path:
            raise SystemExit("TTS_BACKEND=piper needs PIPER_VOICE=<path to .onnx voice>")
        from piper import PiperVoice

        self._voice = PiperVoice.load(voice_path)

    def speak(self, text: str) -> None:
        from voiceclient.audio import play

        if not text:
            return
        chunks = list(self._voice.synthesize(text))
        if chunks:
            play(np.concatenate([c.audio_float_array for c in chunks]), chunks[0].sample_rate)
