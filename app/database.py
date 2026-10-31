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
