import sqlite3
import uuid
from datetime import datetime
from pathlib import Path

DB_PATH = Path(__file__).parent.parent / "paiger.db"

def get_db_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with get_db_connection() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                order_number TEXT NOT NULL,
                customer_name TEXT DEFAULT '',
                notes TEXT DEFAULT '',
                token TEXT UNIQUE NOT NULL,
                status TEXT NOT NULL DEFAULT 'waiting',
                call_count INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                called_at TIMESTAMP,
                completed_at TIMESTAMP
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS push_subscriptions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                token TEXT NOT NULL,
                endpoint TEXT UNIQUE NOT NULL,
                p256dh TEXT NOT NULL,
                auth TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.commit()

def create_order(order_number: str, customer_name: str = "", notes: str = ""):
    token = uuid.uuid4().hex[:10]
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO orders (order_number, customer_name, notes, token, status)
            VALUES (?, ?, ?, ?, 'waiting')
            """,
            (order_number, customer_name, notes, token)
        )
        order_id = cursor.lastrowid
        conn.commit()
        return get_order_by_id(order_id)

def get_order_by_id(order_id: int):
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM orders WHERE id = ?", (order_id,))
        row = cursor.fetchone()
        return dict(row) if row else None

def get_order_by_token(token: str):
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM orders WHERE token = ?", (token,))
        row = cursor.fetchone()
        return dict(row) if row else None

def get_all_orders(limit: int = 100):
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT * FROM orders 
            ORDER BY 
                CASE status
                    WHEN 'ringing' THEN 1
                    WHEN 'onway' THEN 2
                    WHEN 'waiting' THEN 3
                    WHEN 'done' THEN 4
                    ELSE 5
                END,
                id DESC
            LIMIT ?
            """,
            (limit,)
        )
        return [dict(r) for r in cursor.fetchall()]

def update_order_status(order_id: int, status: str):
    now = datetime.now().isoformat()
    with get_db_connection() as conn:
        cursor = conn.cursor()
        if status == 'ringing':
            cursor.execute(
                """
                UPDATE orders 
                SET status = ?, called_at = ?, call_count = call_count + 1
                WHERE id = ?
                """,
                (status, now, order_id)
            )
        elif status == 'done':
            cursor.execute(
                """
                UPDATE orders 
                SET status = ?, completed_at = ?
                WHERE id = ?
                """,
                (status, now, order_id)
            )
        else:
            cursor.execute(
                """
                UPDATE orders SET status = ? WHERE id = ?
                """,
                (status, order_id)
            )
        conn.commit()
        return get_order_by_id(order_id)

def delete_order(order_id: int):
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM orders WHERE id = ?", (order_id,))
        conn.commit()

def save_push_subscription(token: str, endpoint: str, p256dh: str, auth: str):
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO push_subscriptions (token, endpoint, p256dh, auth)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(endpoint) DO UPDATE SET token = excluded.token, p256dh = excluded.p256dh, auth = excluded.auth
            """,
            (token, endpoint, p256dh, auth)
        )
        conn.commit()

def get_push_subscriptions_by_token(token: str):
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM push_subscriptions WHERE token = ?", (token,))
        return [dict(r) for r in cursor.fetchall()]

def delete_push_subscription(endpoint: str):
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM push_subscriptions WHERE endpoint = ?", (endpoint,))
        conn.commit()

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

def get_order_queue_info(order_id: int):
    order = get_order_by_id(order_id)
    if not order:
        return {"orders_ahead": 0, "estimated_wait": "0 دقيقة", "avg_prep_mins": 6.0}

    status = order["status"]
    if status in ("ringing", "onway", "done"):
        return {
            "orders_ahead": 0,
            "estimated_wait": "جاهز للاستلام الآن! 🎉" if status != "done" else "تم الاستلام",
            "avg_prep_mins": 0
        }

    with get_db_connection() as conn:
        cursor = conn.cursor()
        
        # 1. Count active orders ahead of this order in waiting queue
        cursor.execute(
            """
            SELECT COUNT(*) FROM orders 
            WHERE status = 'waiting' AND id < ?
            """,
            (order_id,)
        )
        orders_ahead = cursor.fetchone()[0]

        # 2. Calculate average prep time based on completed orders
        cursor.execute(
            """
            SELECT 
                AVG((strftime('%s', completed_at) - strftime('%s', created_at)) / 60.0) as avg_mins
            FROM orders 
            WHERE status = 'done' AND completed_at IS NOT NULL AND created_at IS NOT NULL
            """
        )
        row = cursor.fetchone()
        avg_mins = row["avg_mins"] if row and row["avg_mins"] else None

        # Fallback to realistic standard prep time (e.g. 6 mins) if no orders completed yet
        if not avg_mins or avg_mins <= 0 or avg_mins > 90:
            avg_mins = 6.0
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
