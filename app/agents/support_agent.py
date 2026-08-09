"""
Support Agent — ticket triage, auto-resolution, escalation, knowledge base.
"""
import json
import structlog
from app.agents.base import BaseAgent
from app.services.ai_engine import ai
from sqlalchemy import text
from app.database import async_session

log = structlog.get_logger()


class SupportAgent(BaseAgent):
    agent_id = "support-agent"
    name = "Support Agent"
    description = "Ticket triage, L1 auto-resolution, escalation management"
    system_prompt = """You are an expert customer support agent. Your responsibilities:
1. Triage incoming support tickets by category and priority
2. Auto-resolve common L1 issues using the knowledge base
3. Escalate complex issues to human agents with full context
4. Maintain and improve the knowledge base from resolved tickets
5. Track SLA compliance and response times
6. Identify patterns in support requests

Tone: Friendly, empathetic, professional. Always acknowledge the customer's frustration.
Provide step-by-step solutions. If unsure, escalate rather than guess."""

    async def handle_task(self, task: dict) -> dict:
        task_type = task["type"]

        if task_type == "process_ticket":
            return await self._process_ticket(task["payload"])
        elif task_type == "auto_resolve":
            return await self._auto_resolve(task["payload"])
        elif task_type == "escalate":
            return await self._escalate(task["payload"])
        elif task_type == "update_kb":
            return await self._update_knowledge_base(task["payload"])
        elif task_type == "sla_check":
            return await self._sla_check()
        elif task_type == "urgent_email":
            return await self._handle_urgent_email(task["payload"])
        elif task_type == "support_report":
            return await self._support_report()
        else:
            return {"error": f"Unknown task type: {task_type}"}

    async def run_scheduled(self, schedule_name: str):
        if schedule_name == "sla_check":
            await self._sla_check()
        elif schedule_name == "daily_report":
            await self._support_report()

    async def _process_ticket(self, payload: dict) -> dict:
        """Process a new support ticket."""
        # Search knowledge base for similar issues
        kb_results = await self.recall(
            f"{payload.get('subject', '')} {payload.get('body', '')[:200]}",
            limit=5,
        )

        kb_context = "\n".join(
            f"- [{r['key']}] (similarity: {r['similarity']}): {r['content'][:200]}"
            for r in kb_results
        ) if kb_results else "No similar issues found in knowledge base."

        triage = await ai.extract_json(f"""
Triage this support ticket:

From: {payload.get('customer_email', 'unknown')}
Subject: {payload.get('subject', '')}
Body: {payload.get('body', '')}

Knowledge base matches:
{kb_context}

Return JSON:
{{
    "category": "billing|technical|account|bug|feature_request|general",
    "priority": "critical|high|medium|low",
    "can_auto_resolve": true/false,
    "confidence": 0.0-1.0,
    "suggested_response": "response text if can_auto_resolve",
    "escalation_reason": "reason if cannot auto-resolve or null",
    "tags": ["tag1", "tag2"]
}}
""", self.system_prompt)

        # Store ticket
        async with async_session() as db:
            result = await db.execute(
                text("""
                    INSERT INTO tickets (source, customer_email, subject, body,
                        category, priority, status)
                    VALUES (:source, :email, :subject, :body, :cat, :priority, 'open')
                    RETURNING id
                """),
                {
                    "source": payload.get("source", "email"),
                    "email": payload.get("customer_email", ""),
                    "subject": payload.get("subject", ""),
                    "body": payload.get("body", ""),
                    "cat": triage.get("category", "general"),
                    "priority": triage.get("priority", "medium"),
                },
            )
            await db.commit()
            ticket_id = result.fetchone()[0]

        # Auto-resolve or escalate
        if triage.get("can_auto_resolve") and triage.get("confidence", 0) > 0.85:
            return await self._auto_resolve({
                "ticket_id": ticket_id,
                "response": triage["suggested_response"],
                "confidence": triage["confidence"],
                "customer_email": payload.get("customer_email"),
                "subject": payload.get("subject"),
            })
        elif triage.get("priority") in ("critical", "high"):
            return await self._escalate({
                "ticket_id": ticket_id,
                "reason": triage.get("escalation_reason", "High priority ticket"),
                "triage": triage,
            })

        return {"ticket_id": ticket_id, "triage": triage, "status": "open"}

    async def _auto_resolve(self, payload: dict) -> dict:
        """Auto-resolve an L1 ticket."""
        ticket_id = payload.get("ticket_id")
        response = payload.get("response", "")

        # Send response via email agent
        await self.send_to("email-agent", "draft_reply", {
            "to": payload.get("customer_email"),
            "subject": f"Re: {payload.get('subject', 'Support ticket')}",
            "body": response,
            "instructions": "Send as customer support reply. Friendly and helpful tone.",
        })

        # Update ticket
        async with async_session() as db:
            await db.execute(
                text("""
                    UPDATE tickets SET status = 'auto_resolved', resolution = :resolution,
                        auto_resolved = TRUE, resolution_confidence = :conf, resolved_at = NOW()
                    WHERE id = :id
                """),
                {
                    "id": ticket_id,
                    "resolution": response,
                    "conf": payload.get("confidence", 0),
                },
            )
            await db.commit()

        await self.log("ticket_auto_resolved", {
            "ticket_id": ticket_id,
            "confidence": payload.get("confidence"),
        })

        return {"ticket_id": ticket_id, "status": "auto_resolved", "response": response}

    async def _escalate(self, payload: dict) -> dict:
        """Escalate a ticket to human support."""
        ticket_id = payload.get("ticket_id")

        async with async_session() as db:
            await db.execute(
                text("UPDATE tickets SET status = 'escalated' WHERE id = :id"),
                {"id": ticket_id},
            )
            await db.commit()

        await self.log("ticket_escalated", {
            "ticket_id": ticket_id,
            "reason": payload.get("reason"),
        }, needs_approval=True)

        return {"ticket_id": ticket_id, "status": "escalated", "reason": payload.get("reason")}

    async def _update_knowledge_base(self, payload: dict) -> dict:
        """Add a resolved ticket's solution to the knowledge base."""
        await self.remember(
            f"kb:{payload.get('category', 'general')}:{payload.get('title', 'untitled')}",
            f"Problem: {payload.get('problem', '')}\nSolution: {payload.get('solution', '')}",
            metadata={"category": payload.get("category"), "source": "resolved_ticket"},
        )
        return {"stored": True}

    async def _sla_check(self) -> dict:
        """Check SLA compliance for open tickets."""
        async with async_session() as db:
            result = await db.execute(
                text("""
                    SELECT id, priority, customer_email, subject, created_at,
                        EXTRACT(EPOCH FROM NOW() - created_at)/3600 as hours_open
                    FROM tickets
                    WHERE status IN ('open', 'escalated')
                    ORDER BY
                        CASE priority
                            WHEN 'critical' THEN 1 WHEN 'high' THEN 2
                            WHEN 'medium' THEN 3 ELSE 4
                        END
                """),
            )
            tickets = [
                {
                    "id": r[0], "priority": r[1], "email": r[2],
                    "subject": r[3], "hours_open": round(float(r[5]), 1),
                }
                for r in result.fetchall()
            ]

        sla_limits = {"critical": 1, "high": 4, "medium": 24, "low": 72}
        breached = [
            t for t in tickets
            if t["hours_open"] > sla_limits.get(t["priority"], 72)
        ]

        if breached:
            await self.log("sla_breach", {"count": len(breached), "tickets": breached}, needs_approval=True)

        return {"open_tickets": len(tickets), "sla_breaches": len(breached), "breached": breached}

    async def _handle_urgent_email(self, payload: dict) -> dict:
        """Handle urgent email from the email agent."""
        return await self._process_ticket({
            "customer_email": payload.get("from"),
            "subject": payload.get("subject"),
            "body": payload.get("summary"),
            "source": "email_agent",
        })

    async def _support_report(self) -> dict:
        """Daily support metrics report."""
        async with async_session() as db:
            stats = await db.execute(
                text("""
                    SELECT
                        COUNT(*) FILTER (WHERE created_at > NOW() - INTERVAL '24 hours') as new_24h,
                        COUNT(*) FILTER (WHERE status = 'auto_resolved' AND resolved_at > NOW() - INTERVAL '24 hours') as auto_resolved_24h,
                        COUNT(*) FILTER (WHERE status = 'open') as currently_open,
                        COUNT(*) FILTER (WHERE status = 'escalated') as currently_escalated,
                        AVG(EXTRACT(EPOCH FROM resolved_at - created_at)/60)
                            FILTER (WHERE resolved_at IS NOT NULL AND resolved_at > NOW() - INTERVAL '7 days') as avg_resolution_minutes
                    FROM tickets
                """),
            )
            row = stats.fetchone()

        report = {
            "new_tickets_24h": row[0],
            "auto_resolved_24h": row[1],
            "currently_open": row[2],
            "currently_escalated": row[3],
            "avg_resolution_minutes": round(float(row[4] or 0), 1),
        }

        return report
