# Marketing-agent

Backend med seks specialiserede Claude-agenter (email, økonomi, marketing, salg, support, dev) plus en Claude-baseret personlig assistent, alarmer og et selvstændigt dashboard. FastAPI + Postgres, klar til deploy på [Railway](https://railway.app).

Denne repo er tænkt som **backend/API-laget** i et to-repo-setup: en søster-service, [`ai-voice-assistant`](https://github.com/tobias20091708-ux/ai-voice-assistant), kører den OpenAI-drevne stemme-/chat-assistent og kalder denne repos `/api/chat`-endpoint, når en besked skal håndteres af en af de 6 specialistagenter herunder. De to services deployes som separate Railway-services i samme Railway-projekt.

## Hvad er det?

- **Personlig assistent (Claude)** — standard-assistenten i dashboardet. Svarer direkte på hverdagsspørgsmål og søger selv på nettet ved behov.
- **Seks Claude-specialistagenter** — email, økonomi, marketing, salg, support og dev. Tilgås direkte ("brug email-agent: ...") eller via `/api/chat` med et `agent_id`.
- **Alarmer** — sæt en alarm ved at sige det i chatten ("sæt alarm til 6:00"). En Windows-klient (`alarm-client/`) kan poll'e `/api/alarms/check` og åbne dashboardet i kiosk-mode når alarmen rammer.
- **Morgen-mode** (`?morning=true`) — fuldskærms opvågningsskærm med ur, vejr, et bibelvers og ambient lyd.
- **Futuristisk dashboard** — mørkt, glasagtigt UI med live HUD-elementer, bygget som én selvstændig `dashboard/index.html` (ingen build-step, ingen eksterne afhængigheder).

## Arkitektur

```
app/
  main.py                  FastAPI-app: alle REST-endpoints, webhooks, alarm-logik
  config.py                Alle indstillinger/env-vars (pydantic-settings)
  database.py               Async SQLAlchemy + Postgres
  scheduler.py               Cron-agtige baggrundsjobs (agent-triggere)
  worker.py                  Task-queue worker for agent-opgaver

  agents/
    personal_assistant_agent.py, email_agent.py, finance_agent.py,
    marketing_agent.py, sales_agent.py, support_agent.py, dev_agent.py
                              Personlig assistent + de 6 Claude-specialistagenter
    base.py                   Fælles agent-grundklasse (memory, audit, AI-kald)

  services/
    ai_engine.py              Claude-wrapper (tool use, web search)
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
7. Generér et public domain til servicen under **Settings → Networking** — den URL er det `ai-voice-assistant` skal pege på.

## Nødvendige miljøvariabler

| Variabel | Bruges til |
|---|---|
| `ANTHROPIC_API_KEY` | De 6 specialistagenter + personlig assistent + web search |
| `OPENAI_API_KEY` | Valgfri — kun embeddings (vector search i memory-tabellen) |
| `DATABASE_URL` | Postgres — sættes automatisk af Railway når du tilføjer en database |

Se `.env.example` for den fulde liste (integrationer til Gmail, Slack, Stripe, GitHub, HubSpot m.fl. er alle valgfrie — kun `ANTHROPIC_API_KEY` er nødvendig for kerne-funktionaliteten).

## Kaldes af `ai-voice-assistant`

Denne repos `/api/chat`-endpoint (og `/api/alarms`) er bevidst åbent for cross-origin-kald (CORS `*`), så søster-servicen kan kalde den direkte over nettet uden ekstra opsætning. Send `{"message": "...", "agent_id": "email-agent"}` for at ramme en specifik specialist direkte.
