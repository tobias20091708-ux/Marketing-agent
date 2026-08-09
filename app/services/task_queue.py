"""
Task queue — distributes work to agents via Redis.
Supports priorities, retries, and inter-agent messaging.
"""
import json
import structlog
from datetime import datetime
from typing import Optional, Callable
from redis.asyncio import Redis
from sqlalchemy import text
from app.config import settings
from app.database import async_session

log = structlog.get_logger()

redis = Redis.from_url(settings.redis_url, decode_responses=True)


class TaskQueue:
    """Priority task queue with persistence."""

    QUEUE_KEY = "tasks:queue"
    PROCESSING_KEY = "tasks:processing"

    async def enqueue(
        self,
        agent_id: str,
        task_type: str,
        payload: dict,
        priority: int = 5,
        parent_task_id: Optional[int] = None,
    ) -> int:
        """Add a task to the queue. Lower priority number = higher priority."""
        async with async_session() as db:
            result = await db.execute(
                text("""
                    INSERT INTO tasks (agent_id, type, status, priority, payload, parent_task_id)
                    VALUES (:agent_id, :type, 'pending', :priority, :payload, :parent_id)
                    RETURNING id
                """),
                {
                    "agent_id": agent_id,
                    "type": task_type,
                    "priority": priority,
                    "payload": json.dumps(payload),
                    "parent_id": parent_task_id,
                },
            )
            await db.commit()
            task_id = result.fetchone()[0]

        # Push to Redis sorted set (score = priority * 1e10 + timestamp for ordering)
        score = priority * 1e10 + datetime.utcnow().timestamp()
        await redis.zadd(
            f"{self.QUEUE_KEY}:{agent_id}",
            {str(task_id): score},
        )

        log.info("task.enqueued", task_id=task_id, agent=agent_id, type=task_type)
        return task_id

    async def dequeue(self, agent_id: str) -> Optional[dict]:
        """Get the highest-priority task for an agent."""
        # Pop from sorted set (lowest score = highest priority)
        result = await redis.zpopmin(f"{self.QUEUE_KEY}:{agent_id}")
        if not result:
            return None

        task_id = int(result[0][0])

        async with async_session() as db:
            row = await db.execute(
                text("""
                    UPDATE tasks SET status = 'processing', started_at = NOW()
                    WHERE id = :id RETURNING id, agent_id, type, payload, priority
                """),
                {"id": task_id},
            )
            await db.commit()
            task = row.fetchone()
            if task:
                return {
                    "id": task[0],
                    "agent_id": task[1],
                    "type": task[2],
                    "payload": json.loads(task[3]) if isinstance(task[3], str) else task[3],
                    "priority": task[4],
                }
        return None

    async def complete(self, task_id: int, result: dict = None):
        """Mark a task as completed."""
        async with async_session() as db:
            await db.execute(
                text("""
                    UPDATE tasks SET status = 'completed', result = :result, completed_at = NOW()
                    WHERE id = :id
                """),
                {"id": task_id, "result": json.dumps(result or {})},
            )
            await db.commit()
        log.info("task.completed", task_id=task_id)

    async def fail(self, task_id: int, error: str):
        """Mark a task as failed."""
        async with async_session() as db:
            await db.execute(
                text("""
                    UPDATE tasks SET status = 'failed', error = :error, completed_at = NOW()
                    WHERE id = :id
                """),
                {"id": task_id, "error": error},
            )
            await db.commit()
        log.error("task.failed", task_id=task_id, error=error)

    async def send_to_agent(self, from_agent: str, to_agent: str, task_type: str, payload: dict):
        """Inter-agent communication — one agent sends a task to another."""
        payload["_from_agent"] = from_agent
        return await self.enqueue(to_agent, task_type, payload, priority=3)

    async def get_stats(self, agent_id: str = None) -> dict:
        """Get queue statistics."""
        async with async_session() as db:
            if agent_id:
                where = "WHERE agent_id = :aid"
                params = {"aid": agent_id}
            else:
                where = ""
                params = {}

            result = await db.execute(
                text(f"""
                    SELECT status, COUNT(*) FROM tasks {where}
                    GROUP BY status
                """),
                params,
            )
            return {row[0]: row[1] for row in result.fetchall()}


queue = TaskQueue()
