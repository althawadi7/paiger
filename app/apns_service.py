import time
import json
import jwt
import httpx
from pathlib import Path
from . import database as db

AUTH_KEY_PATH = Path(__file__).parent / "AuthKey_WQKP75FDML.p8"
KEY_ID = "WQKP75FDML"
TEAM_ID = "KRVGAD732T"
BUNDLE_ID = "com.dawrak.app"

_cached_token = None
_token_generated_at = 0

def get_apns_jwt() -> str:
    global _cached_token, _token_generated_at
    now = time.time()
    if _cached_token and (now - _token_generated_at) < 3000:
        return _cached_token

    with open(AUTH_KEY_PATH, "r") as f:
        secret = f.read()

    headers = {
        "alg": "ES256",
        "kid": KEY_ID
    }
    payload = {
        "iss": TEAM_ID,
        "iat": int(now)
    }
    token = jwt.encode(payload, secret, algorithm="ES256", headers=headers)
    _cached_token = token
    _token_generated_at = now
    return token

async def send_live_activity_update(
    push_token: str,
    order_number: str,
    status_text: str = "طلبك جاهز للاستلام 🔔",
    is_ringing: bool = True,
    phase: str = "calling"
):
    try:
        jwt_token = get_apns_jwt()

        # Clean order number for display (e.g. "8", never a hex token)
        clean_num = str(order_number).strip()
        if len(clean_num) > 5 or any(c.isalpha() for c in clean_num):
            digits = "".join(c for c in clean_num if c.isdigit())
            clean_num = digits[:4] if digits else "1"

        payload = {
            "aps": {
                "timestamp": int(time.time()),
                "event": "update",
                "content-state": {
                    "orderNumber": clean_num,
                    "statusText": status_text,
                    "isRinging": is_ringing,
                    "phase": phase
                }
            }
        }
        if is_ringing:
            payload["aps"]["alert"] = {
                "title": "🔔 طلبك جاهز للاستلام!",
                "body": f"طلب رقم {clean_num} جاهز، تفضل بالاستلام الآن",
                "sound": "default"
            }

        # Support both App and App Clip bundle IDs
        topics = [
            f"{BUNDLE_ID}.push-type.liveactivity",
            f"{BUNDLE_ID}.Clip.push-type.liveactivity"
        ]

        # Support both Development Sandbox and Production APNs
        endpoints = [
            "https://api.sandbox.push.apple.com/3/device/",
            "https://api.push.apple.com/3/device/"
        ]

        headers_base = {
            "apns-push-type": "liveactivity",
            "apns-priority": "10",
            "authorization": f"bearer {jwt_token}"
        }

        async with httpx.AsyncClient(http2=True, timeout=6.0) as client:
            for base_url in endpoints:
                url = f"{base_url}{push_token}"
                for topic in topics:
                    headers = dict(headers_base)
                    headers["apns-topic"] = topic
                    try:
                        resp = await client.post(url, headers=headers, json=payload)
                        print(f"[APNs] Update #{clean_num} via {base_url} (topic {topic}): {resp.status_code} {resp.text}")
                        if resp.status_code == 200:
                            return True
                    except Exception as req_err:
                        print(f"[APNs] Request error for {url}: {req_err}")
            return False
    except Exception as e:
        print(f"[APNs] Error sending Live Activity push: {e}")
        return False

async def notify_order_live_activity(
    order: dict,
    is_ringing: bool = True,
    phase: str = "calling",
    status_text: str = "طلبك جاهز للاستلام 🔔"
):
    token = order.get("token")
    if not token:
        return
    tokens = db.get_live_activity_tokens_by_token(token)
    if not tokens:
        print(f"[APNs] No Live Activity push tokens registered for order token {token}")
        return
    for item in tokens:
        push_token = item.get("push_token")
        if push_token:
            await send_live_activity_update(
                push_token=push_token,
                order_number=str(order.get("order_number", "")),
                status_text=status_text,
                is_ringing=is_ringing,
                phase=phase
            )
