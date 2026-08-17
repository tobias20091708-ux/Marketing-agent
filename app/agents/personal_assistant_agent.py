"""
Personal Assistant Agent — the default, friendly catch-all agent.
"""
import structlog
from app.agents.base import BaseAgent
from app.services.ai_engine import ai

log = structlog.get_logger()


class PersonalAssistantAgent(BaseAgent):
    agent_id = "personal-assistant"
    name = "Personal Assistant"
    description = "General-purpose assistant for everyday questions, reminders, and casual chat"
    system_prompt = """You are a warm, direct personal assistant running on a
family/personal dashboard. Anyone who opens the dashboard can talk to you, so:

1. Answer general questions clearly and concisely.
2. Keep replies short and conversational by default.
3. If a question is clearly about email, finance, marketing, sales, support,
   or dev, note that the specialist agent can take it but still try to help.
4. Never make up facts about the user's actual data — only specialist agents
   have DB access for that.
5. Tone: casual, direct, no corporate fluff. Danish or English, match the user."""

    async def handle_task(self, task: dict) -> dict:
        task_type = task["type"]
        if task_type == "chat":
            reply = await self.quick_think(task["payload"].get("message", ""))
            return {"response": reply}
        elif task_type == "remember_note":
            p = task["payload"]
            key = p.get("key") or p["content"][:40]
            await self.remember(key, p["content"], p.get("metadata"))
            return {"stored": True, "key": key}
        elif task_type == "recall_notes":
            p = task["payload"]
            results = await self.recall(p.get("query", ""), limit=p.get("limit", 5))
            return {"results": results}
        else:
            return {"error": f"Unknown task type: {task_type}"}

    async def run_scheduled(self, schedule_name: str):
        pass
