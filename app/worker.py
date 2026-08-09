"""
Background worker — continuously processes agent task queues.
Each agent gets its own processing loop.
"""
import asyncio
import structlog
from app.agents import AGENTS, get_agent
from app.services.task_queue import queue
from app.config import settings
from sqlalchemy import text
from app.database import async_session

structlog.configure(
    processors=[
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.dev.ConsoleRenderer(),
    ],
)
log = structlog.get_logger()


async def agent_loop(agent_id: str):
    """Processing loop for a single agent."""
    agent = get_agent(agent_id)
    log.info(f"worker.started", agent=agent_id)

    while True:
        try:
            task = await queue.dequeue(agent_id)
            if task:
                # Update agent status
                async with async_session() as db:
                    await db.execute(
                        text("UPDATE agents SET status = 'processing', last_run = NOW() WHERE id = :id"),
                        {"id": agent_id},
                    )
                    await db.commit()

                # Process the task
                await agent.process(task)

                # Update stats
                async with async_session() as db:
                    await db.execute(
                        text("""
                            UPDATE agents SET status = 'idle',
                                stats = jsonb_set(
                                    stats,
                                    '{tasks_completed}',
                                    (COALESCE((stats->>'tasks_completed')::int, 0) + 1)::text::jsonb
                                )
                            WHERE id = :id
                        """),
                        {"id": agent_id},
                    )
                    await db.commit()
            else:
                # No tasks — wait before polling again
                await asyncio.sleep(2)

        except Exception as e:
            log.error(f"worker.error", agent=agent_id, error=str(e))
            # Update error count
            async with async_session() as db:
                await db.execute(
                    text("""
                        UPDATE agents SET status = 'error',
                            stats = jsonb_set(
                                stats,
                                '{errors}',
                                (COALESCE((stats->>'errors')::int, 0) + 1)::text::jsonb
                            )
                        WHERE id = :id
                    """),
                    {"id": agent_id},
                )
                await db.commit()
            await asyncio.sleep(5)


async def main():
    """Start all agent worker loops."""
    log.info("worker.starting", agents=list(AGENTS.keys()))

    # Give the database time to initialize
    await asyncio.sleep(3)

    tasks = [agent_loop(agent_id) for agent_id in AGENTS]
    await asyncio.gather(*tasks)


if __name__ == "__main__":
    asyncio.run(main())
