# AI Platform

En personlig AI-assistent-platform: en veltrænet OpenAI-drevet assistent, du kan **skrive eller tale med i realtid**, med seks specialiserede Claude-agenter i baggrunden til konkrete arbejdsopgaver. Bygget som FastAPI-backend + ét selvstændigt dashboard, klar til deploy på [Railway](https://railway.app).

## Hvad er det?

- **Primær assistent (OpenAI, GPT-4o / Realtime)** — den du taler med. Bred viden, planlægning, alarmer, vejr, small talk og rådgivning. Henter sin tone og stil fra et Notion-dokument, så den lyder som dig vil have den til.
- **Flydende talesamtale** — tryk på mikrofon-knappen i dashboardet og hav en ægte, afbrydelig samtale med assistenten via [OpenAI's Realtime API](https://platform.openai.com/docs/guides/realtime) (WebRTC, lyd-til-lyd — ikke optag-vent-svar).
- **Seks Claude-specialistagenter** — email, økonomi, marketing, salg, support og dev. Den primære assistent kalder dem selv internt når en besked kræver det; du mærker aldrig skiftet. De kan også tilgås direkte ("brug email-agent: ...").
- **Alarmer** — sæt en alarm ved at sige det i chatten ("sæt alarm til 6:00"). En Windows-klient (`alarm-client/`) kan poll'e og åbne dashboardet i kiosk-mode når alarmen rammer.
- **Morgen-mode** (`?morning=true`) — fuldskærms opvågningsskærm med ur, vejr, et bibelvers og ambient lyd.
- **Futuristisk dashboard** — mørkt, glasagtigt UI med live HUD-elementer, bygget som én selvstændig `dashboard/index.html` (ingen build-step, ingen eksterne afhængigheder).

## Arkitektur

```
app/
  main.py                  FastAPI-app: alle REST-endpoints, webhooks, alarm-logik
  config.py                Alle indstillinger/env-vars (pydantic-settings)
  database.py               Async SQLAlchemy + Postgres
  scheduler.py               Cron-agtige baggrundsjobs (agent-triggere, Notion-refresh)
  worker.py                  Task-queue worker for agent-opgaver

  agents/
    openai_assistant.py      Den primære assistent — GPT-4o, Whisper, TTS, function-calling til Claude-agenterne
    personal_assistant_agent.py, email_agent.py, finance_agent.py,
    marketing_agent.py, sales_agent.py, support_agent.py, dev_agent.py
                              De 6 Claude-specialistagenter (+ en Claude-baseret personlig assistent, tilgås eksplicit)
    base.py                   Fælles agent-grundklasse (memory, audit, AI-kald)

  services/
    ai_engine.py              Claude-wrapper (tool use, web search)
    notion_reader.py          Henter og cacher tone/stil-dokument fra Notion (auto-refresh hver 6. time)
    memory.py, audit.py, task_queue.py

dashboard/index.html         Hele frontend — chat, agent-status, indstillinger, morgen-mode
alarm-client/                Windows-baggrundsklient der trigger morgen-mode ved en alarm
init.sql                     Fuldt databaseskema
alarms_migration.sql         Alarm-tabellen (app'en opretter den også selv ved opstart)
```

## Kom i gang lokalt

```bash
cp .env.example .env   # udfyld API-nøgler
docker compose up --build
```

Dashboardet er tilgængeligt på `http://localhost:8000`.

## Deploy til Railway

1. Push denne repo til GitHub.
2. Opret et nyt Railway-projekt → **Deploy from GitHub repo**.
3. Tilføj en **PostgreSQL**-database i samme projekt (Railway sætter automatisk `DATABASE_URL`).
4. Sæt miljøvariablerne fra `.env.example` under service → **Variables** (se tabellen nedenfor for de vigtigste).
5. Railway bruger `Dockerfile` + `railway.json` automatisk og binder til Railways eget `$PORT` — intet ekstra at konfigurere.
6. Kør `init.sql` mod databasen én gang (Railway's Postgres-plugin → Query-fane, eller `psql $DATABASE_URL -f init.sql`). Alarm-tabellen oprettes automatisk af appen ved opstart.

## Nødvendige miljøvariabler

| Variabel | Bruges til |
|---|---|
| `ANTHROPIC_API_KEY` | De 6 Claude-specialistagenter + web search |
| `OPENAI_API_KEY` | Den primære assistent (chat, Whisper, TTS) **og** flydende talesamtale (Realtime API) |
| `NOTION_API_KEY`, `NOTION_PAGE_ID` | Assistentens tone/stil-dokument (valgfrit — assistenten virker uden, men uden personlig tone) |
| `DATABASE_URL` | Postgres — sættes automatisk af Railway når du tilføjer en database |

Se `.env.example` for den fulde liste (integrationer til Gmail, Slack, Stripe, GitHub, HubSpot m.fl. er alle valgfrie — kun de ovenstående er nødvendige for kerneassistenten).

`OPENAI_REALTIME_MODEL` (default `gpt-realtime`) og `OPENAI_REALTIME_VOICE` (default `marin`) kan sættes hvis du vil skifte model/stemme til talesamtalen.

## Talesamtale — hvordan det virker

Mikrofon-knappen i chatten starter en direkte WebRTC-forbindelse fra din browser til OpenAI — lyden går **ikke** via serveren, kun et kortlevet, scoped token gør (mintet server-side af `/api/voice/realtime-session`, aldrig den rigtige `OPENAI_API_KEY`). Det giver en ægte flydende samtale: du kan afbryde assistenten, tale naturligt, og se både din egen og assistentens tale som tekst i chatten mens den foregår.

Kræver en browser med mikrofon-adgang og HTTPS (Railway giver dig det automatisk).
