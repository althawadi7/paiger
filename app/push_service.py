import json
import base64
import asyncio
from pathlib import Path
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives import serialization
from pywebpush import webpush, WebPushException
from . import database as db
from . import i18n

VAPID_FILE = Path(__file__).parent / "vapid_keys.json"

def get_or_create_vapid_keys():
    if VAPID_FILE.exists():
        try:
            with open(VAPID_FILE, "r") as f:
                return json.load(f)
        except Exception:
            pass

    priv = ec.generate_private_key(ec.SECP256R1())
    pub = priv.public_key()
    raw_pub = pub.public_bytes(
        encoding=serialization.Encoding.X962,
        format=serialization.PublicFormat.UncompressedPoint
    )
    b64_pub = base64.urlsafe_b64encode(raw_pub).rstrip(b'=').decode('utf-8')
    pem_priv = priv.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption()
    ).decode('utf-8')

    keys = {
        "public_key": b64_pub,
        "private_key": pem_priv
    }
    with open(VAPID_FILE, "w") as f:
        json.dump(keys, f, indent=2)
    return keys

KEYS = get_or_create_vapid_keys()

def get_public_key():
    return KEYS["public_key"]

def send_push_to_subscription(sub, payload):
    sub_info = {
        "endpoint": sub["endpoint"],
        "keys": {
            "p256dh": sub["p256dh"],
            "auth": sub["auth"]
        }
    }
    try:
        webpush(
            subscription_info=sub_info,
            data=json.dumps(payload),
            vapid_private_key=KEYS["private_key"],
            vapid_claims={"sub": "mailto:support@dawrak.app"},
            headers={"Urgency": "high", "TTL": "60"}
        )
        return True
    except WebPushException as ex:
        # If subscription is expired or cancelled (410 Gone / 404), remove it
        if ex.response and ex.response.status_code in (404, 410):
            db.delete_push_subscription(sub["endpoint"])
        return False
    except Exception as e:
        print("WebPush error:", e)
        return False

async def notify_order_push(order: dict):
    token = order.get("token")
    if not token:
        return

    subs = db.get_push_subscriptions_by_token(token)
    if not subs:
        return

    order_num = str(order.get("order_number", ""))
    loop = asyncio.get_event_loop()
    for sub in subs:
        lang = sub.get("lang") if sub.get("lang") in ("en", "ar") else "en"
        table = i18n.AR if lang == "ar" else i18n.EN
        title = table.get("pager.pushTitle") or i18n.EN.get("pager.pushTitle") or "Your order is ready"
        body = (table.get("pager.pushBody") or i18n.EN.get("pager.pushBody") or "Order #{n} is ready.").replace("{n}", order_num)
        payload = {
            "title": title,
            "body": body,
            "icon": "/static/img/brand-logo.png?v=dawrak",
            "badge": "/static/img/brand-logo.png?v=dawrak",
            "url": f"/pager/{token}",
            "token": token,
            "order_id": order.get("id"),
            "order_number": order_num,
            "status": order.get("status")
        }
        loop.run_in_executor(None, send_push_to_subscription, sub, payload)

