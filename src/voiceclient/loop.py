"""The voice interaction loop: wake -> listen -> assistant -> speak.

Confirmation stays at the orchestrator; this loop is only a surface for it. A
destructive action is read aloud and requires a clear spoken yes — anything
ambiguous is re-asked once and then treated as a no (fails closed), matching
the decision queue's own deny-on-timeout."""

import json
import re
from typing import Any

import numpy as np

from voiceclient.client import AssistantClient
from voiceclient.config import SAMPLE_RATE, load_config

YES_WORDS = {"yes", "yeah", "yep", "sure", "allow", "approve", "approved", "confirm", "ok", "okay", "affirmative"}
NO_WORDS = {"no", "nope", "deny", "denied", "don't", "dont", "cancel", "stop", "not", "never", "reject", "negative"}


def parse_yes_no(text: str) -> bool | None:
    words = set(re.findall(r"[a-z']+", text.lower()))
    yes = bool(words & YES_WORDS)
    no = bool(words & NO_WORDS)
    if yes and not no:
        return True
    if no and not yes:
        return False
    return None  # ambiguous or empty


def _decision_prompt(item: dict[str, Any]) -> str:
    if item["kind"] == "confirm":
        arguments = json.dumps(item.get("arguments", {}))
        if len(arguments) > 200:
            arguments = arguments[:200] + "…"
        return (
            f"Permission check. The assistant wants to run {item['tool_name']} "
            f"with {arguments}. Say yes to allow, or no to deny."
        )
    return f"{item.get('prompt', 'A tool needs approval.')} Say yes to approve, or no to deny."


class VoiceAssistant:
    def __init__(self, *, wake, recorder, stt, tts, client: AssistantClient) -> None:
        self._wake = wake
        self._recorder = recorder
        self._stt = stt
        self._tts = tts
        self._client = client

    def run(self) -> None:
        while self.handle_one():
            pass

    def handle_one(self) -> bool:
        """One wake -> request -> reply cycle. False when the wake source ends."""
        if not self._wake.wait():
            return False
        text = self._listen()
        if not text:
            self._tts.speak("I didn't catch that.")
            return True
        print(f"you> {text}", flush=True)
        reply = self._client.chat(text, on_decision=self._decide)
        print(f"assistant> {reply}", flush=True)
        self._tts.speak(reply or "Done.")
        return True

    def _listen(self) -> str:
        audio: np.ndarray = self._recorder.record_utterance()
        return self._stt.transcribe(audio, SAMPLE_RATE)

    def _decide(self, item: dict[str, Any]) -> bool:
        self._tts.speak(_decision_prompt(item))
        for retry in range(2):
            if retry:
                self._tts.speak("Sorry, was that a yes or a no?")
            answer = self._listen()
            print(f"you> {answer}", flush=True)
            decision = parse_yes_no(answer)
            if decision is not None:
                return decision
        self._tts.speak("I'll take that as a no.")
        return False


def main() -> None:
    from voiceclient.audio import Recorder

    cfg = load_config()

    if cfg.wake_backend == "openwakeword":
        from voiceclient.wake import OpenWakeWord

        wake = OpenWakeWord(cfg.wakeword_model)
    elif cfg.wake_backend == "pushtotalk":
        from voiceclient.wake import PushToTalk

        wake = PushToTalk()
    else:
        raise SystemExit(f"unknown WAKE_BACKEND: {cfg.wake_backend}")

    if cfg.stt_backend == "whisper":
        from voiceclient.stt import WhisperStt

        stt = WhisperStt(cfg.whisper_model)
    else:
        raise SystemExit(f"unknown STT_BACKEND: {cfg.stt_backend}")

    if cfg.tts_backend == "piper":
        from voiceclient.tts import PiperTts

        tts = PiperTts(cfg.piper_voice)
    elif cfg.tts_backend == "say":
        from voiceclient.tts import SayTts

        tts = SayTts()
    else:
        raise SystemExit(f"unknown TTS_BACKEND: {cfg.tts_backend}")

    recorder = Recorder(
        silence_threshold=cfg.silence_threshold,
        trailing_silence_seconds=cfg.trailing_silence_seconds,
        max_utterance_seconds=cfg.max_utterance_seconds,
    )
    print(
        f"voice client -> {cfg.assistant_url} "
        f"(wake={cfg.wake_backend}, stt={cfg.stt_backend}:{cfg.whisper_model}, tts={cfg.tts_backend})",
        flush=True,
    )
    VoiceAssistant(
        wake=wake,
        recorder=recorder,
        stt=stt,
        tts=tts,
        client=AssistantClient(cfg.assistant_url),
    ).run()
