"""Billing emails. One send per (kind, ref_key). Never raises into the caller."""
import logging
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

import pymysql
from jinja2 import Environment, FileSystemLoader, select_autoescape

from . import billing, database as db, i18n, mailer, owner_board

logger = logging.getLogger("uvicorn.error")
_TEMPLATES = Path(__file__).resolve().parent / "templates"
_env = Environment(loader=FileSystemLoader(str(_TEMPLATES)), autoescape=select_autoescape(["html"]))
_env.globals["t"] = i18n.t
_LOCALIZED = {"payment_received"}
_WD = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
_WEEK = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
_MO = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
_MONTH = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"]
_KINDS = {
    "trial_ending", "renewal_soon", "invoice_new", "invoice_reminder",
    "payment_received", "payment_failed", "points_low",
}
_TONE = {
    "ink": ("#111111", "#ffffff"),
    "ready": ("#e7f8ee", "#15803d"),
    "warn": ("#fdf2e3", "#b45309"),
    "error": ("#fdecea", "#b42318"),
    "grey": ("#f6f6f7", "#5f6368"),
}
_KIND_TONE = {
    "trial_ending": "warn",
    "renewal_soon": "grey",
    "invoice_new": "ink",
    "invoice_reminder": "warn",
    "payment_received": "ready",
    "payment_failed": "error",
    "points_low": "warn",
}


def public_base() -> str:
    import os
    domain = (os.getenv("DOMAIN") or "").strip()
    if domain:
        return f"https://{domain}"
    port = (os.getenv("PORT") or "8035").strip()
    return f"http://127.0.0.1:{port}"


def _as_date(value: Any):
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.date()
    if hasattr(value, "year") and hasattr(value, "month") and hasattr(value, "day"):
        return value
    parsed = db._parse_dt(value)
    return parsed.date() if parsed else None


def _parts(value: Any):
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        local = owner_board._as_bahrain(value)
        return local or value
    if hasattr(value, "year"):
        return value
    return owner_board._as_bahrain(value)


def _arabic_date(when: Any) -> str:
    months = i18n.t("date.monthsLong").split(",")
    return f"{when.day} {months[when.month - 1] if len(months) == 12 else when.month} {when.year}"


def short_date(value: Any) -> str:
    when = _parts(value)
    if not when:
        return "—"
    if i18n.current_lang() == "ar":
        return _arabic_date(when)
    return f"{_WD[when.weekday()]} {when.day} {_MO[when.month - 1]} {when.year}"


def long_date(value: Any) -> str:
    when = _parts(value)
    if not when:
        return "—"
    if i18n.current_lang() == "ar":
        return _arabic_date(when)
    return f"{when.day} {_MONTH[when.month - 1]} {when.year}"


def weekday_name(value: Any) -> str:
    when = _parts(value)
    if not when:
        return "the end of the trial"
    return _WEEK[when.weekday()]


def _money(fils: Any) -> str:
    return billing.fils_fixed(fils)


def _settings() -> Dict[str, str]:
    try:
        return billing.settings()
    except Exception:
        logger.exception("Could not read platform settings")
        return {}


def _plans() -> Dict[str, Dict[str, Any]]:
    try:
        return billing._plans_by_code()
    except Exception:
        logger.exception("Could not read plans")
        return {}


def _plans_from() -> str:
    visible = [plan for plan in _plans().values() if plan.get("active") and not plan.get("deleted_at")]
    if not visible:
        return "—"
    plan = min(visible, key=lambda item: int(item.get("price_fils") or 0))
    price = _money(plan.get("price_fils"))
    if (plan.get("type") or "") == "points":
        return f"BHD {price} per order"
    return f"BHD {price}"


def _yearly_save() -> int:
    plans = _plans()
    monthly = int((plans.get("monthly") or {}).get("price_fils") or 0)
    yearly = int((plans.get("yearly") or {}).get("price_fils") or 0)
    if monthly <= 0 or yearly <= 0:
        return 0
    return monthly * 12 - yearly


def _point_price() -> str:
    plan = _plans().get("points") or {}
    if not plan:
        return ""
    return _money(plan.get("price_fils"))


def _support() -> str:
    return (_settings().get("support_email") or "").strip()


def _seller() -> Dict[str, str]:
    cfg = _settings()
    return {
        "seller_name": cfg.get("seller_name") or "",
        "seller_address": cfg.get("seller_address") or "",
        "support_email": cfg.get("support_email") or "",
    }


def _item_label(invoice: Dict[str, Any]) -> str:
    description = str(invoice.get("description") or "")
    for plan in _plans().values():
        name = str(plan.get("name_en") or "")
        if name and name.lower() in description.lower():
            return f"the {name} plan"
    return description or "your Trings plan"


def _orders(restaurant_id: int) -> int:
    conn = db.get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) AS n FROM orders WHERE restaurant_id=%s", (restaurant_id,))
            return int((cur.fetchone() or {}).get("n") or 0)
    finally:
        conn.close()


def _used_this_month(restaurant_id: int) -> int:
    now = owner_board.bahrain_now()
    start = owner_board._naive_local(now.replace(day=1, hour=0, minute=0, second=0, microsecond=0))
    conn = db.get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT COALESCE(SUM(-delta), 0) AS n FROM point_ledger
                WHERE restaurant_id=%s AND kind='order' AND delta<0 AND created_at>=%s
                """,
                (restaurant_id, start),
            )
            return int((cur.fetchone() or {}).get("n") or 0)
    finally:
        conn.close()


def _days_left(points_left: int, used: int) -> Optional[int]:
    if used <= 0:
        return None
    pace = used / max(1, owner_board.bahrain_now().day)
    if pace <= 0:
        return None
    return max(0, int(round(points_left / pace)))


def _rest_from_invoice(invoice: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": invoice.get("restaurant_id"),
        "name": invoice.get("restaurant_name") or "",
        "email": invoice.get("restaurant_email") or "",
        "slug": invoice.get("restaurant_slug") or "",
        "plan_code": invoice.get("plan_code") or "",
    }


def _base_context(restaurant: Dict[str, Any]) -> Dict[str, Any]:
    seller = _seller()
    base = public_base()
    ctx = {
        "restaurant_name": restaurant.get("name") or "",
        "support_email": seller["support_email"],
        "seller_name": seller["seller_name"],
        "seller_address": seller["seller_address"],
        "button_url": f"{base}/dashboard",
        "attachment_name": "",
        "base_url": base,
    }
    return ctx


def _invoice_context(invoice: Dict[str, Any]) -> Dict[str, Any]:
    amount = _money(invoice.get("total_fils") if invoice.get("total_fils") is not None else invoice.get("amount_fils"))
    number = invoice.get("number") or ""
    token = invoice.get("pay_token") or ""
    base = public_base()
    pay = f"{base}/pay/{number}?t={token}" if number and token else f"{base}/dashboard"
    ctx = {
        "number": number,
        "amount": amount,
        "due_label": short_date(invoice.get("due_at")),
        "due_long": long_date(invoice.get("due_at")),
        "item_label": _item_label(invoice),
        "pay_url": pay,
        "active_until": short_date(invoice.get("period_end")),
    }
    ctx.update(billing.receipt_details(invoice))
    return ctx


def render_billing_email(kind: str, restaurant: Dict[str, Any], ref: Optional[Dict[str, Any]] = None) -> Dict[str, str]:
    ref = dict(ref or {})
    tone_name = "error" if kind == "invoice_reminder" and (ref.get("when") or "") == "overdue" else _KIND_TONE.get(kind, "grey")
    tone_bg, tone_fg = _TONE.get(tone_name, _TONE["grey"])
    ctx = _base_context(restaurant)
    ctx["lang"] = i18n.current_lang()
    ctx["dir"] = i18n.current_dir()
    ctx["tone_bg"] = tone_bg
    ctx["tone_fg"] = tone_fg
    invoice = ref.get("invoice") or {}
    if invoice:
        ctx.update(_invoice_context(invoice))
    ctx["ends_label"] = short_date(ref.get("ends_at"))
    ctx["ends_day"] = weekday_name(ref.get("ends_at"))
    ctx["orders_paged"] = ref.get("orders_paged")
    if ctx["orders_paged"] is None and restaurant.get("id"):
        ctx["orders_paged"] = _orders(int(restaurant["id"]))
    ctx["plans_from"] = ref.get("plans_from") or _plans_from()
    renews = ref.get("renews_on")
    ctx["renews_label"] = short_date(renews)
    ctx["plan_name"] = ref.get("plan_name") or ""
    if not ctx["plan_name"]:
        code = ref.get("plan_code") or restaurant.get("plan_code") or ""
        ctx["plan_name"] = str((_plans().get(code) or {}).get("name_en") or code or "—")
    ctx["plan_amount"] = ref.get("plan_amount") or ""
    if not ctx["plan_amount"]:
        code = ref.get("plan_code") or restaurant.get("plan_code") or ""
        plan = _plans().get(code) or {}
        if plan:
            price = _money(plan.get("price_fils"))
            ctx["plan_amount"] = f"BHD {price} per order" if (plan.get("type") or "") == "points" else f"BHD {price}"
        else:
            ctx["plan_amount"] = "—"
    ctx["card_label"] = ref.get("card_label") or billing.payment_label(
        str(ref.get("card_brand") or ""), str(ref.get("card_last4") or ""), "—"
    )
    ctx["when"] = ref.get("when") or "due"
    ctx["next_try"] = short_date(ref.get("next_try"))
    ctx["pauses_on"] = short_date(ref.get("pauses_on"))
    if ref.get("amount_fils") is not None and not ctx.get("amount"):
        ctx["amount"] = _money(ref.get("amount_fils"))
    if ref.get("paid_with"):
        ctx["paid_with"] = ref["paid_with"]
    if ref.get("active_until") is not None:
        ctx["active_until"] = short_date(ref.get("active_until")) if ref.get("active_until") else "—"
    points_left = ref.get("points_left")
    ctx["points_left"] = points_left if points_left is not None else ""
    used = ref.get("used_month")
    if used is None and restaurant.get("id"):
        used = _used_this_month(int(restaurant["id"]))
    ctx["used_month"] = used if used is not None else 0
    days = ref.get("days_left")
    if days is None and points_left is not None:
        days = _days_left(int(points_left), int(ctx["used_month"] or 0))
    ctx["days_left"] = days
    ctx["days_left_label"] = f"{days} day" if days == 1 else (f"{days} days" if days is not None else "")
    point_price = ref.get("point_price")
    if point_price is None:
        point_price = _point_price()
    ctx["point_price"] = point_price
    ctx["point_value"] = f"1 order · BHD {point_price}" if point_price else "1 order"
    save = ref.get("yearly_save_fils")
    if save is None:
        save = _yearly_save()
    ctx["yearly_save"] = _money(save) if save and int(save) > 0 else ""
    if kind in ("invoice_new", "invoice_reminder", "payment_received"):
        number = (invoice or {}).get("number") or ref.get("number") or ""
        if number:
            ctx["attachment_name"] = f"Trings-Receipt-{number}.pdf" if kind == "payment_received" else f"Trings-Invoice-{number}.pdf"
    if kind in ("invoice_new", "invoice_reminder") and ctx.get("pay_url"):
        ctx["button_url"] = ctx["pay_url"]
    html = _env.get_template(f"emails/{kind}.html").render(**ctx)
    text = _env.get_template(f"emails/{kind}.txt").render(**ctx)
    return {"subject": subject_for(kind, ctx), "html": html, "text": text, "attachment_name": ctx.get("attachment_name") or ""}


def subject_for(kind: str, ctx: Dict[str, Any]) -> str:
    number = ctx.get("number") or ""
    amount = ctx.get("amount") or ""
    if kind == "trial_ending":
        return "Your Trings trial ends tomorrow"
    if kind == "renewal_soon":
        return f"Your Trings plan renews on {ctx.get('renews_label') or ''}".strip()
    if kind == "invoice_new":
        return f"Invoice {number} from Trings, BHD {amount}".strip()
    if kind == "invoice_reminder":
        if ctx.get("when") == "overdue":
            return f"Reminder: invoice {number} was due on {ctx.get('due_label') or ''}".strip()
        return f"Reminder: invoice {number} is due tomorrow"
    if kind == "payment_received":
        return i18n.t("receipt.subject").replace("{number}", number)
    if kind == "payment_failed":
        return "We couldn't charge your card for Trings"
    if kind == "points_low":
        return f"You have {ctx.get('points_left')} points left on Trings"
    return "Trings"


def _claim(restaurant_id: Optional[int], kind: str, ref_key: str, to_email: str) -> Optional[int]:
    conn = db.get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO email_log (restaurant_id, kind, ref_key, to_email, status, error, created_at)
                VALUES (%s, %s, %s, %s, 'skipped', NULL, %s)
                """,
                (restaurant_id, kind, ref_key[:120], (to_email or "")[:190], datetime.now()),
            )
            row_id = int(cur.lastrowid)
            conn.commit()
            return row_id
    except pymysql.err.IntegrityError:
        return None
    finally:
        conn.close()


def _finish(row_id: int, status: str, error: str) -> None:
    conn = db.get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE email_log SET status=%s, error=%s WHERE id=%s",
                (status, (error or None), row_id),
            )
            conn.commit()
    finally:
        conn.close()


def _exists(kind: str, ref_key: str) -> bool:
    conn = db.get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM email_log WHERE kind=%s AND ref_key=%s LIMIT 1", (kind, ref_key))
            return cur.fetchone() is not None
    except Exception:
        return False
    finally:
        conn.close()


def send_billing_email(kind: str, restaurant: Dict[str, Any], ref: Optional[Dict[str, Any]] = None, attach_pdf_of: Any = None) -> Dict[str, str]:
    ref = dict(ref or {})
    token = i18n.activate((ref.get("lang") if kind in _LOCALIZED else "") or "en")
    try:
        return _send_billing_email(kind, restaurant, ref, attach_pdf_of)
    finally:
        i18n.reset(token)


def _send_billing_email(kind: str, restaurant: Dict[str, Any], ref: Dict[str, Any], attach_pdf_of: Any) -> Dict[str, str]:
    ref_key = str(ref.get("ref_key") or "")
    try:
        if kind not in _KINDS:
            return {"status": "failed", "error": "unknown email", "response": "", "kind": kind, "ref_key": ref_key}
        if not ref_key:
            return {"status": "failed", "error": "missing ref", "response": "", "kind": kind, "ref_key": ""}
        rendered = render_billing_email(kind, restaurant, ref)
        attachments = []
        if attach_pdf_of and rendered["attachment_name"]:
            from .admin_panel import invoice_pdf_bytes
            attachments.append((rendered["attachment_name"], invoice_pdf_bytes(attach_pdf_of), "application/pdf"))
        to_email = str(restaurant.get("email") or "").strip()
        restaurant_id = restaurant.get("id")
        row_id = _claim(int(restaurant_id) if restaurant_id else None, kind, ref_key, to_email)
        if row_id is None:
            return {"status": "skipped", "error": "already sent", "response": "", "kind": kind, "ref_key": ref_key}
        if "@" not in to_email:
            _finish(row_id, "skipped", "no owner email")
            return {"status": "skipped", "error": "no owner email", "response": "", "kind": kind, "ref_key": ref_key}
        try:
            result = mailer.deliver(
                to_email,
                rendered["subject"],
                rendered["html"],
                rendered["text"],
                reply_to=_support(),
                attachments=attachments,
            )
        except Exception as exc:
            _finish(row_id, "failed", str(exc)[:500])
            return {"status": "failed", "error": str(exc)[:500], "response": "", "kind": kind, "ref_key": ref_key}
        _finish(row_id, result["status"], result.get("error") or "")
        result["kind"] = kind
        result["ref_key"] = ref_key
        return result
    except Exception as exc:
        logger.exception("Could not send %s", kind)
        return {"status": "failed", "error": str(exc)[:500], "response": "", "kind": kind, "ref_key": ref_key}


def _spawn(target, *args) -> None:
    def run():
        try:
            target(*args)
        except Exception:
            logger.exception("billing email")
    threading.Thread(target=run, daemon=True).start()


def on_unpaid_invoice(number: str) -> None:
    _spawn(_send_invoice_new, number)


def on_invoice_paid(number: str) -> None:
    _spawn(_send_receipt, number, i18n.current_lang())


def _send_invoice_new(number: str) -> None:
    invoice = billing.get_invoice(number)
    if not invoice or (invoice.get("status") or "") != "unpaid":
        return
    send_billing_email(
        "invoice_new",
        _rest_from_invoice(invoice),
        {"invoice": invoice, "ref_key": f"inv:{invoice['id']}"},
        attach_pdf_of=invoice,
    )


def _send_receipt(number: str, lang: str = "en") -> None:
    invoice = billing.get_invoice(number)
    if not invoice or (invoice.get("status") or "") != "paid":
        return
    send_billing_email(
        "payment_received",
        _rest_from_invoice(invoice),
        {"invoice": invoice, "ref_key": f"inv:{invoice['id']}", "lang": lang},
        attach_pdf_of=invoice,
    )


def on_points_change(restaurant_id: int, change: Dict[str, Any]) -> None:
    if (change.get("mode") or "prepaid") != "prepaid":
        return
    try:
        low = int(_settings().get("low_points_at") or "100")
    except ValueError:
        low = 100
    before, after = int(change.get("before") or 0), int(change.get("after") or 0)
    if before < low or after >= low:
        return
    ledger_id = int(change.get("id") or 0)
    if not ledger_id:
        return
    _spawn(_send_points_low, int(restaurant_id), ledger_id, after)


def _send_points_low(restaurant_id: int, ledger_id: int, points_left: int) -> None:
    conn = db.get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id, name, email, slug, plan_code FROM restaurants WHERE id=%s", (restaurant_id,))
            restaurant = cur.fetchone()
    finally:
        conn.close()
    if not restaurant:
        return
    send_billing_email(
        "points_low",
        restaurant,
        {"ref_key": f"pts:crossing:{ledger_id}", "points_left": points_left},
    )


def send_payment_failed(
    restaurant: Dict[str, Any],
    ref_key: str,
    amount_fils: Optional[int] = None,
    card_last4: str = "",
    card_brand: str = "",
    next_try: Any = None,
    pauses_on: Any = None,
) -> Dict[str, str]:
    if amount_fils is None:
        code = restaurant.get("plan_code") or "monthly"
        amount_fils = int((_plans().get(code) or {}).get("price_fils") or 0)
    return send_billing_email("payment_failed", restaurant, {
        "ref_key": ref_key,
        "amount_fils": amount_fils,
        "card_last4": card_last4,
        "card_brand": card_brand,
        "next_try": next_try,
        "pauses_on": pauses_on,
        "plan_code": restaurant.get("plan_code") or "",
    })


def send_renewal_soon(
    restaurant: Dict[str, Any],
    renews_on: Any,
    card_last4: str = "",
    card_brand: str = "",
    ref_key: str = "",
) -> Dict[str, str]:
    stamp = renews_on.isoformat() if hasattr(renews_on, "isoformat") else str(renews_on)[:10]
    return send_billing_email("renewal_soon", restaurant, {
        "ref_key": ref_key or f"renew:{restaurant.get('id')}:{stamp}",
        "renews_on": renews_on,
        "card_last4": card_last4,
        "card_brand": card_brand,
        "plan_code": restaurant.get("plan_code") or "",
    })


def resend_invoice(invoice: Dict[str, Any]) -> Dict[str, str]:
    kind = "payment_received" if (invoice.get("status") or "") == "paid" else "invoice_new"
    return send_billing_email(
        kind,
        _rest_from_invoice(invoice),
        {"invoice": invoice, "ref_key": f"inv:{invoice['id']}:again:{int(time.time())}", "lang": i18n.current_lang()},
        attach_pdf_of=invoice,
    )


def send_admin_test(to_email: str, restaurant_name: str = "Sample") -> Dict[str, str]:
    monthly = _plans().get("monthly") or {}
    fils = int(monthly.get("price_fils") or 0)
    name = monthly.get("name_en") or "Monthly"
    now = datetime.now()
    invoice = {
        "id": 0,
        "number": "SAMPLE",
        "status": "paid",
        "total_fils": fils,
        "subtotal_fils": fils,
        "amount_fils": fils,
        "description": f"Trings {name}",
        "line_items": [{"description": f"Trings {name}", "detail": "", "qty": 1, "amount_fils": fils}],
        "paid_method": "card",
        "issued_at": now,
        "paid_at": now,
        "due_at": now,
        "pay_token": "",
        "restaurant_name": restaurant_name,
        "period_end": None,
    }
    return send_billing_email(
        "payment_received",
        {"id": None, "name": restaurant_name, "email": to_email, "slug": ""},
        {"invoice": invoice, "ref_key": f"test:{time.time_ns()}", "lang": i18n.current_lang()},
        attach_pdf_of=invoice,
    )


def admin_status() -> Dict[str, Any]:
    out = {"configured": mailer.smtp_configured(), "last_sent": "", "last_error": ""}
    conn = db.get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT kind, created_at FROM email_log WHERE status='sent' ORDER BY id DESC LIMIT 1")
            sent = cur.fetchone()
            cur.execute(
                "SELECT error, created_at FROM email_log WHERE status='failed' AND error IS NOT NULL AND error<>'' ORDER BY id DESC LIMIT 1"
            )
            failed = cur.fetchone()
    except Exception:
        logger.exception("Could not read email status")
        return out
    finally:
        conn.close()
    if sent:
        when = owner_board._as_bahrain(sent["created_at"])
        stamp = when.strftime("%d %b %H:%M") if when else ""
        out["last_sent"] = f"{sent['kind']} · {stamp}".strip(" ·")
    if failed:
        when = owner_board._as_bahrain(failed["created_at"])
        stamp = when.strftime("%d %b %H:%M") if when else ""
        text = str(failed.get("error") or "").strip()
        out["last_error"] = f"{text} · {stamp}".strip(" ·") if text else stamp
    return out


def _saved_cards() -> List[Dict[str, Any]]:
    conn = db.get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SHOW TABLES LIKE 'saved_cards'")
            if not cur.fetchone():
                return []
            cur.execute("SHOW COLUMNS FROM saved_cards")
            columns = {row.get("Field") or row.get("field") for row in cur.fetchall()}
            last = "last4" if "last4" in columns else "card_last4" if "card_last4" in columns else ""
            if "restaurant_id" not in columns or not last:
                return []
            brand = "brand" if "brand" in columns else ""
            sql = f"SELECT restaurant_id, `{last}` AS last4"
            sql += f", `{brand}` AS brand" if brand else ", '' AS brand"
            sql += " FROM saved_cards"
            cur.execute(sql)
            return list(cur.fetchall())
    except Exception:
        logger.exception("Could not read saved cards")
        return []
    finally:
        conn.close()


def run_daily() -> List[Dict[str, str]]:
    db.init_db()
    sent: List[Dict[str, str]] = []
    sent.extend(_daily_trials())
    sent.extend(_daily_reminders())
    sent.extend(_daily_renewals())
    try:
        from . import payments as pay
        pay.run_auto_renewals()
    except Exception:
        logger.exception("auto renewal job failed")
    return [row for row in sent if row.get("error") != "already sent"]


def _daily_trials() -> List[Dict[str, str]]:
    now = datetime.now()
    soon = now + timedelta(hours=24)
    conn = db.get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, name, email, slug, plan_code, trial_ends_at
                FROM restaurants
                WHERE subscription_status='trialing' AND suspended_at IS NULL
                  AND trial_ends_at IS NOT NULL AND trial_ends_at>%s AND trial_ends_at<=%s
                """,
                (now, soon),
            )
            rows = list(cur.fetchall())
    finally:
        conn.close()
    sent = []
    for restaurant in rows:
        ends = restaurant.get("trial_ends_at")
        stamp = ends.strftime("%Y-%m-%d") if isinstance(ends, datetime) else str(ends)[:10]
        ref_key = f"trial:{restaurant['id']}:{stamp}"
        if _exists("trial_ending", ref_key):
            continue
        result = send_billing_email("trial_ending", restaurant, {"ref_key": ref_key, "ends_at": ends, "orders_paged": _orders(int(restaurant["id"]))})
        if result.get("error") != "already sent":
            sent.append(result)
    return sent


def _daily_reminders() -> List[Dict[str, str]]:
    today = owner_board.bahrain_now().date()
    want = {today + timedelta(days=1): "due", today - timedelta(days=1): "overdue"}
    conn = db.get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT number, due_at FROM invoices WHERE status='unpaid' AND due_at IS NOT NULL")
            rows = list(cur.fetchall())
    finally:
        conn.close()
    sent = []
    for row in rows:
        due = _as_date(row.get("due_at"))
        when = want.get(due)
        if not when:
            continue
        invoice = billing.get_invoice(row["number"])
        if not invoice or invoice.get("status") != "unpaid":
            continue
        ref_key = f"inv:{invoice['id']}:{when}"
        if _exists("invoice_reminder", ref_key):
            continue
        result = send_billing_email(
            "invoice_reminder",
            _rest_from_invoice(invoice),
            {"invoice": invoice, "ref_key": ref_key, "when": when},
            attach_pdf_of=invoice,
        )
        if result.get("error") != "already sent":
            sent.append(result)
    return sent


def _daily_renewals() -> List[Dict[str, str]]:
    cards = _saved_cards()
    if not cards:
        return []
    by_rest = {}
    for card in cards:
        by_rest.setdefault(int(card["restaurant_id"]), card)
    target = owner_board.bahrain_now().date() + timedelta(days=3)
    if not by_rest:
        return []
    conn = db.get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, name, email, slug, plan_code, manual_plan_until
                FROM restaurants
                WHERE subscription_status='active' AND suspended_at IS NULL AND manual_plan_until=%s
                  AND id IN %s
                """,
                (target, tuple(by_rest)),
            )
            rows = list(cur.fetchall())
    finally:
        conn.close()
    sent = []
    for restaurant in rows:
        card = by_rest.get(int(restaurant["id"])) or {}
        result = send_renewal_soon(
            restaurant,
            restaurant.get("manual_plan_until"),
            str(card.get("last4") or ""),
            str(card.get("brand") or ""),
        )
        if result.get("error") != "already sent":
            sent.append(result)
    return sent
