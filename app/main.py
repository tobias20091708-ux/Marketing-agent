"""
AI Operations Platform — Main FastAPI application.
Serves the dashboard, REST API, and webhook endpoints.
"""
import json
import structlog
from datetime import datetime
from pathlib import Path
from fastapi import FastAPI, HTTPException, Request, Depends
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from typing import Optional
from sqlalchemy import text

from app.config import settings
from app.database import async_session
from app.services.task_queue import queue
from app.services.ai_engine import ai
from app.services.audit import audit
from app.services.memory import memory
from app.agents import get_agent, AGENTS

structlog.configure(
    processors=[
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.dev.ConsoleRenderer(),
    ],
    wrapper_class=structlog.make_filtering_bound_logger(20),
)
log = structlog.get_logger()

app = FastAPI(title="AI Operations Platform", version="1.0.0")


# === HEALTH ===
@app.get("/health")
async def health():
    return {"status": "healthy", "timestamp": datetime.utcnow().isoformat()}


# === DASHBOARD ===
@app.get("/", response_class=HTMLResponse)
async def dashboard():
    dashboard_path = Path(__file__).parent.parent / "dashboard" / "index.html"
    if dashboard_path.exists():
        return HTMLResponse(dashboard_path.read_text())
    return HTMLResponse("<h1>AI Platform Running</h1><p>Dashboard not found.</p>")


# === AGENT API ===
class TaskRequest(BaseModel):
    agent_id: str
    task_type: str
    payload: dict = {}
    priority: int = 5


@app.post("/api/tasks")
async def create_task(req: TaskRequest):
    """Submit a task to an agent."""
    if req.agent_id not in AGENTS:
        raise HTTPException(400, f"Unknown agent: {req.agent_id}")
    task_id = await queue.enqueue(req.agent_id, req.task_type, req.payload, req.priority)
    return {"task_id": task_id, "status": "queued"}


@app.get("/api/tasks/{task_id}")
async def get_task(task_id: int):
    """Get task status and result."""
    async with async_session() as db:
        result = await db.execute(
            text("SELECT id, agent_id, type, status, payload, result, error, created_at, completed_at FROM tasks WHERE id = :id"),
            {"id": task_id},
        )
        row = result.fetchone()
        if not row:
            raise HTTPException(404, "Task not found")
        return {
            "id": row[0], "agent_id": row[1], "type": row[2], "status": row[3],
            "payload": row[4], "result": row[5], "error": row[6],
            "created_at": row[7].isoformat() if row[7] else None,
            "completed_at": row[8].isoformat() if row[8] else None,
        }


# === AGENT STATUS ===
@app.get("/api/agents")
async def list_agents():
    """Get status of all agents."""
    async with async_session() as db:
        result = await db.execute(text("SELECT id, name, type, status, last_run, stats FROM agents"))
        return [
            {
                "id": r[0], "name": r[1], "type": r[2], "status": r[3],
                "last_run": r[4].isoformat() if r[4] else None,
                "stats": r[5],
            }
            for r in result.fetchall()
        ]


@app.get("/api/agents/{agent_id}/stats")
async def agent_stats(agent_id: str):
    """Get detailed stats for an agent."""
    stats = await queue.get_stats(agent_id)
    recent = await audit.get_recent(limit=10, agent_id=agent_id)
    return {"queue": stats, "recent_actions": recent}


# === METRICS DASHBOARD API ===
@app.get("/api/dashboard/overview")
async def dashboard_overview():
    """Get overview metrics for the dashboard."""
    async with async_session() as db:
        # Task stats
        tasks = await db.execute(
            text("""
                SELECT status, COUNT(*) FROM tasks
                WHERE created_at > NOW() - INTERVAL '24 hours'
                GROUP BY status
            """),
        )
        task_stats = {r[0]: r[1] for r in tasks.fetchall()}

        # Email stats
        emails = await db.execute(
            text("""
                SELECT category, COUNT(*) FROM emails
                WHERE processed_at > NOW() - INTERVAL '24 hours'
                GROUP BY category
            """),
        )
        email_stats = {r[0]: r[1] for r in emails.fetchall()}

        # Ticket stats
        tickets = await db.execute(
            text("""
                SELECT status, COUNT(*) FROM tickets GROUP BY status
            """),
        )
        ticket_stats = {r[0]: r[1] for r in tickets.fetchall()}

        # Revenue
        revenue = await db.execute(
            text("""
                SELECT COALESCE(SUM(amount), 0) FROM transactions
                WHERE amount > 0 AND transaction_date >= DATE_TRUNC('month', NOW())
            """),
        )
        month_revenue = float(revenue.fetchone()[0])

        # Contact pipeline
        pipeline = await db.execute(
            text("SELECT stage, COUNT(*) FROM contacts GROUP BY stage"),
        )
        pipeline_stats = {r[0]: r[1] for r in pipeline.fetchall()}

    return {
        "tasks_24h": task_stats,
        "emails_24h": email_stats,
        "tickets": ticket_stats,
        "month_revenue": month_revenue,
        "pipeline": pipeline_stats,
        "ai_stats": ai.get_stats(),
    }


# === AUDIT LOG ===
@app.get("/api/audit")
async def get_audit_log(limit: int = 50, agent_id: str = None):
    return await audit.get_recent(limit=limit, agent_id=agent_id)


# === MEMORY / KNOWLEDGE BASE ===
class MemoryRequest(BaseModel):
    namespace: str
    key: str
    content: str
    metadata: dict = {}


@app.post("/api/memory")
async def store_memory(req: MemoryRequest):
    await memory.store(req.namespace, req.key, req.content, req.metadata)
    return {"stored": True}


@app.get("/api/memory/search")
async def search_memory(namespace: str, query: str, limit: int = 5):
    results = await memory.search(namespace, query, limit)
    return {"results": results}


# === CHAT ENDPOINT (talk to any agent) ===
class ChatRequest(BaseModel):
    message: str
    agent_id: Optional[str] = None


@app.post("/api/chat")
async def chat(req: ChatRequest):
    """Chat with an agent or the orchestrator."""
    if req.agent_id and req.agent_id in AGENTS:
        agent = get_agent(req.agent_id)
        response = await agent.quick_think(req.message)
    else:
        # Orchestrator decides which agent to use
        routing = await ai.extract_json(f"""
Given this user message, determine which agent should handle it:
Message: {req.message}

Available agents: {list(AGENTS.keys())}

Return JSON: {{"agent_id": "agent-id", "task_type": "type", "reasoning": "why"}}
""")
        agent_id = routing.get("agent_id", "personal-assistant")
        agent = get_agent(agent_id)
        response = await agent.quick_think(req.message)

    return {"response": response, "agent": req.agent_id or "orchestrator"}


# === WEBHOOKS ===
@app.post("/webhooks/stripe")
async def stripe_webhook(request: Request):
    """Handle Stripe webhook events."""
    body = await request.json()
    event_type = body.get("type", "")

    if event_type in ("charge.succeeded", "charge.failed", "invoice.paid"):
        await queue.enqueue("finance-agent", "sync_transactions", {"event": body}, priority=3)

    return {"received": True}


@app.post("/webhooks/github")
async def github_webhook(request: Request):
    """Handle GitHub webhook events."""
    body = await request.json()
    event = request.headers.get("X-GitHub-Event", "")

    if event == "pull_request" and body.get("action") in ("opened", "synchronize"):
        pr = body.get("pull_request", {})
        await queue.enqueue("dev-agent", "review_pr", {
            "pr": {
                "repo": body.get("repository", {}).get("full_name"),
                "number": pr.get("number"),
                "title": pr.get("title"),
                "author": pr.get("user", {}).get("login"),
                "description": pr.get("body", ""),
                "files_changed": pr.get("changed_files", 0),
            }
        }, priority=3)

    elif event == "deployment_status":
        if body.get("deployment_status", {}).get("state") == "failure":
            await queue.enqueue("dev-agent", "incident_response", {
                "service": body.get("repository", {}).get("name"),
                "error": body.get("deployment_status", {}).get("description", "Deployment failed"),
                "severity": "high",
            }, priority=1)

    return {"received": True}


@app.post("/webhooks/slack")
async def slack_webhook(request: Request):
    """Handle Slack interactive components (approval buttons)."""
    body = await request.form()
    payload = json.loads(body.get("payload", "{}"))

    if payload.get("type") == "block_actions":
        for action in payload.get("actions", []):
            action_id = action.get("action_id", "")
            if action_id.startswith("approve_"):
                await audit.log_action(
                    "system", "approval_granted",
                    {"action": action_id, "user": payload.get("user", {}).get("name")},
                    user_approved=True,
                )
            elif action_id.startswith("deny_"):
                await audit.log_action(
                    "system", "approval_denied",
                    {"action": action_id, "user": payload.get("user", {}).get("name")},
                )

    return {"ok": True}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
