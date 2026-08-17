"""
Notion reader — fetches a Notion page's plain-text content to use as
tone/style context for the OpenAI assistant (see app/agents/openai_assistant.py).

Content is cached locally (in-process). The scheduler re-fetches every 6
hours (see app/scheduler.py); `get_context()` also lazily refreshes if the
cache has gone stale, so the content is still correct even if the separate
scheduler process isn't running.
"""
import time
import structlog
from typing import Optional
from notion_client import AsyncClient
from app.config import settings

log = structlog.get_logger()

CACHE_TTL_SECONDS = 6 * 60 * 60  # 6 hours


class NotionReader:
    """Fetches and caches a single Notion page's plain-text content."""

    def __init__(self):
        self._cache: str = ""
        self._fetched_at: float = 0.0
        self._client: Optional[AsyncClient] = None

    def _get_client(self) -> Optional[AsyncClient]:
        if not settings.notion_api_key:
            return None
        if self._client is None:
            self._client = AsyncClient(auth=settings.notion_api_key)
        return self._client

    @staticmethod
    def _block_to_text(block: dict) -> str:
        block_type = block.get("type", "")
        data = block.get(block_type, {})
        rich_text = data.get("rich_text", [])
        text = "".join(rt.get("plain_text", "") for rt in rich_text).strip()
        if not text:
            return ""
        if block_type in ("heading_1", "heading_2", "heading_3"):
            return f"## {text}"
        if block_type in ("bulleted_list_item", "numbered_list_item"):
            return f"- {text}"
        return text

    async def _fetch_page_text(self, client: AsyncClient, page_id: str) -> str:
        """Read a Notion page's top-level blocks and join their text content."""
        lines = []
        cursor = None
        while True:
            resp = await client.blocks.children.list(block_id=page_id, start_cursor=cursor)
            for block in resp.get("results", []):
                line = self._block_to_text(block)
                if line:
                    lines.append(line)
            if not resp.get("has_more"):
                break
            cursor = resp.get("next_cursor")
        return "\n".join(lines).strip()

    async def refresh(self) -> str:
        """Fetch the Notion page fresh and update the cache. Returns the (possibly unchanged) content."""
        client = self._get_client()
        if not client or not settings.notion_page_id:
            log.warning("notion.not_configured")
            return self._cache

        try:
            content = await self._fetch_page_text(client, settings.notion_page_id)
            self._cache = content
            self._fetched_at = time.time()
            log.info("notion.refreshed", chars=len(content))
        except Exception as e:
            log.error("notion.refresh_failed", error=str(e))
        return self._cache

    async def get_context(self) -> str:
        """Return the cached tone/style content, refreshing first if stale or empty."""
        is_stale = (time.time() - self._fetched_at) > CACHE_TTL_SECONDS
        if not self._cache or is_stale:
            await self.refresh()
        return self._cache


# Singleton
notion_reader = NotionReader()
