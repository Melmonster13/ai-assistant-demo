"""Microphone capture with start-of-speech detection and trailing-silence cutoff.
sounddevice is imported lazily so scripted tests never touch audio hardware."""

import numpy as np

from voiceclient.config import SAMPLE_RATE

FRAME_MS = 30


class Recorder:
    def __init__(
        self,
        *,
        silence_threshold: float = 0.015,
        trailing_silence_seconds: float = 0.9,
        max_utterance_seconds: float = 20.0,
        start_timeout_seconds: float = 8.0,
    ) -> None:
        self._threshold = silence_threshold
        self._trailing = trailing_silence_seconds
        self._max_seconds = max_utterance_seconds
        self._start_timeout = start_timeout_seconds

    def record_utterance(self) -> np.ndarray:
        """Block until speech starts (or start-timeout -> empty array), then
        capture float32 mono @16k until trailing silence or max length."""
        import sounddevice as sd

        frame_len = int(SAMPLE_RATE * FRAME_MS / 1000)
        frames: list[np.ndarray] = []
        started = False
        waited = 0.0
        silent = 0.0
        with sd.InputStream(samplerate=SAMPLE_RATE, channels=1, dtype="float32") as stream:
            while True:
                frame, _ = stream.read(frame_len)
                frame = frame[:, 0]
                rms = float(np.sqrt(np.mean(frame * frame)))
                if not started:
                    if rms >= self._threshold:
                        started = True
                        frames.append(frame)
                        continue
                    waited += FRAME_MS / 1000
                    if waited >= self._start_timeout:
                        return np.zeros(0, dtype=np.float32)
                    continue
                frames.append(frame)
                if rms < self._threshold:
                    silent += FRAME_MS / 1000
                    if silent >= self._trailing:
                        break
                else:
                    silent = 0.0
                if len(frames) * FRAME_MS / 1000 >= self._max_seconds:
                    break
        return np.concatenate(frames)


def play(samples: np.ndarray, sample_rate: int) -> None:
    import sounddevice as sd

    sd.play(samples, sample_rate)
    sd.wait()
