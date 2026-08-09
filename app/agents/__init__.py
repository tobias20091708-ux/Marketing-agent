from app.agents.base import BaseAgent
from app.agents.email_agent import EmailAgent
from app.agents.finance_agent import FinanceAgent
from app.agents.marketing_agent import MarketingAgent
from app.agents.sales_agent import SalesAgent
from app.agents.support_agent import SupportAgent
from app.agents.dev_agent import DevAgent

AGENTS = {
    "email-agent": EmailAgent,
    "finance-agent": FinanceAgent,
    "marketing-agent": MarketingAgent,
    "sales-agent": SalesAgent,
    "support-agent": SupportAgent,
    "dev-agent": DevAgent,
}


def get_agent(agent_id: str) -> BaseAgent:
    cls = AGENTS.get(agent_id)
    if not cls:
        raise ValueError(f"Unknown agent: {agent_id}")
    return cls()
