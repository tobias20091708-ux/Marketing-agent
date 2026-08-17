"""
OpenAI Assistant — the primary assistant the user talks to via voice and chat.

Uses GPT-4o for chat/reasoning, Whisper for speech-to-text, and OpenAI TTS
(tts-1-hd, voice "nova") for text-to-speech. Broad everyday knowledge —
planning, alarms, weather, small talk, advice.

Internally, it can call the six Claude specialist agents (marketing, email,
finance, sales, support, dev) via OpenAI function calling when a message
needs one of them. The user never sees the handoff: GPT-4o receives the
specialist's answer as a tool result and composes the final reply itself.
"""
import io
import json
import structlog
from typing import Optional
from openai import AsyncOpenAI

from app.config import settings
from app.services.notion_reader import notion_reader
from app.agents import get_agent, AGENTS

log = structlog.get_logger()

client = AsyncOpenAI(api_key=settings.openai_api_key)

CHAT_MODEL = "gpt-4o"
WHISPER_MODEL = "whisper-1"
TTS_MODEL = "tts-1-hd"
TTS_VOICE = "nova"

BASE_SYSTEM_PROMPT = """Du er den primære assistent brugeren snakker med — via tale og chat.
Du har bred viden og hjælper med alt fra hverdagsspørgsmål, planlægning, alarmer,
vejr og small talk til rådgivning.

Du kan trække på specialiserede funktioner til konkrete opgaver (marketing, email,
økonomi, salg, support, kode/deploy) — brug dem stille i baggrunden når det er
relevant for det brugeren spørger om, og saml altid selv det endelige svar ud fra
det du får tilbage. Nævn aldrig for brugeren at du har "kaldt" noget, "tjekket med"
nogen, eller hvordan du er bygget — svar bare naturligt, som dig selv.

Svar kortfattet og naturligt — dine svar bliver ofte læst højt, ikke kun læst."""

# One function-calling tool per Claude specialist agent. GPT-4o decides when
# to call these; the actual work still runs on the underlying Claude agent.
SPECIALIST_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "marketing_agent",
            "description": "Marketing-spørgsmål: kampagner, annoncer, content, performance-analyse.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Spørgsmålet eller opgaven, i brugerens egne ord"}
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "email_agent",
            "description": "Email-ting: indbakke, triage, kladder til svar.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Spørgsmålet eller opgaven, i brugerens egne ord"}
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "finance_agent",
            "description": "Økonomi og bogføring: transaktioner, afstemning, rapportering.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Spørgsmålet eller opgaven, i brugerens egne ord"}
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "sales_agent",
            "description": "Salg, leads og CRM: lead-scoring, opfølgning, pipeline.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Spørgsmålet eller opgaven, i brugerens egne ord"}
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "support_agent",
            "description": "Support og tickets: triage, L1-løsning, eskalering.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Spørgsmålet eller opgaven, i brugerens egne ord"}
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "dev_agent",
            "description": "Kode, deploy og GitHub: code review, deployments, incidents.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Spørgsmålet eller opgaven, i brugerens egne ord"}
                },
                "required": ["query"],
            },
        },
    },
]

_TOOL_TO_AGENT_ID = {
    "marketing_agent": "marketing-agent",
    "email_agent": "email-agent",
    "finance_agent": "finance-agent",
    "sales_agent": "sales-agent",
    "support_agent": "support-agent",
    "dev_agent": "dev-agent",
}

MAX_TOOL_ROUNDS = 3


class OpenAIAssistant:
    """Primary voice/chat assistant. GPT-4o with Claude specialists as internal tools."""

    async def _system_prompt(self) -> str:
        tone_context = await notion_reader.get_context()
        if tone_context:
            return f"{BASE_SYSTEM_PROMPT}\n\n--- Din tone og stil (fra brugerens noter) ---\n{tone_context}"
        return BASE_SYSTEM_PROMPT

    async def _call_specialist(self, tool_name: str, query: str) -> str:
        agent_id = _TOOL_TO_AGENT_ID.get(tool_name)
        if not agent_id or agent_id not in AGENTS:
            return "Den funktion findes ikke."
        agent = get_agent(agent_id)
        try:
            return await agent.quick_think(query)
        except Exception as e:
            log.error("openai_assistant.specialist_failed", agent=agent_id, error=str(e))
            return f"Der opstod en fejl ved opslaget: {e}"

    async def chat(self, message: str, history: Optional[list[dict]] = None) -> str:
        """Send a message to GPT-4o, resolving any specialist tool calls along the way."""
        messages = [{"role": "system", "content": await self._system_prompt()}]
        messages.extend(history or [])
        messages.append({"role": "user", "content": message})

        for _ in range(MAX_TOOL_ROUNDS):
            response = await client.chat.completions.create(
                model=CHAT_MODEL,
                messages=messages,
                tools=SPECIALIST_TOOLS,
                tool_choice="auto",
                temperature=0.7,
            )
            choice = response.choices[0]
            msg = choice.message

            if choice.finish_reason == "tool_calls" and msg.tool_calls:
                messages.append({
                    "role": "assistant",
                    "content": msg.content,
                    "tool_calls": [tc.model_dump() for tc in msg.tool_calls],
                })
                for tc in msg.tool_calls:
                    try:
                        args = json.loads(tc.function.arguments or "{}")
                    except json.JSONDecodeError:
                        args = {}
                    result = await self._call_specialist(tc.function.name, args.get("query", message))
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": result,
                    })
                continue  # let GPT-4o compose the final answer from the tool results

            return msg.content or ""

        return "Jeg kunne desværre ikke samle et svar lige nu — prøv igen."

    async def transcribe(self, audio_bytes: bytes, filename: str = "audio.webm") -> str:
        """Speech-to-text via Whisper."""
        audio_file = io.BytesIO(audio_bytes)
        audio_file.name = filename
        result = await client.audio.transcriptions.create(model=WHISPER_MODEL, file=audio_file)
        return result.text

    async def speak(self, text: str) -> bytes:
        """Text-to-speech via OpenAI TTS. Returns MP3 audio bytes."""
        response = await client.audio.speech.create(model=TTS_MODEL, voice=TTS_VOICE, input=text)
        return response.read()


# Singleton
openai_assistant = OpenAIAssistant()
