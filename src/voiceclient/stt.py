"""Speech-to-text backends. Local Whisper (faster-whisper) is the decided target —
audio never leaves the machine; only the transcript goes wherever the Model
layer's API is (that boundary is recorded in the architecture docs)."""

from typing import Protocol

import numpy as np


class Stt(Protocol):
    def transcribe(self, audio: np.ndarray, sample_rate: int) -> str: ...


class WhisperStt:
    def __init__(self, model: str = "base.en") -> None:
        from faster_whisper import WhisperModel

        # int8 on CPU: fast enough for short utterances, no GPU assumption
        self._model = WhisperModel(model, device="cpu", compute_type="int8")

    def transcribe(self, audio: np.ndarray, sample_rate: int) -> str:
        if audio.size == 0:
            return ""
        segments, _ = self._model.transcribe(audio, language="en", beam_size=1)
        return " ".join(segment.text.strip() for segment in segments).strip()
