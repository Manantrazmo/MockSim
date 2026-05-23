"""
Idempotency enforcement (DESIGN.md §6.1).

Every POST requires an Idempotency-Key header.
The check, business-logic execution, and accounts write happen in ONE
Postgres transaction with SELECT FOR UPDATE on the idempotency row.

Endpoint classes:
  money     — idempotency row lives forever (never GC'd)
  non_money — idempotency row expires after 24h

Same key + same body  → return cached response (200/201)
Same key + diff body  → 409 Conflict
"""
from __future__ import annotations
import hashlib
import json
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import Request
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from mocksim.core.errors import ErrorCode, MockSimError, conflict
from mocksim.persistence.models import IdempotencyRecord


def _body_hash(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


async def get_or_start(
    session: AsyncSession,
    mock_tenant_id: uuid.UUID,
    key: str,
    body: bytes,
    endpoint_class: str,  # 'money' | 'non_money'
) -> IdempotencyRecord | None:
    """
    Check idempotency store. Returns:
      - None          → first time we've seen this key; caller should proceed
      - IdempotencyRecord (status='complete') → cached; caller should return stored response
    Raises 409 if same key but different body.
    """
    bh = _body_hash(body)
    now = datetime.now(timezone.utc)

    # SELECT FOR UPDATE — concurrent same-key requests serialize here
    result = await session.execute(
        select(IdempotencyRecord)
        .where(
            IdempotencyRecord.mock_tenant_id == mock_tenant_id,
            IdempotencyRecord.idempotency_key == key,
        )
        .with_for_update()
    )
    existing = result.scalar_one_or_none()

    if existing is None:
        expires_at = None if endpoint_class == "money" else now + timedelta(hours=24)
        record = IdempotencyRecord(
            id=uuid.uuid4(),
            mock_tenant_id=mock_tenant_id,
            idempotency_key=key,
            body_hash=bh,
            endpoint_class=endpoint_class,
            status="pending",
            expires_at=expires_at,
            created_at=now,
        )
        session.add(record)
        return None  # proceed

    if existing.body_hash != bh:
        raise conflict(
            ErrorCode.IDEMPOTENCY_CONFLICT,
            "Idempotency key reused with different request body",
            key=key,
        )

    if existing.status == "complete":
        return existing  # return cached response

    # status == 'pending' means a concurrent request is in-flight; return 409
    raise conflict(
        ErrorCode.IDEMPOTENCY_CONFLICT,
        "Concurrent request with the same idempotency key is in progress",
        key=key,
    )


async def complete(
    session: AsyncSession,
    mock_tenant_id: uuid.UUID,
    key: str,
    response_status: int,
    response_body: dict[str, Any],
    content_type: str = "application/json",
) -> None:
    """Mark idempotency record as complete with the response payload."""
    result = await session.execute(
        select(IdempotencyRecord)
        .where(
            IdempotencyRecord.mock_tenant_id == mock_tenant_id,
            IdempotencyRecord.idempotency_key == key,
        )
        .with_for_update()
    )
    record = result.scalar_one_or_none()
    if record:
        record.status = "complete"
        record.response_status = response_status
        record.response_body = response_body
        record.content_type = content_type


def require_idempotency_key(request: Request) -> str:
    """FastAPI dependency: extracts and validates Idempotency-Key header."""
    key = request.headers.get("Idempotency-Key", "").strip()
    if not key:
        raise MockSimError(
            400,
            ErrorCode.IDEMPOTENCY_KEY_REQUIRED,
            "Idempotency-Key header is required for all POST requests",
        )
    if len(key) > 255:
        raise MockSimError(
            400,
            ErrorCode.IDEMPOTENCY_KEY_REQUIRED,
            "Idempotency-Key must be ≤ 255 characters",
        )
    return key
