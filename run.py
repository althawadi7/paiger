import asyncio
import socket
from pathlib import Path
import uvicorn
from app.main import app, get_local_ip

BASE_DIR = Path(__file__).parent
CERT_FILE = str(BASE_DIR / "cert.pem")
KEY_FILE = str(BASE_DIR / "key.pem")

async def run_dual_servers():
    local_ip = get_local_ip()
    http_port = 8000
    https_port = 8443

    print("=" * 68)
    print("🚀  WEB PAGER DUAL SERVER (HTTP & SECURE HTTPS) IS STARTING...")
    print("=" * 68)
    print(f"👉 لوحة الكاشير (Cashier Dashboard):")
    print(f"   HTTP:  http://localhost:{http_port}/cashier  (أو http://{local_ip}:{http_port}/cashier)")
    print(f"   HTTPS: https://localhost:{https_port}/cashier (أو https://{local_ip}:{https_port}/cashier)")
    print("-" * 68)
    print(f"📱 تجربة الجوال مع فلاش الكاميرا الحقيقي (Camera Torch LED):")
    print(f"   افتح رابط HTTPS على هاتفك لتشغيل فلاش الكاميرا الحقيقي والاهتزاز:")
    print(f"   👉 https://{local_ip}:{https_port}")
    print("=" * 68)

    config_http = uvicorn.Config(
        app, 
        host="0.0.0.0", 
        port=http_port, 
        log_level="info", 
        access_log=False
    )
    config_https = uvicorn.Config(
        app, 
        host="0.0.0.0", 
        port=https_port, 
        ssl_keyfile=KEY_FILE, 
        ssl_certfile=CERT_FILE, 
        log_level="info",
        access_log=False
    )

    server_http = uvicorn.Server(config_http)
    server_https = uvicorn.Server(config_https)

    await asyncio.gather(server_http.serve(), server_https.serve())

def main():
    try:
        asyncio.run(run_dual_servers())
    except (KeyboardInterrupt, SystemExit):
        pass

if __name__ == "__main__":
    main()
