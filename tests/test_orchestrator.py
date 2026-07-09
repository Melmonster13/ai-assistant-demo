"""End-to-end through the orchestrator: discovery + TOFU, tier routing (read
tool needs no confirmation, destructive tool does), and the audit trail —
against live wrappers fronting real MCP servers."""

import uuid

from assistant.config import ToolServer
from assistant.model.base import ModelResponse, ToolCall
from assistant.orchestrator.loop import Orchestrator


class ScriptedModel:
    """Emits list_notes, then write_file (a reaction to tool output), then text."""

    def __init__(self):
        self.step = 0

    def complete(self, messages, tools, system=None):
        self.step += 1
        if self.step == 1:
            calls = [ToolCall(id="c1", name="list_notes", arguments={})]
        elif self.step == 2:
            calls = [ToolCall(id="c2", name="write_file", arguments={"path": "out.txt", "content": "done"})]
        else:
            return ModelResponse(text=f"finished: {messages[-1]['content']}", raw_message={"role": "assistant", "content": "[t]"})
        return ModelResponse(text=None, tool_calls=calls, raw_message={"role": "assistant", "content": "[tc]"})

    def user_message(self, text):
        return {"role": "user", "content": text}

    def tool_result_message(self, tool_call_id, content):
        return {"role": "user", "content": content}


def test_tier_routing_and_audit(db_conn, notes_wrapper, files_wrapper, sandbox, keypair):
    user_id = f"t-{uuid.uuid4().hex[:8]}"
    confirmed: list[str] = []

    orchestrator = Orchestrator(
        ScriptedModel(),
        conn=db_conn,
        tool_servers=(
            ToolServer("notes", f"http://127.0.0.1:{notes_wrapper.port}", "low"),
            ToolServer("files", f"http://127.0.0.1:{files_wrapper.port}", "high"),
        ),
        private_key=keypair.private,
        ttl_seconds=30,
        low_ttl_seconds=900,
        user_id=user_id,
        confirm=lambda name, args: confirmed.append(name) or True,
        approve=lambda prompt: True,  # TOFU-approve all four tools
    )
    notes = orchestrator.startup()
    assert any("2 tool(s) cleared" in n for n in notes)

    reply = orchestrator.run_turn("read my notes then write a summary file")

    assert "finished" in reply
    assert (sandbox / "out.txt").read_text() == "done"
    assert confirmed == ["write_file"]  # low-tier read needed no confirmation

    with db_conn.cursor() as cur:
        cur.execute(
            "SELECT tool_name, event, triggered_by FROM audit_log WHERE user_id = %s"
            " AND event IN ('requested','confirmed','token_minted','executed') ORDER BY id",
            (user_id,),
        )
        rows = cur.fetchall()
    assert rows == [
        ("list_notes", "requested", "user_request"),
        ("list_notes", "token_minted", "user_request"),
        ("list_notes", "executed", "user_request"),
        ("write_file", "requested", "tool_output"),
        ("write_file", "confirmed", "tool_output"),
        ("write_file", "token_minted", "tool_output"),
        ("write_file", "executed", "tool_output"),
    ]


def test_denied_confirmation_blocks_destructive_call(db_conn, notes_wrapper, files_wrapper, sandbox, keypair):
    user_id = f"t-{uuid.uuid4().hex[:8]}"

    orchestrator = Orchestrator(
        ScriptedModel(),
        conn=db_conn,
        tool_servers=(
            ToolServer("notes", f"http://127.0.0.1:{notes_wrapper.port}", "low"),
            ToolServer("files", f"http://127.0.0.1:{files_wrapper.port}", "high"),
        ),
        private_key=keypair.private,
        ttl_seconds=30,
        low_ttl_seconds=900,
        user_id=user_id,
        confirm=lambda name, args: False,
        approve=lambda prompt: True,
    )
    orchestrator.startup()
    orchestrator.run_turn("read my notes then write a summary file")

    assert not (sandbox / "out.txt").exists()
    with db_conn.cursor() as cur:
        cur.execute(
            "SELECT event FROM audit_log WHERE user_id = %s AND tool_name = 'write_file' ORDER BY id",
            (user_id,),
        )
        events = [r[0] for r in cur.fetchall()]
    assert events == ["requested", "denied"]  # no token ever minted
