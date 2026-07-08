"""Audit log: every tool call recorded with what triggered it (audit_log table)."""

from typing import Any


def record(
    conn: Any,
    *,
    user_id: str,
    tool_name: str,
    event: str,
    triggered_by: str,
    detail: str | None = None,
    jti: str | None = None,
) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO audit_log (user_id, tool_name, event, triggered_by, detail, jti)
            VALUES (%s, %s, %s, %s, %s, %s)
            """,
            (user_id, tool_name, event, triggered_by, detail, jti),
        )
