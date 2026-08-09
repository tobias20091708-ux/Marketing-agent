"""
Dev Agent — code review, deployment monitoring, incident response, auto-fixes.
"""
import json
import structlog
from app.agents.base import BaseAgent
from app.services.ai_engine import ai
from app.integrations.github_integration import github_client
from sqlalchemy import text
from app.database import async_session

log = structlog.get_logger()


class DevAgent(BaseAgent):
    agent_id = "dev-agent"
    name = "Dev Agent"
    description = "Code review, deployment monitoring, incident detection and response"
    system_prompt = """You are an expert software engineer and DevOps specialist. Your responsibilities:
1. Review pull requests for security, performance, and correctness
2. Monitor deployments and detect issues
3. Respond to incidents with diagnostic steps
4. Suggest code improvements and refactoring
5. Track technical debt
6. Generate deployment reports

Focus on security vulnerabilities, performance regressions, and breaking changes.
Always explain your reasoning. When reviewing code, be constructive."""

    async def handle_task(self, task: dict) -> dict:
        task_type = task["type"]

        if task_type == "review_pr":
            return await self._review_pr(task["payload"])
        elif task_type == "check_deployments":
            return await self._check_deployments()
        elif task_type == "incident_response":
            return await self._incident_response(task["payload"])
        elif task_type == "monitor_errors":
            return await self._monitor_errors()
        elif task_type == "generate_changelog":
            return await self._generate_changelog(task["payload"])
        elif task_type == "tech_debt_audit":
            return await self._tech_debt_audit(task["payload"])
        else:
            return {"error": f"Unknown task type: {task_type}"}

    async def run_scheduled(self, schedule_name: str):
        if schedule_name == "check_deployments":
            await self._check_deployments()
        elif schedule_name == "monitor_errors":
            await self._monitor_errors()
        elif schedule_name == "daily_review":
            await self._check_open_prs()

    async def _review_pr(self, payload: dict) -> dict:
        """Review a pull request."""
        pr_data = payload.get("pr", {})
        diff = payload.get("diff", "")

        review = await ai.extract_json(f"""
Review this pull request:

Title: {pr_data.get('title', '')}
Author: {pr_data.get('author', '')}
Description: {pr_data.get('description', '')}
Files changed: {pr_data.get('files_changed', 0)}

Diff:
{diff[:8000]}

Return JSON:
{{
    "verdict": "approve|request_changes|comment",
    "security_issues": [{{"file": "...", "line": X, "issue": "...", "severity": "critical|high|medium|low"}}],
    "performance_issues": [{{"file": "...", "description": "..."}}],
    "bugs": [{{"file": "...", "line": X, "description": "..."}}],
    "suggestions": [{{"file": "...", "suggestion": "..."}}],
    "summary": "overall review summary",
    "score": 0-100
}}
""", self.system_prompt)

        # Post review to GitHub if configured
        if pr_data.get("number") and pr_data.get("repo"):
            await github_client.post_review(
                repo=pr_data["repo"],
                pr_number=pr_data["number"],
                body=review.get("summary", ""),
                event="APPROVE" if review.get("verdict") == "approve" else "COMMENT",
            )

        return review

    async def _check_deployments(self) -> dict:
        """Check recent deployment status."""
        deployments = await github_client.get_recent_deployments()

        if not deployments:
            return {"status": "no_recent_deployments"}

        issues = []
        for dep in deployments:
            if dep.get("status") == "failure":
                issues.append({
                    "repo": dep.get("repo"),
                    "ref": dep.get("ref"),
                    "error": dep.get("error", "Unknown error"),
                })

        if issues:
            await self.log("deployment_failures", {"count": len(issues), "details": issues})

        return {"deployments": len(deployments), "failures": len(issues), "issues": issues}

    async def _incident_response(self, payload: dict) -> dict:
        """Respond to a production incident."""
        error = payload.get("error", "")
        service = payload.get("service", "unknown")
        severity = payload.get("severity", "medium")

        # Search memory for similar past incidents
        past = await self.recall(f"incident {service} {error[:100]}", limit=5)
        past_text = "\n".join(p["content"][:200] for p in past) if past else "No similar past incidents."

        response = await ai.extract_json(f"""
Production incident detected:

Service: {service}
Error: {error[:2000]}
Severity: {severity}
Timestamp: {payload.get('timestamp', 'now')}

Similar past incidents:
{past_text}

Return JSON:
{{
    "diagnosis": "likely root cause",
    "immediate_actions": ["step 1", "step 2"],
    "investigation_queries": ["SQL or log query to run"],
    "rollback_recommended": true/false,
    "estimated_impact": "description of user impact",
    "communication": "suggested status page/Slack message"
}}
""", self.system_prompt)

        # Store for future reference
        await self.remember(
            f"incident:{service}:{payload.get('timestamp', 'now')}",
            f"Incident in {service}: {error[:500]}\nDiagnosis: {response.get('diagnosis', '')}",
        )

        await self.log("incident_response", {
            "service": service,
            "severity": severity,
            "diagnosis": response.get("diagnosis"),
        }, needs_approval=True)

        return response

    async def _monitor_errors(self) -> dict:
        """Monitor for new errors across services."""
        # This would connect to error tracking (Sentry, Datadog, etc.)
        # Placeholder structure
        return {"status": "monitoring", "errors_found": 0}

    async def _check_open_prs(self) -> dict:
        """Check for PRs that need review."""
        prs = await github_client.get_open_prs()
        needs_review = [pr for pr in prs if not pr.get("reviewed")]

        for pr in needs_review[:5]:
            diff = await github_client.get_pr_diff(pr["repo"], pr["number"])
            await self._review_pr({"pr": pr, "diff": diff})

        return {"open_prs": len(prs), "reviewed": len(needs_review)}

    async def _generate_changelog(self, payload: dict) -> dict:
        """Generate a changelog from recent commits."""
        commits = payload.get("commits", [])

        changelog = await self.quick_think(f"""
Generate a user-facing changelog from these commits:
{json.dumps(commits[:30], indent=2)}

Group by: Features, Fixes, Improvements, Breaking Changes.
Write for end users, not developers.
""")

        return {"changelog": changelog}

    async def _tech_debt_audit(self, payload: dict) -> dict:
        """Analyze technical debt in a repository."""
        repo = payload.get("repo", "")

        audit = await ai.extract_json(f"""
Based on your knowledge, outline a technical debt audit framework for a typical
web application. What should we check?

Return JSON:
{{
    "categories": [
        {{
            "name": "category",
            "checks": ["what to look for"],
            "priority": "high|medium|low",
            "estimated_effort": "hours/days/weeks"
        }}
    ],
    "recommended_tools": ["tool1", "tool2"],
    "process": "suggested audit process"
}}
""", self.system_prompt)

        return audit
