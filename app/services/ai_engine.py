"""
Central AI engine — wraps Claude/OpenAI with retry, token tracking, and tool use.
All agents call this instead of the API directly.
"""
import json
import structlog
from typing import Optional
from anthropic import AsyncAnthropic
from tenacity import retry, stop_after_attempt, wait_exponential
from app.config import settings

log = structlog.get_logger()

client = AsyncAnthropic(api_key=settings.anthropic_api_key)

# Server-side web search tool — runs on Anthropic's infrastructure, no client
# execution loop needed. Using the basic (non-dynamic-filtering) variant since
# this project targets older Claude models (see settings.default_model/fast_model).
WEB_SEARCH_TOOL = {"type": "web_search_20250305", "name": "web_search"}


class AIEngine:
    """Unified AI inference with tool use, retry, and cost tracking."""

    def __init__(self):
        self.total_input_tokens = 0
        self.total_output_tokens = 0

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=30))
    async def think(
        self,
        system: str,
        messages: list[dict],
        model: Optional[str] = None,
        tools: Optional[list[dict]] = None,
        max_tokens: int = 4096,
        temperature: float = 0.3,
    ) -> dict:
        """
        Send a message to Claude and get a response.
        Returns: {"text": str, "tool_calls": list, "usage": dict}
        """
        model = model or settings.default_model
        kwargs = {
            "model": model,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "system": system,
            "messages": messages,
        }
        if tools:
            kwargs["tools"] = tools

        response = await client.messages.create(**kwargs)

        self.total_input_tokens += response.usage.input_tokens
        self.total_output_tokens += response.usage.output_tokens

        text_parts = []
        tool_calls = []
        for block in response.content:
            if block.type == "text":
                text_parts.append(block.text)
            elif block.type == "tool_use":
                tool_calls.append({
                    "id": block.id,
                    "name": block.name,
                    "input": block.input,
                })

        return {
            "text": "\n".join(text_parts),
            "tool_calls": tool_calls,
            "stop_reason": response.stop_reason,
            "usage": {
                "input_tokens": response.usage.input_tokens,
                "output_tokens": response.usage.output_tokens,
            },
        }

    async def quick(
        self,
        prompt: str,
        context: str = "",
        model: Optional[str] = None,
        tools: Optional[list[dict]] = None,
    ) -> str:
        """Fast single-turn inference for classifications, summaries, etc.

        Pass `tools` (e.g. WEB_SEARCH_TOOL) to give the model live capabilities.
        Server-side tools like web search still resolve in a single API call.
        """
        model = model or settings.fast_model
        result = await self.think(
            system=context or "You are a helpful AI assistant. Be concise.",
            messages=[{"role": "user", "content": prompt}],
            model=model,
            max_tokens=1536 if tools else 1024,
            tools=tools,
        )
        return result["text"]

    async def web_search_answer(self, query: str) -> str:
        """Answer a query using Claude with live web search access."""
        result = await self.think(
            system=(
                "You are a helpful research assistant with access to live web search. "
                "Search the web to answer accurately with up-to-date information, and "
                "briefly mention your sources. Match the user's language (Danish or English)."
            ),
            messages=[{"role": "user", "content": query}],
            model=settings.default_model,
            tools=[WEB_SEARCH_TOOL],
            max_tokens=2048,
        )
        return result["text"]

    async def classify(self, text: str, categories: list[str], context: str = "") -> str:
        """Classify text into one of the given categories."""
        cats = ", ".join(categories)
        prompt = f"""Classify the following text into exactly one of these categories: {cats}

Text: {text}

Respond with ONLY the category name, nothing else."""
        result = await self.quick(prompt, context)
        # Find best match
        result_lower = result.strip().lower()
        for cat in categories:
            if cat.lower() in result_lower:
                return cat
        return categories[0]

    async def extract_json(self, prompt: str, context: str = "") -> dict:
        """Get structured JSON output from the AI."""
        full_prompt = f"""{prompt}

Respond with valid JSON only, no markdown formatting."""
        result = await self.quick(full_prompt, context)
        # Clean potential markdown
        text = result.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1] if "\n" in text else text[3:]
            text = text.rsplit("```", 1)[0]
        return json.loads(text)

    async def generate_embedding(self, text: str) -> list[float]:
        """Generate embedding vector for RAG. Uses OpenAI embeddings."""
        import openai
        oai = openai.AsyncOpenAI(api_key=settings.openai_api_key)
        response = await oai.embeddings.create(
            model="text-embedding-3-small",
            input=text[:8000],
        )
        return response.data[0].embedding

    def get_stats(self) -> dict:
        return {
            "total_input_tokens": self.total_input_tokens,
            "total_output_tokens": self.total_output_tokens,
            "estimated_cost_usd": round(
                (self.total_input_tokens * 0.003 + self.total_output_tokens * 0.015) / 1000, 4
            ),
        }


# Singleton
ai = AIEngine()
