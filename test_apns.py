import sys
sys.path.append('/var/www/pager')
from app import apns_service
import httpx

jwt = apns_service.get_apns_jwt()
headers = {
    'apns-topic': 'com.dawrak.app.push-type.liveactivity',
    'authorization': f'bearer {jwt}',
    'apns-push-type': 'liveactivity'
}
with httpx.Client(http2=True, timeout=10.0) as client:
    resp = client.post(
        'https://api.push.apple.com/3/device/1234567890abcdef1234567890abcdef1234567890abcdef1234567890abcdef',
        headers=headers,
        json={"aps":{"timestamp":123456789,"event":"update","content-state":{"statusText":"test","isRinging":True}}}
    )
    print('STATUS:', resp.status_code)
    print('BODY:', resp.text)
