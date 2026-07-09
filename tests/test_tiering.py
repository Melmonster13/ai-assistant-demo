"""Low-risk tier behavior at the read-only notes boundary: a valid low-tier
token is multi-use and argument-free within its TTL, but every boundary still
demands a signed, unexpired, tool-bound token — and tiers don't cross."""


def test_low_token_is_multi_use(notes_wrapper, post, mint):
    minted = mint(tool_name="list_notes", arguments={}, tier="low", ttl_seconds=900)
    for _ in range(2):  # jti is not consumed on the low tier
        response = post(notes_wrapper, minted.token, tool_name="list_notes", arguments={})
        assert response.status_code == 200
        assert "welcome.md" in response.json()["result"]


def test_low_token_spans_argument_variations(notes_wrapper, post, mint):
    minted = mint(tool_name="read_note", arguments={}, tier="low", ttl_seconds=900)
    for name, expected in [("welcome.md", "# Welcome"), ("todo.md", "# Todo")]:
        response = post(notes_wrapper, minted.token, tool_name="read_note", arguments={"name": name})
        assert response.status_code == 200
        assert expected in response.json()["result"]


def test_low_token_still_tool_bound(notes_wrapper, post, mint):
    minted = mint(tool_name="list_notes", arguments={}, tier="low", ttl_seconds=900)
    response = post(notes_wrapper, minted.token, tool_name="read_note", arguments={"name": "welcome.md"})
    assert response.status_code == 403
    assert response.json()["reason"] == "token was minted for a different tool"


def test_no_token_rejected_on_low_tier_too(notes_wrapper, post):
    response = post(notes_wrapper, token=None, tool_name="list_notes", arguments={})
    assert response.status_code == 403
    assert response.json()["reason"] == "missing token"
    assert notes_wrapper.executions == []


def test_high_token_rejected_at_low_boundary(notes_wrapper, post, mint):
    minted = mint(tool_name="list_notes", arguments={}, tier="high")
    response = post(notes_wrapper, minted.token, tool_name="list_notes", arguments={})
    assert response.status_code == 403
    assert "tier" in response.json()["reason"]


def test_expired_low_token_rejected(notes_wrapper, post, mint):
    minted = mint(tool_name="list_notes", arguments={}, tier="low", ttl_seconds=-1)
    response = post(notes_wrapper, minted.token, tool_name="list_notes", arguments={})
    assert response.status_code == 403
    assert response.json()["reason"] == "token expired"
