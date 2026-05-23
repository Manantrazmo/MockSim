"""
Outbound webhook delivery with HMAC-SHA256 signing (DESIGN.md §6.2).

Signature format (matches Stripe's convention):
  MockSim-Signature: t=<unix_timestamp>,v1=<hmac_hex>
  HMAC input: "{timestamp}.{body_json}"

Trazmo verifies by recomputing the HMAC with its copy of the secret.
"""
from __future__ import annotations
import hashlib
import hmac
import json
import time
from typing import Any

import httpx
import structlog
from tenacity import retry, stop_after_attempt, wait_exponential

from mocksim.config import settings

log = structlog.get_logger(__name__)


def _sign(secret: str, timestamp: int, body: str) -> str:
    """Compute HMAC-SHA256 over '{timestamp}.{body}'."""
    payload = f"{timestamp}.{body}"
    return hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()


def verify_signature(
    secret: str,
    signature_header: str,
    body: bytes,
    tolerance_seconds: int = 300,
) -> bool:
    """
    Verify an inbound MockSim-Signature header.
    Used by Trazmo's webhook receiver to verify authenticity.
    Returns True if valid and within tolerance window.
    """
    parts = dict(p.split("=", 1) for p in signature_header.split(",") if "=" in p)
    t = parts.get("t")
    v1 = parts.get("v1")
    if not t or not v1:
        return False

    try:
        ts = int(t)
    except ValueError:
        return False

    if abs(time.time() - ts) > tolerance_seconds:
        return False  # replay attack guard

    expected = _sign(secret, ts, body.decode())
    return hmac.compare_digest(expected, v1)


async def post_webhook(
    url: str,
    secret: str,
    event_id: str,
    event_type: str,
    partition_key: str,
    payload: dict[str, Any],
    format: str = "per_event",
    extra_headers: dict[str, str] | None = None,
) -> tuple[bool, str | None]:
    """
    POST a signed webhook to target_url.
    Returns (success: bool, error_message: str | None).

    Does NOT retry internally — retries are managed by the outbox dispatcher.

    `format` selects the signing/header contract:
      - 'per_event' (default): Stripe-style MockSim-Signature: t=…,v1=…
      - 'trazmo_settlement': trazmo-platform's acquirer webhook contract —
        X-Acquirer-Signature is plain HMAC-SHA256 hex of the raw body (no
        timestamp prefix), and X-Tenant-ID is read from extra_headers.
        Matches modules/prism/webhooks_acquirer.py:93 on the trazmo side.
    """
    body = json.dumps(payload, separators=(",", ":"), default=str)

    if format == "trazmo_settlement":
        # trazmo verifies: hmac_sha256(secret, raw_body).hexdigest()
        sig = hmac.new(secret.encode(), body.encode(), hashlib.sha256).hexdigest()
        headers = {
            "Content-Type": "application/json",
            "X-Acquirer-Signature": sig,
            "X-MockSim-Event-Id": event_id,  # informational, not used by trazmo
        }
        if extra_headers:
            headers.update(extra_headers)  # carries X-Tenant-ID
    else:
        ts = int(time.time())
        sig = _sign(secret, ts, body)
        headers = {
            "Content-Type": "application/json",
            "MockSim-Signature": f"t={ts},v1={sig}",
            "MockSim-Event-Id": event_id,
            "MockSim-Event-Type": event_type,
            "MockSim-Partition-Key": partition_key,
        }
        if extra_headers:
            headers.update(extra_headers)

    try:
        async with httpx.AsyncClient(timeout=settings.default_webhook_timeout_seconds) as client:
            resp = await client.post(url, content=body.encode(), headers=headers)

        if 200 <= resp.status_code < 300:
            return True, None

        if 400 <= resp.status_code < 500:
            # Non-retryable (4xx = consumer error, treat as dead-letter)
            error = f"HTTP {resp.status_code}: non-retryable"
            log.warning("webhook.4xx", url=url, event_id=event_id, status=resp.status_code)
            return False, error

        # 5xx — retryable
        return False, f"HTTP {resp.status_code}"

    except httpx.TimeoutException:
        return False, "Timeout"
    except httpx.ConnectError as exc:
        return False, f"Connection error: {exc}"
    except Exception as exc:
        log.error("webhook.unexpected_error", url=url, event_id=event_id, error=str(exc))
        return False, str(exc)


def _validate_target_url(url: str) -> None:
    """
    SSRF guard (CEO review S1). Reject loopback, private, link-local addresses.
    Raises ValueError with a safe message if the URL is rejected.
    """
    import ipaddress
    from urllib.parse import urlparse

    parsed = urlparse(url)

    if not settings.mocksim_allow_http and parsed.scheme != "https":
        raise ValueError("Webhook target URL must use HTTPS in production")

    if parsed.scheme not in ("http", "https"):
        raise ValueError("Webhook target URL must use http or https scheme")

    host = parsed.hostname or ""
    if not host:
        raise ValueError("Webhook target URL has no host")

    # Reject known-dangerous hostnames
    _BLOCKED_HOSTS = {"localhost", "metadata.google.internal"}
    if host.lower() in _BLOCKED_HOSTS:
        raise ValueError("Webhook target URL is not allowed")

    # Try to resolve as IP and check for private/loopback ranges
    try:
        addr = ipaddress.ip_address(host)
        if addr.is_loopback or addr.is_private or addr.is_link_local or addr.is_unspecified:
            raise ValueError("Webhook target URL resolves to a non-routable address")
    except ValueError as exc:
        if "not allowed" in str(exc) or "non-routable" in str(exc):
            raise
        # Not an IP literal — DNS hostname, validated at dispatch time
        pass
