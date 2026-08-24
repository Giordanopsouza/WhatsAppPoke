"""Fixed Gmail API requests and compact provider-response normalization."""

from __future__ import annotations

import base64
import html
import re
from email.message import EmailMessage
from html.parser import HTMLParser
from typing import Any, Mapping
from urllib.parse import quote

from app.integrations.composio_proxy import ProxyParameter, ProxyRequest


MAX_SEARCH_RESULTS = 10
MAX_QUERY_CHARS = 500
MAX_PAGE_TOKEN_CHARS = 512
MAX_HEADER_CHARS = 320
MAX_SNIPPET_CHARS = 280
MAX_BODY_CHARS = 4_000
MAX_DRAFT_BODY_CHARS = 20_000
MAX_SUBJECT_CHARS = 300
_WHITESPACE_RE = re.compile(r"\s+")
_EMAIL_RE = re.compile(r"^[^\s@<>]+@[^\s@<>]+\.[^\s@<>]+$")


class GmailPayloadError(ValueError):
    """A provider payload or owned-tool input violated the bounded contract."""


# Pull plain text out of HTML email bodies.
class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.ignored_depth = 0

    def handle_starttag(self, tag: str, _attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style"}:
            self.ignored_depth += 1
        elif tag in {"br", "p", "div", "li", "tr"}:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style"} and self.ignored_depth:
            self.ignored_depth -= 1
        elif tag in {"p", "div", "li", "tr"}:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self.ignored_depth:
            self.parts.append(data)


# Trim and shorten a string to a max length (for safe display/storage).
def compact(value: Any, *, limit: int) -> str:
    text = value if isinstance(value, str) else ""
    return _WHITESPACE_RE.sub(" ", text).strip()[:limit]


# Check that an email address looks valid before we use it.
def validate_email_address(value: str) -> str:
    address = value.strip()
    if len(address) > MAX_HEADER_CHARS or not _EMAIL_RE.fullmatch(address):
        raise GmailPayloadError("invalid recipient email")
    return address


# Validate to/subject/body/thread_id before creating a Gmail draft.
def validate_draft_fields(
    *, to: str, subject: str, body: str, thread_id: str | None
) -> tuple[str, str, str, str | None]:
    recipient = validate_email_address(to)
    clean_subject = subject.strip()
    if not clean_subject or len(clean_subject) > MAX_SUBJECT_CHARS or "\n" in subject or "\r" in subject:
        raise GmailPayloadError("invalid email subject")
    if not body.strip() or len(body) > MAX_DRAFT_BODY_CHARS:
        raise GmailPayloadError("invalid email body")
    clean_thread = thread_id.strip() if isinstance(thread_id, str) else None
    if clean_thread == "" or (clean_thread is not None and len(clean_thread) > 256):
        raise GmailPayloadError("invalid Gmail thread id")
    return recipient, clean_subject, body, clean_thread


# Build the Composio proxy request to search Gmail messages.
def search_request(
    *, query: str, after: str | None, before: str | None, page_token: str | None,
    max_results: int,
) -> ProxyRequest:
    clean_query = " ".join(query.split())
    if len(clean_query) > MAX_QUERY_CHARS:
        raise GmailPayloadError("Gmail query is too long")
    if not 1 <= max_results <= MAX_SEARCH_RESULTS:
        raise GmailPayloadError(f"max_results must be between 1 and {MAX_SEARCH_RESULTS}")
    terms = [clean_query] if clean_query else []
    for label, raw in (("after", after), ("before", before)):
        if raw is None:
            continue
        try:
            year, month, day = raw.split("-")
            if len(year) != 4 or len(month) != 2 or len(day) != 2:
                raise ValueError
            from datetime import date
            date(int(year), int(month), int(day))
        except (AttributeError, TypeError, ValueError):
            raise GmailPayloadError(f"{label} must be YYYY-MM-DD") from None
        terms.append(f"{label}:{year}/{month}/{day}")
    if after and before and after >= before:
        raise GmailPayloadError("after must be earlier than before")
    params = [ProxyParameter("maxResults", str(max_results), "query")]
    if terms:
        params.append(ProxyParameter("q", " ".join(terms), "query"))
    if page_token:
        if len(page_token) > MAX_PAGE_TOKEN_CHARS:
            raise GmailPayloadError("page token is too long")
        params.append(ProxyParameter("pageToken", page_token, "query"))
    return ProxyRequest(
        endpoint="/gmail/v1/users/me/messages",
        method="GET",
        parameters=tuple(params),
    )


# Build the proxy request to fetch one message (metadata or full body).
def message_request(message_id: str, *, metadata_only: bool) -> ProxyRequest:
    clean_id = message_id.strip()
    if not clean_id or len(clean_id) > 256:
        raise GmailPayloadError("invalid Gmail message id")
    params = [ProxyParameter("format", "metadata" if metadata_only else "full", "query")]
    if metadata_only:
        for header in ("From", "To", "Subject", "Date"):
            params.append(ProxyParameter("metadataHeaders", header, "query"))
    return ProxyRequest(
        endpoint=f"/gmail/v1/users/me/messages/{quote(clean_id, safe='')}",
        method="GET",
        parameters=tuple(params),
    )


# Build the proxy request to create a Gmail draft (does not send).
def draft_request(*, to: str, subject: str, body: str, thread_id: str | None) -> ProxyRequest:
    recipient, clean_subject, clean_body, clean_thread = validate_draft_fields(
        to=to, subject=subject, body=body, thread_id=thread_id
    )
    message = EmailMessage()
    message["To"] = recipient
    message["Subject"] = clean_subject
    message.set_content(clean_body)
    raw = base64.urlsafe_b64encode(message.as_bytes()).decode().rstrip("=")
    provider_message: dict[str, str] = {"raw": raw}
    if clean_thread:
        provider_message["threadId"] = clean_thread
    return ProxyRequest(
        endpoint="/gmail/v1/users/me/drafts",
        method="POST",
        body={"message": provider_message},
    )


# Build the proxy request to send a previously created draft.
def send_draft_request(draft_id: str) -> ProxyRequest:
    clean_id = draft_id.strip()
    if not clean_id or len(clean_id) > 256:
        raise GmailPayloadError("invalid Gmail draft id")
    return ProxyRequest(
        endpoint="/gmail/v1/users/me/drafts/send",
        method="POST",
        body={"id": clean_id},
    )


# Turn Gmail search API response into message ids and optional next page token.
def normalize_search_page(data: Any) -> tuple[list[tuple[str, str]], str | None]:
    if not isinstance(data, Mapping):
        return [], None
    raw_messages = data.get("messages")
    refs: list[tuple[str, str]] = []
    if isinstance(raw_messages, list):
        for item in raw_messages[:MAX_SEARCH_RESULTS]:
            if not isinstance(item, Mapping):
                continue
            message_id, thread_id = item.get("id"), item.get("threadId")
            if isinstance(message_id, str) and isinstance(thread_id, str):
                refs.append((message_id[:256], thread_id[:256]))
    token = data.get("nextPageToken")
    return refs, token[:MAX_PAGE_TOKEN_CHARS] if isinstance(token, str) else None


# Extract From/To/Subject/Date headers from a Gmail API payload.
def _headers(payload: Any) -> dict[str, str]:
    if not isinstance(payload, Mapping):
        return {}
    raw = payload.get("headers")
    result: dict[str, str] = {}
    if isinstance(raw, list):
        for item in raw:
            if not isinstance(item, Mapping):
                continue
            name, value = item.get("name"), item.get("value")
            if isinstance(name, str) and isinstance(value, str):
                result[name.casefold()] = compact(value, limit=MAX_HEADER_CHARS)
    return result


# Turn a Gmail message into compact metadata (no body text).
def normalize_metadata(data: Any) -> dict[str, str] | None:
    if not isinstance(data, Mapping) or not isinstance(data.get("id"), str):
        return None
    headers = _headers(data.get("payload"))
    return {
        "message_id": str(data["id"])[:256],
        "thread_id": str(data.get("threadId") or "")[:256],
        "from": headers.get("from", ""),
        "to": headers.get("to", ""),
        "subject": headers.get("subject", "(sem assunto)"),
        "date": headers.get("date", ""),
        "snippet": compact(data.get("snippet"), limit=MAX_SNIPPET_CHARS),
    }


# Decode Gmail's base64url-encoded message part data.
def _decode(data: Any) -> str:
    if not isinstance(data, str) or not data:
        return ""
    try:
        padded = data + "=" * (-len(data) % 4)
        return base64.urlsafe_b64decode(padded).decode("utf-8", errors="replace")
    except (ValueError, TypeError):
        return ""


# Recursively extract plain or HTML text from a MIME message part.
def _part_text(part: Any) -> tuple[str, bool]:
    if not isinstance(part, Mapping):
        return "", False
    mime = part.get("mimeType")
    body = part.get("body")
    encoded = body.get("data") if isinstance(body, Mapping) else None
    if mime == "text/plain":
        return _decode(encoded), True
    if mime == "text/html":
        return _decode(encoded), False
    raw_parts = part.get("parts")
    plain: list[str] = []
    html_parts: list[str] = []
    if isinstance(raw_parts, list):
        for child in raw_parts:
            text, is_plain = _part_text(child)
            if text:
                (plain if is_plain else html_parts).append(text)
    if plain:
        return "\n".join(plain), True
    return "\n".join(html_parts), False


# Turn a full Gmail message into metadata plus a bounded plain-text body.
def normalize_message(data: Any) -> dict[str, str] | None:
    metadata = normalize_metadata(data)
    if metadata is None or not isinstance(data, Mapping):
        return None
    text, is_plain = _part_text(data.get("payload"))
    if text and not is_plain:
        parser = _TextExtractor()
        parser.feed(text)
        text = html.unescape("".join(parser.parts))
    metadata["body"] = compact(text or data.get("snippet"), limit=MAX_BODY_CHARS)
    return metadata


# Extract draft_id and thread_id from a Gmail draft creation response.
def normalize_draft(data: Any) -> tuple[str, str]:
    if not isinstance(data, Mapping) or not isinstance(data.get("id"), str):
        raise GmailPayloadError("Gmail did not return a draft id")
    message = data.get("message")
    thread_id = message.get("threadId") if isinstance(message, Mapping) else ""
    return str(data["id"])[:256], str(thread_id or "")[:256]
