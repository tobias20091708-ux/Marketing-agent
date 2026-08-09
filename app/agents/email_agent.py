"""
Email Agent — monitors inbox, triages, categorizes, drafts replies, sends with approval.
"""
import json
import structlog
from app.agents.base import BaseAgent
from app.integrations.gmail import gmail
from app.services.ai_engine import ai

log = structlog.get_logger()


class EmailAgent(BaseAgent):
    agent_id = "email-agent"
    name = "Email Agent"
    description = "Monitors inbox, triages emails, drafts smart replies"
    system_prompt = """You are an expert email assistant. Your responsibilities:
1. Triage incoming emails by urgency and category
2. Draft professional, contextual replies
3. Summarize email threads
4. Flag important emails that need human attention
5. Auto-reply to routine emails when confidence is high

Categories: urgent, action_required, informational, newsletter, spam, personal
Priority: critical, high, medium, low

Always maintain professional tone. When drafting replies, match the sender's
communication style. Never commit to meetings, payments, or decisions without
flagging for human approval."""

    async def handle_task(self, task: dict) -> dict:
        task_type = task["type"]

        if task_type == "check_inbox":
            return await self._check_inbox()
        elif task_type == "process_email":
            return await self._process_email(task["payload"])
        elif task_type == "draft_reply":
            return await self._draft_reply(task["payload"])
        elif task_type == "send_reply":
            return await self._send_reply(task["payload"])
        elif task_type == "summarize_thread":
            return await self._summarize_thread(task["payload"])
        else:
            return {"error": f"Unknown task type: {task_type}"}

    async def run_scheduled(self, schedule_name: str):
        if schedule_name == "check_inbox":
            await self._check_inbox()

    async def _check_inbox(self) -> dict:
        """Fetch and process new unread emails."""
        emails = await gmail.get_unread(max_results=20)
        processed = 0

        for email_data in emails:
            analysis = await self._analyze_email(email_data)

            # Store in database
            await gmail.store_processed_email(
                email_data=email_data,
                category=analysis["category"],
                priority=analysis["priority"],
                sentiment=analysis.get("sentiment", 0),
                requires_action=analysis["requires_action"],
                suggested_reply=analysis.get("suggested_reply"),
                reply_confidence=analysis.get("reply_confidence", 0),
            )

            # If high confidence auto-reply and enabled
            if (
                analysis.get("reply_confidence", 0) > 0.9
                and not analysis["requires_action"]
                and analysis["category"] == "informational"
            ):
                await self.log(
                    "auto_reply_candidate",
                    {"subject": email_data.get("subject"), "confidence": analysis["reply_confidence"]},
                    needs_approval=True,
                )

            # Forward urgent to Slack
            if analysis["priority"] in ("critical", "high"):
                await self.send_to(
                    "support-agent" if analysis["category"] == "support" else "sales-agent",
                    "urgent_email",
                    {
                        "subject": email_data.get("subject"),
                        "from": email_data.get("from"),
                        "summary": analysis["summary"],
                        "priority": analysis["priority"],
                    },
                )

            processed += 1

        await self.remember(
            f"inbox_check_{processed}",
            f"Checked inbox: {processed} emails processed",
        )
        return {"processed": processed}

    async def _analyze_email(self, email_data: dict) -> dict:
        """Analyze an email for category, priority, sentiment, and draft reply."""
        prompt = f"""Analyze this email and respond with JSON:

From: {email_data.get('from', 'unknown')}
Subject: {email_data.get('subject', 'no subject')}
Body: {email_data.get('body', '')[:2000]}

Respond with this exact JSON structure:
{{
    "category": "urgent|action_required|informational|newsletter|spam|personal",
    "priority": "critical|high|medium|low",
    "sentiment": 0.0 to 1.0 (0=very negative, 1=very positive),
    "requires_action": true/false,
    "summary": "one-sentence summary",
    "suggested_reply": "draft reply text or null if no reply needed",
    "reply_confidence": 0.0 to 1.0
}}"""

        return await ai.extract_json(prompt, self.system_prompt)

    async def _draft_reply(self, payload: dict) -> dict:
        """Draft a reply to an email."""
        # Get relevant context from memory
        context = await self.recall(payload.get("subject", ""), limit=3)
        context_text = "\n".join(c["content"] for c in context) if context else "No prior context."

        prompt = f"""Draft a reply to this email:

From: {payload.get('from', '')}
Subject: {payload.get('subject', '')}
Body: {payload.get('body', '')[:3000]}

Previous context about this sender/topic:
{context_text}

Instructions: {payload.get('instructions', 'Write a professional, helpful reply.')}

Draft the reply only. No meta-commentary."""

        reply = await self.quick_think(prompt)
        return {"draft": reply, "needs_approval": True}

    async def _send_reply(self, payload: dict) -> dict:
        """Send an approved reply."""
        await self.log("send_email", {
            "to": payload["to"],
            "subject": payload.get("subject", ""),
        })
        result = await gmail.send_reply(
            to=payload["to"],
            subject=payload.get("subject", ""),
            body=payload["body"],
            thread_id=payload.get("thread_id"),
        )
        return {"sent": True, "message_id": result}

    async def _summarize_thread(self, payload: dict) -> dict:
        """Summarize an email thread."""
        messages = payload.get("messages", [])
        thread_text = "\n---\n".join(
            f"From: {m.get('from', '?')}\n{m.get('body', '')[:500]}"
            for m in messages
        )
        summary = await self.quick_think(
            f"Summarize this email thread concisely. Key points, decisions, and action items:\n\n{thread_text}"
        )
        return {"summary": summary}

    async def _process_email(self, payload: dict) -> dict:
        """Process a single email (webhook trigger)."""
        analysis = await self._analyze_email(payload)
        return analysis
