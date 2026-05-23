"""
Standardised error envelope and ISO 20022 pain.002 reject reason codes.

All HTTP errors return:
  { "code": "INVALID_IBAN", "message": "...", "trace_id": "...", "details": {...} }

Never leak internal field names, SQL state, or other tenants' data.
"""
from __future__ import annotations
from enum import StrEnum
from typing import Any

from fastapi import HTTPException
from pydantic import BaseModel


# ── ISO 20022 External Code List (subset used by MockSim) ────────
class PacsRejectReason(StrEnum):
    AC01 = "AC01"  # Incorrect account number / malformed IBAN
    AC02 = "AC02"  # Invalid creditor account (currency mismatch)
    AC04 = "AC04"  # Closed account
    AC06 = "AC06"  # Blocked account (sharia mismatch)
    AM04 = "AM04"  # Insufficient funds
    BE01 = "BE01"  # Inconsistent with end customer (tenant routing mismatch)
    RC07 = "RC07"  # Invalid file format (iban_checksum_invalid scenario)
    MS03 = "MS03"  # Not specified (catch-all)


# ── MockSim application error codes ─────────────────────────────
class ErrorCode(StrEnum):
    # Auth
    MISSING_API_KEY = "MISSING_API_KEY"
    INVALID_API_KEY = "INVALID_API_KEY"
    FORBIDDEN = "FORBIDDEN"
    INSUFFICIENT_SCOPE = "INSUFFICIENT_SCOPE"

    # Idempotency
    IDEMPOTENCY_KEY_REQUIRED = "IDEMPOTENCY_KEY_REQUIRED"
    IDEMPOTENCY_CONFLICT = "IDEMPOTENCY_CONFLICT"

    # Validation
    INVALID_IBAN = "INVALID_IBAN"
    INVALID_CURRENCY = "INVALID_CURRENCY"
    INVALID_AMOUNT = "INVALID_AMOUNT"
    INVALID_REGION = "INVALID_REGION"
    CURRENCY_MISMATCH = "CURRENCY_MISMATCH"
    SHARIA_VIOLATION = "SHARIA_VIOLATION"
    INVALID_TARGET_URL = "INVALID_TARGET_URL"
    VALIDATION_ERROR = "VALIDATION_ERROR"

    # Business
    ACCOUNT_NOT_FOUND = "ACCOUNT_NOT_FOUND"
    ACCOUNT_DORMANT = "ACCOUNT_DORMANT"
    ACCOUNT_CLOSED = "ACCOUNT_CLOSED"
    INSUFFICIENT_FUNDS = "INSUFFICIENT_FUNDS"
    MANDATE_NOT_FOUND = "MANDATE_NOT_FOUND"
    MANDATE_EXPIRED = "MANDATE_EXPIRED"
    MANDATE_NOT_ACTIVE = "MANDATE_NOT_ACTIVE"
    AMOUNT_EXCEEDS_LIMIT = "AMOUNT_EXCEEDS_LIMIT"
    PAYMENT_NOT_FOUND = "PAYMENT_NOT_FOUND"
    MERCHANT_NOT_FOUND = "MERCHANT_NOT_FOUND"

    # Scenario
    SCENARIO_INJECTED = "SCENARIO_INJECTED"

    # Server
    INTERNAL_ERROR = "INTERNAL_ERROR"
    NOT_IMPLEMENTED = "NOT_IMPLEMENTED"


class ErrorResponse(BaseModel):
    code: str
    message: str
    trace_id: str
    details: dict[str, Any] = {}


class MockSimError(HTTPException):
    """Base exception for all MockSim domain errors. Maps to ErrorResponse."""

    def __init__(
        self,
        status_code: int,
        code: ErrorCode | str,
        message: str,
        details: dict[str, Any] | None = None,
        trace_id: str = "",
    ) -> None:
        self.error_code = code
        self.error_message = message
        self.error_details = details or {}
        self.trace_id = trace_id
        super().__init__(status_code=status_code, detail=message)


# ── Convenience constructors ──────────────────────────────────────
def bad_request(code: ErrorCode, message: str, **details: Any) -> MockSimError:
    return MockSimError(400, code, message, details)


def unauthorized(message: str = "Invalid or missing API key") -> MockSimError:
    return MockSimError(401, ErrorCode.INVALID_API_KEY, message)


def forbidden(message: str = "Insufficient scope") -> MockSimError:
    return MockSimError(403, ErrorCode.FORBIDDEN, message)


def not_found(code: ErrorCode, message: str) -> MockSimError:
    return MockSimError(404, code, message)


def conflict(code: ErrorCode, message: str, **details: Any) -> MockSimError:
    return MockSimError(409, code, message, details)


def unprocessable(code: ErrorCode, message: str, **details: Any) -> MockSimError:
    return MockSimError(422, code, message, details)


def internal(message: str = "Internal server error") -> MockSimError:
    return MockSimError(500, ErrorCode.INTERNAL_ERROR, message)


def not_implemented_yet(endpoint: str) -> MockSimError:
    return MockSimError(501, ErrorCode.NOT_IMPLEMENTED, f"{endpoint} not yet implemented")
