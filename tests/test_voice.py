"""Voice client: yes/no parsing fails closed, and the loop drives the real
web API end-to-end — spoken confirmation allows/denies the same orchestrator
gate the browser buttons answer."""

import threading
import uuid

import numpy as np
import pytest

from assistant.config import ToolServer
from assistant.model.base import ModelResponse, ToolCall
from assistant.orchestrator.loop import Orchestrator
from assistant.webui.decisions import DecisionQueue
from assistant.webui.server import UIServer
from voiceclient.client import AssistantClient
from voiceclient.loop import VoiceAssistant, parse_yes_no


# --- yes/no parsing ---------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Yes.", True),
        ("yeah go ahead", True),
        ("sure, allow it", True),
        ("No", False),
        ("nope", False),
        ("please don't", False),
        ("absolutely not", False),
        ("banana", None),
        ("", None),
        ("yes... no, wait", None),  # conflicting -> ambiguous -> re-ask/deny
    ],
)
def test_parse_yes_no(text, expected):
    assert parse_yes_no(text) is expected


# --- scripted voice hardware -------------------------------------------------


class ScriptedWake:
    def __init__(self, times: int = 1):
        self.remaining = times

    def wait(self) -> bool:
        if self.remaining <= 0:
            return False
        self.remaining -= 1
        return True


class ScriptedRecorder:
    def record_utterance(self) -> np.ndarray:
        return np.ones(1600, dtype=np.float32)  # content is irrelevant; STT is scripted


class ScriptedStt:
    def __init__(self, texts: list[str]):
        self.texts = list(texts)

    def transcribe(self, audio: np.ndarray, sample_rate: int) -> str:
        return self.texts.pop(0) if self.texts else ""


class SpokenLog:
    def __init__(self):
        self.spoken: list[str] = []

    def speak(self, text: str) -> None:
        self.spoken.append(text)


class ScriptedModel:
    """One destructive tool call, then a text reply."""

    def __init__(self):
        self.step = 0

    def complete(self, messages, tools, system=None):
        self.step += 1
        if self.step == 1:
            return ModelResponse(
                text=None,
                tool_calls=[ToolCall(id="c1", name="write_file", arguments={"path": "voice.txt", "content": "spoken"})],
                raw_message={"role": "assistant", "content": "[tc]"},
            )
        return ModelResponse(text="All done.", raw_message={"role": "assistant", "content": "[t]"})

    def user_message(self, text):
        return {"role": "user", "content": text}

    def tool_result_message(self, tool_call_id, content):
        return {"role": "user", "content": content}


@pytest.fixture()
def web(db_conn, files_wrapper, keypair):
    decisions = DecisionQueue(timeout_seconds=30)
    orchestrator = Orchestrator(
        ScriptedModel(),
        conn=db_conn,
        tool_servers=(ToolServer("files", f"http://127.0.0.1:{files_wrapper.port}", "high"),),
        private_key=keypair.private,
        ttl_seconds=30,
        low_ttl_seconds=900,
        user_id=f"v-{uuid.uuid4().hex[:8]}",
        confirm=decisions.confirm,
        approve=lambda prompt: True,
    )
    server = UIServer(
        0,
        orchestrator=orchestrator,
        decisions=decisions,
        persona_text="",
        browse_store=None,
        user_id="unused",
    )
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield server
    server.shutdown()


def _voice(web, stt_texts: list[str]) -> tuple[VoiceAssistant, SpokenLog]:
    tts = SpokenLog()
    assistant = VoiceAssistant(
        wake=ScriptedWake(times=1),
        recorder=ScriptedRecorder(),
        stt=ScriptedStt(stt_texts),
        tts=tts,
        client=AssistantClient(f"http://127.0.0.1:{web.port}", poll_interval=0.05),
    )
    return assistant, tts


def test_spoken_yes_allows_destructive_call(web, sandbox):
    assistant, tts = _voice(web, ["write the file please", "yes"])
    assistant.run()  # one wake, then the source is exhausted

    assert (sandbox / "voice.txt").read_text() == "spoken"
    assert any("Permission check" in s and "write_file" in s for s in tts.spoken)
    assert tts.spoken[-1] == "All done."


def test_spoken_no_denies_destructive_call(web, sandbox):
    assistant, tts = _voice(web, ["write the file please", "no thanks"])
    assistant.run()

    assert not (sandbox / "voice.txt").exists()
    assert any("Permission check" in s for s in tts.spoken)


def test_ambiguous_answers_fail_closed(web, sandbox):
    assistant, tts = _voice(web, ["write the file please", "banana", "the weather is nice"])
    assistant.run()

    assert not (sandbox / "voice.txt").exists()
    assert any("was that a yes or a no" in s for s in tts.spoken)  # re-asked once
    assert any("I'll take that as a no" in s for s in tts.spoken)  # then denied


def test_empty_transcription_never_reaches_assistant(web):
    assistant, tts = _voice(web, [""])
    assistant.run()

    assert tts.spoken == ["I didn't catch that."]
    assert web.started is False  # no chat request was made


def test_wake_source_exhaustion_ends_loop():
    va = VoiceAssistant(wake=ScriptedWake(times=0), recorder=None, stt=None, tts=None, client=None)
    va.run()  # returns immediately without touching any other component
