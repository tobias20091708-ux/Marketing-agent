"""
Audit logging — every agent action is tracked for compliance and debugging.
"""
import json
import structlog
from sqlalchemy import text
from app.database import async_session

log = structlog.get_logger()


class AuditService:
    async def log_action(
        self,
        agent_id: str,
        action: str,
        details: dict = None,
        user_approved: bool = False,
    ):
        async with async_session() as db:
            await db.execute(
                text("""
                    INSERT INTO audit_log (agent_id, action, details, user_approved)
                    VALUES (:agent_id, :action, :details, :approved)
                """),
                {
                    "agent_id": agent_id,
                    "action": action,
                    "details": json.dumps(details or {}),
                    "approved": user_approved,
                },
            )
            await db.commit()

    async def get_recent(self, limit: int = 50, agent_id: str = None) -> list[dict]:
        async with async_session() as db:
            if agent_id:
                result = await db.execute(
                    text("""
                        SELECT agent_id, action, details, user_approved, created_at
                        FROM audit_log WHERE agent_id = :aid
                        ORDER BY created_at DESC LIMIT :limit
                    """),
                    {"aid": agent_id, "limit": limit},
                )
            else:
                result = await db.execute(
                    text("""
                        SELECT agent_id, action, details, user_approved, created_at
                        FROM audit_log ORDER BY created_at DESC LIMIT :limit
                    """),
                    {"limit": limit},
                )
            return [
                {
                    "agent": r[0],
                    "action": r[1],
                    "details": r[2],
                    "user_approved": r[3],
                    "time": r[4].isoformat() if r[4] else None,
                }
                for r in result.fetchall()
            ]


audit = AuditService()
