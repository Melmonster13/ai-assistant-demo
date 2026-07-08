"""Phase 1 'done means': the fake tool provably won't execute without a confirmed,
single-use, unexpired token. Each test attempts a bypass against a live wrapper
process and asserts the tool did not execute."""


def test_no_token_rejected(execute, wrapper):
    response = execute(token=None)
    assert response.status_code == 403
    assert response.json()["reason"] == "missing token"
    assert wrapper.executions == []


def test_forged_signature_rejected(execute, wrapper, mint, forged_keypair):
    minted = mint(private_key=forged_keypair.private)
    response = execute(minted.token)
    assert response.status_code == 403
    assert "invalid token" in response.json()["reason"]
    assert wrapper.executions == []


def test_expired_token_rejected(execute, wrapper, mint):
    minted = mint(ttl_seconds=-1)
    response = execute(minted.token)
    assert response.status_code == 403
    assert response.json()["reason"] == "token expired"
    assert wrapper.executions == []


def test_replayed_jti_rejected(execute, wrapper, mint):
    minted = mint()
    first = execute(minted.token)
    assert first.status_code == 200
    second = execute(minted.token)
    assert second.status_code == 403
    assert second.json()["reason"] == "token unknown or already used"
    assert len(wrapper.executions) == 1


def test_tampered_arguments_rejected(execute, wrapper, mint):
    minted = mint(arguments={"to": "a@b.c", "body": "hi"})
    response = execute(minted.token, arguments={"to": "attacker@evil.example", "body": "hi"})
    assert response.status_code == 403
    assert response.json()["reason"] == "arguments differ from what was confirmed"
    assert wrapper.executions == []


def test_wrong_tool_rejected(execute, wrapper, mint):
    minted = mint(tool_name="delete_file")
    response = execute(minted.token, tool_name="send_email")
    assert response.status_code == 403
    assert response.json()["reason"] == "token was minted for a different tool"
    assert wrapper.executions == []


def test_valid_token_executes_once(execute, wrapper, mint):
    minted = mint()
    response = execute(minted.token)
    assert response.status_code == 200
    assert response.json()["status"] == "executed"
    assert len(wrapper.executions) == 1
