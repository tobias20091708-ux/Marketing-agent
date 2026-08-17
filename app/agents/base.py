"""
Base agent class — all specialized agents inherit from this.
Provides AI thinking, memory access, task handling, and inter-agent communication.
"""
import structlog
from abc import ABC, abstractmethod
from typing import Optional
from app.services.ai_engine import ai
from app.services.memory import memory
from app.services.task_queue import queue
from app.services.audit import audit

log = structlog.get_logger()


class BaseAgent(ABC):
    """Base class for all AI agents."""

    agent_id: str = "base"
    name: str = "Base Agent"
    description: str = ""
    system_prompt: str = "You are a helpful AI assistant."

    async def think(self, prompt: str, context: str = None, tools: list = None) -> dict:
        """Use AI to reason about a task."""
        system = self.system_prompt
        if context:
            system += f"\n\nAdditional context:\n{context}"

        # Pull relevant memories
        memories = await memory.search(self.agent_id, prompt, limit=3)
        if memories:
            mem_text = "\n".join(
                f"- [{m['key']}]: {m['content'][:200]}" for m in memories
            )
            system += f"\n\nRelevant memories:\n{mem_text}"

        return await ai.think(
            system=system,
            messages=[{"role": "user", "content": prompt}],
            tools=tools,
        )

    async def quick_think(self, prompt: str, context: str = "", tools: list = None) -> str:
        """Fast single-turn reasoning. Pass `tools` to grant capabilities like web search."""
        return await ai.quick(prompt, context or self.system_prompt, tools=tools)

    async def remember(self, key: str, content: str, metadata: dict = None):
        """Store something in long-term memory."""
        await memory.store(self.agent_id, key, content, metadata)

    async def recall(self, query: str, limit: int = 5) -> list[dict]:
        """Search long-term memory."""
        return await memory.search(self.agent_id, query, limit)

    async def send_to(self, target_agent: str, task_type: str, payload: dict):
        """Send a task to another agent."""
        await audit.log_action(
            self.agent_id,
            f"send_to:{target_agent}",
            {"task_type": task_type, "payload_keys": list(payload.keys())},
        )
        return await queue.send_to_agent(self.agent_id, target_agent, task_type, payload)

    async def log(self, action: str, details: dict = None, needs_approval: bool = False):
        """Log an action to the audit trail."""
        await audit.log_action(self.agent_id, action, details, user_approved=not needs_approval)

    @abstractmethod
    async def handle_task(self, task: dict) -> dict:
        """Process a task. Must be implemented by each agent."""
        pass

    @abstractmethod
    async def run_scheduled(self, schedule_name: str):
        """Run a scheduled job. Implemented by each agent."""
        pass

    async def process(self, task: dict) -> dict:
        """Main processing loop — wraps handle_task with error handling."""
        log.info(f"{self.agent_id}.processing", task_type=task["type"], task_id=task["id"])
        try:
            await self.log("task_start", {"type": task["type"], "id": task["id"]})
            result = await self.handle_task(task)
            await queue.complete(task["id"], result)
            await self.log("task_complete", {"type": task["type"], "id": task["id"]})
            return result
        except Exception as e:
            log.error(f"{self.agent_id}.error", error=str(e), task_id=task["id"])
            await queue.fail(task["id"], str(e))
            await self.log("task_error", {"type": task["type"], "error": str(e)})
            return {"error": str(e)}
