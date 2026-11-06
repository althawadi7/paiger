"""Owner dashboard numbers. Every clock is Asia/Bahrain."""
import time
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

from . import database as db

BAHRAIN = ZoneInfo("Asia/Bahrain")
PAGE_SIZE = 50


def bahrain_now() -> datetime:
    return datetime.now(BAHRAIN)


def _naive_local(moment: datetime) -> datetime:
    return moment.astimezone().replace(tzinfo=None)


def _as_bahrain(value: Any) -> Optional[datetime]:
    if value is None or value == "":
        return None
    if isinstance(value, str):
        value = datetime.fromisoformat(value)
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=datetime.now().astimezone().tzinfo)
    return value.astimezone(BAHRAIN)


def day_window(days_ago: int = 0, length_days: int = 1):
    now = bahrain_now()
    if length_days == 1:
        start = (now - timedelta(days=days_ago)).replace(hour=0, minute=0, second=0, microsecond=0)
        end = start + timedelta(days=1)
    else:
        start = (now - timedelta(days=length_days - 1)).replace(hour=0, minute=0, second=0, microsecond=0)
        end = now + timedelta(seconds=1)
    return _naive_local(start), _naive_local(end)


def range_window(key: str):
    if key == "7":
        return day_window(length_days=7)
    if key == "30":
        return day_window(length_days=30)
    return day_window(0, 1)


def _rows(restaurant_id: int, start, end) -> List[Dict[str, Any]]:
    conn = db.get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, order_number, status, call_count, created_at, called_at, completed_at, scanned_at
                FROM orders
                WHERE restaurant_id=%s AND created_at>=%s AND created_at<%s
                ORDER BY id DESC
                """,
                (restaurant_id, start, end),
            )
            return list(cur.fetchall())
    finally:
        conn.close()


def _minutes(start, end) -> Optional[float]:
    a = _as_bahrain(start)
    b = _as_bahrain(end)
    if not a or not b or b < a:
        return None
    return (b - a).total_seconds() / 60.0


def _avg(values: List[float]) -> Optional[float]:
    if not values:
        return None
    return sum(values) / len(values)


def overview(restaurant_id: int) -> Dict[str, Any]:
    start, end = day_window(0, 1)
    prev_start, prev_end = day_window(7, 1)
    today = _rows(restaurant_id, start, end)
    previous = _rows(restaurant_id, prev_start, prev_end)
    prep = [m for m in (_minutes(row["created_at"], row["called_at"]) for row in today) if m is not None]
    pick = [m for m in (_minutes(row["called_at"], row["completed_at"]) for row in today) if m is not None]
    scanned = sum(1 for row in today if row.get("scanned_at"))
    hours = [0] * 24
    for row in today:
        moment = _as_bahrain(row["created_at"])
        if moment:
            hours[moment.hour] += 1
    latest = []
    for row in today[:5]:
        moment = _as_bahrain(row["created_at"])
        latest.append({
            "number": row["order_number"],
            "status": row["status"],
            "ago": int((bahrain_now() - moment).total_seconds() // 60) if moment else 0,
        })
    return {
        "orders_today": len(today),
        "orders_last_week": len(previous),
        "delta": len(today) - len(previous),
        "weekday": bahrain_now().strftime("%A"),
        "avg_prep": _avg(prep),
        "avg_pick": _avg(pick),
        "scanned_pct": round(100 * scanned / len(today)) if today else 0,
        "hours": hours,
        "hour_now": bahrain_now().hour,
        "latest": latest,
    }


def list_orders(restaurant_id: int, range_key: str, status: str, query: str, page: int) -> Dict[str, Any]:
    start, end = range_window(range_key if range_key in ("today", "7", "30") else "today")
    rows = _rows(restaurant_id, start, end)
    status = status if status in ("waiting", "ringing", "onway", "done") else ""
    needle = (query or "").strip()
    if status:
        rows = [row for row in rows if row["status"] == status]
    if needle:
        rows = [row for row in rows if needle in str(row["order_number"])]
    total = len(rows)
    page = max(1, page)
    start_i = (page - 1) * PAGE_SIZE
    page_rows = rows[start_i:start_i + PAGE_SIZE]
    return {"rows": page_rows, "total": total, "page": page, "pages": max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE), "all_rows": rows}


def plans() -> List[Dict[str, Any]]:
    conn = db.get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM plans WHERE active=1 AND deleted_at IS NULL ORDER BY sort, id")
            return list(cur.fetchall())
    finally:
        conn.close()


_public_cache: Dict[str, Any] = {"at": 0.0, "rows": None, "packs": None, "trial_days": 0}


def clear_public_cache() -> None:
    _public_cache["at"] = 0.0
    _public_cache["rows"] = None


def trial_length() -> int:
    raw = db.get_platform_setting("trial_days", "")
    if raw == "":
        raw = "3"
    try:
        days = int(raw)
    except (TypeError, ValueError):
        days = 3
    return max(0, min(days, 60))


def landing_source() -> Dict[str, Any]:
    """Visible plans, packs and trial length. Fresh for at most 60 seconds."""
    now = time.monotonic()
    if _public_cache["rows"] is not None and now - float(_public_cache["at"]) < 60:
        return _public_cache
    _public_cache["rows"] = plans()
    _public_cache["packs"] = point_packs()
    _public_cache["trial_days"] = trial_length()
    _public_cache["at"] = now
    return _public_cache


def point_packs() -> List[Dict[str, Any]]:
    conn = db.get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM point_packs WHERE active=1 AND deleted_at IS NULL ORDER BY sort, id")
            return list(cur.fetchall())
    finally:
        conn.close()


def invoices(restaurant_id: int) -> List[Dict[str, Any]]:
    conn = db.get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM invoices WHERE restaurant_id=%s ORDER BY id DESC",
                (restaurant_id,),
            )
            return list(cur.fetchall())
    finally:
        conn.close()


def fils_label(fils: Optional[int]) -> str:
    value = int(fils or 0)
    return f"{value / 1000:.3f}"


def save_profile(restaurant_id: int, name: str, email: str, phone: str, brand_color: str, prep_minutes: int, password_hash: Optional[str]) -> None:
    conn = db.get_db_connection()
    try:
        with conn.cursor() as cur:
            if password_hash:
                cur.execute(
                    """
                    UPDATE restaurants
                    SET name=%s, email=%s, phone=%s, brand_color=%s, prep_minutes=%s, password_hash=%s
                    WHERE id=%s
                    """,
                    (name, email, phone, brand_color, prep_minutes, password_hash, restaurant_id),
                )
            else:
                cur.execute(
                    """
                    UPDATE restaurants
                    SET name=%s, email=%s, phone=%s, brand_color=%s, prep_minutes=%s
                    WHERE id=%s
                    """,
                    (name, email, phone, brand_color, prep_minutes, restaurant_id),
                )
            conn.commit()
    finally:
        conn.close()
