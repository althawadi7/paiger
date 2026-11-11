"""MyFatoorah checkout intents, settlement, saved cards, and auto-renew."""
import hashlib
import hmac
import json
import logging
import os
import re
import secrets
from base64 import urlsafe_b64encode
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote

from cryptography.fernet import Fernet

from . import auth, billing, database as db, myfatoorah_service as mf, owner_board
from . import billing_mail, i18n

_log = logging.getLogger("pager.payments")
_INTENT_RE = re.compile(r"^chk_[A-Za-z0-9_-]{16,40}$")
_PENDING_TX = {"", "INITIATED", "IN_PROGRESS", "INPROGRESS", "PENDING"}
_CLOSED_INVOICE = {"CANCELED", "CANCELLED", "EXPIRED"}


def _fernet() -> Fernet:
    digest = hashlib.sha256(auth.SECRET_KEY.encode("utf-8")).digest()
    return Fernet(urlsafe_b64encode(digest))


def _enc(text: str) -> str:
    return _fernet().encrypt((text or "").encode("utf-8")).decode("utf-8")


def _dec(text: str) -> str:
    return _fernet().decrypt((text or "").encode("utf-8")).decode("utf-8")


def amount_bhd(fils: int) -> float:
    return round(int(fils) / 1000.0, 3)


def _new_intent_id() -> str:
    return "chk_" + secrets.token_urlsafe(18).replace("/", "_")


def plan_period_end(plan: Dict[str, Any], start: Optional[date] = None) -> date:
    base = start or owner_board.bahrain_now().date()
    if (plan.get("type") or plan.get("code")) == "yearly":
        return base + timedelta(days=365)
    return base + timedelta(days=30)


def paid_period_end(rest: Dict[str, Any]) -> Optional[date]:
    """End of the current paid period while active; new periods stack on top of it."""
    until = billing.as_date(rest.get("manual_plan_until"))
    if until and until > owner_board.bahrain_now().date() and db.effective_status(rest) == "active":
        return until
    return None


def plan_change_mode(rest: Dict[str, Any], code: str) -> str:
    """extend | upgrade | schedule | new — how picking `code` behaves for this restaurant."""
    if not paid_period_end(rest):
        return "new"
    current = (rest.get("plan_code") or "").strip()
    if code == current:
        return "extend"
    if billing.PLAN_RANK.get(code, 0) > billing.PLAN_RANK.get(current, 0):
        return "upgrade"
    return "schedule"


def schedule_plan_change(restaurant_id: int, code: str) -> Dict[str, Any]:
    rest = _restaurant_row(restaurant_id)
    plan = billing.plan_row(code)
    if not plan or not plan.get("active") or plan.get("deleted_at"):
        raise ValueError("plan")
    if plan_change_mode(rest, code) != "schedule":
        raise ValueError("state")
    at = paid_period_end(rest)
    conn = db.get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE restaurants SET scheduled_plan_code=%s, scheduled_plan_at=%s WHERE id=%s",
                (code, at, restaurant_id),
            )
            conn.commit()
    finally:
        conn.close()
    return {"plan_code": code, "at": at.isoformat() if at else ""}


def cancel_plan_change(restaurant_id: int) -> None:
    conn = db.get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE restaurants SET scheduled_plan_code=NULL, scheduled_plan_at=NULL WHERE id=%s",
                (restaurant_id,),
            )
            conn.commit()
    finally:
        conn.close()


def apply_scheduled_plan_changes(today: date) -> int:
    """Switch plans whose scheduled date has arrived; renewal then charges the new plan."""
    conn = db.get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE restaurants
                SET plan_code=scheduled_plan_code, scheduled_plan_code=NULL, scheduled_plan_at=NULL
                WHERE scheduled_plan_code IS NOT NULL AND scheduled_plan_at IS NOT NULL AND scheduled_plan_at <= %s
                """,
                (today,),
            )
            changed = cur.rowcount
            conn.commit()
    finally:
        conn.close()
    return int(changed or 0)


def _restaurant_row(restaurant_id: int) -> Dict[str, Any]:
    conn = db.get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM restaurants WHERE id=%s", (restaurant_id,))
            row = cur.fetchone()
    finally:
        conn.close()
    if not row:
        raise ValueError("restaurant")
    return row


def _pending_subscribe_intent(restaurant_id: int, plan_code: str, price_fils: int) -> Optional[Dict[str, Any]]:
    """Reuse an open checkout for the same plan so reopening the page doesn't issue another invoice."""
    conn = db.get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT ci.id, ci.invoice_number, ci.amount_fils, ci.expires_at
                FROM checkout_intents ci
                JOIN invoices i ON i.number = ci.invoice_number
                WHERE ci.restaurant_id=%s AND ci.purpose='subscribe' AND ci.ref_plan_code=%s
                  AND ci.status='pending' AND ci.expires_at > %s AND ci.amount_fils=%s
                  AND i.status='unpaid' AND i.kind='subscription'
                ORDER BY ci.created_at DESC LIMIT 1
                """,
                (restaurant_id, plan_code, datetime.now(), price_fils),
            )
            row = cur.fetchone()
    finally:
        conn.close()
    if not row:
        return None
    expires = db._parse_dt(row["expires_at"]) or datetime.now()
    return {
        "intent_id": row["id"],
        "invoice_number": row["invoice_number"],
        "amount_fils": int(row["amount_fils"]),
        "amount_bhd": billing.fils_fixed(row["amount_fils"]),
        "purpose": "subscribe",
        "expires_at": expires.isoformat(),
    }


def create_checkout_intent(
    restaurant_id: int,
    purpose: str,
    *,
    plan_code: Optional[str] = None,
    pack_id: Optional[int] = None,
    invoice_number: Optional[str] = None,
    kind_override: Optional[str] = None,
) -> Dict[str, Any]:
    """Server-side amount only; creates unpaid invoice when needed."""
    purpose = (purpose or "").strip().lower()
    if purpose not in ("subscribe", "topup", "invoice"):
        raise ValueError("purpose")
    rest = _restaurant_row(restaurant_id)
    plans = billing._plans_by_code()
    intent_id = _new_intent_id()
    expires = datetime.now() + timedelta(hours=2)

    if purpose == "subscribe":
        code = (plan_code or "").strip()
        plan = plans.get(code)
        if not plan or not plan.get("active") or plan.get("deleted_at"):
            raise ValueError("plan")
        price = int(plan.get("price_fils") or 0)
        if price <= 0:
            raise ValueError("plan")
        start = paid_period_end(rest)
        if not kind_override:
            current = (rest.get("plan_code") or "").strip()
            if start and billing.PLAN_RANK.get(code, 0) < billing.PLAN_RANK.get(current, 0):
                raise ValueError("schedule")
            reusable = _pending_subscribe_intent(restaurant_id, code, price)
            if reusable:
                return reusable
        period_end = plan_period_end(plan, start)
        name = plan.get("name_en") or code
        number = billing.create_invoice(
            restaurant_id,
            kind=kind_override or "subscription",
            description=f"Trings {name}",
            lines=[{
                "description": f"Trings {name}",
                "detail": f"Until {period_end.isoformat()}",
                "qty": 1,
                "amount_fils": price,
                "plan_code": code,
            }],
            due_at=datetime.now() + timedelta(days=1),
            period_end=period_end,
        )
        amount_fils = price
        ref_plan = code
        ref_pack = None
    elif purpose == "topup":
        if (rest.get("plan_code") or "") != "points":
            raise ValueError("plan")
        pack = None
        conn = db.get_db_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT * FROM point_packs WHERE id=%s AND deleted_at IS NULL AND active=1",
                    (int(pack_id or 0),),
                )
                pack = cur.fetchone()
        finally:
            conn.close()
        if not pack:
            raise ValueError("pack")
        price = int(pack.get("price_fils") or 0)
        pts = int(pack.get("points") or 0)
        number = billing.create_invoice(
            restaurant_id,
            kind="topup",
            description=f"Trings points · {pts}",
            lines=[{"description": f"{pts} points", "detail": "", "qty": 1, "amount_fils": price}],
            due_at=datetime.now() + timedelta(days=1),
            pack_id=int(pack["id"]),
            points=pts,
        )
        amount_fils = price
        ref_plan = None
        ref_pack = int(pack["id"])
    else:
        number = (invoice_number or "").strip()
        invoice = billing.get_invoice(number)
        if not invoice or int(invoice["restaurant_id"]) != restaurant_id:
            raise ValueError("invoice")
        if (invoice.get("status") or "") != "unpaid":
            raise ValueError("state")
        amount_fils = int(invoice.get("total_fils") or invoice.get("amount_fils") or 0)
        ref_plan = None
        ref_pack = None

    conn = db.get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO checkout_intents (
                    id, restaurant_id, purpose, amount_fils, invoice_number,
                    ref_plan_code, ref_pack_id, status, save_card_requested, created_at, expires_at
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,'pending',0,%s,%s)
                """,
                (intent_id, restaurant_id, purpose, amount_fils, number, ref_plan, ref_pack, datetime.now(), expires),
            )
            conn.commit()
    finally:
        conn.close()
    return {
        "intent_id": intent_id,
        "invoice_number": number,
        "amount_fils": amount_fils,
        "amount_bhd": billing.fils_fixed(amount_fils),
        "purpose": purpose,
        "expires_at": expires.isoformat(),
    }


def _intent_row(intent_id: str, restaurant_id: int) -> Dict[str, Any]:
    if not _INTENT_RE.match(intent_id or ""):
        raise ValueError("intent")
    conn = db.get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM checkout_intents WHERE id=%s", (intent_id,))
            row = cur.fetchone()
    finally:
        conn.close()
    if not row or int(row["restaurant_id"]) != restaurant_id:
        raise ValueError("intent")
    return row


def get_intent(intent_id: str, restaurant_id: int) -> Dict[str, Any]:
    row = _intent_row(intent_id, restaurant_id)
    if (row.get("status") or "") not in ("pending",):
        raise ValueError("state")
    if row.get("expires_at") and db._parse_dt(row["expires_at"]) < datetime.now():
        raise ValueError("expired")
    return row


def checkout_messages() -> Dict[str, str]:
    """Client strings for mf-embedded.js (current request language)."""
    return {
        "loading": i18n.t("dash.paymentLoading"),
        "err_sdk": i18n.t("dash.paymentSdk"),
        "err_init": i18n.t("dash.paymentInit"),
        "err_session": i18n.t("dash.paymentSession"),
        "err_declined": i18n.t("dash.paymentDeclined"),
        "err_failed": i18n.t("dash.paymentFailed"),
        "retry": i18n.t("dash.paymentRetry"),
        "processing": i18n.t("dash.paymentProcessing"),
    }


def _return_url(base_url: str, intent_id: str) -> str:
    return f"{base_url.rstrip('/')}/payment/myfatoorah/return?intent={intent_id}"


async def session_data_for_intent(
    intent_id: str,
    restaurant_id: int,
    *,
    save_card: bool,
    base_url: str,
    language: str = "en",
) -> Dict[str, Any]:
    """Creates a COMPLETE_PAYMENT session for the intent's server-side amount."""
    row = get_intent(intent_id, restaurant_id)
    rest = _restaurant_row(restaurant_id)
    amount_fils = int(row["amount_fils"])
    session = await mf.initiate_session(
        amount=amount_bhd(amount_fils),
        external_identifier=intent_id,
        customer_reference=f"rest_{restaurant_id}",
        customer_name=str(rest.get("name") or ""),
        customer_email=str(rest.get("email") or ""),
        redirection_url=_return_url(base_url, intent_id),
        save_token=save_card,
        language=language,
    )
    session_id = str(session.get("SessionId") or "")
    key = str(session.get("EncryptionKey") or "")
    if not session_id:
        raise ValueError("session")
    conn = db.get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE checkout_intents
                SET mf_session_id=%s, mf_session_key_enc=%s, save_card_requested=%s
                WHERE id=%s
                """,
                (session_id, _enc(key) if key else None, 1 if save_card else 0, intent_id),
            )
            conn.commit()
    finally:
        conn.close()
    return {
        "intent_id": intent_id,
        "amount_fils": amount_fils,
        "amount_bhd": billing.fils_fixed(amount_fils),
        "session": {"SessionId": session_id, "SessionExpiry": session.get("SessionExpiry")},
    }


def _record_attempt(
    restaurant_id: int,
    intent_id: str,
    *,
    status: str,
    amount_fils: int,
    mf_payment_id: str = "",
    method: str = "",
    error: str = "",
) -> None:
    conn = db.get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO payment_attempts (
                    restaurant_id, intent_id, gateway, mf_payment_id, status, method,
                    error_message, amount_fils, created_at
                ) VALUES (%s,%s,'myfatoorah',%s,%s,%s,%s,%s,%s)
                """,
                (
                    restaurant_id,
                    intent_id,
                    (mf_payment_id or "")[:64] or None,
                    status,
                    (method or "")[:32] or None,
                    (error or "")[:2000] or None,
                    amount_fils,
                    datetime.now(),
                ),
            )
            conn.commit()
    finally:
        conn.close()


def _parts(data: Dict[str, Any]) -> Tuple[Dict[str, Any], Dict[str, Any], Dict[str, Any], Dict[str, Any]]:
    """v3 payment details: Invoice, Transaction, Amount, Transaction.Card."""
    invoice = data.get("Invoice") or {}
    tx = data.get("Transaction") or {}
    amount = data.get("Amount") or {}
    card = tx.get("Card") or {}
    return invoice, tx, amount, card


def _paid_status(data: Dict[str, Any]) -> bool:
    invoice, tx, _, _ = _parts(data)
    return str(invoice.get("Status") or "").upper() == "PAID" or str(tx.get("Status") or "").upper() == "SUCCESS"


def _paid_amount_fils(data: Dict[str, Any]) -> Optional[int]:
    _, _, amount, _ = _parts(data)
    for cur_key, val_key in (
        ("DisplayCurrency", "ValueInDisplayCurrency"),
        ("PayCurrency", "ValueInPayCurrency"),
        ("BaseCurrency", "ValueInBaseCurrency"),
    ):
        if str(amount.get(cur_key) or "").upper() == mf.CURRENCY and amount.get(val_key) not in (None, ""):
            return int(round(float(amount[val_key]) * 1000))
    return None


def _payment_error(data: Dict[str, Any]) -> str:
    invoice, tx, _, _ = _parts(data)
    err = tx.get("Error") or {}
    return str(err.get("Message") or err.get("Code") or tx.get("Status") or invoice.get("Status") or "declined")


def _card_meta(data: Dict[str, Any]) -> Tuple[str, str, str, str, str]:
    _, tx, _, card = _parts(data)
    return (
        billing.card_brand(
            str(card.get("Brand") or ""),
            str(tx.get("PaymentMethod") or ""),
            str(card.get("Number") or ""),
        ),
        str(card.get("Number") or "")[-4:],
        str(card.get("Token") or ""),
        str(card.get("ExpiryMonth") or ""),
        str(card.get("ExpiryYear") or ""),
    )


def _save_card_if_needed(restaurant_id: int, data: Dict[str, Any], save_requested: bool) -> None:
    brand, last4, token, exp_month, exp_year = _card_meta(data)
    if not save_requested or not token:
        return
    conn = db.get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id, token_enc FROM saved_cards WHERE restaurant_id=%s", (restaurant_id,))
            rows = list(cur.fetchall())
            for row in rows:
                try:
                    if _dec(row.get("token_enc") or "") == token:
                        return
                except Exception:
                    continue
            cur.execute(
                """
                INSERT INTO saved_cards (restaurant_id, token_enc, brand, last4, exp_month, exp_year, is_default, created_at)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
                """,
                (
                    restaurant_id,
                    _enc(token),
                    brand or None,
                    last4 or None,
                    int(exp_month) if exp_month.isdigit() else None,
                    int(exp_year) if exp_year.isdigit() else None,
                    0 if rows else 1,
                    datetime.now(),
                ),
            )
            conn.commit()
    finally:
        conn.close()


async def settle_payment(
    payment_id: str,
    source: str,
    *,
    expected_intent: str = "",
) -> Dict[str, Any]:
    """Idempotent settlement (embedded callback, return page, webhook, token charge).

    Status, amount and intent always come from GET /v3/payments/{id}, never from the client.
    """
    pid = (payment_id or "").strip()
    if not pid:
        raise ValueError("payment")
    conn = db.get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM gateway_settlements WHERE mf_payment_id=%s", (pid,))
            existing = cur.fetchone()
            if existing and existing.get("settled_at"):
                return {"status": "already", "invoice_number": existing.get("invoice_number")}
    finally:
        conn.close()

    status_data = await mf.get_payment_status(pid)
    invoice_part, _, _, _ = _parts(status_data)
    intent_id = str(invoice_part.get("ExternalIdentifier") or "")
    if expected_intent and intent_id != expected_intent:
        _log.warning("MyFatoorah %s: intent %r does not match %r (%s)", pid, intent_id, expected_intent, source)
        raise ValueError("intent")
    mf_invoice_id = str(invoice_part.get("Id") or "")
    paid = _paid_status(status_data)
    _, tx_part, _, _ = _parts(status_data)
    if (
        not paid
        and str(tx_part.get("Status") or "").upper() in _PENDING_TX
        and str(invoice_part.get("Status") or "").upper() not in _CLOSED_INVOICE
    ):
        return {"status": "pending", "invoice_number": ""}
    method = str(tx_part.get("PaymentMethod") or "")[:32]
    brand, last4, _, _, _ = _card_meta(status_data)

    conn = db.get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM gateway_settlements WHERE mf_payment_id=%s FOR UPDATE", (pid,))
            locked = cur.fetchone()
            if locked and locked.get("settled_at"):
                conn.commit()
                return {"status": "already", "invoice_number": locked.get("invoice_number")}

            intent = None
            if _INTENT_RE.match(intent_id):
                cur.execute("SELECT * FROM checkout_intents WHERE id=%s FOR UPDATE", (intent_id,))
                intent = cur.fetchone()
            if not intent:
                raise ValueError("intent")

            restaurant_id = int(intent["restaurant_id"])
            amount_fils = int(intent["amount_fils"])
            invoice_number = intent.get("invoice_number") or ""
            error = ""
            if paid:
                paid_fils = _paid_amount_fils(status_data)
                if paid_fils != amount_fils:
                    _log.error(
                        "MyFatoorah %s amount mismatch: paid=%s expected=%s (%s)",
                        pid, paid_fils, amount_fils, source,
                    )
                    paid = False
                    error = f"amount mismatch: paid {paid_fils} expected {amount_fils} fils"
            else:
                error = _payment_error(status_data)
            status_label = "paid" if paid else "failed"
            if locked and not paid and (locked.get("status") or "") == "failed":
                conn.commit()
                return {"status": "failed", "invoice_number": invoice_number, "error": error}

            cur.execute(
                """
                INSERT INTO gateway_settlements (
                    restaurant_id, intent_id, mf_payment_id, mf_invoice_id, invoice_number,
                    status, method, amount_fils, settled_at, created_at
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON DUPLICATE KEY UPDATE status=VALUES(status), method=VALUES(method), settled_at=VALUES(settled_at)
                """,
                (
                    restaurant_id,
                    intent_id,
                    pid,
                    mf_invoice_id[:64] or None,
                    invoice_number,
                    status_label,
                    method or None,
                    amount_fils,
                    datetime.now() if paid else None,
                    datetime.now(),
                ),
            )
            _record_attempt(
                restaurant_id,
                intent_id,
                status=status_label,
                amount_fils=amount_fils,
                mf_payment_id=pid,
                method=method,
                error=error,
            )
            if not paid:
                conn.commit()
                return {"status": "failed", "invoice_number": invoice_number, "error": error}
            try:
                billing.apply_paid_invoice(
                    cur,
                    invoice_number,
                    method="card",
                    reference=mf_invoice_id or pid,
                    gateway="myfatoorah",
                    gateway_method=method,
                    mf_payment_id=pid,
                    card_last4=last4,
                    card_brand=brand,
                )
            except ValueError as exc:
                if str(exc) != "already":
                    raise
            cur.execute("UPDATE checkout_intents SET status='completed' WHERE id=%s", (intent_id,))
            save_requested = bool(int(intent.get("save_card_requested") or 0))
            conn.commit()
    finally:
        conn.close()
    _save_card_if_needed(restaurant_id, status_data, save_requested)
    billing_mail.on_invoice_paid(invoice_number)
    return {"status": "paid", "invoice_number": invoice_number}


def _payment_id_from_url(url: str) -> str:
    match = re.search(r"[?&]paymentId=([0-9A-Za-z-]+)", url or "")
    return match.group(1) if match else ""


async def process_embedded_payment(
    intent_id: str,
    restaurant_id: int,
    *,
    session_id: str,
    payment_data: str = "",
    redirection_url: str = "",
) -> Dict[str, Any]:
    """Settles the result of the embedded `myfatoorah.init` callback (COMPLETE_PAYMENT)."""
    row = _intent_row(intent_id, restaurant_id)
    if not session_id or session_id != (row.get("mf_session_id") or ""):
        raise ValueError("session")
    pid = ""
    key_enc = row.get("mf_session_key_enc") or ""
    if payment_data and key_enc:
        try:
            detail = mf.decrypt_payment_data(payment_data, _dec(key_enc))
            pid = str((detail.get("Transaction") or {}).get("PaymentId") or "")
        except Exception:
            _log.warning("MyFatoorah paymentData decrypt failed for %s", intent_id, exc_info=True)
    if not pid:
        pid = _payment_id_from_url(redirection_url)
    if not pid:
        raise ValueError("payment")
    result = await settle_payment(pid, "embedded", expected_intent=intent_id)
    return {"ok": result.get("status") in ("paid", "already"), "payment_id": pid, "settlement": result}


def failure_reason(error: str) -> str:
    """Plain-language i18n key for a gateway error; the raw text never reaches the page."""
    text = (error or "").upper()
    if "AUTHENTICAT" in text or "3DS" in text:
        return "pay.reason3ds"
    if "CANCEL" in text or "EXPIRED" in text:
        return "pay.reasonCancelled"
    if "MISMATCH" in text:
        return "pay.reasonGeneric"
    return "pay.reasonDeclined"


async def resolve_result(payment_id: str, intent_id: str, source: str) -> Dict[str, str]:
    """Result-page state: paid | failed | pending (gateway still working or unreachable)."""
    intent_id = intent_id if _INTENT_RE.match(intent_id or "") else ""
    pid = (payment_id or "").strip()
    if pid:
        try:
            out = await settle_payment(pid, source, expected_intent=intent_id)
        except ValueError:
            return {"state": "failed", "reason": "pay.reasonGeneric"}
        except Exception:
            _log.exception("MyFatoorah result check failed for %s", pid)
            return {"state": "pending", "reason": ""}
        status = out.get("status") or ""
        if status in ("paid", "already"):
            return {"state": "paid", "reason": ""}
        if status == "pending":
            return {"state": "pending", "reason": ""}
        return {"state": "failed", "reason": failure_reason(out.get("error") or "")}
    if intent_id:
        conn = db.get_db_connection()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT status FROM checkout_intents WHERE id=%s", (intent_id,))
                row = cur.fetchone()
        finally:
            conn.close()
        if row and (row.get("status") or "") == "completed":
            return {"state": "paid", "reason": ""}
    return {"state": "pending", "reason": ""}


def result_view(intent_id: str, viewer_restaurant_id: Optional[int]) -> Dict[str, Any]:
    """Display-only details for the payment result page, keyed by the checkout intent."""
    view: Dict[str, Any] = {"rows": [], "email": "", "receipt_url": "", "retry_url": "/dashboard/subscription"}
    if not _INTENT_RE.match(intent_id or ""):
        return view
    conn = db.get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM checkout_intents WHERE id=%s", (intent_id,))
            intent = cur.fetchone()
    finally:
        conn.close()
    if not intent:
        return view
    invoice = billing.get_invoice(intent.get("invoice_number") or "")
    if not invoice:
        return view
    number = invoice.get("number") or ""
    purpose = intent.get("purpose") or ""
    if purpose == "subscribe" and intent.get("ref_plan_code"):
        view["retry_url"] = "/dashboard/subscription?plan=" + quote(str(intent["ref_plan_code"]))
    elif purpose == "invoice" and number:
        token = invoice.get("pay_token") or ""
        view["retry_url"] = f"/pay/{quote(number)}" + (f"?t={quote(token)}" if token else "")
    if (invoice.get("status") or "") != "paid":
        return view

    rows: List[Dict[str, str]] = []
    restaurant_id = int(invoice["restaurant_id"])
    if (invoice.get("kind") or "") == "topup":
        rows.append({"label": "pay.pointsAdded", "value": f"{int(invoice.get('points') or 0):,}"})
        rows.append({"label": "pay.pointsBalance", "value": f"{int(billing.wallet(restaurant_id).get('balance') or 0):,}"})
    else:
        plan = billing.plan_row(billing.invoice_plan_code(invoice)) or {}
        name = plan.get("name_ar" if i18n.current_lang() == "ar" else "name_en") or invoice.get("description") or ""
        if name:
            rows.append({"label": "pay.plan", "value": name})
    rows.append({"label": "pay.amount", "value": "BHD " + billing.fils_fixed(intent.get("amount_fils")), "ltr": "1"})
    rows.append({"label": "pay.invoiceNo", "value": number, "ltr": "1"})
    last4 = str(invoice.get("card_last4") or "")
    brand = str(invoice.get("card_brand") or "")
    if brand in billing.WALLETS:
        rows.append({"label": "pay.paidWith", "value": brand, "ltr": "1"})
    elif last4:
        rows.append({"label": "pay.paidWith", "value": f"{brand or i18n.t('pay.card')} ••{last4}", "ltr": "1"})
    if (invoice.get("kind") or "") in ("subscription", "renewal", "manual"):
        renewal = billing.date_label(invoice.get("period_end"))
        if renewal:
            rows.append({"label": "pay.renewal", "value": renewal})
    view["rows"] = rows
    view["email"] = str(invoice.get("restaurant_email") or "")
    if viewer_restaurant_id == restaurant_id and number:
        view["receipt_url"] = f"/dashboard/invoices/{quote(number)}.pdf"
    return view


async def charge_saved_card(
    card_id: int,
    restaurant_id: int,
    intent_id: str,
    rest: Dict[str, Any],
    base_url: str,
) -> Dict[str, Any]:
    row = get_intent(intent_id, restaurant_id)
    conn = db.get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM saved_cards WHERE id=%s AND restaurant_id=%s",
                (int(card_id), restaurant_id),
            )
            card = cur.fetchone()
    finally:
        conn.close()
    if not card:
        raise ValueError("card")
    token = _dec(card.get("token_enc") or "")
    if not token:
        raise ValueError("card")
    try:
        data = await mf.charge_token(
            amount=amount_bhd(int(row["amount_fils"])),
            token=token,
            external_identifier=intent_id,
            customer_reference=f"rest_{restaurant_id}",
            customer_name=str(rest.get("name") or ""),
            customer_email=str(rest.get("email") or ""),
            redirection_url=_return_url(base_url, intent_id),
        )
    except Exception as exc:
        _record_attempt(restaurant_id, intent_id, status="failed", amount_fils=int(row["amount_fils"]), error=str(exc))
        raise
    payment_id = str(data.get("PaymentId") or "")
    if payment_id and data.get("PaymentCompleted"):
        result = await settle_payment(payment_id, "token", expected_intent=intent_id)
        return {"ok": result.get("status") in ("paid", "already"), "payment_id": payment_id, "settlement": result}
    return {"ok": True, "payment_id": payment_id, "redirect_url": data.get("PaymentURL") or ""}


def list_cards(restaurant_id: int) -> List[Dict[str, Any]]:
    conn = db.get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, brand, last4, exp_month, exp_year, is_default, created_at
                FROM saved_cards WHERE restaurant_id=%s ORDER BY is_default DESC, id DESC
                """,
                (restaurant_id,),
            )
            rows = list(cur.fetchall())
    finally:
        conn.close()
    out = []
    for row in rows:
        out.append({
            "id": int(row["id"]),
            "brand": row.get("brand") or "",
            "last4": row.get("last4") or "",
            "exp_month": row.get("exp_month"),
            "exp_year": row.get("exp_year"),
            "is_default": bool(row.get("is_default")),
        })
    return out


def remove_card(restaurant_id: int, card_id: int) -> None:
    conn = db.get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM saved_cards WHERE id=%s AND restaurant_id=%s", (card_id, restaurant_id))
            if cur.rowcount < 1:
                raise ValueError("card")
            cur.execute("SELECT id FROM saved_cards WHERE restaurant_id=%s ORDER BY id DESC LIMIT 1", (restaurant_id,))
            fallback = cur.fetchone()
            if fallback:
                cur.execute("UPDATE saved_cards SET is_default=0 WHERE restaurant_id=%s", (restaurant_id,))
                cur.execute("UPDATE saved_cards SET is_default=1 WHERE id=%s", (int(fallback["id"]),))
            conn.commit()
    finally:
        conn.close()


def set_default_card(restaurant_id: int, card_id: int) -> None:
    conn = db.get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM saved_cards WHERE id=%s AND restaurant_id=%s", (card_id, restaurant_id))
            if not cur.fetchone():
                raise ValueError("card")
            cur.execute("UPDATE saved_cards SET is_default=0 WHERE restaurant_id=%s", (restaurant_id,))
            cur.execute("UPDATE saved_cards SET is_default=1 WHERE id=%s AND restaurant_id=%s", (card_id, restaurant_id))
            conn.commit()
    finally:
        conn.close()


def billing_preferences(restaurant_id: int) -> Dict[str, Any]:
    rest = _restaurant_row(restaurant_id)
    return {
        "auto_renew_enabled": bool(int(rest.get("auto_renew_enabled") if rest.get("auto_renew_enabled") is not None else 1)),
        "auto_topup_enabled": bool(int(rest.get("auto_topup_enabled") or 0)),
    }


def update_preferences(restaurant_id: int, *, auto_renew: Optional[bool] = None, auto_topup: Optional[bool] = None) -> None:
    sets = []
    args: List[Any] = []
    if auto_renew is not None:
        sets.append("auto_renew_enabled=%s")
        args.append(1 if auto_renew else 0)
    if auto_topup is not None:
        sets.append("auto_topup_enabled=%s")
        args.append(1 if auto_topup else 0)
    if not sets:
        return
    args.append(restaurant_id)
    conn = db.get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(f"UPDATE restaurants SET {', '.join(sets)} WHERE id=%s", tuple(args))
            conn.commit()
    finally:
        conn.close()


def payment_history(restaurant_id: int, limit: int = 40) -> List[Dict[str, Any]]:
    conn = db.get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT mf_payment_id, method, status, amount_fils, settled_at, created_at, invoice_number
                FROM gateway_settlements
                WHERE restaurant_id=%s
                ORDER BY id DESC LIMIT %s
                """,
                (restaurant_id, limit),
            )
            return list(cur.fetchall())
    finally:
        conn.close()


def admin_attempt_rows(query: str = "") -> List[Dict[str, Any]]:
    conn = db.get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT p.*, r.name AS restaurant_name
                FROM payment_attempts p
                JOIN restaurants r ON r.id=p.restaurant_id
                ORDER BY p.id DESC LIMIT 200
                """
            )
            rows = list(cur.fetchall())
    finally:
        conn.close()
    needle = (query or "").strip().lower()
    if not needle:
        return rows
    out = []
    for row in rows:
        blob = " ".join([
            str(row.get("restaurant_name") or ""),
            str(row.get("mf_payment_id") or ""),
            str(row.get("method") or ""),
            str(row.get("status") or ""),
            str(row.get("error_message") or ""),
        ]).lower()
        if needle in blob:
            out.append(row)
    return out


def _default_card(restaurant_id: int) -> Optional[Dict[str, Any]]:
    conn = db.get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM saved_cards WHERE restaurant_id=%s ORDER BY is_default DESC, id DESC LIMIT 1",
                (restaurant_id,),
            )
            return cur.fetchone()
    finally:
        conn.close()


def create_renewal_intent(restaurant_id: int) -> Dict[str, Any]:
    rest = _restaurant_row(restaurant_id)
    code = (rest.get("plan_code") or "monthly").strip()
    if code not in ("monthly", "yearly"):
        raise ValueError("plan")
    return create_checkout_intent(restaurant_id, "subscribe", plan_code=code, kind_override="renewal")


async def run_auto_renewals() -> List[Dict[str, Any]]:
    """Charge default card on renewal day; retry daily until renewal_retry_days."""
    today = owner_board.bahrain_now().date()
    apply_scheduled_plan_changes(today)
    try:
        max_retry = int(db.get_platform_setting("renewal_retry_days", "3"))
    except ValueError:
        max_retry = 3
    conn = db.get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, name, email, phone, plan_code, manual_plan_until, auto_renew_enabled,
                       renewal_retry_count, subscription_status
                FROM restaurants
                WHERE suspended_at IS NULL AND plan_code IN ('monthly','yearly')
                  AND subscription_status IN ('active','past_due')
                  AND manual_plan_until IS NOT NULL
                  AND manual_plan_until <= %s
                """,
                (today,),
            )
            due = list(cur.fetchall())
    finally:
        conn.close()

    results: List[Dict[str, Any]] = []
    import asyncio

    async def _one(rest: Dict[str, Any]) -> Dict[str, Any]:
        rid = int(rest["id"])
        if not int(rest.get("auto_renew_enabled") if rest.get("auto_renew_enabled") is not None else 1):
            intent = create_renewal_intent(rid)
            billing_mail.on_unpaid_invoice(intent["invoice_number"])
            return {"restaurant_id": rid, "status": "invoice_only"}
        card = _default_card(rid)
        if not card:
            intent = create_renewal_intent(rid)
            billing_mail.on_unpaid_invoice(intent["invoice_number"])
            return {"restaurant_id": rid, "status": "no_card"}
        try:
            intent = create_renewal_intent(rid)
            base = (os.getenv("DOMAIN") or "").strip()
            base_url = f"https://{base}" if base else f"http://127.0.0.1:{(os.getenv('PORT') or '8035').strip()}"
            out = await charge_saved_card(int(card["id"]), rid, intent["intent_id"], rest, base_url)
            if out.get("settlement", {}).get("status") == "paid":
                conn2 = db.get_db_connection()
                try:
                    with conn2.cursor() as cur:
                        cur.execute(
                            "UPDATE restaurants SET renewal_retry_count=0, subscription_status='active' WHERE id=%s",
                            (rid,),
                        )
                        conn2.commit()
                finally:
                    conn2.close()
                return {"restaurant_id": rid, "status": "paid"}
            raise ValueError("declined")
        except Exception as exc:
            retries = int(rest.get("renewal_retry_count") or 0) + 1
            status = "expired" if retries >= max_retry else "past_due"
            conn2 = db.get_db_connection()
            try:
                with conn2.cursor() as cur:
                    cur.execute(
                        "UPDATE restaurants SET renewal_retry_count=%s, subscription_status=%s WHERE id=%s",
                        (retries, status, rid),
                    )
                    conn2.commit()
            finally:
                conn2.close()
            billing_mail.send_payment_failed(
                rest,
                f"renew-fail:{rid}:{today.isoformat()}",
                card_last4=str(card.get("last4") or ""),
                card_brand=str(card.get("brand") or ""),
            )
            return {"restaurant_id": rid, "status": "failed", "error": str(exc)[:200]}

    for rest in due:
        try:
            results.append(asyncio.run(_one(rest)))
        except Exception as exc:
            results.append({"restaurant_id": rest["id"], "status": "error", "error": str(exc)[:200]})
    return results


def maybe_auto_topup(restaurant_id: int) -> None:
    rest = _restaurant_row(restaurant_id)
    if not int(rest.get("auto_topup_enabled") or 0):
        return
    if (rest.get("plan_code") or "") != "points":
        return
    wallet = billing.wallet(restaurant_id)
    if int(wallet.get("balance") or 0) >= int(billing.settings().get("low_points_at") or 100):
        return
    conn = db.get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, price_fils FROM point_packs WHERE deleted_at IS NULL AND active=1 ORDER BY sort, id LIMIT 1"
            )
            pack = cur.fetchone()
    finally:
        conn.close()
    if not pack or not _default_card(restaurant_id):
        return
    create_checkout_intent(restaurant_id, "topup", pack_id=int(pack["id"]))


def verify_pay_link(invoice_number: str, pay_token: str, intent_id: str, *, pending: bool = True) -> Dict[str, Any]:
    invoice = billing.get_invoice(invoice_number)
    expected = (invoice or {}).get("pay_token") or ""
    offered = pay_token or ""
    if not invoice or not offered or len(offered) != len(expected) or not hmac.compare_digest(offered, expected):
        raise ValueError("token")
    lookup = get_intent if pending else _intent_row
    row = lookup(intent_id, int(invoice["restaurant_id"]))
    if (row.get("invoice_number") or "") != invoice_number:
        raise ValueError("intent")
    return invoice


def apple_association_bytes() -> Optional[bytes]:
    path = (os.getenv("MYFATOORAH_APPLE_DOMAIN_ASSOCIATION_PATH") or "").strip()
    if not path or not os.path.isfile(path):
        return None
    with open(path, "rb") as handle:
        return handle.read()
