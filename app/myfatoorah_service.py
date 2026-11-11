"""
MyFatoorah Payment Gateway Integration Service for Trings (FastAPI).
Handles:
  1. Direct Embedded Payment / In-Page Session (no redirect).
  2. Apple Pay / Google Pay / Benefit / Cards.
  3. Save Card / Tokenization (Charge saved card directly without redirect).
  4. Webhook verification & payment finalization (HMAC-SHA256).
"""
import os
import hmac
import base64
import hashlib
import json
import logging
from typing import Dict, Any, Optional, List
import httpx
from dotenv import load_dotenv
from pathlib import Path

ENV_FILE = Path(__file__).parent.parent / ".env"
load_dotenv(ENV_FILE)

logger = logging.getLogger("myfatoorah")

# Configuration from environment (MYFATOORAH_ENV=test|live; legacy MYFATOORAH_PAYMENT_MODE still supported)
_env_raw = (os.getenv("MYFATOORAH_ENV") or os.getenv("MYFATOORAH_PAYMENT_MODE") or "live").strip().lower()
MYFATOORAH_ENV = "test" if _env_raw == "test" else "live"
is_test = MYFATOORAH_ENV == "test"
MYFATOORAH_PAYMENT_MODE = MYFATOORAH_ENV

_DEFAULT_TEST = "https://apitest.myfatoorah.com"
_DEFAULT_LIVE = "https://api.myfatoorah.com"
_DEFAULT_SCRIPT_TEST = "https://demo.myfatoorah.com/sessions/v1/session.js"
_DEFAULT_SCRIPT_LIVE = "https://portal.myfatoorah.com/sessions/v1/session.js"
API_URL = (
    os.getenv("MYFATOORAH_TEST_API_URL" if is_test else "MYFATOORAH_API_URL", _DEFAULT_TEST if is_test else _DEFAULT_LIVE)
).rstrip("/")
SESSION_SCRIPT_URL = (
    os.getenv(
        "MYFATOORAH_TEST_SESSION_SCRIPT_URL" if is_test else "MYFATOORAH_SESSION_SCRIPT_URL",
        _DEFAULT_SCRIPT_TEST if is_test else _DEFAULT_SCRIPT_LIVE,
    )
).strip()
API_KEY = (
    os.getenv("MYFATOORAH_TEST_API_KEY" if is_test else "MYFATOORAH_API_KEY", "")
    or os.getenv("MYFATOORAH_API_KEY", "")
).strip()
WEBHOOK_SECRET = os.getenv("MYFATOORAH_WEBHOOK_SECRET", "").strip()
CURRENCY = os.getenv("MYFATOORAH_CURRENCY", "BHD").upper()
COUNTRY_ISO = os.getenv("MYFATOORAH_COUNTRY_ISO", "BHR").upper()

HEADERS = {
    "Authorization": f"Bearer {API_KEY}",
    "Content-Type": "application/json"
}

def get_payment_config() -> Dict[str, Any]:
    """Returns frontend configuration for embedded checkout (session.js)."""
    return {
        "gateway": "myfatoorah",
        "currency": CURRENCY,
        "country": COUNTRY_ISO,
        "mode": MYFATOORAH_ENV,
        "is_test": is_test,
        "api_base": API_URL,
        "session_script_url": SESSION_SCRIPT_URL,
    }

def _api_error(data: Dict[str, Any], fallback: str) -> str:
    errors = data.get("ValidationErrors") or []
    if errors and isinstance(errors, list):
        parts = [f"{e.get('Name')}: {e.get('Error')}" for e in errors if isinstance(e, dict)]
        if parts:
            return "; ".join(parts)
    return str(data.get("Message") or fallback)


def _customer(name: str, email: str, reference: str) -> Dict[str, Any]:
    out: Dict[str, Any] = {"Reference": reference}
    if name:
        out["Name"] = name[:100]
    if email:
        out["Email"] = email[:100]
    return out


async def initiate_session(
    *,
    amount: float,
    external_identifier: str,
    customer_reference: str,
    customer_name: str = "",
    customer_email: str = "",
    redirection_url: str = "",
    save_token: bool = False,
    language: str = "EN",
    currency: Optional[str] = None,
) -> Dict[str, Any]:
    """POST /v3/sessions (COMPLETE_PAYMENT). Returns Data incl. SessionId and EncryptionKey."""
    payload: Dict[str, Any] = {
        "PaymentMode": "COMPLETE_PAYMENT",
        "Order": {
            "Amount": round(float(amount), 3),
            "Currency": currency or CURRENCY,
            "ExternalIdentifier": external_identifier,
        },
        "Customer": _customer(customer_name, customer_email, customer_reference),
        "SaveCardOptions": {"SaveToken": bool(save_token)},
        "Language": "AR" if (language or "").upper() == "AR" else "EN",
    }
    if redirection_url:
        payload["IntegrationUrls"] = {"Redirection": redirection_url}

    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.post(f"{API_URL}/v3/sessions", headers=HEADERS, json=payload)
        data = resp.json()
        if not data.get("IsSuccess"):
            logger.error("MyFatoorah initiate_session error: %s", data)
            raise Exception(_api_error(data, "Failed to initiate MyFatoorah session"))
        return data.get("Data") or {}


def decrypt_payment_data(encrypted: str, encryption_key: str) -> Dict[str, Any]:
    """Decrypts the embedded callback `paymentData` (AES-128-CBC, key = IV = first 16 key bytes)."""
    from cryptography.hazmat.primitives import padding
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

    key = (encryption_key or "").encode("utf-8")[:16].ljust(16, b"\0")
    raw = base64.b64decode(encrypted or "")
    decryptor = Cipher(algorithms.AES(key), modes.CBC(key)).decryptor()
    padded = decryptor.update(raw) + decryptor.finalize()
    unpadder = padding.PKCS7(128).unpadder()
    plain = unpadder.update(padded) + unpadder.finalize()
    return json.loads(plain.decode("utf-8"))


async def charge_token(
    *,
    amount: float,
    token: str,
    external_identifier: str,
    customer_reference: str,
    customer_name: str = "",
    customer_email: str = "",
    redirection_url: str = "",
    currency: Optional[str] = None,
) -> Dict[str, Any]:
    """POST /v3/payments with a saved card token."""
    payload: Dict[str, Any] = {
        "PaymentMethod": "CARD",
        "Order": {
            "Amount": round(float(amount), 3),
            "Currency": currency or CURRENCY,
            "ExternalIdentifier": external_identifier,
        },
        "SourceOfFund": {"Token": token},
        "Customer": _customer(customer_name, customer_email, customer_reference),
    }
    if redirection_url:
        payload["IntegrationUrls"] = {"Redirection": redirection_url}

    async with httpx.AsyncClient(timeout=20.0) as client:
        resp = await client.post(f"{API_URL}/v3/payments", headers=HEADERS, json=payload)
        data = resp.json()
        if not data.get("IsSuccess"):
            logger.error("MyFatoorah charge_token error: %s", data)
            raise Exception(_api_error(data, "Token payment failed"))
        return data.get("Data") or {}

async def get_payment_status(payment_id: str) -> Dict[str, Any]:
    """
    Inquiry / Verify payment status via GET /v3/payments/{paymentId}
    """
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.get(f"{API_URL}/v3/payments/{payment_id}", headers=HEADERS)
        data = resp.json()
        if not data.get("IsSuccess"):
            logger.error("MyFatoorah get_payment_status error: %s", data)
            raise Exception(_api_error(data, "Failed to verify payment status"))
        return data.get("Data") or {}

def verify_webhook_signature(payload_bytes: bytes, received_signature: str) -> bool:
    """
    Validates MyFatoorah Webhook V2 signature with HMAC-SHA256.
    """
    if not WEBHOOK_SECRET or not received_signature:
        logger.warning("MyFatoorah webhook rejected: secret or signature is missing.")
        return False
    try:
        secret_bytes = WEBHOOK_SECRET.encode("utf-8")
        computed = hmac.new(secret_bytes, payload_bytes, hashlib.sha256).digest()
        expected_sig = base64.b64encode(computed).decode("utf-8")
        return hmac.compare_digest(expected_sig, received_signature.strip())
    except Exception as ex:
        logger.error(f"Signature calculation failed: {ex}")
        return False
