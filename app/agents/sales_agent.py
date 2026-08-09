"""
Sales Agent — lead scoring, outreach automation, pipeline management, follow-ups.
"""
import json
import structlog
from datetime import datetime, timedelta
from app.agents.base import BaseAgent
from app.services.ai_engine import ai
from sqlalchemy import text
from app.database import async_session

log = structlog.get_logger()


class SalesAgent(BaseAgent):
    agent_id = "sales-agent"
    name = "Sales Agent"
    description = "Lead scoring, automated outreach, pipeline tracking"
    system_prompt = """You are an expert sales strategist and CRM specialist. Your responsibilities:
1. Score and qualify leads based on behavior, demographics, and engagement
2. Personalize outreach messages for each prospect
3. Track pipeline stages and predict close probability
4. Automate follow-up sequences
5. Identify upsell and cross-sell opportunities
6. Generate sales forecasts

Be data-driven but relationship-focused. Every outreach should feel personal,
not automated. Focus on value delivery, not hard selling."""

    async def handle_task(self, task: dict) -> dict:
        task_type = task["type"]

        if task_type == "score_lead":
            return await self._score_lead(task["payload"])
        elif task_type == "generate_outreach":
            return await self._generate_outreach(task["payload"])
        elif task_type == "pipeline_update":
            return await self._pipeline_update(task["payload"])
        elif task_type == "follow_up_check":
            return await self._follow_up_check()
        elif task_type == "sales_forecast":
            return await self._sales_forecast()
        elif task_type == "urgent_email":
            return await self._handle_urgent_email(task["payload"])
        else:
            return {"error": f"Unknown task type: {task_type}"}

    async def run_scheduled(self, schedule_name: str):
        if schedule_name == "follow_up_check":
            await self._follow_up_check()
        elif schedule_name == "daily_pipeline":
            await self._pipeline_update({})
        elif schedule_name == "weekly_forecast":
            await self._sales_forecast()

    async def _score_lead(self, payload: dict) -> dict:
        """Score a lead based on available data."""
        contact = payload.get("contact", {})

        # Get historical context
        context = await self.recall(
            f"{contact.get('company', '')} {contact.get('email', '')}",
            limit=3,
        )

        scoring = await ai.extract_json(f"""
Score this lead on a 0-100 scale:

Name: {contact.get('name', 'Unknown')}
Email: {contact.get('email', '')}
Company: {contact.get('company', '')}
Title: {contact.get('title', '')}
Source: {payload.get('source', 'unknown')}
Engagement: {json.dumps(payload.get('engagement', {{}}))}
Previous interactions: {json.dumps([c['content'][:100] for c in context])}

Return JSON:
{{
    "score": 0-100,
    "stage": "cold|warm|hot|qualified",
    "reasoning": "...",
    "recommended_action": "...",
    "ideal_outreach_channel": "email|linkedin|phone|none",
    "talking_points": ["..."]
}}
""", self.system_prompt)

        # Update contact in database
        async with async_session() as db:
            await db.execute(
                text("""
                    INSERT INTO contacts (email, name, company, title, lead_score, stage, source, metadata)
                    VALUES (:email, :name, :company, :title, :score, :stage, :source, :meta)
                    ON CONFLICT (email) DO UPDATE SET
                        lead_score = :score, stage = :stage,
                        last_interaction = NOW(), metadata = :meta
                """),
                {
                    "email": contact.get("email", ""),
                    "name": contact.get("name", ""),
                    "company": contact.get("company", ""),
                    "title": contact.get("title", ""),
                    "score": scoring.get("score", 0),
                    "stage": scoring.get("stage", "cold"),
                    "source": payload.get("source", "unknown"),
                    "meta": json.dumps(scoring),
                },
            )
            await db.commit()

        # If hot lead, trigger outreach
        if scoring.get("score", 0) >= 70:
            await self.send_to("email-agent", "draft_reply", {
                "to": contact.get("email"),
                "subject": f"Re: {contact.get('company', 'Your inquiry')}",
                "body": "",
                "instructions": f"Draft personalized outreach. Talking points: {scoring.get('talking_points', [])}",
            })

        return scoring

    async def _generate_outreach(self, payload: dict) -> dict:
        """Generate personalized outreach message."""
        contact = payload.get("contact", {})
        channel = payload.get("channel", "email")

        # Research the contact/company from memory
        context = await self.recall(contact.get("company", ""), limit=5)
        context_text = "\n".join(c["content"][:200] for c in context) if context else ""

        prompt = f"""Write a personalized {channel} outreach message:

To: {contact.get('name', 'Prospect')} at {contact.get('company', '')}
Title: {contact.get('title', '')}
Context: {context_text}
Goal: {payload.get('goal', 'Initial outreach')}
Product/Service: {payload.get('product', 'Our platform')}

Requirements:
- Feel personal, not templated
- Reference something specific about their company
- Clear value proposition
- Soft CTA (meeting suggestion, not hard sell)
- Under 150 words for email, under 300 chars for LinkedIn"""

        message = await self.quick_think(prompt)
        return {"message": message, "channel": channel, "needs_approval": True}

    async def _follow_up_check(self) -> dict:
        """Check for contacts that need follow-up."""
        async with async_session() as db:
            result = await db.execute(
                text("""
                    SELECT id, email, name, company, stage, lead_score, last_interaction
                    FROM contacts
                    WHERE stage IN ('warm', 'hot', 'qualified')
                      AND (last_interaction IS NULL OR last_interaction < NOW() - INTERVAL '3 days')
                    ORDER BY lead_score DESC LIMIT 10
                """),
            )
            overdue = [
                {
                    "id": r[0], "email": r[1], "name": r[2], "company": r[3],
                    "stage": r[4], "score": r[5],
                    "last_interaction": r[6].isoformat() if r[6] else "never",
                }
                for r in result.fetchall()
            ]

        for contact in overdue:
            outreach = await self._generate_outreach({
                "contact": contact,
                "channel": "email",
                "goal": "Follow-up",
            })
            await self.send_to("email-agent", "draft_reply", {
                "to": contact["email"],
                "subject": f"Following up - {contact.get('company', '')}",
                "body": outreach["message"],
                "instructions": "Send as follow-up",
            })

        return {"follow_ups_triggered": len(overdue), "contacts": overdue}

    async def _pipeline_update(self, payload: dict) -> dict:
        """Update and analyze the sales pipeline."""
        async with async_session() as db:
            result = await db.execute(
                text("""
                    SELECT stage, COUNT(*) as count, AVG(lead_score) as avg_score
                    FROM contacts
                    WHERE stage != 'new'
                    GROUP BY stage
                """),
            )
            pipeline = [
                {"stage": r[0], "count": r[1], "avg_score": round(float(r[2] or 0), 1)}
                for r in result.fetchall()
            ]

        return {"pipeline": pipeline}

    async def _sales_forecast(self) -> dict:
        """Generate sales forecast based on pipeline."""
        pipeline = await self._pipeline_update({})

        forecast = await ai.extract_json(f"""
Based on this pipeline:
{json.dumps(pipeline.get('pipeline', []), indent=2)}

Generate a sales forecast. Use typical conversion rates:
- warm → qualified: 30%
- qualified → closed: 25%

Return JSON:
{{
    "forecast_period": "next_30_days",
    "expected_conversions": X,
    "pipeline_value_estimate": X,
    "confidence": "low|medium|high",
    "bottlenecks": ["..."],
    "recommendations": ["..."]
}}
""", self.system_prompt)

        return forecast

    async def _handle_urgent_email(self, payload: dict) -> dict:
        """Handle an urgent email forwarded by the email agent."""
        analysis = await self.quick_think(f"""
Urgent email received:
From: {payload.get('from', 'unknown')}
Subject: {payload.get('subject', '')}
Summary: {payload.get('summary', '')}

Determine: Is this a sales opportunity, a customer issue, or something else?
What's the recommended immediate action?
""")

        await self.log("urgent_email_handled", {
            "from": payload.get("from"),
            "analysis": analysis,
        })

        return {"analysis": analysis, "handled": True}
