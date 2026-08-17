from pydantic_settings import BaseSettings
from typing import Optional


class Settings(BaseSettings):
    # Core
    platform_name: str = "ai-platform"
    domain: str = "localhost"
    admin_email: str = "admin@example.com"
    admin_password: str = "changeme"
    secret_key: str = "dev-secret-change-in-production"
    debug: bool = False

    # AI
    anthropic_api_key: str = ""
    openai_api_key: str = ""
    default_model: str = "claude-sonnet-4-20250514"
    fast_model: str = "claude-haiku-4-5-20251001"

    # Database
    database_url: str = "postgresql+asyncpg://aiplatform:password@postgres:5432/aiplatform"
    redis_url: str = "redis://redis:6379/0"

    # Email
    gmail_client_id: str = ""
    gmail_client_secret: str = ""
    gmail_refresh_token: str = ""
    smtp_host: str = "smtp.gmail.com"
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""

    # Calendar
    google_calendar_credentials: str = ""

    # Slack
    slack_bot_token: str = ""
    slack_signing_secret: str = ""
    slack_channel_default: str = "general"

    # Stripe
    stripe_api_key: str = ""
    stripe_webhook_secret: str = ""

    # GitHub
    github_token: str = ""
    github_org: str = ""
    github_webhook_secret: str = ""

    # CRM
    hubspot_api_key: str = ""
    hubspot_portal_id: str = ""

    # Ads
    meta_ads_access_token: str = ""
    meta_ads_account_id: str = ""
    google_ads_developer_token: str = ""
    google_ads_client_id: str = ""
    google_ads_client_secret: str = ""
    google_ads_refresh_token: str = ""
    google_ads_customer_id: str = ""

    # Twilio
    twilio_account_sid: str = ""
    twilio_auth_token: str = ""
    twilio_phone_number: str = ""

    # S3
    s3_endpoint: str = ""
    s3_access_key: str = ""
    s3_secret_key: str = ""
    s3_bucket: str = "ai-platform-files"
    s3_region: str = "eu-west-1"

    # Agent behavior
    email_check_interval: int = 60
    email_auto_reply: bool = False
    email_auto_reply_confidence: float = 0.9
    support_auto_resolve_l1: bool = True
    support_escalation_channel: str = "support-escalation"

    # Notion (tone/style context for the OpenAI assistant)
    notion_api_key: str = ""
    notion_page_id: str = ""

    # Monitoring
    enable_prometheus: bool = True
    log_level: str = "INFO"

    class Config:
        env_file = ".env"
        case_sensitive = False


settings = Settings()
