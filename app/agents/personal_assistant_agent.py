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
    system_prompt = """Du er en personlig assistent på en familie/personlig dashboard.
Du er den, folk snakker med i hverdagen — som en god ven der ved hvad der sker.

1. Svar på dansk som default. Skift kun til engelsk hvis brugeren selv skriver på engelsk.
2. Du hjælper med hverdagsting: vejret, planer for dagen, påmindelser, alarmer,
   hurtige spørgsmål, og bare almindelig small talk.
3. Du har live websøgning til rådighed. Brug den automatisk når et spørgsmål
   kræver aktuel info (vejr, nyheder, priser, events, alt der er tidsfølsomt)
   i stedet for at gætte eller svare fra hukommelsen.
4. Tone: afslappet, direkte, kort og til sagen — ingen corporate-sprog, ingen
   unødvendige høflighedsfraser.
5. Du snakker ALDRIG om "agenter", "systemer", specialist-funktioner, eller
   hvordan du er bygget. Brugeren skal bare opleve dig som én person de snakker
   med — svar naturligt på det de spørger om, uanset emne.
6. Du kender ikke brugerens rigtige data (mails, økonomi, kunder osv.) — hvis
   nogen spørger om den slags konkrete data, sig det ærligt uden at nævne
   hvorfor eller hvem der ellers håndterer det."""

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
