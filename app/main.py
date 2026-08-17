"""
AI Operations Platform — Main FastAPI application.
Serves the dashboard, REST API, and webhook endpoints.
"""
import json
import re
import structlog
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
from fastapi import FastAPI, HTTPException, Request, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from typing import Optional
from sqlalchemy import text

from app.config import settings
from app.database import async_session
from app.services.task_queue import queue
from app.services.ai_engine import ai, WEB_SEARCH_TOOL
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

# CORS — allow the dashboard (or any origin) to reach the API.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,  # must be False when allow_origins is "*"
    allow_methods=["*"],
    allow_headers=["*"],
)

LOCAL_TZ = ZoneInfo("Europe/Copenhagen")


@app.on_event("startup")
async def ensure_alarms_table():
    """Idempotently ensure the alarms table exists.

    Railway's managed Postgres doesn't auto-run init.sql-style scripts, so
    the app creates the table itself on boot (see alarms_migration.sql for
    the same DDL, kept for manual/documentation purposes).
    """
    async with async_session() as db:
        await db.execute(
            text("""
                CREATE TABLE IF NOT EXISTS alarms (
                    id SERIAL PRIMARY KEY,
                    time TIME NOT NULL,
                    label TEXT,
                    repeat VARCHAR(20) DEFAULT 'daily',
                    active BOOLEAN DEFAULT true,
                    created_at TIMESTAMP DEFAULT NOW()
                )
            """)
        )
        await db.execute(text("CREATE INDEX IF NOT EXISTS idx_alarms_active ON alarms(active)"))
        await db.commit()


# Matches explicit specialist-agent requests, e.g. "brug email-agent",
# "brug support agent", "use dev-agent" — the only case that skips the
# personal assistant and routes straight to a specialist.
_EXPLICIT_AGENT_RE = re.compile(
    r"\b(?:brug|bruge?r?|use)\s+([a-zæøå]+)[\s-]?agent", re.IGNORECASE
)


def detect_explicit_agent(message: str) -> Optional[str]:
    """Detect an explicit 'brug X-agent' / 'use X-agent' request without an AI call."""
    match = _EXPLICIT_AGENT_RE.search(message or "")
    if not match:
        return None
    candidate = f"{match.group(1).lower()}-agent"
    return candidate if candidate in AGENTS else None


# Matches simple alarm requests in chat, e.g. "sæt alarm til 6:00",
# "sæt en alarm til kl. 06:00", "væk mig kl 7", "wake me at 7:30" —
# keyword matching only, no AI call.
_ALARM_TRIGGER_RE = re.compile(
    r"(?:sæt(?:te)?\s+(?:en\s+)?alarm|saet(?:te)?\s+(?:en\s+)?alarm|set\s+(?:an\s+)?alarm"
    r"|væk\s+mig|vaek\s+mig|wake\s+me(?:\s+up)?)"
    r"[^\d]{0,20}?(\d{1,2})(?:[:.](\d{2}))?",
    re.IGNORECASE,
)


def detect_alarm_time(message: str) -> Optional[str]:
    """Detect a 'set alarm to HH:MM' style request in a chat message."""
    match = _ALARM_TRIGGER_RE.search(message or "")
    if not match:
        return None
    hour = int(match.group(1))
    minute = int(match.group(2) or 0)
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None
    return f"{hour:02d}:{minute:02d}"


def _row_to_alarm(row) -> dict:
    return {
        "id": row[0],
        "time": row[1].strftime("%H:%M"),
        "label": row[2],
        "repeat": row[3],
        "active": row[4],
        "created_at": row[5].isoformat() if row[5] else None,
    }


def _validate_alarm_time(value: str) -> str:
    if not re.fullmatch(r"([01]?\d|2[0-3]):[0-5]\d", (value or "").strip()):
        raise HTTPException(400, "time skal være i formatet HH:MM, fx '06:00'")
    hour, minute = value.strip().split(":")
    return f"{int(hour):02d}:{int(minute):02d}"


async def create_alarm(time_str: str, label: str = "Alarm", repeat: str = "daily") -> dict:
    """Insert a new alarm. Shared by the /api/alarms endpoint and the chat integration."""
    time_str = _validate_alarm_time(time_str)
    async with async_session() as db:
        result = await db.execute(
            text("""
                INSERT INTO alarms (time, label, repeat)
                VALUES (:time, :label, :repeat)
                RETURNING id, time, label, repeat, active, created_at
            """),
            {"time": time_str, "label": label, "repeat": repeat},
        )
        row = result.fetchone()
        await db.commit()
    return _row_to_alarm(row)


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
    """Chat with an agent.

    The personal assistant handles every message directly with a single AI
    call (including automatic web search when needed). It only routes to a
    specialist agent when the user explicitly asks for one (e.g. "brug
    email-agent") — no separate classification call is made. A "set alarm to
    HH:MM" request is caught first via keyword matching, with no AI call at all.
    """
    alarm_time = detect_alarm_time(req.message)
    if alarm_time and not (req.agent_id and req.agent_id in AGENTS):
        alarm = await create_alarm(alarm_time)
        await audit.log_action(
            "personal-assistant", "alarm_created_via_chat", {"time": alarm_time}
        )
        return {
            "response": f"Alarm sat til kl. {alarm_time} ⏰ (gentages dagligt)",
            "agent": "personal-assistant",
            "alarm": alarm,
        }

    if req.agent_id and req.agent_id in AGENTS:
        agent_id = req.agent_id
    else:
        agent_id = detect_explicit_agent(req.message) or "personal-assistant"

    agent = get_agent(agent_id)
    # Only the personal assistant gets live web search — specialist agents
    # work off the DB/integrations they already have.
    tools = [WEB_SEARCH_TOOL] if agent_id == "personal-assistant" else None
    response = await agent.quick_think(req.message, tools=tools)

    return {"response": response, "agent": agent_id}


# === WEB SEARCH ===
@app.get("/api/search")
async def web_search(q: str):
    """Search the web via Claude (server-side web search tool) and return an answer."""
    if not q or not q.strip():
        raise HTTPException(400, "Query parameter 'q' is required")
    answer = await ai.web_search_answer(q)
    return {"query": q, "answer": answer}


# === ALARMS ===
class AlarmRequest(BaseModel):
    time: str  # "HH:MM"
    label: str = "Alarm"
    repeat: str = "daily"  # "daily" stays active after triggering; "once" deactivates itself


@app.post("/api/alarms")
async def create_alarm_endpoint(req: AlarmRequest):
    """Create a new alarm."""
    alarm = await create_alarm(req.time, req.label, req.repeat)
    await audit.log_action("system", "alarm_created", {"time": alarm["time"], "label": alarm["label"]})
    return alarm


@app.get("/api/alarms")
async def list_alarms():
    """Get all active alarms."""
    async with async_session() as db:
        result = await db.execute(
            text("""
                SELECT id, time, label, repeat, active, created_at
                FROM alarms WHERE active = true ORDER BY time
            """)
        )
        rows = result.fetchall()
    return [_row_to_alarm(r) for r in rows]


@app.delete("/api/alarms/{alarm_id}")
async def delete_alarm(alarm_id: int):
    """Delete an alarm."""
    async with async_session() as db:
        result = await db.execute(
            text("DELETE FROM alarms WHERE id = :id RETURNING id"), {"id": alarm_id}
        )
        deleted = result.fetchone()
        await db.commit()
    if not deleted:
        raise HTTPException(404, "Alarm not found")
    return {"deleted": True, "id": alarm_id}


@app.get("/api/alarms/check")
async def check_alarms():
    """Check whether any active alarm matches the current time (±1 minute)."""
    now = datetime.now(LOCAL_TZ)
    now_minutes = now.hour * 60 + now.minute

    async with async_session() as db:
        result = await db.execute(
            text("SELECT id, time, label, repeat, active, created_at FROM alarms WHERE active = true")
        )
        rows = result.fetchall()

    for row in rows:
        alarm_minutes = row[1].hour * 60 + row[1].minute
        diff = min(abs(alarm_minutes - now_minutes), 1440 - abs(alarm_minutes - now_minutes))
        if diff <= 1:
            alarm = _row_to_alarm(row)
            if row[3] == "once":
                # One-off alarms deactivate themselves once triggered.
                async with async_session() as db:
                    await db.execute(
                        text("UPDATE alarms SET active = false WHERE id = :id"), {"id": row[0]}
                    )
                    await db.commit()
            return {"triggered": True, "alarm": alarm}

    return {"triggered": False, "alarm": None}


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
