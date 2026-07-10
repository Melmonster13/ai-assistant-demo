"""Wake word backends. Local by definition — no hot mic streaming anywhere.

PushToTalk is the dev fallback (Enter key = wake). OpenWakeWord is the decided
target: pretrained local models (default listens for "hey jarvis"), scores
computed on-device over 80ms frames.
"""

from typing import Protocol

import numpy as np

from voiceclient.config import SAMPLE_RATE


class WakeSource(Protocol):
    def wait(self) -> bool:
        """Block until wake. False means the source is exhausted — stop the loop."""
        ...


class PushToTalk:
    def wait(self) -> bool:
        try:
            input("\n[Enter to talk, Ctrl-D to quit] ")
            return True
        except (EOFError, KeyboardInterrupt):
            print()
            return False


class OpenWakeWord:
    FRAME_SAMPLES = 1280  # 80ms @ 16k, what openwakeword expects

    def __init__(self, model_name: str, threshold: float = 0.5) -> None:
        import openwakeword.utils
        from openwakeword.model import Model

        openwakeword.utils.download_models()  # cached after first run
        self._model = Model(wakeword_models=[model_name], inference_framework="onnx")
        self._name = model_name
        self._threshold = threshold

    def wait(self) -> bool:
        import sounddevice as sd

        self._model.reset()
        try:
            with sd.InputStream(samplerate=SAMPLE_RATE, channels=1, dtype="int16") as stream:
                while True:
                    frame, _ = stream.read(self.FRAME_SAMPLES)
                    scores = self._model.predict(np.asarray(frame[:, 0]))
                    if max(scores.values()) >= self._threshold:
                        return True
        except KeyboardInterrupt:
            return False
