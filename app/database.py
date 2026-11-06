import os
import uuid
from datetime import datetime
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

def _serialize_row(row: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not row:
        return None
    d = dict(row)
    for k, v in d.items():
        if isinstance(v, datetime):
            d[k] = v.isoformat()
        elif isinstance(v, Decimal):
            d[k] = int(v) if v % 1 == 0 else float(v)
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
            return _serialize_row(cur.fetchone())
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
    brand_icon: str = ""
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
                INSERT INTO orders (restaurant_id, order_number, customer_name, notes, token, status, restaurant_name, brand_color, brand_icon)
                VALUES (%s, %s, %s, %s, %s, 'waiting', %s, %s, %s)
                """,
                (restaurant_id, order_number, customer_name, notes, token, final_restaurant, final_color, final_icon)
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
        return {"orders_ahead": 0, "estimated_wait": "0 دقيقة", "avg_prep_mins": 5.0}

    status = order["status"]
    if status in ("ringing", "onway", "done"):
        return {
            "orders_ahead": 0,
            "estimated_wait": "جاهز للاستلام الآن! 🎉" if status != "done" else "تم الاستلام",
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

def save_push_subscription(token: str, endpoint: str, p256dh: str, auth: str):
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO push_subscriptions (token, endpoint, p256dh, auth)
                VALUES (%s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE 
                    token = VALUES(token),
                    p256dh = VALUES(p256dh),
                    auth = VALUES(auth)
                """,
                (token, endpoint, p256dh, auth)
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
