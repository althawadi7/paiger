"""Cashier PIN, device cookies, and lockouts. The owner can still read the PIN."""
import hashlib
import hmac
import os
import secrets
from base64 import urlsafe_b64encode
from datetime import datetime, timedelta
from typing import Any, Dict, Optional

from cryptography.fernet import Fernet

from . import auth, database as db

COOKIE_PREFIX = "paiger_cashier_"
LOCK_MINUTES = (5, 15, 60)
FAILS_BEFORE_LOCK = 5
GLOBAL_FAIL_CAP = 30
GLOBAL_WINDOW = timedelta(minutes=10)


def _fernet() -> Fernet:
    digest = hashlib.sha256(auth.SECRET_KEY.encode("utf-8")).digest()
    return Fernet(urlsafe_b64encode(digest))


def encrypt_pin(pin: str) -> str:
    return _fernet().encrypt(pin.encode("utf-8")).decode("utf-8")


def decrypt_pin(token: str) -> str:
    if not token:
        return ""
    return _fernet().decrypt(token.encode("utf-8")).decode("utf-8")


def pin_is_weak(pin: str) -> bool:
    if not pin.isdigit() or len(pin) != 6:
        return True
    if len(set(pin)) == 1:
        return True
    nums = [int(ch) for ch in pin]
    diffs = [nums[i + 1] - nums[i] for i in range(5)]
    if all(step == 1 for step in diffs) or all(step == -1 for step in diffs):
        return True
    if pin[:2] * 3 == pin or pin[:3] * 2 == pin:
        return True
    if pin[0] == pin[1] and pin[2] == pin[3] and pin[4] == pin[5] and pin[0] != pin[2]:
        return True
    return False


def generate_pin() -> str:
    for _ in range(100):
        pin = f"{secrets.randbelow(900000) + 100000:06d}"
        if not pin_is_weak(pin):
            return pin
    return "246810"


def device_key(ip: str, user_agent: str) -> str:
    raw = f"{(ip or '').strip()}|{(user_agent or '')[:200]}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def cookie_name(restaurant_id: int) -> str:
    return f"{COOKIE_PREFIX}{int(restaurant_id)}"


def _raw_pin(restaurant_id: int) -> Optional[Dict[str, Any]]:
    conn = db.get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, cashier_pin_enc, cashier_pin_version FROM restaurants WHERE id=%s",
                (restaurant_id,),
            )
            return cur.fetchone()
    finally:
        conn.close()


def current_pin(restaurant_id: int) -> str:
    row = _raw_pin(restaurant_id)
    if not row or not row.get("cashier_pin_enc"):
        return ""
    return decrypt_pin(row["cashier_pin_enc"])


def issue_initial_pin(restaurant_id: int) -> str:
    """The PIN a new restaurant gets once, the same way startup fills missing ones."""
    row = _raw_pin(restaurant_id)
    if row and row.get("cashier_pin_enc"):
        return decrypt_pin(row["cashier_pin_enc"])
    pin = generate_pin()
    conn = db.get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE restaurants
                SET cashier_pin_enc=%s, cashier_pin_version=1, cashier_pin_updated_at=%s
                WHERE id=%s AND (cashier_pin_enc IS NULL OR cashier_pin_enc = '')
                """,
                (encrypt_pin(pin), datetime.now(), restaurant_id),
            )
            conn.commit()
    finally:
        conn.close()
    return current_pin(restaurant_id) or pin


def ensure_pins() -> None:
    conn = db.get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id FROM restaurants WHERE cashier_pin_enc IS NULL OR cashier_pin_enc = ''"
            )
            missing = [int(row["id"]) for row in cur.fetchall()]
        for restaurant_id in missing:
            pin = generate_pin()
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE restaurants
                    SET cashier_pin_enc=%s, cashier_pin_version=1, cashier_pin_updated_at=%s
                    WHERE id=%s AND (cashier_pin_enc IS NULL OR cashier_pin_enc = '')
                    """,
                    (encrypt_pin(pin), datetime.now(), restaurant_id),
                )
        conn.commit()
    finally:
        conn.close()


def save_pin(restaurant_id: int, pin: str) -> int:
    conn = db.get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE restaurants
                SET cashier_pin_enc=%s,
                    cashier_pin_version=cashier_pin_version+1,
                    cashier_pin_updated_at=%s
                WHERE id=%s
                """,
                (encrypt_pin(pin), datetime.now(), restaurant_id),
            )
            cur.execute(
                "UPDATE cashier_devices SET revoked_at=%s WHERE restaurant_id=%s AND revoked_at IS NULL",
                (datetime.now(), restaurant_id),
            )
            cur.execute("SELECT cashier_pin_version AS v FROM restaurants WHERE id=%s", (restaurant_id,))
            version = int((cur.fetchone() or {}).get("v") or 1)
            conn.commit()
            return version
    finally:
        conn.close()


def revoke_devices(restaurant_id: int) -> None:
    conn = db.get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE cashier_devices SET revoked_at=%s WHERE restaurant_id=%s AND revoked_at IS NULL",
                (datetime.now(), restaurant_id),
            )
            conn.commit()
    finally:
        conn.close()


def _global_paused(restaurant_id: int, key: str) -> Optional[datetime]:
    since = datetime.now() - GLOBAL_WINDOW
    conn = db.get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT COUNT(*) AS n FROM cashier_pin_fail_log
                WHERE restaurant_id=%s AND created_at>=%s
                """,
                (restaurant_id, since),
            )
            count = int((cur.fetchone() or {}).get("n") or 0)
            if count < GLOBAL_FAIL_CAP:
                return None
            cur.execute(
                """
                SELECT id FROM cashier_pin_attempts
                WHERE restaurant_id=%s AND device_key=%s AND updated_at < %s
                """,
                (restaurant_id, key, since),
            )
            if cur.fetchone():
                return None
            return datetime.now() + GLOBAL_WINDOW
    finally:
        conn.close()


def pin_status(restaurant_id: int, key: str) -> Dict[str, Any]:
    paused = _global_paused(restaurant_id, key)
    conn = db.get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT failed_count, locked_until FROM cashier_pin_attempts
                WHERE restaurant_id=%s AND device_key=%s
                """,
                (restaurant_id, key),
            )
            row = cur.fetchone() or {}
    finally:
        conn.close()
    locked = row.get("locked_until")
    if isinstance(locked, str):
        locked = datetime.fromisoformat(locked)
    if paused and not (locked and locked > datetime.now()):
        locked = paused
    failed = int(row.get("failed_count") or 0)
    remaining = 0 if locked and locked > datetime.now() else FAILS_BEFORE_LOCK - (failed % FAILS_BEFORE_LOCK or (FAILS_BEFORE_LOCK if failed and failed % FAILS_BEFORE_LOCK == 0 else failed % FAILS_BEFORE_LOCK))
    if locked and locked > datetime.now():
        remaining = 0
    else:
        remaining = FAILS_BEFORE_LOCK - (failed % FAILS_BEFORE_LOCK)
    return {
        "remaining": remaining,
        "locked_until": locked.isoformat() if locked and locked > datetime.now() else None,
    }


def check_pin(restaurant_id: int, key: str, pin: str, user_agent: str, ip: str) -> Dict[str, Any]:
    paused = _global_paused(restaurant_id, key)
    conn = db.get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT failed_count, locked_until, updated_at FROM cashier_pin_attempts
                WHERE restaurant_id=%s AND device_key=%s
                """,
                (restaurant_id, key),
            )
            row = cur.fetchone()
            known = row is not None and row.get("updated_at") and row["updated_at"] < datetime.now() - GLOBAL_WINDOW
            if paused and not known:
                return {"ok": False, "remaining": 0, "locked_until": paused.isoformat()}
            locked = (row or {}).get("locked_until")
            if locked and locked > datetime.now():
                return {"ok": False, "remaining": 0, "locked_until": locked.isoformat()}
            expected = current_pin(restaurant_id)
            offered = pin or ""
            if expected and len(expected) == len(offered) and hmac.compare_digest(expected, offered):
                cur.execute(
                    """
                    INSERT INTO cashier_pin_attempts (restaurant_id, device_key, failed_count, locked_until, updated_at)
                    VALUES (%s, %s, 0, NULL, %s)
                    ON DUPLICATE KEY UPDATE failed_count=0, locked_until=NULL, updated_at=VALUES(updated_at)
                    """,
                    (restaurant_id, key, datetime.now()),
                )
                conn.commit()
                return {"ok": True, "remaining": FAILS_BEFORE_LOCK, "locked_until": None}
            failed = int((row or {}).get("failed_count") or 0) + 1
            lock_until = None
            if failed % FAILS_BEFORE_LOCK == 0:
                tier = min(failed // FAILS_BEFORE_LOCK, len(LOCK_MINUTES)) - 1
                lock_until = datetime.now() + timedelta(minutes=LOCK_MINUTES[max(tier, 0)])
            cur.execute(
                """
                INSERT INTO cashier_pin_attempts (restaurant_id, device_key, failed_count, locked_until, updated_at)
                VALUES (%s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE failed_count=VALUES(failed_count), locked_until=VALUES(locked_until), updated_at=VALUES(updated_at)
                """,
                (restaurant_id, key, failed, lock_until, datetime.now()),
            )
            cur.execute(
                "INSERT INTO cashier_pin_fail_log (restaurant_id, device_key, created_at) VALUES (%s, %s, %s)",
                (restaurant_id, key, datetime.now()),
            )
            conn.commit()
            remaining = 0 if lock_until else FAILS_BEFORE_LOCK - (failed % FAILS_BEFORE_LOCK)
            return {
                "ok": False,
                "remaining": remaining,
                "locked_until": lock_until.isoformat() if lock_until else None,
            }
    finally:
        conn.close()


def issue_device(restaurant_id: int, user_agent: str, ip: str) -> str:
    token = secrets.token_urlsafe(32)
    row = _raw_pin(restaurant_id)
    version = int((row or {}).get("cashier_pin_version") or 1)
    conn = db.get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO cashier_devices
                    (restaurant_id, token_hash, pin_version, user_agent, ip, created_at, last_used_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                """,
                (restaurant_id, token_hash(token), version, (user_agent or "")[:255], (ip or "")[:64], datetime.now(), datetime.now()),
            )
            conn.commit()
    finally:
        conn.close()
    return token


def valid_device(restaurant_id: int, token: str) -> Optional[Dict[str, Any]]:
    if not token:
        return None
    conn = db.get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT d.id, d.restaurant_id, d.pin_version, d.last_used_at, d.user_agent, d.revoked_at,
                       r.cashier_pin_version
                FROM cashier_devices d
                JOIN restaurants r ON r.id = d.restaurant_id
                WHERE d.restaurant_id=%s AND d.token_hash=%s
                """,
                (restaurant_id, token_hash(token)),
            )
            row = cur.fetchone()
            if not row or row.get("revoked_at"):
                return None
            if int(row["pin_version"]) != int(row["cashier_pin_version"]):
                return None
            last = row.get("last_used_at")
            if not last or last <= datetime.now() - timedelta(minutes=1):
                cur.execute(
                    "UPDATE cashier_devices SET last_used_at=%s WHERE id=%s",
                    (datetime.now(), row["id"]),
                )
                conn.commit()
            return {"id": int(row["id"]), "restaurant_id": int(row["restaurant_id"])}
    finally:
        conn.close()


def device_restaurant_ids(cookies) -> list:
    """Every restaurant this browser has a valid cashier device cookie for."""
    found = []
    for name in cookies.keys():
        if not name.startswith(COOKIE_PREFIX):
            continue
        try:
            rid = int(name[len(COOKIE_PREFIX):])
        except ValueError:
            continue
        if valid_device(rid, cookies.get(name) or ""):
            found.append(rid)
    return found


def device_from_cookies(cookies, restaurant_id: Optional[int] = None) -> Optional[Dict[str, Any]]:
    names = [cookie_name(restaurant_id)] if restaurant_id else [
        name for name in cookies.keys() if name.startswith(COOKIE_PREFIX)
    ]
    for name in names:
        try:
            rid = int(name[len(COOKIE_PREFIX):])
        except ValueError:
            continue
        if restaurant_id and rid != int(restaurant_id):
            continue
        found = valid_device(rid, cookies.get(name) or "")
        if found:
            return found
    return None


def recent_device_orders(device_id: int, seconds: int = 60) -> int:
    since = datetime.now() - timedelta(seconds=seconds)
    conn = db.get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT COUNT(*) AS n FROM orders
                WHERE cashier_device_id=%s AND created_at>=%s
                """,
                (device_id, since),
            )
            return int((cur.fetchone() or {}).get("n") or 0)
    finally:
        conn.close()


def list_devices(restaurant_id: int) -> list:
    conn = db.get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, user_agent, ip, created_at, last_used_at
                FROM cashier_devices
                WHERE restaurant_id=%s AND revoked_at IS NULL
                ORDER BY last_used_at DESC, id DESC
                """,
                (restaurant_id,),
            )
            return list(cur.fetchall())
    finally:
        conn.close()


def describe_agent(user_agent: str) -> str:
    ua = user_agent or ""
    if "iPad" in ua:
        device = "iPad"
    elif "iPhone" in ua:
        device = "iPhone"
    elif "Android" in ua:
        device = "Android"
    elif "Mac OS" in ua or "Macintosh" in ua:
        device = "Mac"
    elif "Windows" in ua:
        device = "Windows"
    else:
        device = "Computer"
    if "Edg/" in ua:
        browser = "Edge"
    elif "Chrome/" in ua and "Chromium" not in ua:
        browser = "Chrome"
    elif "Firefox/" in ua:
        browser = "Firefox"
    elif "Safari/" in ua:
        browser = "Safari"
    else:
        browser = "Browser"
    return f"{device} · {browser}"
