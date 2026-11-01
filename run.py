import os
import sys
import asyncio
from pathlib import Path
import uvicorn
from dotenv import load_dotenv

ENV_FILE = Path(__file__).parent / ".env"
load_dotenv(ENV_FILE)

from app.main import app, get_local_ip

BASE_DIR = Path(__file__).parent
CERT_FILE = str(BASE_DIR / "cert.pem")
KEY_FILE = str(BASE_DIR / "key.pem")

HOST = os.getenv("HOST", "127.0.0.1")
PORT = int(os.getenv("PORT", "8035"))
DOMAIN = os.getenv("DOMAIN", "pager.rahapp.com")

async def run_single_server():
    print("=" * 68)
    print(f"🚀  WEB PAGER SERVER RUNNING FOR DOMAIN: {DOMAIN}")
    print(f"👉 Listening on: http://{HOST}:{PORT}")
    print(f"👉 Public URL:   https://{DOMAIN}")
    print("=" * 68)

    config = uvicorn.Config(
        app,
        host=HOST,
        port=PORT,
        log_level="info",
        access_log=True,
        proxy_headers=True,
        forwarded_allow_ips="*"
    )
    server = uvicorn.Server(config)
    await server.serve()

async def run_dual_servers():
    local_ip = get_local_ip()
    http_port = 8000
    https_port = 8443

    print("=" * 68)
    print("🚀  WEB PAGER DUAL LOCAL DEV SERVER IS STARTING...")
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
        if "--dual" in sys.argv:
            asyncio.run(run_dual_servers())
        else:
            asyncio.run(run_single_server())
    except (KeyboardInterrupt, SystemExit):
        pass

if __name__ == "__main__":
    main()
