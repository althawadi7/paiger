import hashlib
import json
import os
import re
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, Dict, Any, List
import pymysql
from pymysql.cursors import DictCursor
from dbutils.pooled_db import PooledDB
from dotenv import load_dotenv

# Load environment variables
ENV_FILE = Path(__file__).parent.parent / ".env"
load_dotenv(ENV_FILE)

DB_HOST = os.getenv("DB_HOST", "127.0.0.1")
DB_PORT = int(os.getenv("DB_PORT", "3306"))
DB_USER = os.getenv("DB_USER", "pager_user")
DB_PASSWORD = os.getenv("DB_PASSWORD", "")
DB_NAME = os.getenv("DB_NAME", "pager_db")

pool: Optional[PooledDB] = None

def get_pool() -> PooledDB:
    global pool
    if pool is None:
        pool = PooledDB(
            creator=pymysql,
            maxconnections=25,
            mincached=2,
            maxcached=10,
            blocking=True,
            host=DB_HOST,
            port=DB_PORT,
            user=DB_USER,
            password=DB_PASSWORD,
            database=DB_NAME,
            charset="utf8mb4",
            cursorclass=DictCursor,
            autocommit=False
        )
    return pool

def get_db_connection():
    return get_pool().connection()

from decimal import Decimal

def _serialize_row(row: Optional[Dict[str, Any]], include_secrets: bool = False) -> Optional[Dict[str, Any]]:
    if not row:
        return None
    d = dict(row)
    for k, v in d.items():
        if isinstance(v, datetime):
            d[k] = v.isoformat()
        elif isinstance(v, Decimal):
            d[k] = int(v) if v % 1 == 0 else float(v)
    if not include_secrets:
        d.pop("password_hash", None)
        d.pop("cashier_pin_enc", None)
    if "scanned_at" in d:
        d["scanned"] = bool(d.get("scanned_at"))
    return d

def init_db():
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            # Super admins
            cur.execute("""
                CREATE TABLE IF NOT EXISTS super_admins (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    username VARCHAR(100) UNIQUE NOT NULL,
                    email VARCHAR(150) UNIQUE NOT NULL,
                    password_hash VARCHAR(255) NOT NULL,
                    full_name VARCHAR(150) DEFAULT 'Super Admin',
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
            """)

            # Restaurants / Tenants
            cur.execute("""
                CREATE TABLE IF NOT EXISTS restaurants (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    name VARCHAR(150) NOT NULL,
                    slug VARCHAR(100) UNIQUE NOT NULL,
                    email VARCHAR(150) UNIQUE NOT NULL,
                    password_hash VARCHAR(255) NOT NULL,
                    phone VARCHAR(50) DEFAULT '',
                    brand_color VARCHAR(30) DEFAULT '#C05621',
                    brand_icon VARCHAR(50) DEFAULT 'cup.and.saucer.fill',
                    logo_url VARCHAR(255) DEFAULT '',
                    avg_prep_time INT DEFAULT 5,
                    is_active TINYINT(1) DEFAULT 1,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                    INDEX idx_slug (slug),
                    INDEX idx_email (email)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
            """)

            # Orders
            cur.execute("""
                CREATE TABLE IF NOT EXISTS orders (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    restaurant_id INT NOT NULL DEFAULT 1,
                    order_number VARCHAR(50) NOT NULL,
                    customer_name VARCHAR(100) DEFAULT '',
                    notes TEXT,
                    token VARCHAR(64) UNIQUE NOT NULL,
                    status VARCHAR(20) NOT NULL DEFAULT 'waiting',
                    call_count INT DEFAULT 0,
                    restaurant_name VARCHAR(100) DEFAULT '',
                    brand_color VARCHAR(20) DEFAULT '#C05621',
                    brand_icon VARCHAR(50) DEFAULT 'cup.and.saucer.fill',
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    called_at DATETIME NULL,
                    completed_at DATETIME NULL,
                    INDEX idx_token (token),
                    INDEX idx_status (status),
                    INDEX idx_restaurant (restaurant_id)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
            """)

            # Push Subscriptions
            cur.execute("""
                CREATE TABLE IF NOT EXISTS push_subscriptions (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    token VARCHAR(64) NOT NULL,
                    endpoint VARCHAR(500) UNIQUE NOT NULL,
                    p256dh VARCHAR(255) NOT NULL,
                    auth VARCHAR(255) NOT NULL,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    INDEX idx_sub_token (token)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
            """)

            # System Settings
            cur.execute("""
                CREATE TABLE IF NOT EXISTS settings (
                    `key` VARCHAR(100) PRIMARY KEY,
                    `value` TEXT NOT NULL
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
            """)

            # Live Activity APNs tokens
            cur.execute("""
                CREATE TABLE IF NOT EXISTS live_activity_tokens (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    order_token VARCHAR(64) NOT NULL,
                    push_token VARCHAR(512) UNIQUE NOT NULL,
                    device_type VARCHAR(50) DEFAULT 'ios_live_activity',
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                    INDEX idx_order_token (order_token)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
            """)

            # Custom Pages (Privacy Policy, Terms of Service, About, etc.)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS custom_pages (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    slug VARCHAR(100) UNIQUE NOT NULL,
                    title VARCHAR(200) NOT NULL,
                    title_en VARCHAR(200) DEFAULT '',
                    content MEDIUMTEXT NOT NULL,
                    content_en MEDIUMTEXT DEFAULT '',
                    meta_description VARCHAR(300) DEFAULT '',
                    is_published TINYINT(1) DEFAULT 1,
                    show_in_footer TINYINT(1) DEFAULT 1,
                    sort_order INT DEFAULT 0,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                    INDEX idx_page_slug (slug),
                    INDEX idx_page_published (is_published)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS password_reset_tokens (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    restaurant_id INT NOT NULL,
                    token_hash CHAR(64) NOT NULL,
                    expires_at DATETIME NOT NULL,
                    used_at DATETIME NULL,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    INDEX idx_reset_hash (token_hash),
                    INDEX idx_reset_rest (restaurant_id)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
            """)
            conn.commit()
    finally:
        conn.close()
    ensure_column("orders", "scanned_at", "DATETIME NULL")
    ensure_column("push_subscriptions", "lang", "VARCHAR(8) NULL")
    ensure_column("restaurants", "plan_code", "VARCHAR(20) NULL")
    ensure_column("restaurants", "subscription_status", "VARCHAR(20) NULL")
    ensure_column("restaurants", "trial_ends_at", "DATETIME NULL")
    ensure_column("restaurants", "onboarded_at", "DATETIME NULL")
    ensure_column("restaurants", "prep_minutes", "INT NOT NULL DEFAULT 6")
    ensure_column("restaurants", "cashier_pin_enc", "TEXT NULL")
    ensure_column("restaurants", "cashier_pin_version", "INT NOT NULL DEFAULT 1")
    ensure_column("restaurants", "cashier_pin_updated_at", "DATETIME NULL")
    ensure_column("orders", "cashier_device_id", "INT NULL")
    _ensure_owner_tables()
    _backfill_legacy_subscriptions()
    ensure_table(
        "admin_login_attempts",
        """
        CREATE TABLE admin_login_attempts (
            id INT AUTO_INCREMENT PRIMARY KEY,
            ip VARCHAR(64) NOT NULL,
            username VARCHAR(150) NOT NULL,
            success TINYINT(1) NOT NULL,
            created_at DATETIME NOT NULL,
            INDEX idx_admin_attempt_ip (ip, created_at)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """,
    )
    _ensure_billing()


_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _ensure_owner_tables() -> None:
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS plans (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    code VARCHAR(40) NOT NULL,
                    type VARCHAR(20) NOT NULL,
                    name_en VARCHAR(120) NOT NULL,
                    name_ar VARCHAR(120) NOT NULL,
                    price_fils INT NOT NULL,
                    point_price_fils INT NULL, -- deprecated, unused: every plan price is price_fils (points = price of 1 order)
                    active TINYINT(1) NOT NULL DEFAULT 1,
                    sort INT NOT NULL DEFAULT 0,
                    UNIQUE KEY uq_plan_code (code)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS point_packs (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    points INT NOT NULL,
                    price_fils INT NOT NULL,
                    active TINYINT(1) NOT NULL DEFAULT 1,
                    sort INT NOT NULL DEFAULT 0
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS invoices (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    restaurant_id INT NOT NULL,
                    description VARCHAR(255) NOT NULL,
                    amount_fils INT NOT NULL,
                    status VARCHAR(20) NOT NULL DEFAULT 'open',
                    provider_ref VARCHAR(120) NULL,
                    pdf_path VARCHAR(255) NULL,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    INDEX idx_inv_rest (restaurant_id)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS cashier_devices (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    restaurant_id INT NOT NULL,
                    token_hash CHAR(64) NOT NULL,
                    pin_version INT NOT NULL,
                    user_agent VARCHAR(255) DEFAULT '',
                    ip VARCHAR(64) DEFAULT '',
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    last_used_at DATETIME NULL,
                    revoked_at DATETIME NULL,
                    UNIQUE KEY uq_device_hash (token_hash),
                    INDEX idx_device_rest (restaurant_id)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS cashier_pin_attempts (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    restaurant_id INT NOT NULL,
                    device_key CHAR(64) NOT NULL,
                    failed_count INT NOT NULL DEFAULT 0,
                    locked_until DATETIME NULL,
                    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE KEY uq_pin_attempt (restaurant_id, device_key)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS cashier_pin_fail_log (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    restaurant_id INT NOT NULL,
                    device_key CHAR(64) NOT NULL,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    INDEX idx_fail_rest (restaurant_id, created_at)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            """)
            cur.execute("SELECT COUNT(*) AS n FROM plans")
            if int((cur.fetchone() or {}).get("n") or 0) == 0:
                cur.executemany(
                    """
                    INSERT INTO plans (code, type, name_en, name_ar, price_fils, active, sort)
                    VALUES (%s, %s, %s, %s, %s, 1, %s)
                    """,
                    [
                        ("monthly", "monthly", "Monthly", "شهري", 15000, 1),
                        ("yearly", "yearly", "Yearly", "سنوي", 150000, 2),
                        ("points", "points", "Pay per order", "الدفع لكل طلب", 100, 3),
                    ],
                )
            cur.execute("SELECT COUNT(*) AS n FROM point_packs")
            if int((cur.fetchone() or {}).get("n") or 0) == 0:
                cur.executemany(
                    "INSERT INTO point_packs (points, price_fils, active, sort) VALUES (%s, %s, 1, %s)",
                    [(500, 50000, 1), (1000, 100000, 2), (2500, 250000, 3)],
                )
            conn.commit()
    finally:
        conn.close()


def _backfill_legacy_subscriptions() -> None:
    """Restaurants that existed before trials stay on the legacy plan."""
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE restaurants
                SET subscription_status = 'legacy'
                WHERE subscription_status IS NULL OR subscription_status = ''
                """
            )
            conn.commit()
    finally:
        conn.close()


def _parse_dt(value: Any) -> Optional[datetime]:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str) and value:
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return None
    return None


def access_state(restaurant: Optional[Dict[str, Any]]) -> str:
    """trialing, active, or expired. Legacy accounts stay active. Nothing is blocked yet."""
    if not restaurant:
        return "expired"
    status = (restaurant.get("subscription_status") or "legacy").strip()
    if status == "trialing":
        ends = _parse_dt(restaurant.get("trial_ends_at"))
        if ends and ends <= datetime.now():
            return "expired"
        return "trialing"
    if status in ("past_due", "expired"):
        return "expired"
    return "active"


RESERVED_SLUGS = {
    "admin", "api", "app", "login", "signup", "dashboard", "static", "pager",
    "order", "r", "p", "docs", "www", "main", "support", "billing",
}
_SLUG_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")


def slug_problem(slug: str) -> Optional[str]:
    """None when the slug can be registered. Otherwise 'invalid' or 'taken'."""
    value = (slug or "").strip().lower()
    if not (3 <= len(value) <= 40) or not _SLUG_RE.match(value) or value in RESERVED_SLUGS:
        return "invalid" if value not in RESERVED_SLUGS else "taken"
    if len(value) < 3 or len(value) > 40 or not _SLUG_RE.match(value):
        return "invalid"
    if get_restaurant_by_slug(value):
        return "taken"
    return None


def start_restaurant_trial(restaurant_id: int, plan_code: str, days: Optional[int] = None) -> None:
    if days is None:
        try:
            days = int(get_platform_setting("trial_days") or "3")
        except ValueError:
            days = 3
    days = max(0, min(int(days), 60))
    ends = datetime.now() + timedelta(days=days)
    chosen = plan_code if plan_code in ("monthly", "yearly", "points") else "monthly"
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE restaurants
                SET plan_code = %s, subscription_status = 'trialing', trial_ends_at = %s
                WHERE id = %s
                """,
                (chosen, ends, restaurant_id),
            )
            conn.commit()
    finally:
        conn.close()


def set_restaurant_password(restaurant_id: int, password_hash: str) -> None:
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE restaurants SET password_hash = %s WHERE id = %s",
                (password_hash, restaurant_id),
            )
            conn.commit()
    finally:
        conn.close()


def set_restaurant_brand(restaurant_id: int, brand_color: str) -> None:
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE restaurants
                SET brand_color = %s, onboarded_at = COALESCE(onboarded_at, %s)
                WHERE id = %s
                """,
                (brand_color, datetime.now(), restaurant_id),
            )
            conn.commit()
    finally:
        conn.close()


def email_in_use(email: str) -> bool:
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM restaurants WHERE email = %s LIMIT 1", (email.strip().lower(),))
            return cur.fetchone() is not None
    finally:
        conn.close()


def _token_hash(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


def create_password_reset(restaurant_id: int, raw_token: str) -> None:
    digest = _token_hash(raw_token)
    expires = datetime.now() + timedelta(hours=1)
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE password_reset_tokens
                SET used_at = %s
                WHERE restaurant_id = %s AND used_at IS NULL
                """,
                (datetime.now(), restaurant_id),
            )
            cur.execute(
                """
                INSERT INTO password_reset_tokens (restaurant_id, token_hash, expires_at)
                VALUES (%s, %s, %s)
                """,
                (restaurant_id, digest, expires),
            )
            conn.commit()
    finally:
        conn.close()


def reset_token_restaurant(raw_token: str) -> Optional[int]:
    """Return the restaurant id when the token is unused and unexpired."""
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT restaurant_id, expires_at, used_at
                FROM password_reset_tokens
                WHERE token_hash = %s
                LIMIT 1
                """,
                (_token_hash(raw_token),),
            )
            row = cur.fetchone()
            if not row or row.get("used_at"):
                return None
            expires = row.get("expires_at")
            if not isinstance(expires, datetime) or expires <= datetime.now():
                return None
            return int(row["restaurant_id"])
    finally:
        conn.close()


def consume_password_reset(raw_token: str) -> Optional[int]:
    conn = get_db_connection()
    try:
        conn.begin()
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, restaurant_id, expires_at, used_at
                FROM password_reset_tokens
                WHERE token_hash = %s
                LIMIT 1
                FOR UPDATE
                """,
                (_token_hash(raw_token),),
            )
            row = cur.fetchone()
            if not row or row.get("used_at"):
                conn.rollback()
                return None
            expires = row.get("expires_at")
            if not isinstance(expires, datetime) or expires <= datetime.now():
                conn.rollback()
                return None
            cur.execute(
                "UPDATE password_reset_tokens SET used_at = %s WHERE id = %s",
                (datetime.now(), row["id"]),
            )
            conn.commit()
            return int(row["restaurant_id"])
    finally:
        conn.close()


def ensure_table(name: str, ddl: str) -> None:
    """Create a table when information_schema says it is missing."""
    if not _IDENT.match(name):
        raise ValueError("invalid SQL identifier")
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT COUNT(*) AS n
                FROM information_schema.TABLES
                WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s
                """,
                (name,),
            )
            row = cur.fetchone() or {}
            count = row.get("n", 0) if isinstance(row, dict) else row[0]
            if int(count or 0):
                return
            cur.execute(ddl)
            conn.commit()
    finally:
        conn.close()


def ensure_column(table: str, column: str, definition: str) -> None:
    """Add a column when it is missing. Later batches can reuse this for new columns."""
    if not _IDENT.match(table) or not _IDENT.match(column):
        raise ValueError("invalid SQL identifier")
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT COUNT(*) AS n
                FROM information_schema.COLUMNS
                WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s AND COLUMN_NAME = %s
                """,
                (table, column),
            )
            row = cur.fetchone() or {}
            count = row.get("n", 0) if isinstance(row, dict) else row[0]
            if int(count or 0):
                return
            cur.execute(f"ALTER TABLE `{table}` ADD COLUMN `{column}` {definition}")
            conn.commit()
    finally:
        conn.close()


def ensure_index(table: str, name: str, ddl: str) -> None:
    if not _IDENT.match(table) or not _IDENT.match(name):
        raise ValueError("invalid SQL identifier")
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT COUNT(*) AS n
                FROM information_schema.STATISTICS
                WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s AND INDEX_NAME = %s
                """,
                (table, name),
            )
            row = cur.fetchone() or {}
            count = row.get("n", 0) if isinstance(row, dict) else row[0]
            if int(count or 0):
                return
            cur.execute(ddl)
            conn.commit()
    finally:
        conn.close()


def get_platform_setting(key: str, default: str = "") -> str:
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT svalue FROM platform_settings WHERE skey=%s", (key,))
            row = cur.fetchone()
            if not row:
                return default
            return str(row.get("svalue") if isinstance(row, dict) else row[0])
    except Exception:
        return default
    finally:
        conn.close()


def _copy_deprecated_point_price() -> None:
    """One-time fill: points plans stored the order price in point_price_fils."""
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE plans
                SET price_fils = point_price_fils
                WHERE (price_fils IS NULL OR price_fils = 0)
                  AND point_price_fils IS NOT NULL AND point_price_fils > 0
                  AND deleted_at IS NULL
                """
            )
            conn.commit()
    finally:
        conn.close()


def _ensure_billing() -> None:
    ensure_column("restaurants", "suspended_at", "DATETIME NULL")
    ensure_column("restaurants", "manual_plan_until", "DATE NULL")
    ensure_column("plans", "blurb_en", "VARCHAR(180) NULL")
    ensure_column("plans", "blurb_ar", "VARCHAR(180) NULL")
    ensure_column("plans", "features", "JSON NULL")
    ensure_column("plans", "featured", "TINYINT(1) NOT NULL DEFAULT 0")
    ensure_column("plans", "badge_en", "VARCHAR(80) NULL")
    ensure_column("plans", "badge_ar", "VARCHAR(80) NULL")
    ensure_column("plans", "deleted_at", "DATETIME NULL")
    ensure_column("point_packs", "deleted_at", "DATETIME NULL")
    _copy_deprecated_point_price()
    ensure_column("invoices", "number", "VARCHAR(32) NULL")
    ensure_column("invoices", "lines", "LONGTEXT NULL")
    ensure_column("invoices", "subtotal_fils", "INT NOT NULL DEFAULT 0")
    ensure_column("invoices", "total_fils", "INT NOT NULL DEFAULT 0")
    ensure_column("invoices", "currency", "VARCHAR(8) NOT NULL DEFAULT 'BHD'")
    ensure_column("invoices", "issued_at", "DATETIME NULL")
    ensure_column("invoices", "due_at", "DATETIME NULL")
    ensure_column("invoices", "paid_at", "DATETIME NULL")
    ensure_column("invoices", "paid_method", "VARCHAR(32) NULL")
    ensure_column("invoices", "card_last4", "VARCHAR(4) NULL")
    ensure_column("invoices", "kind", "VARCHAR(24) NULL")
    ensure_column("invoices", "related_invoice_id", "INT NULL")
    ensure_column("invoices", "pay_token", "VARCHAR(80) NULL")
    ensure_column("invoices", "period_end", "DATE NULL")
    ensure_column("invoices", "pack_id", "INT NULL")
    ensure_column("invoices", "points", "INT NULL")
    ensure_index("invoices", "uq_invoice_number", "ALTER TABLE invoices ADD UNIQUE KEY uq_invoice_number (number)")
    ensure_table(
        "point_wallets",
        """
        CREATE TABLE point_wallets (
            restaurant_id INT PRIMARY KEY,
            balance INT NOT NULL DEFAULT 0,
            mode ENUM('prepaid','postpaid') NOT NULL DEFAULT 'prepaid',
            updated_at DATETIME NULL
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """,
    )
    ensure_table(
        "point_ledger",
        """
        CREATE TABLE point_ledger (
            id INT AUTO_INCREMENT PRIMARY KEY,
            restaurant_id INT NOT NULL,
            delta INT NOT NULL,
            reason VARCHAR(255) NOT NULL,
            kind ENUM('topup','order','adjust','refund') NOT NULL,
            ref_invoice_id INT NULL,
            admin_id INT NULL,
            created_at DATETIME NOT NULL,
            INDEX idx_ledger_rest (restaurant_id, id)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """,
    )
    ensure_table(
        "platform_settings",
        """
        CREATE TABLE platform_settings (
            skey VARCHAR(64) PRIMARY KEY,
            svalue TEXT NOT NULL
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """,
    )
    ensure_table(
        "admin_audit_log",
        """
        CREATE TABLE admin_audit_log (
            id INT AUTO_INCREMENT PRIMARY KEY,
            admin_id INT NOT NULL,
            restaurant_id INT NULL,
            action VARCHAR(80) NOT NULL,
            details TEXT NULL,
            ip VARCHAR(64) NULL,
            created_at DATETIME NOT NULL,
            INDEX idx_audit_rest (restaurant_id, id)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """,
    )
    ensure_table(
        "invoice_counters",
        """
        CREATE TABLE invoice_counters (
            year INT PRIMARY KEY,
            last_n INT NOT NULL
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """,
    )
    ensure_table(
        "email_log",
        """
        CREATE TABLE email_log (
            id INT AUTO_INCREMENT PRIMARY KEY,
            restaurant_id INT NULL,
            kind VARCHAR(40) NOT NULL,
            ref_key VARCHAR(120) NOT NULL,
            to_email VARCHAR(190) NOT NULL,
            status ENUM('sent','failed','skipped') NOT NULL,
            error TEXT NULL,
            created_at DATETIME NOT NULL,
            UNIQUE KEY uq_email_kind_ref (kind, ref_key),
            INDEX idx_email_rest (restaurant_id, id)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """,
    )
    support = (os.getenv("SUPPORT_EMAIL") or os.getenv("SUPER_ADMIN_EMAIL") or "support@paiger.app").strip()
    billing = (os.getenv("SUPER_ADMIN_EMAIL") or support).strip()
    defaults = {
        "trial_days": "3",
        "points_empty_rule": "block",
        "low_points_at": "100",
        "renewal_retry_days": "3",
        "seller_name": "Code Crafter",
        "seller_cr": "169261-1",
        "seller_address": "Manama, Kingdom of Bahrain",
        "billing_email": billing,
        "support_email": support,
    }
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            for key, value in defaults.items():
                cur.execute(
                    "INSERT INTO platform_settings (skey, svalue) VALUES (%s, %s) ON DUPLICATE KEY UPDATE skey=skey",
                    (key, value),
                )
            seeds = {
                "monthly": {
                    "en": ["Unlimited orders", "iPad cashier and order board", "Your branding on every page", "Reports and prep times"],
                    "ar": ["طلبات بلا حدود", "كاشير الآيباد ولوحة الطلبات", "علامتك على كل صفحة", "تقارير وأوقات التحضير"],
                },
                "yearly": {
                    "en": ["Unlimited orders", "Everything in Monthly", "Priority support"],
                    "ar": ["طلبات بلا حدود", "كل مزايا الشهري", "دعم بأولوية"],
                },
                "points": {
                    "en": ["Top up points in advance, or pay at the end of the month", "Alert before your balance runs out", "Everything in Monthly"],
                    "ar": ["اشحن نقاط مقدماً، أو ادفع نهاية الشهر", "تنبيه قبل ما يخلص رصيدك", "كل مزايا الشهري"],
                },
            }
            cur.execute(
                "SELECT code, features FROM plans WHERE code IN ('monthly','yearly','points') AND deleted_at IS NULL"
            )
            for row in cur.fetchall():
                if row.get("features"):
                    continue
                cur.execute(
                    "UPDATE plans SET features=%s WHERE code=%s AND features IS NULL",
                    (json.dumps(seeds[row["code"]], ensure_ascii=False), row["code"]),
                )
                if row["code"] == "yearly":
                    cur.execute(
                        "UPDATE plans SET featured=1, badge_en=%s, badge_ar=%s WHERE code='yearly' AND featured=0 AND badge_en IS NULL",
                        ("Best value", "الأوفر"),
                    )
                    cur.execute(
                        "UPDATE plans SET featured=0 WHERE code<>'yearly' AND featured=1 AND badge_en IS NULL AND deleted_at IS NULL"
                    )
            conn.commit()
    finally:
        conn.close()

# ─────────────────────────────────────────────────────────────
# SUPER ADMIN QUERIES
# ─────────────────────────────────────────────────────────────

def get_super_admin_by_identifier(identifier: str) -> Optional[Dict[str, Any]]:
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM super_admins WHERE username = %s OR email = %s LIMIT 1",
                (identifier, identifier)
            )
            return _serialize_row(cur.fetchone(), include_secrets=True)
    finally:
        conn.close()

def get_super_admin_by_id(admin_id: int) -> Optional[Dict[str, Any]]:
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id, username, email, full_name, created_at FROM super_admins WHERE id = %s", (admin_id,))
            return _serialize_row(cur.fetchone())
    finally:
        conn.close()


def log_admin_login_attempt(ip: str, username: str, success: bool) -> None:
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO admin_login_attempts (ip, username, success, created_at)
                VALUES (%s, %s, %s, %s)
                """,
                ((ip or "")[:64], (username or "")[:150], 1 if success else 0, datetime.now()),
            )
            conn.commit()
    finally:
        conn.close()


def admin_login_failures(ip: str, within_minutes: int = 15) -> int:
    since = datetime.now() - timedelta(minutes=within_minutes)
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT COUNT(*) AS n
                FROM admin_login_attempts
                WHERE ip=%s AND success=0 AND created_at>=%s
                """,
                ((ip or "")[:64], since),
            )
            return int((cur.fetchone() or {}).get("n") or 0)
    finally:
        conn.close()

def get_platform_stats() -> Dict[str, Any]:
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            # 1. Total & Active Restaurants
            cur.execute("SELECT COUNT(*) as total, SUM(is_active = 1) as active FROM restaurants")
            rest_row = cur.fetchone() or {"total": 0, "active": 0}

            # 2. Total Orders All Time
            cur.execute("SELECT COUNT(*) as total FROM orders")
            total_orders = (cur.fetchone() or {}).get("total", 0)

            # 3. Orders Today
            cur.execute("SELECT COUNT(*) as today FROM orders WHERE DATE(created_at) = CURDATE()")
            today_orders = (cur.fetchone() or {}).get("today", 0)

            # 4. Active Orders Now (waiting, ringing, onway)
            cur.execute("SELECT COUNT(*) as active_now FROM orders WHERE status IN ('waiting', 'ringing', 'onway')")
            active_now = (cur.fetchone() or {}).get("active_now", 0)

            # 5. Completed Orders Today
            cur.execute("SELECT COUNT(*) as done_today FROM orders WHERE status = 'done' AND DATE(completed_at) = CURDATE()")
            done_today = (cur.fetchone() or {}).get("done_today", 0)

            # 6. Recent Orders
            cur.execute("""
                SELECT o.*, r.name as restaurant_display_name, r.slug as restaurant_slug
                FROM orders o
                LEFT JOIN restaurants r ON o.restaurant_id = r.id
                ORDER BY o.id DESC LIMIT 10
            """)
            recent_orders = [_serialize_row(r) for r in cur.fetchall()]

            return {
                "total_restaurants": int(rest_row.get("total", 0) or 0),
                "active_restaurants": int(rest_row.get("active", 0) or 0),
                "total_orders": int(total_orders or 0),
                "today_orders": int(today_orders or 0),
                "active_orders_now": int(active_now or 0),
                "done_today": int(done_today or 0),
                "recent_orders": recent_orders
            }
    finally:
        conn.close()

def get_all_restaurants(limit: int = 100, search: str = "") -> List[Dict[str, Any]]:
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            if search.strip():
                term = f"%{search.strip()}%"
                cur.execute("""
                    SELECT r.*, 
                           COUNT(o.id) as total_orders_count,
                           SUM(DATE(o.created_at) = CURDATE()) as today_orders_count,
                           SUM(o.status IN ('waiting', 'ringing', 'onway')) as active_orders_count
                    FROM restaurants r
                    LEFT JOIN orders o ON r.id = o.restaurant_id
                    WHERE r.name LIKE %s OR r.slug LIKE %s OR r.email LIKE %s
                    GROUP BY r.id
                    ORDER BY r.id DESC
                    LIMIT %s
                """, (term, term, term, limit))
            else:
                cur.execute("""
                    SELECT r.*, 
                           COUNT(o.id) as total_orders_count,
                           SUM(DATE(o.created_at) = CURDATE()) as today_orders_count,
                           SUM(o.status IN ('waiting', 'ringing', 'onway')) as active_orders_count
                    FROM restaurants r
                    LEFT JOIN orders o ON r.id = o.restaurant_id
                    GROUP BY r.id
                    ORDER BY r.id DESC
                    LIMIT %s
                """, (limit,))
            rows = cur.fetchall()
            return [_serialize_row(r) for r in rows]
    finally:
        conn.close()

def create_restaurant(
    name: str,
    slug: str,
    email: str,
    password_hash: str,
    phone: str = "",
    brand_color: str = "#C05621",
    brand_icon: str = "cup.and.saucer.fill",
    avg_prep_time: int = 5
) -> Optional[Dict[str, Any]]:
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO restaurants (name, slug, email, password_hash, phone, brand_color, brand_icon, avg_prep_time, is_active)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 1)
            """, (name.strip(), slug.strip().lower(), email.strip().lower(), password_hash, phone.strip(), brand_color.strip() or "#C05621", brand_icon.strip() or "cup.and.saucer.fill", avg_prep_time))
            new_id = cur.lastrowid
            conn.commit()
            return get_restaurant_by_id(new_id)
    finally:
        conn.close()

def update_restaurant(
    restaurant_id: int,
    name: str,
    slug: str,
    email: str,
    phone: str = "",
    brand_color: str = "#C05621",
    brand_icon: str = "cup.and.saucer.fill",
    avg_prep_time: int = 5,
    is_active: int = 1,
    new_password_hash: Optional[str] = None
) -> Optional[Dict[str, Any]]:
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            if new_password_hash:
                cur.execute("""
                    UPDATE restaurants
                    SET name=%s, slug=%s, email=%s, phone=%s, brand_color=%s, brand_icon=%s, avg_prep_time=%s, is_active=%s, password_hash=%s
                    WHERE id=%s
                """, (name.strip(), slug.strip().lower(), email.strip().lower(), phone.strip(), brand_color.strip(), brand_icon.strip(), avg_prep_time, is_active, new_password_hash, restaurant_id))
            else:
                cur.execute("""
                    UPDATE restaurants
                    SET name=%s, slug=%s, email=%s, phone=%s, brand_color=%s, brand_icon=%s, avg_prep_time=%s, is_active=%s
                    WHERE id=%s
                """, (name.strip(), slug.strip().lower(), email.strip().lower(), phone.strip(), brand_color.strip(), brand_icon.strip(), avg_prep_time, is_active, restaurant_id))
            conn.commit()
            return get_restaurant_by_id(restaurant_id)
    finally:
        conn.close()

def delete_restaurant(restaurant_id: int):
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM orders WHERE restaurant_id = %s", (restaurant_id,))
            cur.execute("DELETE FROM restaurants WHERE id = %s", (restaurant_id,))
            conn.commit()
    finally:
        conn.close()

# ─────────────────────────────────────────────────────────────
# RESTAURANT TENANT QUERIES
# ─────────────────────────────────────────────────────────────

def get_restaurant_by_id(restaurant_id: int) -> Optional[Dict[str, Any]]:
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM restaurants WHERE id = %s", (restaurant_id,))
            return _serialize_row(cur.fetchone())
    finally:
        conn.close()

def get_restaurant_for_login(identifier: str) -> Optional[Dict[str, Any]]:
    ident = identifier.strip().lower()
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM restaurants WHERE email = %s OR slug = %s LIMIT 1",
                (ident, ident)
            )
            return _serialize_row(cur.fetchone(), include_secrets=True)
    finally:
        conn.close()

def get_restaurant_by_slug(slug: str) -> Optional[Dict[str, Any]]:
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM restaurants WHERE slug = %s", (slug.strip().lower(),))
            return _serialize_row(cur.fetchone())
    finally:
        conn.close()

def get_restaurant_by_email(email: str) -> Optional[Dict[str, Any]]:
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM restaurants WHERE email = %s", (email.strip().lower(),))
            return _serialize_row(cur.fetchone())
    finally:
        conn.close()

def get_restaurant_stats(restaurant_id: int) -> Dict[str, Any]:
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            # Total orders
            cur.execute("SELECT COUNT(*) as total FROM orders WHERE restaurant_id = %s", (restaurant_id,))
            total = (cur.fetchone() or {}).get("total", 0) or 0

            # Today orders
            cur.execute("SELECT COUNT(*) as today FROM orders WHERE restaurant_id = %s AND DATE(created_at) = CURDATE()", (restaurant_id,))
            today = (cur.fetchone() or {}).get("today", 0) or 0

            # Waiting
            cur.execute("SELECT COUNT(*) as waiting FROM orders WHERE restaurant_id = %s AND status = 'waiting'", (restaurant_id,))
            waiting = (cur.fetchone() or {}).get("waiting", 0) or 0

            # Ringing
            cur.execute("SELECT COUNT(*) as ringing FROM orders WHERE restaurant_id = %s AND status = 'ringing'", (restaurant_id,))
            ringing = (cur.fetchone() or {}).get("ringing", 0) or 0

            # Onway
            cur.execute("SELECT COUNT(*) as onway FROM orders WHERE restaurant_id = %s AND status = 'onway'", (restaurant_id,))
            onway = (cur.fetchone() or {}).get("onway", 0) or 0

            # Done
            cur.execute("SELECT COUNT(*) as done FROM orders WHERE restaurant_id = %s AND status = 'done'", (restaurant_id,))
            done = (cur.fetchone() or {}).get("done", 0) or 0

            # Done today
            cur.execute("SELECT COUNT(*) as done_today FROM orders WHERE restaurant_id = %s AND status = 'done' AND DATE(completed_at) = CURDATE()", (restaurant_id,))
            done_today = (cur.fetchone() or {}).get("done_today", 0) or 0

            # Average prep time calculated
            cur.execute("""
                SELECT AVG(TIMESTAMPDIFF(SECOND, created_at, completed_at) / 60.0) as avg_mins
                FROM orders 
                WHERE restaurant_id = %s AND status = 'done' AND completed_at IS NOT NULL AND created_at IS NOT NULL
            """, (restaurant_id,))
            avg_row = cur.fetchone()
            calc_avg = avg_row.get("avg_mins") if avg_row else None
            if calc_avg and 0 < float(calc_avg) < 120:
                final_avg = round(float(calc_avg), 1)
            else:
                final_avg = 5.0

            return {
                "total_orders": total,
                "today_orders": today,
                "waiting_orders": waiting,
                "ringing_orders": ringing,
                "onway_orders": onway,
                "active_orders": waiting + ringing + onway,
                "done_orders": done,
                "done_today": done_today,
                "avg_prep_mins": final_avg
            }
    finally:
        conn.close()

def get_restaurant_orders(
    restaurant_id: int, 
    limit: int = 150, 
    status: Optional[str] = None,
    search: Optional[str] = None
) -> List[Dict[str, Any]]:
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            query = "SELECT * FROM orders WHERE restaurant_id = %s"
            params: List[Any] = [restaurant_id]

            if status and status.strip() and status != "all":
                query += " AND status = %s"
                params.append(status.strip())

            if search and search.strip():
                query += " AND (order_number LIKE %s OR customer_name LIKE %s OR notes LIKE %s)"
                s_term = f"%{search.strip()}%"
                params.extend([s_term, s_term, s_term])

            query += """
                ORDER BY 
                    CASE status
                        WHEN 'ringing' THEN 1
                        WHEN 'onway' THEN 2
                        WHEN 'waiting' THEN 3
                        WHEN 'done' THEN 4
                        ELSE 5
                    END,
                    id DESC
                LIMIT %s
            """
            params.append(limit)
            cur.execute(query, tuple(params))
            rows = cur.fetchall()
            return [_serialize_row(r) for r in rows]
    finally:
        conn.close()

# ─────────────────────────────────────────────────────────────
# CORE ORDER MANAGEMENT
# ─────────────────────────────────────────────────────────────

def create_order(
    order_number: str,
    restaurant_id: int,
    customer_name: str = "",
    notes: str = "",
    restaurant_name: str = "",
    brand_color: str = "",
    brand_icon: str = "",
    cashier_device_id: Optional[int] = None,
) -> Optional[Dict[str, Any]]:
    token = uuid.uuid4().hex[:10]
    
    # Resolve restaurant details if provided by ID
    rest = get_restaurant_by_id(restaurant_id)
    if rest:
        final_restaurant = restaurant_name.strip() or rest.get("name") or "بيجر الطلبات"
        final_color = brand_color.strip() or rest.get("brand_color") or "#C05621"
        final_icon = brand_icon.strip() or rest.get("brand_icon") or "cup.and.saucer.fill"
    else:
        final_restaurant = restaurant_name.strip() or get_setting("restaurant_name", "بيجر الطلبات")
        final_color = brand_color.strip() or "#C05621"
        final_icon = brand_icon.strip() or "cup.and.saucer.fill"

    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO orders (restaurant_id, order_number, customer_name, notes, token, status, restaurant_name, brand_color, brand_icon, cashier_device_id)
                VALUES (%s, %s, %s, %s, %s, 'waiting', %s, %s, %s, %s)
                """,
                (restaurant_id, order_number, customer_name, notes, token, final_restaurant, final_color, final_icon, cashier_device_id)
            )
            order_id = cur.lastrowid
            conn.commit()
            return get_order_by_id(order_id)
    finally:
        conn.close()

def get_order_by_id(order_id: int) -> Optional[Dict[str, Any]]:
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM orders WHERE id = %s", (order_id,))
            row = cur.fetchone()
            return _serialize_row(row)
    finally:
        conn.close()

def get_order_by_token(token: str) -> Optional[Dict[str, Any]]:
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM orders WHERE token = %s", (token,))
            row = cur.fetchone()
            return _serialize_row(row)
    finally:
        conn.close()

def find_active_order_number(restaurant_id: int, order_number: str) -> Optional[Dict[str, Any]]:
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT * FROM orders
                WHERE restaurant_id = %s AND order_number = %s
                  AND status IN ('waiting', 'ringing', 'onway')
                ORDER BY id DESC
                LIMIT 1
                """,
                (restaurant_id, str(order_number).strip()),
            )
            return _serialize_row(cur.fetchone())
    finally:
        conn.close()


def mark_order_scanned(order_id: int) -> Optional[Dict[str, Any]]:
    """Set scanned_at the first time only. Returns the order when this call set it."""
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE orders
                SET scanned_at = %s
                WHERE id = %s AND scanned_at IS NULL
                """,
                (datetime.now(), order_id),
            )
            changed = cur.rowcount
            conn.commit()
        if not changed:
            return None
        return get_order_by_id(order_id)
    finally:
        conn.close()


def cashier_today_stats(restaurant_id: int) -> Dict[str, Any]:
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    SUM(DATE(created_at) = CURDATE()) AS today_count,
                    SUM(called_at IS NOT NULL AND DATE(called_at) = CURDATE()) AS called_count,
                    AVG(CASE
                        WHEN called_at IS NOT NULL AND DATE(called_at) = CURDATE()
                        THEN TIMESTAMPDIFF(SECOND, created_at, called_at)
                    END) AS avg_seconds
                FROM orders
                WHERE restaurant_id = %s
                """,
                (restaurant_id,),
            )
            row = cur.fetchone() or {}
        if not isinstance(row, dict):
            return {"today_count": 0, "called_count": 0, "avg_seconds": None}
        return {
            "today_count": int(row.get("today_count") or 0),
            "called_count": int(row.get("called_count") or 0),
            "avg_seconds": float(row["avg_seconds"]) if row.get("avg_seconds") is not None else None,
        }
    finally:
        conn.close()


def get_order_by_number_and_restaurant(order_number: str, restaurant_id: int) -> Optional[Dict[str, Any]]:
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT * FROM orders 
                WHERE order_number = %s AND restaurant_id = %s 
                ORDER BY id DESC LIMIT 1
            """, (str(order_number).strip(), restaurant_id))
            row = cur.fetchone()
            return _serialize_row(row)
    finally:
        conn.close()

_PUBLIC_ORDER_FIELDS = (
    "id", "token", "order_number", "status", "restaurant_id",
    "created_at", "called_at", "completed_at",
)


def get_recent_public_orders(restaurant_id: int, since: datetime) -> List[Dict[str, Any]]:
    """Fields Dawrak reads from the public list. No names, notes, or phone."""
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, token, order_number, status, restaurant_id,
                       created_at, called_at, completed_at
                FROM orders
                WHERE restaurant_id = %s
                  AND status IN ('waiting', 'ringing', 'onway')
                  AND created_at >= %s
                ORDER BY id DESC
                LIMIT 150
                """,
                (restaurant_id, since),
            )
            rows = []
            for row in cur.fetchall():
                item = _serialize_row(row) or {}
                rows.append({key: item.get(key) for key in _PUBLIC_ORDER_FIELDS})
            return rows
    finally:
        conn.close()


def get_all_orders(limit: int = 150, restaurant_id: Optional[int] = None) -> List[Dict[str, Any]]:
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            if restaurant_id:
                cur.execute(
                    """
                    SELECT o.*, r.name as restaurant_display_name, r.slug as restaurant_slug, r.brand_color as restaurant_color
                    FROM orders o
                    LEFT JOIN restaurants r ON o.restaurant_id = r.id
                    WHERE o.restaurant_id = %s
                    ORDER BY 
                        CASE o.status
                            WHEN 'ringing' THEN 1
                            WHEN 'onway' THEN 2
                            WHEN 'waiting' THEN 3
                            WHEN 'done' THEN 4
                            ELSE 5
                        END,
                        o.id DESC
                    LIMIT %s
                    """,
                    (restaurant_id, limit)
                )
            else:
                cur.execute(
                    """
                    SELECT o.*, r.name as restaurant_display_name, r.slug as restaurant_slug, r.brand_color as restaurant_color
                    FROM orders o
                    LEFT JOIN restaurants r ON o.restaurant_id = r.id
                    ORDER BY 
                        CASE o.status
                            WHEN 'ringing' THEN 1
                            WHEN 'onway' THEN 2
                            WHEN 'waiting' THEN 3
                            WHEN 'done' THEN 4
                            ELSE 5
                        END,
                        o.id DESC
                    LIMIT %s
                    """,
                    (limit,)
                )
            rows = cur.fetchall()
            return [_serialize_row(r) for r in rows]
    finally:
        conn.close()

def update_order_status(order_id: int, status: str) -> Optional[Dict[str, Any]]:
    now = datetime.now()
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            if status == 'ringing':
                cur.execute(
                    """
                    UPDATE orders 
                    SET status = %s, called_at = %s, call_count = call_count + 1
                    WHERE id = %s
                    """,
                    (status, now, order_id)
                )
            elif status == 'done':
                cur.execute(
                    """
                    UPDATE orders 
                    SET status = %s, completed_at = %s
                    WHERE id = %s
                    """,
                    (status, now, order_id)
                )
            else:
                cur.execute(
                    """
                    UPDATE orders SET status = %s WHERE id = %s
                    """,
                    (status, order_id)
                )
            conn.commit()
            return get_order_by_id(order_id)
    finally:
        conn.close()

def delete_order(order_id: int):
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM orders WHERE id = %s", (order_id,))
            conn.commit()
    finally:
        conn.close()

def format_arabic_time(total_minutes: int) -> str:
    if total_minutes <= 0:
        return 'أقل من دقيقة'
    if total_minutes == 1:
        return 'دقيقة واحدة'
    if total_minutes == 2:
        return 'دقيقتان'
    if 3 <= total_minutes <= 10:
        return f'{total_minutes} دقائق'
    if 11 <= total_minutes < 60:
        return f'{total_minutes} دقيقة'
    
    hours = total_minutes // 60
    mins = total_minutes % 60
    
    h_str = '1 ساعة' if hours == 1 else ('ساعتان' if hours == 2 else f'{hours} ساعات')
    if mins == 0:
        return h_str
    if mins == 1:
        return f'{h_str} ودقيقة'
    if mins == 2:
        return f'{h_str} ودقيقتان'
    if 3 <= mins <= 10:
        return f'{h_str} و {mins} دقائق'
    return f'{h_str} و {mins} دقيقة'

def get_order_queue_info(order_id: int) -> Dict[str, Any]:
    order = get_order_by_id(order_id)
    if not order:
        return {"orders_ahead": 0, "estimated_wait": "0 دقيقة", "estimated_wait_minutes": 0, "avg_prep_mins": 5.0}

    status = order["status"]
    if status in ("ringing", "onway", "done"):
        return {
            "orders_ahead": 0,
            "estimated_wait": "جاهز للاستلام الآن! 🎉" if status != "done" else "تم الاستلام",
            "estimated_wait_minutes": 0,
            "avg_prep_mins": 0
        }

    rest_id = order.get("restaurant_id", 1)

    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            # 1. Count active orders ahead in the SAME restaurant's waiting queue
            cur.execute(
                """
                SELECT COUNT(*) as count FROM orders 
                WHERE restaurant_id = %s AND status = 'waiting' AND id < %s
                """,
                (rest_id, order_id)
            )
            ahead_row = cur.fetchone()
            orders_ahead = ahead_row["count"] if ahead_row else 0

            # 2. Calculate average prep time based on completed orders in THIS restaurant
            cur.execute(
                """
                SELECT 
                    AVG(TIMESTAMPDIFF(SECOND, created_at, completed_at) / 60.0) as avg_mins
                FROM orders 
                WHERE restaurant_id = %s AND status = 'done' AND completed_at IS NOT NULL AND created_at IS NOT NULL
                """,
                (rest_id,)
            )
            row = cur.fetchone()
            avg_mins = row["avg_mins"] if row and row["avg_mins"] is not None else None

            # Fallback to restaurant's configured avg_prep_time or default 5 mins
            if not avg_mins or float(avg_mins) <= 0 or float(avg_mins) > 90:
                rest = get_restaurant_by_id(rest_id)
                avg_mins = float(rest.get("avg_prep_time", 5) if rest else 5.0)
            else:
                avg_mins = round(float(avg_mins), 1)

            # 3. Estimated minutes = (orders_ahead + 1) * average prep time
            est_total_mins = max(2, round((orders_ahead + 1) * avg_mins))
            est_wait_str = format_arabic_time(est_total_mins)

            return {
                "orders_ahead": orders_ahead,
                "estimated_wait": est_wait_str,
                "estimated_wait_minutes": int(est_total_mins),
                "avg_prep_mins": avg_mins
            }
    finally:
        conn.close()

# ─────────────────────────────────────────────────────────────
# SETTINGS & PUSH SUBSCRIPTIONS & LIVE ACTIVITIES
# ─────────────────────────────────────────────────────────────

def get_setting(key: str, default: str = "") -> str:
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT `value` FROM settings WHERE `key` = %s", (key,))
            row = cur.fetchone()
            return row["value"] if row else default
    finally:
        conn.close()

def set_restaurant_logo(restaurant_id: int, logo_url: str) -> None:
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE restaurants SET logo_url = %s WHERE id = %s",
                (logo_url, restaurant_id),
            )
            conn.commit()
    finally:
        conn.close()

def set_setting(key: str, value: str):
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO settings (`key`, `value`) VALUES (%s, %s)
                ON DUPLICATE KEY UPDATE `value` = VALUES(`value`)
                """,
                (key, value)
            )
            conn.commit()
    finally:
        conn.close()

def save_push_subscription(token: str, endpoint: str, p256dh: str, auth: str, lang: str = "en"):
    chosen = lang if lang in ("en", "ar") else "en"
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO push_subscriptions (token, endpoint, p256dh, auth, lang)
                VALUES (%s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                    token = VALUES(token),
                    p256dh = VALUES(p256dh),
                    auth = VALUES(auth),
                    lang = VALUES(lang)
                """,
                (token, endpoint, p256dh, auth, chosen)
            )
            conn.commit()
    finally:
        conn.close()

def get_push_subscriptions_by_token(token: str) -> List[Dict[str, Any]]:
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM push_subscriptions WHERE token = %s", (token,))
            rows = cur.fetchall()
            return [_serialize_row(r) for r in rows]
    finally:
        conn.close()

def delete_push_subscription(endpoint: str):
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM push_subscriptions WHERE endpoint = %s", (endpoint,))
            conn.commit()
    finally:
        conn.close()

def save_live_activity_token(order_token: str, push_token: str, device_type: str = "ios_live_activity"):
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO live_activity_tokens (order_token, push_token, device_type)
                VALUES (%s, %s, %s)
                ON DUPLICATE KEY UPDATE
                    order_token = VALUES(order_token),
                    device_type = VALUES(device_type),
                    updated_at = NOW()
                """,
                (order_token, push_token, device_type)
            )
            conn.commit()
    finally:
        conn.close()

def has_live_activity(order_token: str) -> bool:
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT 1 FROM live_activity_tokens WHERE order_token = %s AND push_token <> '' LIMIT 1",
                (order_token,),
            )
            return cur.fetchone() is not None
    finally:
        conn.close()


def get_live_activity_tokens_by_token(order_token: str) -> List[Dict[str, Any]]:
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM live_activity_tokens WHERE order_token = %s", (order_token,))
            rows = cur.fetchall()
            return [_serialize_row(r) for r in rows]
    finally:
        conn.close()

# ─────────────────────────────────────────────────────────────
# CUSTOM PAGES (PRIVACY, TERMS, ABOUT, ETC.)
# ─────────────────────────────────────────────────────────────

def get_all_custom_pages(published_only: bool = False, footer_only: bool = False) -> List[Dict[str, Any]]:
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            conditions = []
            if published_only:
                conditions.append("is_published = 1")
            if footer_only:
                conditions.append("show_in_footer = 1")
            
            where_sql = f"WHERE {' AND '.join(conditions)}" if conditions else ""
            cur.execute(f"SELECT * FROM custom_pages {where_sql} ORDER BY sort_order ASC, id ASC")
            rows = cur.fetchall()
            return [_serialize_row(r) for r in rows]
    finally:
        conn.close()

def get_custom_page_by_slug(slug: str) -> Optional[Dict[str, Any]]:
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM custom_pages WHERE slug = %s", (slug,))
            row = cur.fetchone()
            return _serialize_row(row)
    finally:
        conn.close()

def get_custom_page_by_id(page_id: int) -> Optional[Dict[str, Any]]:
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM custom_pages WHERE id = %s", (page_id,))
            row = cur.fetchone()
            return _serialize_row(row)
    finally:
        conn.close()

def create_custom_page(
    slug: str,
    title: str,
    content: str,
    title_en: str = "",
    content_en: str = "",
    meta_description: str = "",
    is_published: int = 1,
    show_in_footer: int = 1,
    sort_order: int = 0
) -> Dict[str, Any]:
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO custom_pages 
                (slug, title, title_en, content, content_en, meta_description, is_published, show_in_footer, sort_order)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (slug, title, title_en, content, content_en, meta_description, is_published, show_in_footer, sort_order)
            )
            page_id = cur.lastrowid
            conn.commit()
            return get_custom_page_by_id(page_id)
    finally:
        conn.close()

def update_custom_page(
    page_id: int,
    title: Optional[str] = None,
    slug: Optional[str] = None,
    content: Optional[str] = None,
    title_en: Optional[str] = None,
    content_en: Optional[str] = None,
    meta_description: Optional[str] = None,
    is_published: Optional[int] = None,
    show_in_footer: Optional[int] = None,
    sort_order: Optional[int] = None
) -> Optional[Dict[str, Any]]:
    current = get_custom_page_by_id(page_id)
    if not current:
        return None

    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE custom_pages SET
                    title = %s,
                    slug = %s,
                    content = %s,
                    title_en = %s,
                    content_en = %s,
                    meta_description = %s,
                    is_published = %s,
                    show_in_footer = %s,
                    sort_order = %s,
                    updated_at = NOW()
                WHERE id = %s
                """,
                (
                    title if title is not None else current["title"],
                    slug if slug is not None else current["slug"],
                    content if content is not None else current["content"],
                    title_en if title_en is not None else current.get("title_en", ""),
                    content_en if content_en is not None else current.get("content_en", ""),
                    meta_description if meta_description is not None else current.get("meta_description", ""),
                    is_published if is_published is not None else current["is_published"],
                    show_in_footer if show_in_footer is not None else current["show_in_footer"],
                    sort_order if sort_order is not None else current["sort_order"],
                    page_id
                )
            )
            conn.commit()
            return get_custom_page_by_id(page_id)
    finally:
        conn.close()

def delete_custom_page(page_id: int):
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM custom_pages WHERE id = %s", (page_id,))
            conn.commit()
    finally:
        conn.close()
