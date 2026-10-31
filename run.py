import socket
import uvicorn
from app.main import app, get_local_ip

def main():
    local_ip = get_local_ip()
    port = 8000

    print("=" * 65)
    print("🚀  WEB PAGER SYSTEM IS STARTING...")
    print("=" * 65)
    print(f"👉 Cashier Dashboard (لوحة الكاشير):")
    print(f"   http://localhost:{port}/cashier")
    print(f"   http://{local_ip}:{port}/cashier")
    print("-" * 65)
    print(f"📱 Testing on Mobile (iPhone / Android على نفس شبكة الواي فاي):")
    print(f"   افتح الكاشير، أنشئ طلباً، وامسح الـ QR بكاميرا هاتفك!")
    print(f"   رابط الشبكة: http://{local_ip}:{port}")
    print("=" * 65)

    uvicorn.run("app.main:app", host="0.0.0.0", port=port, reload=True)

if __name__ == "__main__":
    main()

