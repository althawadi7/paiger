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
VAPID_PEM = Path(__file__).parent / "vapid_private.pem"

def get_or_create_vapid_keys():
    # 1. Prefer database persistent settings to survive redeploys and restarts
    try:
        db_pub = db.get_setting("vapid_public_key")
        db_priv = db.get_setting("vapid_private_key")
        if db_pub and db_priv:
            try:
                VAPID_PEM.write_text(db_priv)
            except Exception:
                pass
            return {"public_key": db_pub, "private_key": db_priv}
    except Exception:
        pass

    # 2. Check local JSON file
    if VAPID_FILE.exists():
        try:
            with open(VAPID_FILE, "r") as f:
                keys = json.load(f)
                if keys.get("public_key") and keys.get("private_key"):
                    try:
                        VAPID_PEM.write_text(keys["private_key"])
                        db.set_setting("vapid_public_key", keys["public_key"])
                        db.set_setting("vapid_private_key", keys["private_key"])
                    except Exception:
                        pass
                    return keys
        except Exception:
            pass

    # 3. Generate new EC P-256 pair
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
    try:
        with open(VAPID_FILE, "w") as f:
            json.dump(keys, f, indent=2)
    except Exception:
        pass
    try:
        VAPID_PEM.write_text(pem_priv)
    except Exception:
        pass
    try:
        db.set_setting("vapid_public_key", b64_pub)
        db.set_setting("vapid_private_key", pem_priv)
    except Exception:
        pass
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
    # Use PEM file path if available, or raw PEM string
    priv_key_ref = str(VAPID_PEM) if VAPID_PEM.exists() else KEYS["private_key"]
    try:
        webpush(
            subscription_info=sub_info,
            data=json.dumps(payload),
            vapid_private_key=priv_key_ref,
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
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = asyncio.get_event_loop()

    for sub in subs:
        lang = sub.get("lang") if sub.get("lang") in ("en", "ar") else "ar"
        table = i18n.AR if lang == "ar" else i18n.EN
        title = table.get("pager.pushTitle") or i18n.AR.get("pager.pushTitle") or "طلبك جاهز للاستلام!"
        body_tmpl = table.get("pager.pushBody") or i18n.AR.get("pager.pushBody") or "طلب رقم #{n} جاهز، تفضل بالاستلام."
        body = body_tmpl.replace("{n}", order_num)
        payload = {
            "title": title,
            "body": body,
            "icon": "/static/brand/icon-192.png",
            "badge": "/static/brand/icon-192.png",
            "url": f"/pager/{token}",
            "token": token,
            "order_id": order.get("id"),
            "order_number": order_num,
            "status": order.get("status") or "ringing"
        }
        loop.run_in_executor(None, send_push_to_subscription, sub, payload)
