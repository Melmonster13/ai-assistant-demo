"""MCP tool definition integrity: trust-on-first-use fingerprinting + drift
detection. Extends "tool output is untrusted" to tool *definitions* — a server
can present a benign tool, get approved, then mutate its description or schema
(the MCP "rug pull"). Fingerprints cover name + description + input schema:
the parts exploitable for description-injection or bait-and-switch.
"""

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Callable

from assistant.audit import log as audit


def fingerprint(definition: dict[str, Any]) -> str:
    canonical = json.dumps(
        {
            "name": definition["name"],
            "description": definition["description"],
            "input_schema": definition["input_schema"],
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


@dataclass
class ApprovedTool:
    definition: dict[str, Any]
    # set when the definition drifted this session: even after re-approval the
    # tool is blocked from auto-invocation and forced through per-call
    # confirmation regardless of its normal risk tier
    force_confirm: bool = False


def _describe(definition: dict[str, Any]) -> str:
    return (
        f"  description: {definition['description']}\n"
        f"  schema: {json.dumps(definition['input_schema'], sort_keys=True)}"
    )


def reconcile(
    conn: Any,
    server_id: str,
    definitions: list[dict[str, Any]],
    *,
    approve: Callable[[str], bool],
    user_id: str,
) -> list[ApprovedTool]:
    """Check every discovered tool against the registry baseline. Returns the
    tools cleared for use this session. New tools require one-time approval
    (TOFU); changed tools are flagged, audited with old vs. new, and excluded
    unless explicitly re-approved — and stay confirmation-forced even then."""
    approved_tools: list[ApprovedTool] = []
    for definition in definitions:
        name = definition["name"]
        new_fp = fingerprint(definition)

        with conn.cursor() as cur:
            cur.execute(
                "SELECT fingerprint, approval_status, definition FROM tool_registry"
                " WHERE server_id = %s AND tool_name = %s",
                (server_id, name),
            )
            row = cur.fetchone()

        if row is None:
            ok = approve(
                f"New tool '{name}' from server '{server_id}' (first use):\n"
                f"{_describe(definition)}\nApprove this tool?"
            )
            status = "approved" if ok else "denied"
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO tool_registry (server_id, tool_name, fingerprint, definition, approval_status)"
                    " VALUES (%s, %s, %s, %s, %s)",
                    (server_id, name, new_fp, json.dumps(definition), status),
                )
            audit.record(
                conn,
                user_id=user_id,
                tool_name=name,
                event="tool_approved" if ok else "tool_approval_denied",
                triggered_by="tofu",
                detail=new_fp,
            )
            if ok:
                approved_tools.append(ApprovedTool(definition))
            continue

        stored_fp, status, stored_definition = row

        if stored_fp == new_fp:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE tool_registry SET last_verified = now()"
                    " WHERE server_id = %s AND tool_name = %s",
                    (server_id, name),
                )
            if status == "approved":
                approved_tools.append(ApprovedTool(definition))
            continue  # denied tools stay silently excluded

        # definition drift — auditable regardless of what the user decides
        audit.record(
            conn,
            user_id=user_id,
            tool_name=name,
            event="definition_drift",
            triggered_by="discovery",
            detail=json.dumps({"server_id": server_id, "old": stored_definition, "new": definition}),
        )
        ok = approve(
            f"WARNING: tool '{name}' on server '{server_id}' HAS CHANGED since approval.\n"
            f"Previously approved:\n{_describe(stored_definition)}\n"
            f"Now presents as:\n{_describe(definition)}\n"
            f"A changed tool cannot be trusted at its old tier. Re-approve new definition?"
        )
        # baseline moves to the new definition either way, so the same drift
        # isn't re-reported forever; denial makes it a silently excluded tool
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE tool_registry SET fingerprint = %s, definition = %s,"
                " approval_status = %s, last_verified = now()"
                " WHERE server_id = %s AND tool_name = %s",
                (new_fp, json.dumps(definition), "approved" if ok else "denied", server_id, name),
            )
        audit.record(
            conn,
            user_id=user_id,
            tool_name=name,
            event="tool_approved" if ok else "tool_approval_denied",
            triggered_by="drift_reapproval",
            detail=new_fp,
        )
        if ok:
            approved_tools.append(ApprovedTool(definition, force_confirm=True))
    return approved_tools
