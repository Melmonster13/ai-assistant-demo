"""The security-spine guarantee, now against a real destructive MCP server:
the tool provably won't execute without a confirmed, single-use, unexpired,
argument-bound high-tier token. Each test attempts a bypass against a live
wrapper (which spawned the real files MCP server) and asserts no side effect
reached the filesystem."""

import socket
import threading

from conftest import FILES_ARGS


def _no_side_effects(wrapper, sandbox):
    assert wrapper.executions == []
    assert list(sandbox.iterdir()) == []


def test_no_token_rejected(files_wrapper, post, sandbox):
    response = post(files_wrapper, token=None)
    assert response.status_code == 403
    assert response.json()["reason"] == "missing token"
    _no_side_effects(files_wrapper, sandbox)


def test_forged_signature_rejected(files_wrapper, post, sandbox, mint, forged_keypair):
    minted = mint(private_key=forged_keypair.private)
    response = post(files_wrapper, minted.token)
    assert response.status_code == 403
    assert "invalid token" in response.json()["reason"]
    _no_side_effects(files_wrapper, sandbox)


def test_expired_token_rejected(files_wrapper, post, sandbox, mint):
    minted = mint(ttl_seconds=-1)
    response = post(files_wrapper, minted.token)
    assert response.status_code == 403
    assert response.json()["reason"] == "token expired"
    _no_side_effects(files_wrapper, sandbox)


def test_replayed_jti_rejected(files_wrapper, post, sandbox, mint):
    minted = mint()
    first = post(files_wrapper, minted.token)
    assert first.status_code == 200
    second = post(files_wrapper, minted.token)
    assert second.status_code == 403
    assert second.json()["reason"] == "token unknown or already used"
    assert len(files_wrapper.executions) == 1
    assert (sandbox / "a.txt").read_text() == "hi"


def test_tampered_arguments_rejected(files_wrapper, post, sandbox, mint):
    minted = mint(arguments=FILES_ARGS)
    response = post(files_wrapper, minted.token, arguments={"path": "evil.txt", "content": "hi"})
    assert response.status_code == 403
    assert response.json()["reason"] == "arguments differ from what was confirmed"
    _no_side_effects(files_wrapper, sandbox)


def test_wrong_tool_rejected(files_wrapper, post, sandbox, mint):
    minted = mint(tool_name="delete_file", arguments={"path": "a.txt"})
    response = post(files_wrapper, minted.token, tool_name="write_file")
    assert response.status_code == 403
    assert response.json()["reason"] == "token was minted for a different tool"
    _no_side_effects(files_wrapper, sandbox)


def test_low_tier_token_rejected_at_high_boundary(files_wrapper, post, sandbox, mint):
    # a longer-lived read-tier token must not authorize a destructive call,
    # no matter what the orchestrator minted — the boundary enforces its tier
    minted = mint(tier="low", ttl_seconds=900)
    response = post(files_wrapper, minted.token)
    assert response.status_code == 403
    assert "tier" in response.json()["reason"]
    _no_side_effects(files_wrapper, sandbox)


def test_valid_token_executes_once(files_wrapper, post, sandbox, mint):
    minted = mint()
    response = post(files_wrapper, minted.token)
    assert response.status_code == 200
    assert response.json()["status"] == "executed"
    assert (sandbox / "a.txt").read_text() == "hi"
    assert len(files_wrapper.executions) == 1


def test_concurrent_same_token_executes_once(files_wrapper, post, sandbox, mint):
    # single-use must hold under a threaded server: two threads race the same
    # high-tier token; exactly one may execute (atomic jti consumption).
    minted = mint()
    barrier = threading.Barrier(2)
    responses: list = []

    def fire() -> None:
        barrier.wait()  # release both threads together to maximize contention
        responses.append(post(files_wrapper, minted.token))

    threads = [threading.Thread(target=fire) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    codes = sorted(r.status_code for r in responses)
    assert codes == [200, 403]
    loser = next(r for r in responses if r.status_code == 403)
    assert loser.json()["reason"] == "token unknown or already used"
    assert len(files_wrapper.executions) == 1
    assert (sandbox / "a.txt").read_text() == "hi"


def test_oversized_body_rejected(files_wrapper, sandbox):
    # Content-Length is client-controlled: declaring a huge body must be rejected
    # with 413 *before* the wrapper reads it. We declare 2 MB but send 2 bytes —
    # if the wrapper read first it would block waiting for bytes that never come,
    # so a prompt 413 also proves it rejects before reading.
    with socket.create_connection(("127.0.0.1", files_wrapper.port), timeout=5) as s:
        request = (
            "POST /execute HTTP/1.1\r\n"
            "Host: 127.0.0.1\r\n"
            "Content-Type: application/json\r\n"
            f"Content-Length: {2 * 1024 * 1024}\r\n"
            "Connection: close\r\n\r\n"
        ).encode() + b"{}"
        s.sendall(request)
        status_line = s.recv(256).decode(errors="replace").splitlines()[0]
    assert "413" in status_line
    _no_side_effects(files_wrapper, sandbox)
