import io
import json
import socket
import asyncio
from typing import Dict, List
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Request
from fastapi.responses import HTMLResponse, StreamingResponse, RedirectResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
import qrcode
from pathlib import Path

from . import database as db
from . import push_service

BASE_DIR = Path(__file__).parent
app = FastAPI(title="Web Pager - جرس الطلبات الذكي")

# Static files & templates
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

def get_local_ip() -> str:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(('8.8.8.8', 80))
        ip = s.getsockname()[0]
    except Exception:
        ip = "127.0.0.1"
    finally:
        s.close()
    return ip

class ConnectionManager:
    def __init__(self):
        self.pager_connections: Dict[str, List[WebSocket]] = {}
        self.cashier_connections: List[WebSocket] = []

    async def connect_pager(self, token: str, websocket: WebSocket):
        await websocket.accept()
        if token not in self.pager_connections:
            self.pager_connections[token] = []
        self.pager_connections[token].append(websocket)

    def disconnect_pager(self, token: str, websocket: WebSocket):
        if token in self.pager_connections:
            if websocket in self.pager_connections[token]:
                self.pager_connections[token].remove(websocket)
            if not self.pager_connections[token]:
                del self.pager_connections[token]

    async def connect_cashier(self, websocket: WebSocket):
        await websocket.accept()
        self.cashier_connections.append(websocket)

    def disconnect_cashier(self, websocket: WebSocket):
        if websocket in self.cashier_connections:
            self.cashier_connections.remove(websocket)

    async def notify_pager(self, token: str, data: dict):
        if token in self.pager_connections:
            message = json.dumps(data)
            for ws in list(self.pager_connections[token]):
                try:
                    await ws.send_text(message)
                except Exception:
                    pass

    async def notify_cashiers(self, data: dict):
        message = json.dumps(data)
        for ws in list(self.cashier_connections):
            try:
                await ws.send_text(message)
            except Exception:
                pass

manager = ConnectionManager()

class CreateOrderRequest(BaseModel):
    order_number: str
    customer_name: str = ""
    notes: str = ""

class PushSubscriptionRequest(BaseModel):
    token: str
    endpoint: str
    p256dh: str
    auth: str

@app.on_event("startup")
def startup_event():
    db.init_db()

@app.api_route("/sw.js", methods=["GET", "HEAD"])
async def get_service_worker():
    return FileResponse(
        str(BASE_DIR / "static" / "sw.js"),
        media_type="application/javascript",
        headers={"Service-Worker-Allowed": "/", "Cache-Control": "no-cache"}
    )

@app.api_route("/manifest.json", methods=["GET", "HEAD"])
async def get_manifest():
    return FileResponse(str(BASE_DIR / "static" / "manifest.json"), media_type="application/json")

@app.get("/api/push/public_key")
async def get_push_public_key():
    return {"public_key": push_service.get_public_key()}

@app.post("/api/push/subscribe")
async def subscribe_push(req: PushSubscriptionRequest):
    db.save_push_subscription(req.token, req.endpoint, req.p256dh, req.auth)
    return {"status": "ok"}

@app.get("/", response_class=RedirectResponse)
async def index():
    return RedirectResponse(url="/cashier")

@app.get("/cashier", response_class=HTMLResponse)
async def cashier_page(request: Request):
    local_ip = get_local_ip()
    scheme = request.url.scheme
    port = request.url.port or (443 if scheme == "https" else 80)
    base_url = f"{scheme}://{local_ip}:{port}"
    return templates.TemplateResponse(
        "cashier.html", 
        {"request": request, "local_ip": local_ip, "port": port, "base_url": base_url}
    )

@app.get("/pager/{token}", response_class=HTMLResponse)
async def pager_page(request: Request, token: str):
    order = db.get_order_by_token(token)
    if not order:
        return HTMLResponse(
            """
            <div style="font-family:'Readex Pro',sans-serif; text-align:center; padding:50px; background:#08090d; color:#f3f5f9; height:100vh;">
                <h1>⚠️ طلب غير موجود | Order Not Found</h1>
                <p style="color:#8a93a6; margin-top:10px;">الرابط غير صحيح أو قد تم حذف الطلب من الكاشير.</p>
                <a href="/cashier" style="display:inline-block; margin-top:20px; color:#8b7bff; text-decoration:none;">العودة للرئيسية</a>
            </div>
            """, 
            status_code=404
        )
    local_ip = get_local_ip()
    scheme = request.url.scheme
    port = request.url.port or (443 if scheme == "https" else 80)
    base_url = f"{scheme}://{local_ip}:{port}"
    queue_info = db.get_order_queue_info(order["id"])
    return templates.TemplateResponse(
        "pager.html",
        {
            "request": request, 
            "order": order, 
            "token": token, 
            "base_url": base_url,
            "queue_info": queue_info
        }
    )

@app.get("/api/pager/{token}/status")
async def get_pager_status(token: str):
    order = db.get_order_by_token(token)
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    q_info = db.get_order_queue_info(order["id"])
    return {
        "id": order["id"],
        "order_number": order["order_number"],
        "customer_name": order["customer_name"],
        "notes": order["notes"],
        "status": order["status"],
        "orders_ahead": q_info["orders_ahead"],
        "estimated_wait": q_info["estimated_wait"]
    }

# Orders REST APIs
@app.get("/api/orders")
async def list_orders():
    return db.get_all_orders()

@app.post("/api/orders")
async def create_new_order(order_data: CreateOrderRequest):
    order = db.create_order(
        order_number=order_data.order_number,
        customer_name=order_data.customer_name,
        notes=order_data.notes
    )
    await manager.notify_cashiers({"event": "order_created", "order": order})
    return order

@app.post("/api/orders/{order_id}/ring")
@app.post("/api/orders/{order_id}/call")
async def ring_order(order_id: int):
    order = db.update_order_status(order_id, "ringing")
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    
    await manager.notify_pager(order["token"], {
        "event": "order_ringing",
        "order": order
    })
    await manager.notify_cashiers({"event": "order_updated", "order": order})
    
    # Trigger Web Push Notification
    try:
        asyncio.create_task(push_service.notify_order_push(order))
    except Exception as e:
        print("WebPush trigger error:", e)

    return order

@app.post("/api/orders/{order_id}/onway")
async def onway_order(order_id: int):
    order = db.update_order_status(order_id, "onway")
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    
    await manager.notify_pager(order["token"], {
        "event": "order_onway",
        "order": order
    })
    await manager.notify_cashiers({
        "event": "customer_onway",
        "order": order
    })
    return order

@app.post("/api/orders/{order_id}/done")
@app.post("/api/orders/{order_id}/complete")
async def complete_order(order_id: int):
    order = db.update_order_status(order_id, "done")
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    
    await manager.notify_pager(order["token"], {
        "event": "order_done",
        "order": order
    })
    await manager.notify_cashiers({"event": "order_updated", "order": order})
    return order

@app.post("/api/orders/{order_id}/waiting")
@app.post("/api/orders/{order_id}/reset")
async def reset_order(order_id: int):
    order = db.update_order_status(order_id, "waiting")
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    
    await manager.notify_pager(order["token"], {
        "event": "order_waiting",
        "order": order
    })
    await manager.notify_cashiers({"event": "order_updated", "order": order})
    return order

@app.delete("/api/orders/{order_id}")
async def delete_order(order_id: int):
    order = db.get_order_by_id(order_id)
    if order:
        db.delete_order(order_id)
        await manager.notify_pager(order["token"], {"event": "order_deleted"})
        await manager.notify_cashiers({"event": "order_deleted", "order_id": order_id})
    return {"status": "success"}

# QR Code Generator
@app.api_route("/api/qr/{token}", methods=["GET", "HEAD"])
async def get_qr_image(request: Request, token: str, host: str = None):
    if host:
        base = host.rstrip('/')
    else:
        client_host = request.headers.get("host") or f"{get_local_ip()}:8000"
        scheme = request.url.scheme
        base = f"{scheme}://{client_host}"

    url = f"{base}/pager/{token}"
    
    qr = qrcode.QRCode(
        version=1,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=10,
        border=2,
    )
    qr.add_data(url)
    qr.make(fit=True)
    img = qr.make_image(fill_color="#000000", back_color="#ffffff")
    
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return StreamingResponse(buf, media_type="image/png")

# WebSocket for client pager
@app.websocket("/ws/pager/{token}")
async def websocket_pager(websocket: WebSocket, token: str):
    await manager.connect_pager(token, websocket)
    order = db.get_order_by_token(token)
    if order:
        q_info = db.get_order_queue_info(order["id"])
        await websocket.send_text(json.dumps({
            "event": "connected",
            "order": order,
            "queue_info": q_info
        }))
    try:
        while True:
            data = await websocket.receive_text()
            try:
                msg = json.loads(data)
                if msg.get("action") == "onway" or msg.get("action") == "acknowledge":
                    if order:
                        updated = db.update_order_status(order["id"], "onway")
                        await manager.notify_cashiers({
                            "event": "customer_onway",
                            "order": updated
                        })
                        await manager.notify_pager(token, {
                            "event": "order_onway",
                            "order": updated
                        })
                elif msg.get("action") == "ping":
                    await websocket.send_text(json.dumps({"event": "pong"}))
            except Exception:
                pass
    except WebSocketDisconnect:
        manager.disconnect_pager(token, websocket)

# WebSocket for cashier dashboard
@app.websocket("/ws/cashier")
async def websocket_cashier(websocket: WebSocket):
    await manager.connect_cashier(websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect_cashier(websocket)
