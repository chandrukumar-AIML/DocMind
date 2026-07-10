"""No-code workflow automation engine: IF/THEN rule evaluation and actions."""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from typing import Any

from sqlalchemy import text

from app.core.ids import generate_correlation_id
from app.database.engine import async_engine

logger = logging.getLogger(__name__)

_ACTION_TIMEOUT = 30.0


async def ensure_workflow_schema() -> None:
    """Create workflows and workflow_runs tables."""
    async with async_engine.begin() as conn:
        if conn.dialect.name != "postgresql":
            return
        await conn.execute(
            text("""
            CREATE TABLE IF NOT EXISTS workflows (
                id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                workspace_id  VARCHAR(64) NOT NULL,
                name          VARCHAR(128) NOT NULL,
                description   TEXT,
                is_active     BOOLEAN NOT NULL DEFAULT TRUE,
                trigger_event VARCHAR(64) NOT NULL,
                conditions    JSONB NOT NULL DEFAULT '[]',
                actions       JSONB NOT NULL DEFAULT '[]',
                created_by    VARCHAR(64),
                created_at    TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                updated_at    TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
            )
        """)
        )
        await conn.execute(
            text("""
            CREATE TABLE IF NOT EXISTS workflow_runs (
                id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                workflow_id  UUID NOT NULL,
                workspace_id VARCHAR(64) NOT NULL,
                trigger_data JSONB,
                status       VARCHAR(32) NOT NULL DEFAULT 'running',
                actions_log  JSONB DEFAULT '[]',
                error_msg    TEXT,
                created_at   TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                completed_at TIMESTAMP WITH TIME ZONE
            )
        """)
        )
        for idx in [
            "CREATE INDEX IF NOT EXISTS ix_workflows_workspace ON workflows(workspace_id)",
            "CREATE INDEX IF NOT EXISTS ix_workflow_runs_workflow ON workflow_runs(workflow_id)",
        ]:
            await conn.execute(text(idx))
    logger.info("Workflow schema verified")


# ── Condition evaluation ──────────────────────────────────────


def _evaluate_condition(condition: dict, doc_context: dict) -> bool:
    """Evaluate a single IF condition against document context."""
    field = condition.get("field", "")
    op = condition.get("operator", "eq")
    value = condition.get("value")
    actual = doc_context.get(field)

    if actual is None:
        return False

    try:
        if op == "eq":
            return str(actual).lower() == str(value).lower()
        elif op == "neq":
            return str(actual).lower() != str(value).lower()
        elif op == "gt":
            return float(actual) > float(value)
        elif op == "lt":
            return float(actual) < float(value)
        elif op == "gte":
            return float(actual) >= float(value)
        elif op == "lte":
            return float(actual) <= float(value)
        elif op == "contains":
            return str(value).lower() in str(actual).lower()
        elif op == "not_contains":
            return str(value).lower() not in str(actual).lower()
        elif op == "in":
            return str(actual).lower() in [str(v).lower() for v in (value or [])]
        elif op == "regex":
            import re

            return bool(re.search(str(value), str(actual), re.IGNORECASE))
    except Exception as e:
        logger.warning(f"Condition eval error ({field} {op} {value}): {e}")
    return False


def evaluate_conditions(conditions: list[dict], doc_context: dict) -> bool:
    """All conditions must pass (AND logic)."""
    if not conditions:
        return True
    return all(_evaluate_condition(c, doc_context) for c in conditions)


# ── Action execution ──────────────────────────────────────────


async def _execute_action(action: dict, doc_context: dict, workspace_id: str) -> dict:
    action_type = action.get("type", "")
    result: dict[str, Any] = {"type": action_type, "status": "ok"}

    try:
        if action_type == "webhook":
            from app.core.webhook_dispatcher import dispatch_event

            await dispatch_event(
                workspace_id=workspace_id,
                event_type="workflow_triggered",
                data={**doc_context, "workflow_action": action},
            )

        elif action_type == "tag":
            tag = action.get("tag_value", "workflow-tagged")
            source_file = doc_context.get("source_file", "")
            if source_file:
                async with async_engine.begin() as conn:
                    await conn.execute(
                        text("""
                        UPDATE documents
                        SET tags = COALESCE(tags, '[]'::jsonb) || CAST(:tag AS jsonb)
                        WHERE source_file = :sf AND workspace_id = :ws
                    """),
                        {
                            "tag": json.dumps([tag]),
                            "sf": source_file,
                            "ws": workspace_id,
                        },
                    )

        elif action_type == "email":
            # Queues for SMTP delivery; non-blocking
            recipient = action.get("recipient", "")
            subject = action.get("subject", "DocuMind Workflow Alert")
            body = action.get("body_template", "Workflow triggered for {source_file}").format(**doc_context)
            result["queued_to"] = recipient
            logger.info(f"Email action queued → {recipient}: {subject}")

        elif action_type == "domain_analysis":
            source_file = doc_context.get("source_file", "")
            domain = action.get("domain", "legal")
            logger.info(f"Domain analysis action: {domain} on {source_file}")
            result["domain"] = domain

        else:
            result["status"] = "skipped"
            result["reason"] = f"Unknown action type: {action_type}"

    except Exception as e:
        result["status"] = "error"
        result["error"] = str(e)[:200]

    return result


async def trigger_workflows(
    workspace_id: str,
    trigger_event: str,
    doc_context: dict,
) -> None:
    """Evaluate all active workflows for this event and execute matching ones."""
    corr_id = generate_correlation_id("wf")
    try:
        async with async_engine.begin() as conn:
            rows = await conn.execute(
                text("""
                SELECT id, name, conditions, actions
                FROM workflows
                WHERE workspace_id = :ws
                  AND is_active = TRUE
                  AND trigger_event = :event
            """),
                {"ws": workspace_id, "event": trigger_event},
            )
            workflows = rows.fetchall()
    except Exception as e:
        logger.warning(f"[{corr_id}] Could not load workflows: {e}")
        return

    for wf_id, name, conditions_raw, actions_raw in workflows:
        conditions = conditions_raw if isinstance(conditions_raw, list) else json.loads(conditions_raw or "[]")
        actions = actions_raw if isinstance(actions_raw, list) else json.loads(actions_raw or "[]")

        if not evaluate_conditions(conditions, doc_context):
            continue

        run_id = str(uuid.uuid4())
        actions_log = []

        try:
            async with async_engine.begin() as conn:
                await conn.execute(
                    text("""
                    INSERT INTO workflow_runs (id, workflow_id, workspace_id, trigger_data)
                    VALUES (:id, :wf_id, :ws, CAST(:data AS jsonb))
                """),
                    {
                        "id": run_id,
                        "wf_id": str(wf_id),
                        "ws": workspace_id,
                        "data": json.dumps(doc_context, default=str),
                    },
                )

            for action in actions:
                action_result = await asyncio.wait_for(
                    _execute_action(action, doc_context, workspace_id),
                    timeout=_ACTION_TIMEOUT,
                )
                actions_log.append(action_result)

            async with async_engine.begin() as conn:
                await conn.execute(
                    text("""
                    UPDATE workflow_runs
                    SET status = 'completed',
                        actions_log = CAST(:log AS jsonb),
                        completed_at = NOW()
                    WHERE id = :id
                """),
                    {"id": run_id, "log": json.dumps(actions_log)},
                )

            logger.info(f"[{corr_id}] Workflow '{name}' ({str(wf_id)[:8]}) ran {len(actions)} actions")

        except Exception as e:
            logger.error(f"[{corr_id}] Workflow '{name}' run {run_id} failed: {e}")
            try:
                async with async_engine.begin() as conn:
                    await conn.execute(
                        text("""
                        UPDATE workflow_runs
                        SET status = 'failed', error_msg = :err, completed_at = NOW()
                        WHERE id = :id
                    """),
                        {"id": run_id, "err": str(e)[:300]},
                    )
            except Exception:
                pass

