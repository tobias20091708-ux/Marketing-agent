"""
Finance Agent — bookkeeping, reconciliation, cashflow forecasting, reports.
"""
import json
import structlog
from datetime import datetime, timedelta
from app.agents.base import BaseAgent
from app.services.ai_engine import ai
from app.integrations.stripe_integration import stripe_client
from sqlalchemy import text
from app.database import async_session

log = structlog.get_logger()


class FinanceAgent(BaseAgent):
    agent_id = "finance-agent"
    name = "Finance Agent"
    description = "Automated bookkeeping, reconciliation, and financial reporting"
    system_prompt = """You are an expert financial controller and accountant. Your responsibilities:
1. Categorize and reconcile transactions
2. Generate financial reports (P&L, balance sheet, cash flow)
3. Forecast cash flow based on historical patterns
4. Flag anomalous transactions
5. Prepare month-end close documentation
6. Track accounts receivable and payable

Always use double-entry accounting principles. Flag any transaction over the
materiality threshold for review. Currency is DKK unless stated otherwise.
Follow Danish accounting standards (årsregnskabsloven) where applicable."""

    async def handle_task(self, task: dict) -> dict:
        task_type = task["type"]

        if task_type == "sync_transactions":
            return await self._sync_transactions()
        elif task_type == "categorize_transaction":
            return await self._categorize_transaction(task["payload"])
        elif task_type == "reconcile":
            return await self._reconcile(task["payload"])
        elif task_type == "generate_report":
            return await self._generate_report(task["payload"])
        elif task_type == "cashflow_forecast":
            return await self._cashflow_forecast(task["payload"])
        elif task_type == "month_end_close":
            return await self._month_end_close()
        elif task_type == "anomaly_check":
            return await self._anomaly_check()
        else:
            return {"error": f"Unknown task type: {task_type}"}

    async def run_scheduled(self, schedule_name: str):
        if schedule_name == "daily_sync":
            await self._sync_transactions()
            await self._anomaly_check()
        elif schedule_name == "reconciliation":
            await self._reconcile({"period": "last_day"})
        elif schedule_name == "month_end":
            await self._month_end_close()

    async def _sync_transactions(self) -> dict:
        """Pull transactions from Stripe and other sources."""
        transactions = await stripe_client.get_recent_transactions(days=1)
        stored = 0

        async with async_session() as db:
            for txn in transactions:
                # Check if already exists
                existing = await db.execute(
                    text("SELECT id FROM transactions WHERE external_id = :eid AND source = 'stripe'"),
                    {"eid": txn["id"]},
                )
                if existing.fetchone():
                    continue

                # AI categorization
                category = await self._ai_categorize(txn)

                await db.execute(
                    text("""
                        INSERT INTO transactions (source, external_id, type, amount, currency,
                            description, category, counterparty, transaction_date, metadata)
                        VALUES ('stripe', :eid, :type, :amount, :currency, :desc, :cat,
                            :counterparty, :date, :meta)
                    """),
                    {
                        "eid": txn["id"],
                        "type": txn.get("type", "payment"),
                        "amount": txn["amount"] / 100,  # Stripe uses cents
                        "currency": txn.get("currency", "DKK").upper(),
                        "desc": txn.get("description", ""),
                        "cat": category,
                        "counterparty": txn.get("customer_email", txn.get("customer_name", "")),
                        "date": datetime.fromtimestamp(txn["created"]).date().isoformat(),
                        "meta": json.dumps(txn),
                    },
                )
                stored += 1
            await db.commit()

        return {"synced": stored, "source": "stripe"}

    async def _ai_categorize(self, txn: dict) -> str:
        """Use AI to categorize a transaction."""
        categories = [
            "revenue", "cost_of_goods", "salary", "software", "marketing",
            "office", "travel", "professional_services", "tax", "other",
        ]
        desc = txn.get("description", "") or txn.get("customer_name", "")
        return await ai.classify(
            f"Transaction: {desc}, Amount: {txn.get('amount', 0)/100}, Type: {txn.get('type', '')}",
            categories,
            "You are a financial categorization expert.",
        )

    async def _reconcile(self, payload: dict) -> dict:
        """Reconcile transactions against expected amounts."""
        period = payload.get("period", "last_month")

        async with async_session() as db:
            if period == "last_day":
                start = (datetime.utcnow() - timedelta(days=1)).date()
                end = datetime.utcnow().date()
            else:
                today = datetime.utcnow()
                start = today.replace(day=1, month=today.month - 1 if today.month > 1 else 12).date()
                end = today.replace(day=1).date()

            result = await db.execute(
                text("""
                    SELECT category, SUM(amount) as total, COUNT(*) as count
                    FROM transactions
                    WHERE transaction_date >= :start AND transaction_date < :end
                      AND reconciled = FALSE
                    GROUP BY category
                """),
                {"start": start.isoformat(), "end": end.isoformat()},
            )
            unreconciled = [
                {"category": r[0], "total": float(r[1]), "count": r[2]}
                for r in result.fetchall()
            ]

        analysis = await self.quick_think(f"""
Analyze these unreconciled transactions for period {start} to {end}:
{json.dumps(unreconciled, indent=2)}

Identify any discrepancies, suggest reconciliation actions, and flag anomalies.
""")

        return {"period": f"{start} to {end}", "unreconciled": unreconciled, "analysis": analysis}

    async def _generate_report(self, payload: dict) -> dict:
        """Generate a financial report."""
        report_type = payload.get("type", "pnl")

        async with async_session() as db:
            result = await db.execute(
                text("""
                    SELECT category, SUM(amount) as total, COUNT(*) as count
                    FROM transactions
                    WHERE transaction_date >= :start AND transaction_date <= :end
                    GROUP BY category ORDER BY total DESC
                """),
                {
                    "start": payload.get("start", (datetime.utcnow() - timedelta(days=30)).date().isoformat()),
                    "end": payload.get("end", datetime.utcnow().date().isoformat()),
                },
            )
            data = [{"category": r[0], "total": float(r[1]), "count": r[2]} for r in result.fetchall()]

        report = await self.quick_think(f"""
Generate a {report_type} report from this data:
{json.dumps(data, indent=2)}

Format as a clear financial report with totals, subtotals, and key insights.
Include revenue, expenses, and net result.
""")

        # Store report
        async with async_session() as db:
            await db.execute(
                text("""
                    INSERT INTO financial_reports (type, period_start, period_end, data, generated_by)
                    VALUES (:type, :start, :end, :data, :agent)
                """),
                {
                    "type": report_type,
                    "start": payload.get("start", (datetime.utcnow() - timedelta(days=30)).date().isoformat()),
                    "end": payload.get("end", datetime.utcnow().date().isoformat()),
                    "data": json.dumps({"raw": data, "report": report}),
                    "agent": self.agent_id,
                },
            )
            await db.commit()

        return {"report": report, "data": data}

    async def _cashflow_forecast(self, payload: dict) -> dict:
        """Forecast cash flow based on historical data."""
        async with async_session() as db:
            result = await db.execute(
                text("""
                    SELECT DATE_TRUNC('week', transaction_date) as week,
                           SUM(CASE WHEN amount > 0 THEN amount ELSE 0 END) as inflow,
                           SUM(CASE WHEN amount < 0 THEN ABS(amount) ELSE 0 END) as outflow
                    FROM transactions
                    WHERE transaction_date >= NOW() - INTERVAL '90 days'
                    GROUP BY week ORDER BY week
                """),
            )
            history = [
                {"week": r[0].isoformat(), "inflow": float(r[1]), "outflow": float(r[2])}
                for r in result.fetchall()
            ]

        forecast = await ai.extract_json(f"""
Based on this 90-day cash flow history, forecast the next 4 weeks:
{json.dumps(history, indent=2)}

Return JSON:
{{
    "forecast": [
        {{"week": "YYYY-MM-DD", "predicted_inflow": X, "predicted_outflow": X, "net": X}},
        ...
    ],
    "trend": "improving|stable|declining",
    "risk_level": "low|medium|high",
    "commentary": "brief analysis"
}}
""", self.system_prompt)

        return forecast

    async def _month_end_close(self) -> dict:
        """Run month-end close procedures."""
        steps = []

        # Step 1: Sync all transactions
        sync_result = await self._sync_transactions()
        steps.append({"step": "sync_transactions", "result": sync_result})

        # Step 2: Reconcile
        recon_result = await self._reconcile({"period": "last_month"})
        steps.append({"step": "reconciliation", "result": recon_result})

        # Step 3: Generate P&L
        pnl = await self._generate_report({"type": "pnl"})
        steps.append({"step": "pnl_report", "result": "generated"})

        # Step 4: AI review
        review = await self.quick_think(f"""
Review this month-end close:
- Transactions synced: {sync_result.get('synced', 0)}
- Unreconciled items: {len(recon_result.get('unreconciled', []))}

Flag any issues that need human review before closing the period.
""")
        steps.append({"step": "ai_review", "result": review})

        await self.log("month_end_close", {"steps": len(steps)})
        return {"steps": steps, "status": "completed"}

    async def _anomaly_check(self) -> dict:
        """Check for anomalous transactions."""
        async with async_session() as db:
            result = await db.execute(
                text("""
                    SELECT id, amount, description, category, counterparty, transaction_date
                    FROM transactions
                    WHERE transaction_date >= NOW() - INTERVAL '7 days'
                    ORDER BY ABS(amount) DESC LIMIT 20
                """),
            )
            recent = [
                {
                    "id": r[0], "amount": float(r[1]), "description": r[2],
                    "category": r[3], "counterparty": r[4], "date": r[5].isoformat(),
                }
                for r in result.fetchall()
            ]

        if not recent:
            return {"anomalies": []}

        anomalies = await ai.extract_json(f"""
Review these recent transactions for anomalies (unusual amounts, duplicate charges,
unexpected vendors, potential fraud):
{json.dumps(recent, indent=2)}

Return JSON:
{{
    "anomalies": [
        {{"transaction_id": X, "reason": "description", "severity": "low|medium|high"}}
    ],
    "summary": "brief overall assessment"
}}
""", self.system_prompt)

        if anomalies.get("anomalies"):
            await self.log("anomalies_detected", anomalies, needs_approval=True)

        return anomalies
