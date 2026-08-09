"""
Slack integration — notifications, commands, channel monitoring.
"""
import structlog
from slack_sdk.web.async_client import AsyncWebClient
from app.config import settings

log = structlog.get_logger()


class SlackClient:
    def __init__(self):
        self._client = None

    def _get_client(self):
        if self._client:
            return self._client
        if not settings.slack_bot_token:
            return None
        self._client = AsyncWebClient(token=settings.slack_bot_token)
        return self._client

    async def send_message(self, channel: str, text: str, blocks: list = None):
        """Send a message to a Slack channel."""
        client = self._get_client()
        if not client:
            log.warning("slack.not_configured")
            return
        try:
            await client.chat_postMessage(channel=channel, text=text, blocks=blocks)
        except Exception as e:
            log.error("slack.send_failed", channel=channel, error=str(e))

    async def send_notification(self, title: str, message: str, severity: str = "info",
                                 channel: str = None):
        """Send a formatted notification."""
        channel = channel or settings.slack_channel_default
        color_map = {
            "info": "#36a64f",
            "warning": "#ff9500",
            "error": "#dc3545",
            "critical": "#dc3545",
        }
        blocks = [
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": f"*{title}*\n{message}"},
            }
        ]
        attachments = [{"color": color_map.get(severity, "#36a64f"), "text": ""}]

        client = self._get_client()
        if client:
            try:
                await client.chat_postMessage(
                    channel=channel, text=title, blocks=blocks, attachments=attachments
                )
            except Exception as e:
                log.error("slack.notification_failed", error=str(e))

    async def send_approval_request(self, agent: str, action: str, details: str,
                                     channel: str = None):
        """Send an approval request with interactive buttons."""
        channel = channel or settings.slack_channel_default
        blocks = [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*Approval needed* — `{agent}`\n\n*Action:* {action}\n*Details:* {details}",
                },
            },
            {
                "type": "actions",
                "elements": [
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "Approve"},
                        "style": "primary",
                        "action_id": f"approve_{agent}_{action}",
                    },
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "Deny"},
                        "style": "danger",
                        "action_id": f"deny_{agent}_{action}",
                    },
                ],
            },
        ]

        client = self._get_client()
        if client:
            try:
                await client.chat_postMessage(channel=channel, text=f"Approval: {action}", blocks=blocks)
            except Exception as e:
                log.error("slack.approval_failed", error=str(e))


slack = SlackClient()
