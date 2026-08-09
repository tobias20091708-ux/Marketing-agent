"""
Marketing Agent — ad optimization, content generation, campaign analysis, reporting.
"""
import json
import structlog
from datetime import datetime, timedelta
from app.agents.base import BaseAgent
from app.services.ai_engine import ai
from sqlalchemy import text
from app.database import async_session

log = structlog.get_logger()


class MarketingAgent(BaseAgent):
    agent_id = "marketing-agent"
    name = "Marketing Agent"
    description = "Ad optimization, content generation, campaign performance analysis"
    system_prompt = """You are an expert digital marketing strategist and analyst. Your responsibilities:
1. Analyze campaign performance across Meta Ads, Google Ads, and other platforms
2. Optimize ad spend and ROAS
3. Generate ad copy, landing page content, and social media posts
4. Identify audience segments and targeting opportunities
5. Produce weekly marketing reports with actionable insights
6. A/B test recommendations

Focus on ROI and actionable recommendations. Use data to back every suggestion.
When generating content, maintain brand voice consistency."""

    async def handle_task(self, task: dict) -> dict:
        task_type = task["type"]

        if task_type == "sync_campaigns":
            return await self._sync_campaigns()
        elif task_type == "analyze_performance":
            return await self._analyze_performance(task["payload"])
        elif task_type == "generate_content":
            return await self._generate_content(task["payload"])
        elif task_type == "optimize_budget":
            return await self._optimize_budget(task["payload"])
        elif task_type == "weekly_report":
            return await self._weekly_report()
        elif task_type == "ab_test_recommendation":
            return await self._ab_test_recommendation(task["payload"])
        else:
            return {"error": f"Unknown task type: {task_type}"}

    async def run_scheduled(self, schedule_name: str):
        if schedule_name == "daily_sync":
            await self._sync_campaigns()
        elif schedule_name == "weekly_report":
            await self._weekly_report()
        elif schedule_name == "budget_optimization":
            await self._optimize_budget({})

    async def _sync_campaigns(self) -> dict:
        """Sync campaign data from ad platforms."""
        # This would connect to Meta/Google Ads APIs
        # For now, structure shows the data flow
        async with async_session() as db:
            result = await db.execute(
                text("SELECT COUNT(*) FROM campaigns WHERE last_synced > NOW() - INTERVAL '1 hour'"),
            )
            recent = result.fetchone()[0]

        await self.log("campaigns_synced", {"count": recent})
        return {"synced": True, "campaigns_updated": recent}

    async def _analyze_performance(self, payload: dict) -> dict:
        """Analyze campaign performance with AI insights."""
        async with async_session() as db:
            result = await db.execute(
                text("""
                    SELECT name, platform, status, budget, spend, impressions,
                           clicks, conversions, roas, data
                    FROM campaigns
                    WHERE status = 'active'
                    ORDER BY spend DESC LIMIT 20
                """),
            )
            campaigns = [
                {
                    "name": r[0], "platform": r[1], "status": r[2],
                    "budget": float(r[3] or 0), "spend": float(r[4] or 0),
                    "impressions": r[5], "clicks": r[6], "conversions": r[7],
                    "roas": float(r[8] or 0),
                }
                for r in result.fetchall()
            ]

        if not campaigns:
            return {"message": "No active campaigns found"}

        analysis = await ai.extract_json(f"""
Analyze these active campaigns:
{json.dumps(campaigns, indent=2)}

Return JSON:
{{
    "top_performers": [{{"name": "...", "reason": "..."}}],
    "underperformers": [{{"name": "...", "issue": "...", "recommendation": "..."}}],
    "overall_roas": X.X,
    "total_spend": X,
    "total_conversions": X,
    "recommendations": ["..."],
    "budget_reallocation": [{{"from": "campaign", "to": "campaign", "amount": X, "reason": "..."}}]
}}
""", self.system_prompt)

        return analysis

    async def _generate_content(self, payload: dict) -> dict:
        """Generate marketing content."""
        content_type = payload.get("type", "ad_copy")
        target_audience = payload.get("audience", "general")
        product = payload.get("product", "")
        tone = payload.get("tone", "professional")
        platform = payload.get("platform", "facebook")

        # Get brand context from memory
        brand_context = await self.recall("brand voice", limit=2)
        brand_text = "\n".join(c["content"] for c in brand_context) if brand_context else ""

        prompt = f"""Generate {content_type} for {platform}:

Product/Service: {product}
Target Audience: {target_audience}
Tone: {tone}
Brand Context: {brand_text}

Generate 3 variations with different angles. For each include:
- Headline
- Body text
- Call to action
- Suggested targeting notes"""

        content = await self.quick_think(prompt)

        return {
            "content_type": content_type,
            "platform": platform,
            "variations": content,
            "needs_approval": True,
        }

    async def _optimize_budget(self, payload: dict) -> dict:
        """AI-driven budget optimization across campaigns."""
        async with async_session() as db:
            result = await db.execute(
                text("""
                    SELECT platform, SUM(budget) as total_budget, SUM(spend) as total_spend,
                           SUM(conversions) as total_conv,
                           AVG(roas) as avg_roas
                    FROM campaigns WHERE status = 'active'
                    GROUP BY platform
                """),
            )
            platform_data = [
                {
                    "platform": r[0], "budget": float(r[1] or 0),
                    "spend": float(r[2] or 0), "conversions": r[3],
                    "avg_roas": round(float(r[4] or 0), 2),
                }
                for r in result.fetchall()
            ]

        optimization = await ai.extract_json(f"""
Optimize marketing budget allocation across platforms:
{json.dumps(platform_data, indent=2)}

Return JSON:
{{
    "current_allocation": {{}},
    "recommended_allocation": {{}},
    "expected_improvement": "X% more conversions",
    "reasoning": "..."
}}
""", self.system_prompt)

        return optimization

    async def _weekly_report(self) -> dict:
        """Generate comprehensive weekly marketing report."""
        performance = await self._analyze_performance({})
        forecast = await self.quick_think(f"""
Based on this week's marketing performance:
{json.dumps(performance, indent=2)}

Write a concise weekly marketing report covering:
1. Key metrics summary
2. Top performing campaigns
3. Issues and recommendations
4. Next week's priorities
""")

        # Notify via email agent
        await self.send_to("email-agent", "draft_reply", {
            "to": "team",
            "subject": f"Weekly Marketing Report - {datetime.utcnow().strftime('%Y-W%W')}",
            "body": forecast,
            "instructions": "Format as a professional internal report email",
        })

        return {"report": forecast, "sent_to_email_agent": True}

    async def _ab_test_recommendation(self, payload: dict) -> dict:
        """Recommend A/B tests based on current performance."""
        campaign_name = payload.get("campaign", "")

        recommendation = await ai.extract_json(f"""
For campaign "{campaign_name}", recommend A/B tests:

Return JSON:
{{
    "tests": [
        {{
            "element": "headline|image|cta|audience|placement",
            "hypothesis": "...",
            "variant_a": "...",
            "variant_b": "...",
            "expected_impact": "...",
            "min_sample_size": X,
            "priority": "high|medium|low"
        }}
    ]
}}
""", self.system_prompt)

        return recommendation
