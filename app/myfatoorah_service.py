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

# Configuration from environment
MYFATOORAH_PAYMENT_MODE = os.getenv("MYFATOORAH_PAYMENT_MODE", "live").lower()
is_test = MYFATOORAH_PAYMENT_MODE == "test"

API_URL = os.getenv("MYFATOORAH_TEST_API_URL" if is_test else "MYFATOORAH_API_URL", "https://api.myfatoorah.com").rstrip("/")
API_KEY = os.getenv("MYFATOORAH_TEST_API_KEY" if is_test else "MYFATOORAH_API_KEY", "").strip()
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
        "mode": MYFATOORAH_PAYMENT_MODE,
        "is_test": is_test,
        "api_base": API_URL,
        "session_script_url": f"{API_URL}/payment/v1/session.js",
    }

async def initiate_session(amount: float, currency: Optional[str] = None, customer_identifier: Optional[str] = None) -> Dict[str, Any]:
    """
    Creates an embedded session for in-page payment without external redirect.
    Call MyFatoorah /v3/sessions to obtain SessionId and CountryCode.
    """
    cur = currency or CURRENCY
    payload: Dict[str, Any] = {
        "Order": {
            "Amount": round(float(amount), 3),
            "Currency": cur
        }
    }
    if customer_identifier:
        payload["CustomerIdentifier"] = customer_identifier

    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.post(f"{API_URL}/v3/sessions", headers=HEADERS, json=payload)
        data = resp.json()
        if not data.get("IsSuccess"):
            logger.error(f"MyFatoorah initiate_session error: {data}")
            raise Exception(data.get("Message") or "Failed to initiate MyFatoorah session")
        return data.get("Data", {})

async def execute_payment(
    *,
    amount: float,
    currency: Optional[str] = None,
    customer_name: str,
    customer_email: str,
    customer_phone: str,
    order_id: str,
    session_id: Optional[str] = None,
    payment_method_id: Optional[int] = None,
    callback_url: str,
    error_url: str,
    save_token: bool = False,
    customer_identifier: Optional[str] = None
) -> Dict[str, Any]:
    """
    Executes payment using embedded session or payment method.
    """
    cur = currency or CURRENCY
    phone_clean = "".join(filter(str.isdigit, customer_phone or ""))
    phone_number = phone_clean[-8:] if len(phone_clean) >= 8 else phone_clean

    payload: Dict[str, Any] = {
        "PaymentMethodId": payment_method_id or 0,
        "CustomerName": customer_name or "Valued Customer",
        "DisplayCurrencyIso": cur,
        "MobileCountryCode": "+973",
        "CustomerMobile": phone_number or "33000000",
        "CustomerEmail": customer_email or "billing@pager.rahapp.com",
        "InvoiceValue": round(amount, 3),
        "CallBackUrl": callback_url,
        "ErrorUrl": error_url,
        "CustomerReference": order_id,
        "SaveToken": save_token,
    }

    if session_id:
        payload["SessionId"] = session_id

    if customer_identifier:
        payload["CustomerIdentifier"] = customer_identifier

    async with httpx.AsyncClient(timeout=20.0) as client:
        resp = await client.post(f"{API_URL}/v3/payments", headers=HEADERS, json=payload)
        data = resp.json()
        if not data.get("IsSuccess"):
            logger.error(f"MyFatoorah execute_payment error: {data}")
            raise Exception(data.get("Message") or "Payment execution failed")
        return data.get("Data", {})

async def direct_charge_saved_card(
    *,
    amount: float,
    token: str,
    order_id: str,
    customer_name: str,
    customer_phone: str,
    callback_url: str,
    error_url: str,
    security_code: Optional[str] = None
) -> Dict[str, Any]:
    """
    Direct payment using saved card token (Tokenization).
    """
    payload = {
        "InvoiceValue": round(amount, 3),
        "DisplayCurrencyIso": CURRENCY,
        "CustomerName": customer_name,
        "CustomerMobile": "".join(filter(str.isdigit, customer_phone))[-8:] or "33000000",
        "MobileCountryCode": "+973",
        "CallBackUrl": callback_url,
        "ErrorUrl": error_url,
        "CustomerReference": order_id,
        "SourceInfo": {
            "PaymentType": "Token",
            "Token": token
        }
    }
    if security_code:
        payload["SourceInfo"]["Cvv"] = security_code

    async with httpx.AsyncClient(timeout=20.0) as client:
        resp = await client.post(f"{API_URL}/v3/payments", headers=HEADERS, json=payload)
        data = resp.json()
        if not data.get("IsSuccess"):
            logger.error(f"MyFatoorah direct_charge_saved_card error: {data}")
            raise Exception(data.get("Message") or "Token payment failed")
        return data.get("Data", {})

async def get_payment_status(payment_id: str) -> Dict[str, Any]:
    """
    Inquiry / Verify payment status via GET /v3/payments/{paymentId}
    """
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.get(f"{API_URL}/v3/payments/{payment_id}", headers=HEADERS)
        data = resp.json()
        if not data.get("IsSuccess"):
            logger.error(f"MyFatoorah get_payment_status error: {data}")
            raise Exception(data.get("Message") or "Failed to verify payment status")
        return data.get("Data", {})

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
