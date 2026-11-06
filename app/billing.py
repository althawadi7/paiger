"""Invoices, point wallets, and platform admin numbers. Money is integer fils."""
import json
import logging
import re
import secrets
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

from . import database as db
from . import owner_board

BAHRAIN = ZoneInfo("Asia/Bahrain")
_log = logging.getLogger("uvicorn.error")


def bhd(fils: Optional[int]) -> str:
    text = f"{int(fils or 0) / 1000:.3f}".rstrip("0").rstrip(".")
    return text or "0"


def fils_fixed(fils: Optional[int]) -> str:
    return f"{int(fils or 0) / 1000:.3f}"


def parse_bhd(value: str) -> int:
    cleaned = (value or "").strip().replace(",", "")
    if not cleaned:
        raise ValueError("amount")
    negative = cleaned.startswith("-")
    if negative:
        cleaned = cleaned[1:]
    whole, _, frac = cleaned.partition(".")
    if not whole.isdigit() or (frac and not frac.isdigit()) or len(frac) > 3:
        raise ValueError("amount")
    fils = int(whole) * 1000 + int((frac + "000")[:3])
    return -fils if negative else fils


def settings() -> Dict[str, str]:
    conn = db.get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT skey, svalue FROM platform_settings")
            return {row["skey"]: row["svalue"] for row in cur.fetchall()}
    finally:
        conn.close()


def save_settings(values: Dict[str, str]) -> None:
    conn = db.get_db_connection()
    try:
        with conn.cursor() as cur:
            for key, value in values.items():
                cur.execute(
                    """
                    INSERT INTO platform_settings (skey, svalue) VALUES (%s, %s)
                    ON DUPLICATE KEY UPDATE svalue=VALUES(svalue)
                    """,
                    (key, value),
                )
            conn.commit()
        owner_board.clear_public_cache()
    finally:
        conn.close()


def audit(admin_id: int, action: str, restaurant_id: Optional[int], details: Dict[str, Any], ip: str) -> None:
    conn = db.get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO admin_audit_log (admin_id, restaurant_id, action, details, ip, created_at)
                VALUES (%s, %s, %s, %s, %s, %s)
                """,
                (admin_id, restaurant_id, action, json.dumps(details, ensure_ascii=False)[:4000], (ip or "")[:64], datetime.now()),
            )
            conn.commit()
    finally:
        conn.close()


def restaurant_status(rest: Dict[str, Any]) -> str:
    if rest.get("suspended_at"):
        return "suspended"
    raw = (rest.get("subscription_status") or "legacy").strip() or "legacy"
    if raw == "trialing":
        ends = db._parse_dt(rest.get("trial_ends_at"))
        if ends and ends <= datetime.now():
            return "expired"
        return "trialing"
    if raw in ("active", "past_due", "expired", "legacy"):
        return raw
    return raw or "legacy"


def _plans_by_code(include_deleted: bool = False) -> Dict[str, Dict[str, Any]]:
    """Live plans by default. Code checks pass include_deleted so a deleted code stays reserved."""
    conn = db.get_db_connection()
    try:
        with conn.cursor() as cur:
            sql = "SELECT * FROM plans"
            if not include_deleted:
                sql += " WHERE deleted_at IS NULL"
            cur.execute(sql)
            return {row["code"]: row for row in cur.fetchall()}
    finally:
        conn.close()


def overview() -> Dict[str, Any]:
    plans = _plans_by_code()
    conn = db.get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id, plan_code, subscription_status, trial_ends_at, suspended_at, name, slug FROM restaurants")
            restaurants = list(cur.fetchall())
    finally:
        conn.close()
    paying = {"monthly": 0, "yearly": 0, "points": 0}
    mrr = 0
    trialing = 0
    ending = 0
    soon = datetime.now() + timedelta(hours=24)
    counts = {key: 0 for key in ("all", "active", "trialing", "past_due", "expired", "suspended", "legacy")}
    for rest in restaurants:
        status = restaurant_status(rest)
        counts["all"] += 1
        counts[status] = counts.get(status, 0) + 1
        if status == "active":
            code = (rest.get("plan_code") or "monthly").strip()
            if code in paying:
                paying[code] += 1
            plan = plans.get(code) or {}
            if code == "monthly":
                mrr += int(plan.get("price_fils") or 0)
            elif code == "yearly":
                mrr += int(plan.get("price_fils") or 0) // 12
        if status == "trialing":
            trialing += 1
            ends = db._parse_dt(rest.get("trial_ends_at"))
            if ends and datetime.now() < ends <= soon:
                ending += 1
    start, end = owner_board.day_window(0, 1)
    conn = db.get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) AS n FROM orders WHERE created_at>=%s AND created_at<%s", (start, end))
            orders_today = int((cur.fetchone() or {}).get("n") or 0)
            cur.execute(
                "SELECT COUNT(DISTINCT restaurant_id) AS n FROM orders WHERE created_at>=%s AND created_at<%s",
                (start, end),
            )
            restaurants_today = int((cur.fetchone() or {}).get("n") or 0)
    finally:
        conn.close()
    months = _revenue_months()
    return {
        "mrr": bhd(mrr),
        "paying": paying["monthly"] + paying["yearly"] + paying["points"],
        "paying_monthly": paying["monthly"],
        "paying_yearly": paying["yearly"],
        "paying_points": paying["points"],
        "trialing": trialing,
        "ending": ending,
        "orders_today": orders_today,
        "restaurants_today": restaurants_today,
        "months": months,
        "counts": counts,
        "attention": _attention(restaurants, plans),
    }


def _revenue_months() -> List[Dict[str, Any]]:
    now = owner_board.bahrain_now()
    points = []
    year, month = now.year, now.month
    for _ in range(5):
        month -= 1
        if month == 0:
            month = 12
            year -= 1
        points.append((year, month))
    points.append((now.year, now.month))
    points.sort()
    conn = db.get_db_connection()
    out = []
    try:
        with conn.cursor() as cur:
            for index, (year, month) in enumerate(points):
                start = datetime(year, month, 1, tzinfo=BAHRAIN)
                end = datetime(year + (month == 12), 1 if month == 12 else month + 1, 1, tzinfo=BAHRAIN)
                cur.execute(
                    """
                    SELECT COALESCE(SUM(total_fils), 0) AS n
                    FROM invoices
                    WHERE status='paid' AND paid_at>=%s AND paid_at<%s
                    """,
                    (owner_board._naive_local(start), owner_board._naive_local(end)),
                )
                fils = int((cur.fetchone() or {}).get("n") or 0)
                out.append({
                    "label": start.strftime("%b"),
                    "fils": fils,
                    "bhd": fils_fixed(fils),
                    "current": index == len(points) - 1,
                })
    finally:
        conn.close()
    return out


def _attention(restaurants: List[Dict[str, Any]], plans: Dict[str, Dict[str, Any]]) -> List[Dict[str, Any]]:
    items = []
    low = int(settings().get("low_points_at") or "100")
    conn = db.get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT i.number, i.total_fils, r.name, r.id
                FROM invoices i JOIN restaurants r ON r.id=i.restaurant_id
                WHERE i.status='unpaid' ORDER BY i.id DESC LIMIT 8
                """
            )
            for row in cur.fetchall():
                items.append({
                    "tone": "r", "title": row["name"],
                    "text": f"Unpaid {row['number']} · BHD {fils_fixed(row['total_fils'])}",
                    "href": f"/admin/restaurants?open={row['id']}",
                })
            soon = datetime.now() + timedelta(hours=24)
            for rest in restaurants:
                if restaurant_status(rest) != "trialing":
                    continue
                ends = db._parse_dt(rest.get("trial_ends_at"))
                if ends and datetime.now() < ends <= soon:
                    items.append({
                        "tone": "a", "title": rest["name"],
                        "text": "Trial ends within 24 hours",
                        "href": f"/admin/restaurants?open={rest['id']}",
                    })
            cur.execute(
                """
                SELECT w.restaurant_id, w.balance, r.name
                FROM point_wallets w JOIN restaurants r ON r.id=w.restaurant_id
                WHERE w.mode='prepaid' AND w.balance < %s AND r.suspended_at IS NULL
                """,
                (low,),
            )
            for row in cur.fetchall():
                items.append({
                    "tone": "a", "title": row["name"],
                    "text": f"Prepaid balance {row['balance']}",
                    "href": f"/admin/restaurants?open={row['restaurant_id']}",
                })
            week = owner_board.day_window(length_days=7)[0]
            cur.execute(
                """
                SELECT r.id, r.name FROM restaurants r
                WHERE r.suspended_at IS NULL AND r.subscription_status='active'
                  AND NOT EXISTS (
                    SELECT 1 FROM orders o WHERE o.restaurant_id=r.id AND o.created_at>=%s
                  )
                LIMIT 8
                """,
                (week,),
            )
            for row in cur.fetchall():
                items.append({
                    "tone": "g", "title": row["name"],
                    "text": "No orders in 7 days",
                    "href": f"/admin/restaurants?open={row['id']}",
                })
    finally:
        conn.close()
    return items[:12]


def list_restaurants(status: str, query: str, page: int) -> Dict[str, Any]:
    conn = db.get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT r.*, (
                    SELECT COUNT(*) FROM orders o
                    WHERE o.restaurant_id=r.id AND o.created_at>=%s
                ) AS orders_30d
                FROM restaurants r ORDER BY r.id DESC
                """,
                (datetime.now() - timedelta(days=30),),
            )
            rows = list(cur.fetchall())
            cur.execute("SELECT restaurant_id, balance, mode FROM point_wallets")
            wallets = {row["restaurant_id"]: row for row in cur.fetchall()}
    finally:
        conn.close()
    counts = {key: 0 for key in ("all", "active", "trialing", "past_due", "expired", "suspended", "legacy")}
    prepared = []
    needle = (query or "").strip().lower()
    for row in rows:
        state = restaurant_status(row)
        counts["all"] += 1
        counts[state] = counts.get(state, 0) + 1
        blob = " ".join([str(row.get("name") or ""), str(row.get("slug") or ""), str(row.get("email") or "")]).lower()
        if needle and needle not in blob:
            continue
        if status and status != "all" and state != status:
            continue
        wallet = wallets.get(row["id"])
        prepared.append(_restaurant_row(row, state, wallet))
    total = len(prepared)
    page = max(1, page)
    start = (page - 1) * 50
    return {
        "rows": prepared[start:start + 50],
        "all_rows": prepared,
        "total": total,
        "page": page,
        "pages": max(1, (total + 49) // 50),
        "counts": counts,
    }


def _restaurant_row(row: Dict[str, Any], state: str, wallet: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    renew = row.get("manual_plan_until") or row.get("trial_ends_at")
    when = owner_board._as_bahrain(renew) if not hasattr(renew, "year") or isinstance(renew, datetime) else None
    if hasattr(renew, "year") and not isinstance(renew, datetime):
        label = renew.isoformat()
    else:
        shown = owner_board._as_bahrain(renew)
        label = shown.strftime("%d %b %Y") if shown else "—"
    return {
        "id": row["id"],
        "name": row.get("name") or "",
        "slug": row.get("slug") or "",
        "email": row.get("email") or "",
        "phone": row.get("phone") or "",
        "status": state,
        "plan_code": row.get("plan_code") or "",
        "renew": label,
        "orders_30d": int(row.get("orders_30d") or 0),
        "joined": owner_board._as_bahrain(row.get("created_at")).strftime("%d %b %Y") if row.get("created_at") else "",
        "initial": (row.get("name") or "P")[:1],
        "points": None if not wallet else int(wallet["balance"]),
        "wallet_mode": None if not wallet else wallet["mode"],
        "suspended": bool(row.get("suspended_at")),
    }


def restaurant_detail(restaurant_id: int) -> Optional[Dict[str, Any]]:
    rest = db.get_restaurant_by_id(restaurant_id)
    if not rest:
        conn = db.get_db_connection()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM restaurants WHERE id=%s", (restaurant_id,))
                raw = cur.fetchone()
        finally:
            conn.close()
        if not raw:
            return None
        rest = raw
    else:
        conn = db.get_db_connection()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM restaurants WHERE id=%s", (restaurant_id,))
                raw = cur.fetchone() or rest
        finally:
            conn.close()
    conn = db.get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT COUNT(*) AS n FROM orders WHERE restaurant_id=%s AND created_at>=%s",
                (restaurant_id, datetime.now() - timedelta(days=30)),
            )
            orders_30 = int((cur.fetchone() or {}).get("n") or 0)
            cur.execute("SELECT balance, mode FROM point_wallets WHERE restaurant_id=%s", (restaurant_id,))
            wallet = cur.fetchone()
    finally:
        conn.close()
    state = restaurant_status(raw)
    shown = owner_board._as_bahrain(raw.get("manual_plan_until") or raw.get("trial_ends_at"))
    if hasattr(raw.get("manual_plan_until"), "isoformat") and not isinstance(raw.get("manual_plan_until"), datetime):
        renew = raw["manual_plan_until"].isoformat()
    else:
        renew = shown.strftime("%d %b %Y") if shown else "—"
    history = _history(restaurant_id)
    return {
        "id": raw["id"],
        "name": raw.get("name") or "",
        "slug": raw.get("slug") or "",
        "email": raw.get("email") or "",
        "phone": raw.get("phone") or "",
        "status": state,
        "plan_code": raw.get("plan_code") or "",
        "renew": renew,
        "orders_30d": orders_30,
        "points": None if not wallet else int(wallet["balance"]),
        "card": "none",
        "suspended": bool(raw.get("suspended_at")),
        "initial": (raw.get("name") or "P")[:1],
        "history": history,
    }


def _history(restaurant_id: int) -> List[Dict[str, str]]:
    events = []
    conn = db.get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT created_at, action, details FROM admin_audit_log
                WHERE restaurant_id=%s ORDER BY id DESC LIMIT 20
                """,
                (restaurant_id,),
            )
            for row in cur.fetchall():
                when = owner_board._as_bahrain(row["created_at"])
                events.append((row["created_at"], when.strftime("%d %b %H:%M") if when else "", row["action"]))
            cur.execute(
                """
                SELECT created_at, number, status, total_fils FROM invoices
                WHERE restaurant_id=%s ORDER BY id DESC LIMIT 20
                """,
                (restaurant_id,),
            )
            for row in cur.fetchall():
                when = owner_board._as_bahrain(row["created_at"])
                events.append((
                    row["created_at"],
                    when.strftime("%d %b %H:%M") if when else "",
                    f"{row.get('number') or 'Invoice'} {row['status']} BHD {fils_fixed(row.get('total_fils'))}",
                ))
            try:
                cur.execute(
                    """
                    SELECT created_at, kind, status FROM email_log
                    WHERE restaurant_id=%s ORDER BY id DESC LIMIT 20
                    """,
                    (restaurant_id,),
                )
                mail_rows = list(cur.fetchall())
            except Exception:
                mail_rows = []
            from . import i18n
            for row in mail_rows:
                when = owner_board._as_bahrain(row["created_at"])
                kind = i18n.t(f"admin.mail.{row['kind']}")
                state = i18n.t(f"admin.mailSt.{row['status']}")
                events.append((row["created_at"], when.strftime("%d %b %H:%M") if when else "", f"{kind} · {state}"))
    finally:
        conn.close()
    events.sort(key=lambda item: item[0] or datetime.min, reverse=True)
    return [{"when": when, "text": text} for _, when, text in events[:20]]


def extend_trial(restaurant_id: int, days: int) -> None:
    if days < 1 or days > 60:
        raise ValueError("days")
    conn = db.get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT trial_ends_at, subscription_status FROM restaurants WHERE id=%s", (restaurant_id,))
            row = cur.fetchone()
            if not row:
                raise ValueError("missing")
            ends = db._parse_dt(row.get("trial_ends_at"))
            base = ends if ends and ends > datetime.now() else datetime.now()
            new_end = base + timedelta(days=days)
            status = row.get("subscription_status") or "trialing"
            if status in ("expired", "past_due") or (ends and ends <= datetime.now()):
                status = "trialing"
            elif status != "trialing":
                status = "trialing"
            cur.execute(
                "UPDATE restaurants SET trial_ends_at=%s, subscription_status=%s WHERE id=%s",
                (new_end, status, restaurant_id),
            )
            conn.commit()
    finally:
        conn.close()


def set_manual_plan(restaurant_id: int, code: str, until_date: str, admin_id: int) -> str:
    if code not in ("monthly", "yearly", "points"):
        raise ValueError("plan")
    try:
        until = datetime.strptime(until_date, "%Y-%m-%d").date()
    except ValueError:
        raise ValueError("date")
    plans = _plans_by_code()
    plan = plans.get(code)
    if not plan:
        raise ValueError("plan")
    conn = db.get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE restaurants
                SET plan_code=%s, subscription_status='active', manual_plan_until=%s
                WHERE id=%s
                """,
                (code, until, restaurant_id),
            )
            conn.commit()
    finally:
        conn.close()
    price = int(plan.get("price_fils") or 0)
    name = plan.get("name_en") or code
    return create_invoice(
        restaurant_id,
        kind="manual",
        description=f"Paiger {name}",
        lines=[{"description": f"Paiger {name}", "detail": f"Until {until.isoformat()}", "qty": 1, "amount_fils": price, "plan_code": code}],
        due_at=datetime.now() + timedelta(days=7),
        period_end=until,
        admin_id=admin_id,
    )


def adjust_points(restaurant_id: int, delta: int, reason: str, admin_id: int) -> int:
    if not delta or not (reason or "").strip():
        raise ValueError("points")
    conn = db.get_db_connection()
    try:
        with conn.cursor() as cur:
            change = _ledger(cur, restaurant_id, delta, reason.strip()[:255], "adjust", None, admin_id)
            cur.execute("SELECT balance FROM point_wallets WHERE restaurant_id=%s", (restaurant_id,))
            balance = int((cur.fetchone() or {}).get("balance") or 0)
            conn.commit()
    finally:
        conn.close()
    _queue_points_email(restaurant_id, change)
    return balance


def set_legacy_plan(restaurant_id: int) -> None:
    conn = db.get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE restaurants
                SET plan_code=NULL, subscription_status='legacy', trial_ends_at=NULL, manual_plan_until=NULL
                WHERE id=%s
                """,
                (restaurant_id,),
            )
            conn.commit()
    finally:
        conn.close()


def ensure_wallet(restaurant_id: int) -> None:
    conn = db.get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO point_wallets (restaurant_id, balance, mode, updated_at)
                VALUES (%s, 0, 'prepaid', %s)
                ON DUPLICATE KEY UPDATE restaurant_id=restaurant_id
                """,
                (restaurant_id, datetime.now()),
            )
            conn.commit()
    finally:
        conn.close()


def set_suspended(restaurant_id: int, suspended: bool) -> None:
    conn = db.get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE restaurants SET suspended_at=%s WHERE id=%s",
                (datetime.now() if suspended else None, restaurant_id),
            )
            conn.commit()
    finally:
        conn.close()


def _ledger(cur, restaurant_id: int, delta: int, reason: str, kind: str, ref_invoice_id: Optional[int], admin_id: Optional[int]) -> Dict[str, Any]:
    cur.execute("SELECT balance, mode FROM point_wallets WHERE restaurant_id=%s", (restaurant_id,))
    existing = cur.fetchone()
    cur.execute("SELECT COALESCE(SUM(delta), 0) AS n FROM point_ledger WHERE restaurant_id=%s", (restaurant_id,))
    before = int((cur.fetchone() or {}).get("n") or 0)
    cur.execute(
        """
        INSERT INTO point_ledger (restaurant_id, delta, reason, kind, ref_invoice_id, admin_id, created_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        """,
        (restaurant_id, delta, reason, kind, ref_invoice_id, admin_id, datetime.now()),
    )
    ledger_id = int(cur.lastrowid or 0)
    cur.execute("SELECT COALESCE(SUM(delta), 0) AS n FROM point_ledger WHERE restaurant_id=%s", (restaurant_id,))
    balance = int((cur.fetchone() or {}).get("n") or 0)
    cur.execute(
        """
        INSERT INTO point_wallets (restaurant_id, balance, mode, updated_at)
        VALUES (%s, %s, 'prepaid', %s)
        ON DUPLICATE KEY UPDATE balance=VALUES(balance), updated_at=VALUES(updated_at)
        """,
        (restaurant_id, balance, datetime.now()),
    )
    return {
        "before": before,
        "after": balance,
        "id": ledger_id,
        "mode": (existing or {}).get("mode") or "prepaid",
    }


def _queue_points_email(restaurant_id: int, change: Optional[Dict[str, Any]]) -> None:
    if not change:
        return
    try:
        from . import billing_mail
        billing_mail.on_points_change(restaurant_id, change)
    except Exception:
        _log.exception("Could not queue the points email")


def _queue_invoice_email(number: str) -> None:
    try:
        from . import billing_mail
        billing_mail.on_unpaid_invoice(number)
    except Exception:
        _log.exception("Could not queue the invoice email")


def _queue_receipt_email(number: str) -> None:
    try:
        from . import billing_mail
        billing_mail.on_invoice_paid(number)
    except Exception:
        _log.exception("Could not queue the receipt email")


def wallet(restaurant_id: int) -> Dict[str, Any]:
    conn = db.get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT balance, mode FROM point_wallets WHERE restaurant_id=%s", (restaurant_id,))
            row = cur.fetchone()
            if row:
                return {"balance": int(row["balance"]), "mode": row["mode"]}
            cur.execute("SELECT COALESCE(SUM(delta),0) AS n FROM point_ledger WHERE restaurant_id=%s", (restaurant_id,))
            return {"balance": int((cur.fetchone() or {}).get("n") or 0), "mode": "prepaid"}
    finally:
        conn.close()


def restaurant_suspended(restaurant_id: int) -> bool:
    conn = db.get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT suspended_at FROM restaurants WHERE id=%s", (restaurant_id,))
            row = cur.fetchone()
            return bool(row and row.get("suspended_at"))
    finally:
        conn.close()


def plan_row(code: str) -> Optional[Dict[str, Any]]:
    return _plans_by_code().get(code)


def points_block_reason(restaurant_id: int) -> Optional[str]:
    rest_conn = db.get_db_connection()
    try:
        with rest_conn.cursor() as cur:
            cur.execute("SELECT plan_code, suspended_at FROM restaurants WHERE id=%s", (restaurant_id,))
            rest = cur.fetchone()
    finally:
        rest_conn.close()
    if not rest or (rest.get("plan_code") or "") != "points":
        return None
    info = wallet(restaurant_id)
    if info["mode"] != "prepaid":
        return None
    if info["balance"] > 0:
        return None
    rule = settings().get("points_empty_rule") or "block"
    if rule == "block":
        return "Top up points to page new orders."
    return None


def charge_order_point(restaurant_id: int) -> None:
    rest_conn = db.get_db_connection()
    try:
        with rest_conn.cursor() as cur:
            cur.execute("SELECT plan_code FROM restaurants WHERE id=%s", (restaurant_id,))
            rest = cur.fetchone()
    finally:
        rest_conn.close()
    if not rest or (rest.get("plan_code") or "") != "points":
        return
    info = wallet(restaurant_id)
    if info["mode"] != "prepaid":
        return
    change = None
    conn = db.get_db_connection()
    try:
        with conn.cursor() as cur:
            change = _ledger(cur, restaurant_id, -1, "Order", "order", None, None)
            conn.commit()
    finally:
        conn.close()
    _queue_points_email(restaurant_id, change)


def _next_number(cur) -> str:
    year = datetime.now(BAHRAIN).year
    cur.execute("INSERT INTO invoice_counters (year, last_n) VALUES (%s, 0) ON DUPLICATE KEY UPDATE year=year", (year,))
    cur.execute("SELECT last_n FROM invoice_counters WHERE year=%s FOR UPDATE", (year,))
    last = int((cur.fetchone() or {}).get("last_n") or 0) + 1
    cur.execute("UPDATE invoice_counters SET last_n=%s WHERE year=%s", (last, year))
    return f"INV-{year}-{last:04d}"


def create_invoice(
    restaurant_id: int,
    kind: str,
    description: str,
    lines: List[Dict[str, Any]],
    due_at: datetime,
    period_end=None,
    admin_id: Optional[int] = None,
    pack_id: Optional[int] = None,
    points: Optional[int] = None,
) -> str:
    total = sum(int(line.get("amount_fils") or 0) for line in lines)
    token = secrets.token_urlsafe(32)
    conn = db.get_db_connection()
    try:
        with conn.cursor() as cur:
            number = _next_number(cur)
            cur.execute(
                """
                INSERT INTO invoices (
                    restaurant_id, description, amount_fils, status, number, `lines`,
                    subtotal_fils, total_fils, currency, issued_at, due_at, kind,
                    pay_token, period_end, pack_id, points, created_at
                ) VALUES (%s, %s, %s, 'unpaid', %s, %s, %s, %s, 'BHD', %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    restaurant_id, description[:255], total, number, json.dumps(lines, ensure_ascii=False),
                    total, total, datetime.now(), due_at, kind, token, period_end, pack_id, points, datetime.now(),
                ),
            )
            conn.commit()
    finally:
        conn.close()
    _queue_invoice_email(number)
    return number


def get_invoice(number: str) -> Optional[Dict[str, Any]]:
    conn = db.get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM invoices WHERE number=%s", (number,))
            row = cur.fetchone()
            if not row:
                return None
            cur.execute("SELECT name, email, phone, slug FROM restaurants WHERE id=%s", (row["restaurant_id"],))
            rest = cur.fetchone() or {}
            related = None
            if row.get("related_invoice_id"):
                cur.execute("SELECT number FROM invoices WHERE id=%s", (row["related_invoice_id"],))
                related = (cur.fetchone() or {}).get("number")
    finally:
        conn.close()
    try:
        lines = json.loads(row.get("lines") or "[]")
    except json.JSONDecodeError:
        lines = []
    row["line_items"] = lines
    row["restaurant_name"] = rest.get("name") or ""
    row["restaurant_email"] = rest.get("email") or ""
    row["restaurant_phone"] = rest.get("phone") or ""
    row["restaurant_slug"] = rest.get("slug") or ""
    row["related_number"] = related
    return row


def list_invoices(status: str, query: str) -> List[Dict[str, Any]]:
    conn = db.get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT i.*, r.name AS restaurant_name
                FROM invoices i JOIN restaurants r ON r.id=i.restaurant_id
                ORDER BY i.id DESC
                """
            )
            rows = list(cur.fetchall())
    finally:
        conn.close()
    needle = (query or "").strip().lower()
    out = []
    for row in rows:
        state = row.get("status") or ""
        if status and status != "all" and state != status:
            continue
        blob = " ".join([
            str(row.get("restaurant_name") or ""),
            str(row.get("number") or ""),
            str(row.get("provider_ref") or ""),
            str(row.get("description") or ""),
        ]).lower()
        if needle and needle not in blob:
            continue
        out.append(row)
    return out


def payment_kpis() -> Dict[str, Any]:
    now = owner_board.bahrain_now()
    start = datetime(now.year, now.month, 1, tzinfo=BAHRAIN)
    end = datetime(now.year + (now.month == 12), 1 if now.month == 12 else now.month + 1, 1, tzinfo=BAHRAIN)
    naive_start, naive_end = owner_board._naive_local(start), owner_board._naive_local(end)
    point_price = _plan_fils(_plans_by_code().get("points") or {})
    conn = db.get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT COALESCE(SUM(total_fils),0) AS n FROM invoices WHERE status='paid' AND paid_at>=%s AND paid_at<%s",
                (naive_start, naive_end),
            )
            collected = int((cur.fetchone() or {}).get("n") or 0)
            cur.execute("SELECT COALESCE(SUM(total_fils),0) AS n, COUNT(*) AS c FROM invoices WHERE status='unpaid'")
            unpaid = cur.fetchone() or {}
            cur.execute(
                """
                SELECT COALESCE(SUM(cnt),0) AS orders, COUNT(*) AS restaurants FROM (
                    SELECT o.restaurant_id, COUNT(*) AS cnt
                    FROM orders o
                    JOIN point_wallets w ON w.restaurant_id=o.restaurant_id AND w.mode='postpaid'
                    WHERE o.created_at>=%s AND o.created_at<%s
                    GROUP BY o.restaurant_id
                ) t
                """,
                (naive_start, naive_end),
            )
            post = cur.fetchone() or {}
    finally:
        conn.close()
    orders = int(post.get("orders") or 0)
    return {
        "collected": fils_fixed(collected),
        "unpaid": fils_fixed(unpaid.get("n")),
        "unpaid_count": int(unpaid.get("c") or 0),
        "postpaid": fils_fixed(orders * point_price),
        "postpaid_orders": orders,
        "postpaid_restaurants": int(post.get("restaurants") or 0),
        "postpaid_month": end.strftime("%b"),
    }


def mark_paid(number: str, method: str, reference: str, admin_id: int) -> str:
    if method not in ("cash", "bank_transfer", "other", "card"):
        raise ValueError("method")
    conn = db.get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM invoices WHERE number=%s FOR UPDATE", (number,))
            row = cur.fetchone()
            if not row:
                raise ValueError("missing")
            if row.get("status") == "paid":
                raise ValueError("already")
            if row.get("status") != "unpaid":
                raise ValueError("state")
            cur.execute(
                """
                UPDATE invoices
                SET status='paid', paid_at=%s, paid_method=%s, provider_ref=%s
                WHERE id=%s
                """,
                (datetime.now(), method, (reference or "")[:120] or None, row["id"]),
            )
            if row.get("kind") == "topup":
                cur.execute(
                    "SELECT id FROM point_ledger WHERE ref_invoice_id=%s AND kind='topup' LIMIT 1",
                    (row["id"],),
                )
                if not cur.fetchone():
                    points = int(row.get("points") or 0)
                    if points:
                        _ledger(cur, int(row["restaurant_id"]), points, row.get("description") or "Top up", "topup", row["id"], admin_id)
            if row.get("kind") in ("subscription", "manual") and row.get("period_end"):
                cur.execute(
                    """
                    UPDATE restaurants
                    SET subscription_status='active', manual_plan_until=%s
                    WHERE id=%s
                    """,
                    (row["period_end"], row["restaurant_id"]),
                )
            conn.commit()
    finally:
        conn.close()
    _queue_receipt_email(number)
    return "paid"


def owner_documents(restaurant_id: int) -> List[Dict[str, Any]]:
    conn = db.get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT * FROM invoices WHERE restaurant_id=%s
                ORDER BY (status='unpaid') DESC, id DESC
                """,
                (restaurant_id,),
            )
            rows = list(cur.fetchall())
    finally:
        conn.close()
    docs = []
    for row in rows:
        status = row.get("status") or ""
        issued = owner_board._as_bahrain(row.get("issued_at") or row.get("created_at"))
        due = owner_board._as_bahrain(row.get("due_at"))
        docs.append({
            "number": row.get("number") or "",
            "created_at": issued.strftime("%d %b %Y") if issued else "",
            "due": due.strftime("%d %b %Y") if due else "",
            "description": row.get("description") or "",
            "amount_label": fils_fixed(row.get("total_fils") if row.get("total_fils") is not None else row.get("amount_fils")),
            "status": status,
            "doc": "Credit note" if status == "refunded" else "Receipt" if status == "paid" else "Invoice",
            "pay": f"/pay/{row['number']}?t={row['pay_token']}" if status == "unpaid" and row.get("number") and row.get("pay_token") else "",
        })
    return docs


_PLAN_CODE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


def parse_features(raw: Any) -> Dict[str, List[str]]:
    data = raw
    if isinstance(raw, str) and raw.strip():
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            data = {}
    if not isinstance(data, dict):
        data = {}

    def clean(items: Any) -> List[str]:
        out = []
        for item in items or []:
            text = str(item or "").strip()[:80]
            if text:
                out.append(text)
            if len(out) >= 8:
                break
        return out

    return {"en": clean(data.get("en")), "ar": clean(data.get("ar"))}


def _plan_fils(plan: Dict[str, Any]) -> int:
    return int(plan.get("price_fils") or 0)


def reserved_plan_codes() -> List[Dict[str, Any]]:
    """Every code, including soft-deleted rows, so a new plan cannot reuse one."""
    return [
        {"code": code, "deleted": bool(row.get("deleted_at"))}
        for code, row in _plans_by_code(include_deleted=True).items()
    ]


def plans_admin() -> List[Dict[str, Any]]:
    conn = db.get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM plans WHERE deleted_at IS NULL ORDER BY sort, id")
            plans = list(cur.fetchall())
            cur.execute(
                """
                SELECT plan_code, COUNT(*) AS n FROM restaurants
                WHERE subscription_status IN ('active','trialing','past_due') AND suspended_at IS NULL
                GROUP BY plan_code
                """
            )
            counts = {row["plan_code"]: int(row["n"]) for row in cur.fetchall()}
            cur.execute("SELECT plan_code, COUNT(*) AS n FROM restaurants GROUP BY plan_code")
            holders = {row["plan_code"]: int(row["n"]) for row in cur.fetchall()}
            cur.execute("SELECT `lines`, description FROM invoices")
            blobs = [f"{row.get('lines') or ''}\n{row.get('description') or ''}" for row in cur.fetchall()]
    finally:
        conn.close()
    invoiced = set()
    for blob in blobs:
        invoiced.update(re.findall(r'"plan_code"\s*:\s*"([a-z0-9-]+)"', blob))
    for plan in plans:
        plan["subscribers"] = counts.get(plan["code"], 0)
        plan["restaurants"] = holders.get(plan["code"], 0)
        names = {str(plan.get("name_en") or ""), str(plan.get("name_ar") or ""), f"Paiger {plan.get('name_en') or ''}", f"Paiger {plan.get('name_ar') or ''}"}
        named = any(name and name in blob for blob in blobs for name in names)
        plan["invoiced"] = plan["code"] in invoiced or named
        plan["deletable"] = plan["restaurants"] == 0 and not plan["invoiced"]
        plan["features"] = parse_features(plan.get("features"))
        plan["price_label"] = fils_fixed(plan.get("price_fils"))
    return plans


def save_plan(code: str, kind: str, name_en: str, name_ar: str, price_bhd: str, blurb_en: str, blurb_ar: str, active: int, creating: bool = False, features: Optional[Dict[str, Any]] = None, featured: bool = False, badge_en: str = "", badge_ar: str = "") -> None:
    code = (code or "").strip().lower()
    if not _PLAN_CODE.fullmatch(code):
        raise ValueError("format")
    if creating:
        row = _plans_by_code(include_deleted=True).get(code)
        if row:
            raise ValueError("deleted" if row.get("deleted_at") else "taken")
        existing = None
    else:
        existing = _plans_by_code().get(code)
        if not existing:
            raise ValueError("plan")
    fils = parse_bhd(price_bhd)
    if fils < 0 or (active and fils <= 0):
        raise ValueError("price")
    conn = db.get_db_connection()
    try:
        with conn.cursor() as cur:
            if existing:
                if kind != existing["type"]:
                    cur.execute(
                        "SELECT COUNT(*) AS n FROM restaurants WHERE plan_code=%s",
                        (code,),
                    )
                    if int((cur.fetchone() or {}).get("n") or 0):
                        raise ValueError("type")
                cur.execute(
                    """
                    UPDATE plans
                    SET type=%s, name_en=%s, name_ar=%s, price_fils=%s, blurb_en=%s, blurb_ar=%s, active=%s
                    WHERE code=%s
                    """,
                    (kind, name_en[:120], name_ar[:120], fils, blurb_en[:180], blurb_ar[:180], active, code),
                )
            else:
                cur.execute("SELECT COALESCE(MAX(sort),0)+1 AS s FROM plans")
                sort = int((cur.fetchone() or {}).get("s") or 1)
                cur.execute(
                    """
                    INSERT INTO plans (code, type, name_en, name_ar, price_fils, active, sort, blurb_en, blurb_ar)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        code, kind, name_en[:120], name_ar[:120],
                        fils, active, sort, blurb_en[:180], blurb_ar[:180],
                    ),
                )
            payload = json.dumps(parse_features(features or {}), ensure_ascii=False)
            if featured:
                cur.execute("UPDATE plans SET featured=0 WHERE code<>%s", (code,))
            cur.execute(
                "UPDATE plans SET features=%s, featured=%s, badge_en=%s, badge_ar=%s WHERE code=%s",
                (payload, 1 if featured else 0, (badge_en or "")[:80] or None, (badge_ar or "")[:80] or None, code),
            )
            conn.commit()
        owner_board.clear_public_cache()
    finally:
        conn.close()


def set_plan_visible(code: str, active: int) -> None:
    plan = _plans_by_code().get(code)
    if not plan:
        raise ValueError("plan")
    if active and _plan_fils(plan) <= 0:
        raise ValueError("price")
    conn = db.get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE plans SET active=%s WHERE code=%s AND deleted_at IS NULL",
                (1 if active else 0, code),
            )
            conn.commit()
        owner_board.clear_public_cache()
    finally:
        conn.close()


def delete_plan(code: str) -> None:
    """Soft delete: the row stays so its code cannot be reused."""
    code = (code or "").strip().lower()
    row = next((plan for plan in plans_admin() if plan["code"] == code), None)
    if not row or not row.get("deletable"):
        raise ValueError("used")
    conn = db.get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE plans
                SET deleted_at=%s, active=0, featured=0
                WHERE code=%s AND deleted_at IS NULL
                """,
                (datetime.now(), code),
            )
            if cur.rowcount < 1:
                raise ValueError("used")
            conn.commit()
        owner_board.clear_public_cache()
    finally:
        conn.close()


def restore_plan(code: str) -> None:
    code = (code or "").strip().lower()
    if not _PLAN_CODE.fullmatch(code):
        raise ValueError("code")
    conn = db.get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE plans
                SET deleted_at=NULL, active=1
                WHERE code=%s AND deleted_at IS NOT NULL
                """,
                (code,),
            )
            if cur.rowcount < 1:
                raise ValueError("plan")
            conn.commit()
        owner_board.clear_public_cache()
    finally:
        conn.close()


def packs_admin() -> List[Dict[str, Any]]:
    conn = db.get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM point_packs WHERE deleted_at IS NULL ORDER BY sort, id")
            packs = list(cur.fetchall())
            cur.execute(
                "SELECT pack_id, COUNT(*) AS n FROM invoices WHERE status='paid' AND pack_id IS NOT NULL GROUP BY pack_id"
            )
            sold = {row["pack_id"]: int(row["n"]) for row in cur.fetchall()}
    finally:
        conn.close()
    for pack in packs:
        pack["sold"] = sold.get(pack["id"], 0)
        pack["price_label"] = fils_fixed(pack["price_fils"])
        points = int(pack["points"] or 0)
        pack["per_point"] = fils_fixed(int(pack["price_fils"] or 0) // points) if points else "0.000"
    return packs


def save_pack(pack_id: Optional[int], points: int, price_bhd: str, active: int) -> None:
    if points < 1:
        raise ValueError("points")
    fils = parse_bhd(price_bhd)
    conn = db.get_db_connection()
    try:
        with conn.cursor() as cur:
            if pack_id:
                cur.execute(
                    """
                    UPDATE point_packs
                    SET points=%s, price_fils=%s, active=%s
                    WHERE id=%s AND deleted_at IS NULL
                    """,
                    (points, fils, 1 if active else 0, pack_id),
                )
            else:
                cur.execute("SELECT COALESCE(MAX(sort),0)+1 AS s FROM point_packs")
                sort = int((cur.fetchone() or {}).get("s") or 1)
                cur.execute(
                    "INSERT INTO point_packs (points, price_fils, active, sort) VALUES (%s, %s, %s, %s)",
                    (points, fils, 1 if active else 0, sort),
                )
            conn.commit()
        owner_board.clear_public_cache()
    finally:
        conn.close()


def delete_pack(pack_id: int) -> None:
    conn = db.get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) AS n FROM invoices WHERE pack_id=%s AND status='paid'", (pack_id,))
            if int((cur.fetchone() or {}).get("n") or 0):
                raise ValueError("sold")
            cur.execute(
                """
                UPDATE point_packs
                SET deleted_at=%s, active=0
                WHERE id=%s AND deleted_at IS NULL
                """,
                (datetime.now(), pack_id),
            )
            if cur.rowcount < 1:
                raise ValueError("pack")
            conn.commit()
        owner_board.clear_public_cache()
    finally:
        conn.close()


def restore_pack(pack_id: int) -> None:
    conn = db.get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE point_packs
                SET deleted_at=NULL, active=1
                WHERE id=%s AND deleted_at IS NOT NULL
                """,
                (pack_id,),
            )
            if cur.rowcount < 1:
                raise ValueError("pack")
            conn.commit()
        owner_board.clear_public_cache()
    finally:
        conn.close()


def recently_deleted(days: int = 30) -> List[Dict[str, Any]]:
    cutoff = datetime.now() - timedelta(days=days)
    conn = db.get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT code, name_en, name_ar, deleted_at
                FROM plans
                WHERE deleted_at IS NOT NULL AND deleted_at >= %s
                ORDER BY deleted_at DESC
                """,
                (cutoff,),
            )
            plans = list(cur.fetchall())
            cur.execute(
                """
                SELECT id, points, deleted_at
                FROM point_packs
                WHERE deleted_at IS NOT NULL AND deleted_at >= %s
                ORDER BY deleted_at DESC
                """,
                (cutoff,),
            )
            packs = list(cur.fetchall())
    finally:
        conn.close()
    items: List[Dict[str, Any]] = []
    for plan in plans:
        items.append({
            "kind": "plan",
            "name_en": plan.get("name_en") or plan["code"],
            "name_ar": plan.get("name_ar") or plan["code"],
            "restore": f"/admin/plans/{plan['code']}/restore",
            "when": plan.get("deleted_at"),
        })
    for pack in packs:
        items.append({
            "kind": "pack",
            "points": int(pack.get("points") or 0),
            "restore": f"/admin/packs/{int(pack['id'])}/restore",
            "when": pack.get("deleted_at"),
        })
    items.sort(key=lambda item: item.get("when") or datetime.min, reverse=True)
    return items
