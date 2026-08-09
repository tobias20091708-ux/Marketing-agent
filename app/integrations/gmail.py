"""
Gmail integration — read, send, and manage emails via Gmail API.
"""
import json
import base64
import structlog
from typing import Optional
from email.mime.text import MIMEText
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from sqlalchemy import text
from app.config import settings
from app.database import async_session

log = structlog.get_logger()


class GmailClient:
    def __init__(self):
        self._service = None

    def _get_service(self):
        if self._service:
            return self._service
        if not settings.gmail_client_id:
            log.warning("gmail.not_configured")
            return None
        creds = Credentials(
            token=None,
            refresh_token=settings.gmail_refresh_token,
            client_id=settings.gmail_client_id,
            client_secret=settings.gmail_client_secret,
            token_uri="https://oauth2.googleapis.com/token",
        )
        self._service = build("gmail", "v1", credentials=creds)
        return self._service

    async def get_unread(self, max_results: int = 20) -> list[dict]:
        """Fetch unread emails from inbox."""
        service = self._get_service()
        if not service:
            return []

        try:
            results = service.users().messages().list(
                userId="me",
                q="is:unread in:inbox",
                maxResults=max_results,
            ).execute()

            messages = results.get("messages", [])
            emails = []

            for msg in messages:
                detail = service.users().messages().get(
                    userId="me", id=msg["id"], format="full"
                ).execute()

                headers = {h["name"]: h["value"] for h in detail["payload"].get("headers", [])}
                body = self._extract_body(detail["payload"])

                emails.append({
                    "id": msg["id"],
                    "thread_id": detail.get("threadId"),
                    "from": headers.get("From", ""),
                    "to": headers.get("To", ""),
                    "subject": headers.get("Subject", ""),
                    "date": headers.get("Date", ""),
                    "body": body[:5000],
                    "labels": detail.get("labelIds", []),
                })

            return emails
        except Exception as e:
            log.error("gmail.fetch_failed", error=str(e))
            return []

    def _extract_body(self, payload: dict) -> str:
        """Extract email body from Gmail payload."""
        if payload.get("body", {}).get("data"):
            return base64.urlsafe_b64decode(payload["body"]["data"]).decode("utf-8", errors="replace")

        parts = payload.get("parts", [])
        for part in parts:
            if part.get("mimeType") == "text/plain" and part.get("body", {}).get("data"):
                return base64.urlsafe_b64decode(part["body"]["data"]).decode("utf-8", errors="replace")
            if part.get("parts"):
                result = self._extract_body(part)
                if result:
                    return result
        return ""

    async def send_reply(self, to: str, subject: str, body: str, thread_id: str = None) -> str:
        """Send an email reply."""
        service = self._get_service()
        if not service:
            return "gmail_not_configured"

        message = MIMEText(body)
        message["to"] = to
        message["subject"] = subject if subject.startswith("Re:") else f"Re: {subject}"

        raw = base64.urlsafe_b64encode(message.as_bytes()).decode()
        send_body = {"raw": raw}
        if thread_id:
            send_body["threadId"] = thread_id

        result = service.users().messages().send(userId="me", body=send_body).execute()
        return result.get("id", "")

    async def send(self, to: str, subject: str, body: str) -> str:
        """Send a new email."""
        service = self._get_service()
        if not service:
            return "gmail_not_configured"

        message = MIMEText(body)
        message["to"] = to
        message["subject"] = subject

        raw = base64.urlsafe_b64encode(message.as_bytes()).decode()
        result = service.users().messages().send(userId="me", body={"raw": raw}).execute()
        return result.get("id", "")

    async def store_processed_email(self, email_data: dict, category: str, priority: str,
                                     sentiment: float, requires_action: bool,
                                     suggested_reply: str = None, reply_confidence: float = 0):
        """Store a processed email in the database."""
        async with async_session() as db:
            await db.execute(
                text("""
                    INSERT INTO emails (message_id, thread_id, from_address, subject,
                        body_preview, full_body, labels, category, priority, sentiment,
                        requires_action, suggested_reply, reply_confidence, status,
                        received_at, processed_at)
                    VALUES (:mid, :tid, :from_addr, :subject, :preview, :body,
                        :labels, :cat, :priority, :sentiment, :action, :reply,
                        :confidence, 'processed', NOW(), NOW())
                    ON CONFLICT (message_id) DO NOTHING
                """),
                {
                    "mid": email_data.get("id"),
                    "tid": email_data.get("thread_id"),
                    "from_addr": email_data.get("from", ""),
                    "subject": email_data.get("subject", ""),
                    "preview": email_data.get("body", "")[:500],
                    "body": email_data.get("body", ""),
                    "labels": email_data.get("labels", []),
                    "cat": category,
                    "priority": priority,
                    "sentiment": sentiment,
                    "action": requires_action,
                    "reply": suggested_reply,
                    "confidence": reply_confidence,
                },
            )
            await db.commit()


gmail = GmailClient()
