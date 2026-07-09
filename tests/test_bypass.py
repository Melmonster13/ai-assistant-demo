"""The security-spine guarantee, now against a real destructive MCP server:
the tool provably won't execute without a confirmed, single-use, unexpired,
argument-bound high-tier token. Each test attempts a bypass against a live
wrapper (which spawned the real files MCP server) and asserts no side effect
reached the filesystem."""

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
