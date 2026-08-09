"""
Stripe integration — transactions, invoices, subscriptions.
"""
import structlog
from datetime import datetime, timedelta
import stripe as stripe_lib
from app.config import settings

log = structlog.get_logger()


class StripeClient:
    def __init__(self):
        if settings.stripe_api_key:
            stripe_lib.api_key = settings.stripe_api_key

    async def get_recent_transactions(self, days: int = 1) -> list[dict]:
        """Get recent charges and payments."""
        if not settings.stripe_api_key:
            return []
        try:
            since = int((datetime.utcnow() - timedelta(days=days)).timestamp())
            charges = stripe_lib.Charge.list(created={"gte": since}, limit=100)
            return [
                {
                    "id": c.id,
                    "amount": c.amount,
                    "currency": c.currency,
                    "description": c.description or "",
                    "customer_email": c.receipt_email or "",
                    "customer_name": c.billing_details.get("name", "") if c.billing_details else "",
                    "status": c.status,
                    "created": c.created,
                    "type": "charge",
                    "metadata": dict(c.metadata) if c.metadata else {},
                }
                for c in charges.auto_paging_iter()
            ]
        except Exception as e:
            log.error("stripe.fetch_failed", error=str(e))
            return []

    async def get_mrr(self) -> float:
        """Calculate monthly recurring revenue from active subscriptions."""
        if not settings.stripe_api_key:
            return 0.0
        try:
            subs = stripe_lib.Subscription.list(status="active", limit=100)
            total = sum(
                s.plan.amount * s.quantity
                for s in subs.auto_paging_iter()
                if s.plan
            )
            return total / 100  # cents to dollars/DKK
        except Exception as e:
            log.error("stripe.mrr_failed", error=str(e))
            return 0.0

    async def get_revenue_summary(self, days: int = 30) -> dict:
        """Revenue summary for a period."""
        if not settings.stripe_api_key:
            return {"total": 0, "count": 0}
        try:
            since = int((datetime.utcnow() - timedelta(days=days)).timestamp())
            charges = stripe_lib.Charge.list(created={"gte": since}, status="succeeded", limit=100)
            items = list(charges.auto_paging_iter())
            total = sum(c.amount for c in items)
            return {
                "total": total / 100,
                "count": len(items),
                "currency": items[0].currency.upper() if items else "DKK",
            }
        except Exception as e:
            log.error("stripe.summary_failed", error=str(e))
            return {"total": 0, "count": 0}


stripe_client = StripeClient()
