import io
import os
import json
import logging
import socket
import asyncio
from urllib.parse import quote
from typing import Any, Dict, List, Optional
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Request, Form, Depends, status
from fastapi.responses import HTMLResponse, StreamingResponse, RedirectResponse, FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
import qrcode
import markdown
from pathlib import Path

from . import database as db
from . import push_service
from . import auth

logger = logging.getLogger("pager")

BASE_DIR = Path(__file__).parent
_enable_docs = os.getenv("ENABLE_DOCS") == "1"
app = FastAPI(
    title="Paiger SaaS - جرس الطلبات الذكي للمطاعم",
    docs_url="/docs" if _enable_docs else None,
    redoc_url="/redoc" if _enable_docs else None,
    openapi_url="/openapi.json" if _enable_docs else None,
)

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

def get_public_base_url(request: Request) -> str:
    env_domain = os.getenv("DOMAIN", "").strip()
    if env_domain:
        return f"https://{env_domain}"
    host = request.headers.get("x-forwarded-host") or request.headers.get("host")
    proto = request.headers.get("x-forwarded-proto", request.url.scheme)
    if host:
        return f"{proto}://{host}"
    return f"{request.url.scheme}://{get_local_ip()}:8035"

class ConnectionManager:
    def __init__(self):
        self.pager_connections: Dict[str, List[WebSocket]] = {}
        self.cashier_connections: List[Dict[str, Any]] = []

    async def connect_pager(self, token: str, websocket: WebSocket):
        await websocket.accept()
        token_str = str(token).strip()
        if token_str not in self.pager_connections:
            self.pager_connections[token_str] = []
        self.pager_connections[token_str].append(websocket)

    def disconnect_pager(self, token: str, websocket: WebSocket):
        token_str = str(token).strip()
        if token_str in self.pager_connections:
            if websocket in self.pager_connections[token_str]:
                self.pager_connections[token_str].remove(websocket)
            if not self.pager_connections[token_str]:
                del self.pager_connections[token_str]

    async def connect_cashier(self, websocket: WebSocket, restaurant_id: Optional[int]):
        await websocket.accept()
        self.cashier_connections.append({"ws": websocket, "restaurant_id": restaurant_id})

    def disconnect_cashier(self, websocket: WebSocket):
        self.cashier_connections = [c for c in self.cashier_connections if c["ws"] is not websocket]

    async def notify_pager(self, token: str, data: dict):
        token_str = str(token).strip()
        if token_str in self.pager_connections:
            message = json.dumps(data)
            dead_ws = []
            for ws in list(self.pager_connections[token_str]):
                try:
                    await ws.send_text(message)
                except Exception:
                    dead_ws.append(ws)
            for ws in dead_ws:
                self.disconnect_pager(token_str, ws)

    async def notify_cashiers(self, data: dict):
        payload = dict(data)
        restaurant_id = payload.get("restaurant_id")
        order = payload.get("order")
        if restaurant_id is None and isinstance(order, dict):
            restaurant_id = order.get("restaurant_id")
        if restaurant_id is not None:
            payload["restaurant_id"] = int(restaurant_id)
        message = json.dumps(payload)
        for conn in list(self.cashier_connections):
            conn_restaurant = conn.get("restaurant_id")
            if conn_restaurant is not None and payload.get("restaurant_id") is not None and int(conn_restaurant) != int(payload["restaurant_id"]):
                continue
            try:
                await conn["ws"].send_text(message)
            except Exception:
                pass

    async def broadcast_all(self, data: dict):
        await self.notify_cashiers(data)
        message = json.dumps(data)
        for token, ws_list in list(self.pager_connections.items()):
            for ws in list(ws_list):
                try:
                    await ws.send_text(message)
                except Exception:
                    pass

manager = ConnectionManager()

# ─────────────────────────────────────────────────────────────
# PYDANTIC SCHEMAS
# ─────────────────────────────────────────────────────────────

class CreateOrderRequest(BaseModel):
    order_number: str
    customer_name: str = ""
    notes: str = ""
    restaurant_id: Optional[int] = None
    restaurant_name: Optional[str] = ""
    brand_color: Optional[str] = ""
    brand_icon: Optional[str] = ""

class PushSubscriptionRequest(BaseModel):
    token: str
    endpoint: str
    p256dh: str
    auth: str

class ThemeSettingRequest(BaseModel):
    theme: str

class LiveActivityTokenRequest(BaseModel):
    push_token: Optional[str] = None
    pushToken: Optional[str] = None
    device_type: Optional[str] = "ios_live_activity"

class CreateRestaurantDto(BaseModel):
    name: str
    slug: str
    email: str
    password: str
    phone: Optional[str] = ""
    brand_color: Optional[str] = "#C05621"
    brand_icon: Optional[str] = "cup.and.saucer.fill"
    avg_prep_time: Optional[int] = 5

class UpdateRestaurantDto(BaseModel):
    name: Optional[str] = None
    slug: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    brand_color: Optional[str] = None
    brand_icon: Optional[str] = None
    avg_prep_time: Optional[int] = None
    is_active: Optional[int] = None
    password: Optional[str] = None

class MerchantSettingsDto(BaseModel):
    name: str
    phone: Optional[str] = ""
    brand_color: Optional[str] = "#C05621"
    brand_icon: Optional[str] = "cup.and.saucer.fill"
    avg_prep_time: Optional[int] = 5
    new_password: Optional[str] = None

class CreateCustomPageDto(BaseModel):
    slug: str
    title: str
    content: str
    title_en: Optional[str] = ""
    content_en: Optional[str] = ""
    meta_description: Optional[str] = ""
    is_published: Optional[int] = 1
    show_in_footer: Optional[int] = 1
    sort_order: Optional[int] = 0

class UpdateCustomPageDto(BaseModel):
    slug: Optional[str] = None
    title: Optional[str] = None
    content: Optional[str] = None
    title_en: Optional[str] = None
    content_en: Optional[str] = None
    meta_description: Optional[str] = None
    is_published: Optional[int] = None
    show_in_footer: Optional[int] = None
    sort_order: Optional[int] = None

@app.on_event("startup")
def startup_event():
    db.init_db()

# ─────────────────────────────────────────────────────────────
# APPLE APP CLIP & SYSTEM FILES
# ─────────────────────────────────────────────────────────────

@app.api_route("/.well-known/apple-app-site-association", methods=["GET", "HEAD"])
@app.api_route("/apple-app-site-association", methods=["GET", "HEAD"])
async def apple_app_site_association():
    aasa_path = BASE_DIR / "static" / "apple-app-site-association"
    if aasa_path.exists():
        return FileResponse(
            str(aasa_path),
            media_type="application/json",
            headers={"Cache-Control": "public, max-age=60"}
        )
    return JSONResponse(
        content={
            "appclips": {"apps": ["TEAMID.com.rahapp.pager.Clip"]},
            "applinks": {
                "apps": [],
                "details": [
                    {
                        "appIDs": ["TEAMID.com.rahapp.pager"],
                        "components": [{"/": "/pager/*"}]
                    }
                ]
            }
        },
        headers={"Cache-Control": "public, max-age=60"}
    )

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

# ─────────────────────────────────────────────────────────────
# SUPER ADMIN AUTH & DASHBOARD
# ─────────────────────────────────────────────────────────────

@app.get("/admin/login", response_class=HTMLResponse)
async def super_admin_login_page(request: Request):
    user = auth.get_current_super_admin(request)
    if user:
        return RedirectResponse(url="/admin", status_code=status.HTTP_302_FOUND)
    footer_pages = db.get_all_custom_pages(published_only=True, footer_only=True)
    return templates.TemplateResponse("super_admin_login.html", {"request": request, "error": None, "footer_pages": footer_pages})

@app.post("/admin/login", response_class=HTMLResponse)
async def super_admin_login_submit(
    request: Request,
    response: Response,
    identifier: str = Form(...),
    password: str = Form(...)
):
    admin = db.get_super_admin_by_identifier(identifier.strip())
    if not admin or not auth.verify_password(password, admin["password_hash"]):
        return templates.TemplateResponse(
            "super_admin_login.html",
            {"request": request, "error": "بيانات الدخول غير صحيحة، يرجى التحقق من الاسم أو كلمة المرور", "identifier": identifier},
            status_code=400
        )

    # Issue JWT token
    token = auth.create_access_token({
        "sub": str(admin["id"]),
        "username": admin["username"],
        "role": "super_admin"
    })

    redirect = RedirectResponse(url="/admin", status_code=status.HTTP_302_FOUND)
    redirect.set_cookie(
        key=auth.SUPER_ADMIN_COOKIE,
        value=token,
        httponly=True,
        max_age=86400 * 7,
        samesite="lax",
        secure=True
    )
    return redirect

@app.get("/admin/logout")
async def super_admin_logout():
    redirect = RedirectResponse(url="/admin/login", status_code=status.HTTP_302_FOUND)
    redirect.delete_cookie(auth.SUPER_ADMIN_COOKIE)
    return redirect

@app.get("/admin", response_class=HTMLResponse)
@app.get("/admin/dashboard", response_class=HTMLResponse)
async def super_admin_dashboard(request: Request):
    user = auth.get_current_super_admin(request)
    if not user:
        return RedirectResponse(url="/admin/login", status_code=status.HTTP_302_FOUND)

    admin_record = db.get_super_admin_by_id(int(user["sub"])) or {"full_name": "مدير النظام العام"}
    stats = db.get_platform_stats()
    restaurants = db.get_all_restaurants(limit=200)
    all_orders = db.get_all_orders(limit=300)
    custom_pages = db.get_all_custom_pages()

    return templates.TemplateResponse(
        "super_admin_dashboard.html",
        {
            "request": request,
            "admin_user": admin_record,
            "stats": stats,
            "restaurants": restaurants,
            "all_orders": all_orders,
            "custom_pages": custom_pages
        }
    )

# Super Admin Restaurant Management APIs
@app.post("/api/admin/restaurants")
async def api_admin_create_restaurant(req: CreateRestaurantDto, request: Request):
    user = auth.get_current_super_admin(request)
    if not user:
        raise HTTPException(status_code=401, detail="Unauthorized")

    # Check slug uniqueness
    existing_slug = db.get_restaurant_by_slug(req.slug)
    if existing_slug:
        raise HTTPException(status_code=400, detail="المعرف (slug) مستخدم بالفعل، اختر معرفاً آخر")

    existing_email = db.get_restaurant_by_email(req.email)
    if existing_email:
        raise HTTPException(status_code=400, detail="البريد الإلكتروني مستخدم بالفعل")

    pwd_hash = auth.hash_password(req.password)
    new_rest = db.create_restaurant(
        name=req.name,
        slug=req.slug,
        email=req.email,
        password_hash=pwd_hash,
        phone=req.phone or "",
        brand_color=req.brand_color or "#C05621",
        brand_icon=req.brand_icon or "cup.and.saucer.fill",
        avg_prep_time=req.avg_prep_time or 5
    )
    return {"status": "ok", "restaurant": new_rest}

@app.get("/api/admin/stats")
async def api_admin_stats(request: Request):
    user = auth.get_current_super_admin(request)
    if not user:
        raise HTTPException(status_code=401, detail="Unauthorized")
    return db.get_platform_stats()

@app.put("/api/admin/restaurants/{restaurant_id}")
async def api_admin_update_restaurant(restaurant_id: int, req: UpdateRestaurantDto, request: Request):
    user = auth.get_current_super_admin(request)
    if not user:
        raise HTTPException(status_code=401, detail="Unauthorized")

    rest = db.get_restaurant_by_id(restaurant_id)
    if not rest:
        raise HTTPException(status_code=404, detail="Restaurant not found")

    new_pwd_hash = auth.hash_password(req.password) if req.password and req.password.strip() else None

    updated = db.update_restaurant(
        restaurant_id=restaurant_id,
        name=req.name if req.name is not None else rest["name"],
        slug=req.slug if req.slug is not None else rest["slug"],
        email=req.email if req.email is not None else rest["email"],
        phone=req.phone if req.phone is not None else rest["phone"],
        brand_color=req.brand_color if req.brand_color is not None else rest["brand_color"],
        brand_icon=req.brand_icon if req.brand_icon is not None else rest["brand_icon"],
        avg_prep_time=req.avg_prep_time if req.avg_prep_time is not None else rest["avg_prep_time"],
        is_active=req.is_active if req.is_active is not None else rest["is_active"],
        new_password_hash=new_pwd_hash
    )
    return {"status": "ok", "restaurant": updated}

@app.post("/api/admin/restaurants/{restaurant_id}/toggle-status")
async def api_admin_toggle_restaurant_status(restaurant_id: int, request: Request):
    user = auth.get_current_super_admin(request)
    if not user:
        raise HTTPException(status_code=401, detail="Unauthorized")

    rest = db.get_restaurant_by_id(restaurant_id)
    if not rest:
        raise HTTPException(status_code=404, detail="Restaurant not found")

    new_status = 0 if rest.get("is_active", 1) == 1 else 1
    updated = db.update_restaurant(
        restaurant_id=restaurant_id,
        name=rest["name"],
        slug=rest["slug"],
        email=rest["email"],
        phone=rest["phone"],
        brand_color=rest["brand_color"],
        brand_icon=rest["brand_icon"],
        avg_prep_time=rest["avg_prep_time"],
        is_active=new_status
    )
    return {"status": "ok", "is_active": new_status, "restaurant": updated}

@app.delete("/api/admin/restaurants/{restaurant_id}")
async def api_admin_delete_restaurant(restaurant_id: int, request: Request):
    user = auth.get_current_super_admin(request)
    if not user:
        raise HTTPException(status_code=401, detail="Unauthorized")
    if restaurant_id == 1:
        raise HTTPException(status_code=400, detail="لا يمكن حذف المطعم الافتراضي الرئيسي")

    db.delete_restaurant(restaurant_id)
    return {"status": "ok"}

# ─────────────────────────────────────────────────────────────
# SUPER ADMIN CUSTOM PAGES MANAGEMENT APIS (CRUD)
# ─────────────────────────────────────────────────────────────

@app.get("/api/admin/pages")
async def api_admin_list_pages(request: Request):
    user = auth.get_current_super_admin(request)
    if not user:
        raise HTTPException(status_code=401, detail="Unauthorized")
    return {"status": "ok", "pages": db.get_all_custom_pages()}

@app.post("/api/admin/pages")
async def api_admin_create_page(req: CreateCustomPageDto, request: Request):
    user = auth.get_current_super_admin(request)
    if not user:
        raise HTTPException(status_code=401, detail="Unauthorized")

    clean_slug = req.slug.strip().lower().replace(" ", "-")
    existing = db.get_custom_page_by_slug(clean_slug)
    if existing:
        raise HTTPException(status_code=400, detail="المعرف (slug) مستخدم لصفحة أخرى بالفعل")

    page = db.create_custom_page(
        slug=clean_slug,
        title=req.title,
        content=req.content,
        title_en=req.title_en or "",
        content_en=req.content_en or "",
        meta_description=req.meta_description or "",
        is_published=req.is_published if req.is_published is not None else 1,
        show_in_footer=req.show_in_footer if req.show_in_footer is not None else 1,
        sort_order=req.sort_order or 0
    )
    return {"status": "ok", "page": page}

@app.put("/api/admin/pages/{page_id}")
async def api_admin_update_page(page_id: int, req: UpdateCustomPageDto, request: Request):
    user = auth.get_current_super_admin(request)
    if not user:
        raise HTTPException(status_code=401, detail="Unauthorized")

    page = db.get_custom_page_by_id(page_id)
    if not page:
        raise HTTPException(status_code=404, detail="الصفحة غير موجودة")

    clean_slug = req.slug.strip().lower().replace(" ", "-") if req.slug else page["slug"]
    if clean_slug != page["slug"]:
        existing = db.get_custom_page_by_slug(clean_slug)
        if existing and existing["id"] != page_id:
            raise HTTPException(status_code=400, detail="المعرف (slug) مستخدم لصفحة أخرى بالفعل")

    updated = db.update_custom_page(
        page_id=page_id,
        title=req.title if req.title is not None else page["title"],
        slug=clean_slug,
        content=req.content if req.content is not None else page["content"],
        title_en=req.title_en if req.title_en is not None else page.get("title_en", ""),
        content_en=req.content_en if req.content_en is not None else page.get("content_en", ""),
        meta_description=req.meta_description if req.meta_description is not None else page.get("meta_description", ""),
        is_published=req.is_published if req.is_published is not None else page["is_published"],
        show_in_footer=req.show_in_footer if req.show_in_footer is not None else page["show_in_footer"],
        sort_order=req.sort_order if req.sort_order is not None else page["sort_order"]
    )
    return {"status": "ok", "page": updated}

@app.delete("/api/admin/pages/{page_id}")
async def api_admin_delete_page(page_id: int, request: Request):
    user = auth.get_current_super_admin(request)
    if not user:
        raise HTTPException(status_code=401, detail="Unauthorized")

    page = db.get_custom_page_by_id(page_id)
    if not page:
        raise HTTPException(status_code=404, detail="الصفحة غير موجودة")
    if page["slug"] in ("privacy-policy", "terms-of-service"):
        raise HTTPException(status_code=400, detail="لا يمكن حذف سياسة الخصوصية أو شروط الاستخدام الأساسية")

    db.delete_custom_page(page_id)
    return {"status": "ok"}

@app.post("/api/admin/pages/{page_id}/toggle-published")
async def api_admin_toggle_page_published(page_id: int, request: Request):
    user = auth.get_current_super_admin(request)
    if not user:
        raise HTTPException(status_code=401, detail="Unauthorized")
    page = db.get_custom_page_by_id(page_id)
    if not page:
        raise HTTPException(status_code=404, detail="الصفحة غير موجودة")
    new_status = 0 if page["is_published"] == 1 else 1
    updated = db.update_custom_page(page_id=page_id, is_published=new_status)
    return {"status": "ok", "is_published": new_status, "page": updated}

@app.post("/api/admin/pages/{page_id}/toggle-footer")
async def api_admin_toggle_page_footer(page_id: int, request: Request):
    user = auth.get_current_super_admin(request)
    if not user:
        raise HTTPException(status_code=401, detail="Unauthorized")
    page = db.get_custom_page_by_id(page_id)
    if not page:
        raise HTTPException(status_code=404, detail="الصفحة غير موجودة")
    new_footer = 0 if page["show_in_footer"] == 1 else 1
    updated = db.update_custom_page(page_id=page_id, show_in_footer=new_footer)
    return {"status": "ok", "show_in_footer": new_footer, "page": updated}

# ─────────────────────────────────────────────────────────────
# RESTAURANT TENANT AUTH & DASHBOARD
# ─────────────────────────────────────────────────────────────

@app.get("/login", response_class=HTMLResponse)
@app.get("/restaurant/login", response_class=HTMLResponse)
async def restaurant_login_page(request: Request, next: Optional[str] = None):
    safe_next = _safe_next(next)
    user = auth.get_current_restaurant(request)
    if user:
        return RedirectResponse(url=_next_for_restaurant(user, safe_next), status_code=status.HTTP_302_FOUND)
    footer_pages = db.get_all_custom_pages(published_only=True, footer_only=True)
    return templates.TemplateResponse("restaurant_login.html", {
        "request": request, "error": None, "footer_pages": footer_pages, "next": safe_next or ""
    })

@app.post("/login", response_class=HTMLResponse)
@app.post("/restaurant/login", response_class=HTMLResponse)
async def restaurant_login_submit(
    request: Request,
    identifier: str = Form(...),
    password: str = Form(...),
    next: Optional[str] = Form(None)
):
    safe_next = _safe_next(next)
    ident = identifier.strip().lower()
    # Check by email or slug
    rest = db.get_restaurant_for_login(ident)
    if not rest or not auth.verify_password(password, rest["password_hash"]):
        return templates.TemplateResponse(
            "restaurant_login.html",
            {"request": request, "error": "بيانات الدخول غير صحيحة، يرجى التحقق من البريد أو اسم المستخدم وكلمة المرور", "identifier": identifier, "next": safe_next or ""},
            status_code=400
        )

    if rest.get("is_active") == 0:
        return templates.TemplateResponse(
            "restaurant_login.html",
            {"request": request, "error": "حساب هذا المطعم معطل حالياً من قبل الإدارة العامة", "identifier": identifier, "next": safe_next or ""},
            status_code=403
        )

    token = auth.create_access_token({
        "sub": str(rest["id"]),
        "slug": rest["slug"],
        "name": rest["name"],
        "role": "restaurant"
    })

    redirect = RedirectResponse(url=_next_for_restaurant(rest, safe_next), status_code=status.HTTP_302_FOUND)
    redirect.set_cookie(
        key=auth.RESTAURANT_COOKIE,
        value=token,
        httponly=True,
        max_age=86400 * 30,
        samesite="lax",
        secure=True
    )
    return redirect

@app.get("/logout")
@app.get("/restaurant/logout")
async def restaurant_logout():
    redirect = RedirectResponse(url="/login", status_code=status.HTTP_302_FOUND)
    redirect.delete_cookie(auth.RESTAURANT_COOKIE)
    return redirect

@app.get("/dashboard", response_class=HTMLResponse)
@app.get("/merchant", response_class=HTMLResponse)
async def restaurant_dashboard(request: Request):
    user = auth.get_current_restaurant(request)
    if not user:
        return RedirectResponse(url="/login", status_code=status.HTTP_302_FOUND)

    rest_id = int(user["sub"])
    rest = db.get_restaurant_by_id(rest_id)
    if not rest:
        return RedirectResponse(url="/logout", status_code=status.HTTP_302_FOUND)

    stats = db.get_restaurant_stats(rest_id)
    orders = db.get_restaurant_orders(rest_id, limit=150)

    return templates.TemplateResponse(
        "restaurant_dashboard.html",
        {
            "request": request,
            "restaurant": rest,
            "stats": stats,
            "orders": orders
        }
    )

@app.post("/api/merchant/settings")
async def api_merchant_save_settings(req: MerchantSettingsDto, request: Request):
    user = auth.get_current_restaurant(request)
    if not user:
        raise HTTPException(status_code=401, detail="Unauthorized")

    rest_id = int(user["sub"])
    rest = db.get_restaurant_by_id(rest_id)
    if not rest:
        raise HTTPException(status_code=404, detail="Restaurant not found")

    new_pwd_hash = auth.hash_password(req.new_password) if req.new_password and req.new_password.strip() else None

    updated = db.update_restaurant(
        restaurant_id=rest_id,
        name=req.name,
        slug=rest["slug"],
        email=rest["email"],
        phone=req.phone or rest.get("phone", ""),
        brand_color=req.brand_color or rest.get("brand_color", "#C05621"),
        brand_icon=req.brand_icon or rest.get("brand_icon", "cup.and.saucer.fill"),
        avg_prep_time=req.avg_prep_time or rest.get("avg_prep_time", 5),
        is_active=rest.get("is_active", 1),
        new_password_hash=new_pwd_hash
    )
    return {"status": "ok", "restaurant": updated}

# ─────────────────────────────────────────────────────────────
# CASHIER TERMINALS (PER-RESTAURANT & CENTRAL)
# ─────────────────────────────────────────────────────────────

@app.get("/r/{slug}", response_class=HTMLResponse)
async def restaurant_customer_track(request: Request, slug: str, num: Optional[str] = None):
    rest = db.get_restaurant_by_slug(slug)
    if not rest:
        raise HTTPException(status_code=404, detail="Restaurant not found")

    if num and num.strip():
        matched = db.get_order_by_number_and_restaurant(num.strip(), rest["id"])
        if matched and matched.get("token"):
            return RedirectResponse(url=f"/pager/{matched['token']}", status_code=status.HTTP_302_FOUND)

    return templates.TemplateResponse(
        "customer_track.html",
        {
            "request": request,
            "restaurant": rest,
            "num": num,
            "error": "لم يتم العثور على طلب نشط بهذا الرقم" if num else None
        }
    )

@app.get("/r/{slug}/cashier", response_class=HTMLResponse)
async def restaurant_dedicated_cashier(request: Request, slug: str):
    rest = db.get_restaurant_by_slug(slug)
    if not rest:
        raise HTTPException(status_code=404, detail="Restaurant not found")
    if not _can_open_restaurant(request, rest["id"]):
        return _login_redirect(request)

    local_ip = get_local_ip()
    base_url = get_public_base_url(request)
    port = request.url.port or (443 if request.url.scheme == "https" else 80)
    theme = db.get_setting("theme", "light")

    return templates.TemplateResponse(
        "cashier.html",
        {
            "request": request,
            "restaurant": rest,
            "local_ip": local_ip,
            "port": port,
            "base_url": base_url,
            "theme": theme
        }
    )

@app.get("/r/{slug}/print", response_class=HTMLResponse)
async def restaurant_qr_print_sheet(request: Request, slug: str):
    rest = db.get_restaurant_by_slug(slug)
    if not rest:
        raise HTTPException(status_code=404, detail="Restaurant not found")
    if not _can_open_restaurant(request, rest["id"]):
        return _login_redirect(request)

    return templates.TemplateResponse(
        "qr_print.html",
        {
            "request": request,
            "restaurant": rest
        }
    )

@app.get("/cashier", response_class=HTMLResponse)
async def default_cashier_redirect(request: Request):
    # If logged in as restaurant, redirect to their dedicated cashier
    rest_user = auth.get_current_restaurant(request)
    if rest_user and "slug" in rest_user:
        return RedirectResponse(url=f"/r/{rest_user['slug']}/cashier", status_code=status.HTTP_302_FOUND)

    if not auth.get_current_super_admin(request):
        return _login_redirect(request)

    rest = db.get_restaurant_by_id(1) or {
        "id": 1,
        "name": "بيجر الطلبات - الفرع الرئيسي",
        "slug": "main",
        "brand_color": "#C05621",
        "brand_icon": "cup.and.saucer.fill"
    }

    local_ip = get_local_ip()
    base_url = get_public_base_url(request)
    port = request.url.port or (443 if request.url.scheme == "https" else 80)
    theme = db.get_setting("theme", "light")

    return templates.TemplateResponse(
        "cashier.html",
        {
            "request": request,
            "restaurant": rest,
            "local_ip": local_ip,
            "port": port,
            "base_url": base_url,
            "theme": theme
        }
    )

@app.get("/", response_class=HTMLResponse)
async def index_route(request: Request):
    footer_pages = db.get_all_custom_pages(published_only=True, footer_only=True)
    return templates.TemplateResponse(
        "landing.html",
        {
            "request": request,
            "footer_pages": footer_pages,
            "current_admin": auth.get_current_super_admin(request),
            "current_restaurant": auth.get_current_restaurant(request),
        }
    )

# ─────────────────────────────────────────────────────────────
# PUBLIC CUSTOM PAGES & FLUTTER APIS
# ─────────────────────────────────────────────────────────────

@app.get("/api/pages")
async def api_get_public_pages():
    pages = db.get_all_custom_pages(published_only=True)
    compact = [
        {
            "id": p["id"],
            "slug": p["slug"],
            "title": p["title"],
            "title_en": p.get("title_en", ""),
            "meta_description": p.get("meta_description", ""),
            "show_in_footer": p.get("show_in_footer", 1),
            "sort_order": p.get("sort_order", 0),
            "updated_at": p.get("updated_at")
        }
        for p in pages
    ]
    return {"status": "ok", "pages": compact}

@app.get("/api/pages/{slug}")
async def api_get_public_page_detail(slug: str):
    page = db.get_custom_page_by_slug(slug.strip().lower())
    if not page or page.get("is_published") == 0:
        raise HTTPException(status_code=404, detail="الصفحة المطلوبة غير موجودة")
    return {"status": "ok", "page": page}

@app.get("/p/{slug}", response_class=HTMLResponse)
@app.get("/page/{slug}", response_class=HTMLResponse)
async def public_custom_page(request: Request, slug: str):
    page = db.get_custom_page_by_slug(slug.strip().lower())
    if not page or page.get("is_published") == 0:
        raise HTTPException(status_code=404, detail="الصفحة المطلوبة غير موجودة أو قيد المراجعة")

    footer_pages = db.get_all_custom_pages(published_only=True, footer_only=True)
    html_ar = markdown.markdown(page.get("content", ""), extensions=['extra', 'nl2br'])
    html_en = markdown.markdown(page.get("content_en", "") or page.get("content", ""), extensions=['extra', 'nl2br'])

    return templates.TemplateResponse(
        "custom_page.html",
        {
            "request": request,
            "page": page,
            "footer_pages": footer_pages,
            "html_content_ar": html_ar,
            "html_content_en": html_en
        }
    )

# ─────────────────────────────────────────────────────────────
# CUSTOMER PAGER PAGE & MOBILE CLIENT APIS
# ─────────────────────────────────────────────────────────────

@app.get("/pager/{token}", response_class=HTMLResponse)
@app.get("/order/{token}", response_class=HTMLResponse)
async def pager_page(request: Request, token: str):
    order = db.get_order_by_token(token)
    if not order:
        return HTMLResponse(
            """
            <div style="font-family:'Readex Pro',sans-serif; text-align:center; padding:50px; background:#08090d; color:#f3f5f9; height:100vh;">
                <h1>⚠️ طلب غير موجود | Order Not Found</h1>
                <p style="color:#8a93a6; margin-top:10px;">الرابط غير صحيح أو قد تم حذف الطلب من الكاشير.</p>
                <a href="/login" style="display:inline-block; margin-top:20px; color:#8b7bff; text-decoration:none;">العودة للرئيسية</a>
            </div>
            """,
            status_code=404
        )
    local_ip = get_local_ip()
    base_url = get_public_base_url(request)
    port = request.url.port or (443 if request.url.scheme == "https" else 80)
    queue_info = db.get_order_queue_info(order["id"])
    theme = db.get_setting("theme", "light")
    return templates.TemplateResponse(
        "pager.html",
        {
            "request": request,
            "order": order,
            "token": token,
            "base_url": base_url,
            "queue_info": queue_info,
            "theme": theme
        }
    )

@app.get("/api/pager/{token}/status")
@app.get("/api/orders/{token}/details")
async def get_order_details(token: str):
    order = db.get_order_by_token(token)
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    q_info = db.get_order_queue_info(order["id"])
    theme = db.get_setting("theme", "light")
    default_rest = db.get_setting("restaurant_name", "بيجر الطلبات")
    return {
        "id": order["id"],
        "order_number": order["order_number"],
        "customer_name": order["customer_name"],
        "notes": order["notes"],
        "status": order["status"],
        "token": order["token"],
        "restaurant_id": order.get("restaurant_id", 1),
        "restaurant_name": order.get("restaurant_name") or default_rest,
        "brand_color": order.get("brand_color") or "#C05621",
        "brand_icon": order.get("brand_icon") or "cup.and.saucer.fill",
        "call_count": order.get("call_count", 0),
        "created_at": order["created_at"],
        "called_at": order["called_at"],
        "completed_at": order["completed_at"],
        "orders_ahead": q_info["orders_ahead"],
        "estimated_wait": q_info["estimated_wait"],
        "avg_prep_mins": q_info["avg_prep_mins"],
        "theme": theme
    }

@app.post("/api/orders/{token}/onway")
async def mobile_onway_order(token: str, request: Request):
    order = db.get_order_by_token(token)
    # Numeric ids share this shape. A real order token always wins and stays public.
    if not order and token.isdigit():
        return await onway_order(int(token), request)
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    updated = db.update_order_status(order["id"], "onway")
    await manager.notify_cashiers({
        "event": "customer_onway",
        "order": updated
    })
    await manager.notify_pager(token, {
        "event": "order_onway",
        "order": updated
    })
    return updated

@app.post("/api/orders/{token}/live-activity-token")
@app.post("/api/orders/{token}/live-activity")
async def register_live_activity_token(token: str, req: LiveActivityTokenRequest):
    order = db.get_order_by_token(token)
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    actual_tok = req.push_token or req.pushToken or ""
    db.save_live_activity_token(
        order_token=token,
        push_token=actual_tok,
        device_type=req.device_type or "ios_live_activity"
    )
    return {"status": "ok", "message": "Live Activity token registered successfully"}

# ─────────────────────────────────────────────────────────────
# CORE ORDERS REST APIS
# ─────────────────────────────────────────────────────────────

def _order_actor(request: Request) -> Dict[str, Any]:
    """Restaurant id comes from the restaurant JWT. Super-admin has no tenant id."""
    restaurant = auth.get_current_restaurant(request)
    if restaurant and restaurant.get("sub"):
        return {"role": "restaurant", "restaurant_id": int(restaurant["sub"])}
    admin = auth.get_current_super_admin(request)
    if admin:
        return {"role": "super_admin", "restaurant_id": None}
    raise HTTPException(status_code=401, detail="Unauthorized")


def _order_for_actor(order_id: int, actor: Dict[str, Any]) -> Dict[str, Any]:
    order = db.get_order_by_id(order_id)
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    if actor["role"] == "restaurant" and int(order.get("restaurant_id") or 0) != actor["restaurant_id"]:
        raise HTTPException(status_code=404, detail="Order not found")
    return order


def _safe_next(value: Optional[str]) -> Optional[str]:
    if not value or not value.startswith("/") or value.startswith("//") or "\\" in value:
        return None
    return value


def _next_for_restaurant(user: Dict[str, Any], target: Optional[str]) -> str:
    if not target:
        return "/dashboard"
    slug = user.get("slug") or ""
    allowed = (
        target == "/cashier"
        or target.startswith("/dashboard")
        or target.startswith("/merchant")
        or (slug and (target == f"/r/{slug}/cashier" or target.startswith(f"/r/{slug}/cashier?")
                     or target == f"/r/{slug}/print" or target.startswith(f"/r/{slug}/print?")))
    )
    return target if allowed else "/dashboard"


def _login_redirect(request: Request) -> RedirectResponse:
    target = request.url.path
    if request.url.query:
        target = f"{target}?{request.url.query}"
    return RedirectResponse(url=f"/login?next={quote(target, safe='/')}", status_code=status.HTTP_302_FOUND)


def _can_open_restaurant(request: Request, restaurant_id: int) -> bool:
    restaurant = auth.get_current_restaurant(request)
    if restaurant and str(restaurant.get("sub")) == str(restaurant_id):
        return True
    return auth.get_current_super_admin(request) is not None


def _restaurant_id_for_create(actor: Dict[str, Any], requested_id: Optional[int]) -> int:
    if actor["role"] == "restaurant":
        return actor["restaurant_id"]
    if not requested_id:
        raise HTTPException(status_code=400, detail="restaurant_id is required")
    restaurant = db.get_restaurant_by_id(int(requested_id))
    if not restaurant:
        raise HTTPException(status_code=404, detail="Restaurant not found")
    return int(restaurant["id"])


@app.get("/api/orders")
async def list_orders(request: Request, restaurant_id: Optional[int] = None):
    """Staff sessions keep the Phase 0 scope.

    The shipped Dawrak app calls this with ?restaurant_id= and no cookie, then
    registers the Live Activity token. A 401 there aborts that registration,
    so the later ring has nothing to push. Writes stay session-only.
    """
    restaurant = auth.get_current_restaurant(request)
    if restaurant and restaurant.get("sub"):
        return db.get_all_orders(limit=150, restaurant_id=int(restaurant["sub"]))
    if auth.get_current_super_admin(request):
        return db.get_all_orders(limit=150)
    if restaurant_id:
        if not db.get_restaurant_by_id(int(restaurant_id)):
            raise HTTPException(status_code=404, detail="Restaurant not found")
        return db.get_all_orders(limit=150, restaurant_id=int(restaurant_id))
    raise HTTPException(status_code=401, detail="Unauthorized")


@app.post("/api/orders")
async def create_new_order(order_data: CreateOrderRequest, request: Request):
    actor = _order_actor(request)
    restaurant_id = _restaurant_id_for_create(actor, order_data.restaurant_id)
    order = db.create_order(
        order_number=order_data.order_number,
        restaurant_id=restaurant_id,
        customer_name=order_data.customer_name,
        notes=order_data.notes,
        restaurant_name=order_data.restaurant_name or "",
        brand_color=order_data.brand_color or "",
        brand_icon=order_data.brand_icon or ""
    )
    await manager.notify_cashiers({"event": "order_created", "order": order})
    return order

@app.post("/api/orders/{order_id}/ring")
@app.post("/api/orders/{order_id}/call")
async def ring_order(order_id: int, request: Request):
    actor = _order_actor(request)
    _order_for_actor(order_id, actor)
    order = db.update_order_status(order_id, "ringing")
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")

    await manager.notify_pager(order["token"], {
        "event": "order_ringing",
        "status": "ringing",
        "order": order
    })
    await manager.notify_cashiers({"event": "order_updated", "order": order})

    try:
        asyncio.create_task(push_service.notify_order_push(order))
    except Exception as e:
        print("WebPush trigger error:", e)

    try:
        from . import apns_service
        asyncio.create_task(apns_service.notify_order_live_activity(order, is_ringing=True, phase="calling"))
    except Exception as e:
        print("APNs trigger error:", e)

    return order

@app.post("/api/orders/{order_id}/onway")
async def onway_order(order_id: int, request: Request):
    actor = _order_actor(request)
    _order_for_actor(order_id, actor)
    order = db.update_order_status(order_id, "onway")
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")

    await manager.notify_pager(order["token"], {
        "event": "order_onway",
        "status": "onway",
        "order": order
    })
    await manager.notify_cashiers({
        "event": "customer_onway",
        "order": order
    })
    return order

@app.post("/api/orders/{order_id}/done")
@app.post("/api/orders/{order_id}/complete")
async def complete_order(order_id: int, request: Request):
    actor = _order_actor(request)
    _order_for_actor(order_id, actor)
    order = db.update_order_status(order_id, "done")
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")

    await manager.notify_pager(order["token"], {
        "event": "order_done",
        "status": "done",
        "order": order
    })
    await manager.notify_cashiers({"event": "order_updated", "order": order})
    return order

@app.post("/api/orders/{order_id}/waiting")
@app.post("/api/orders/{order_id}/reset")
async def reset_order(order_id: int, request: Request):
    actor = _order_actor(request)
    _order_for_actor(order_id, actor)
    order = db.update_order_status(order_id, "waiting")
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")

    await manager.notify_pager(order["token"], {
        "event": "order_waiting",
        "status": "waiting",
        "order": order
    })
    await manager.notify_cashiers({"event": "order_updated", "order": order})
    return order

@app.delete("/api/orders/{order_id}")
async def delete_order(order_id: int, request: Request):
    actor = _order_actor(request)
    order = _order_for_actor(order_id, actor)
    if order:
        db.delete_order(order_id)
        await manager.notify_pager(order["token"], {"event": "order_deleted"})
        await manager.notify_cashiers({
            "event": "order_deleted",
            "order_id": order_id,
            "restaurant_id": order.get("restaurant_id"),
        })
    return {"status": "success"}

# ─────────────────────────────────────────────────────────────
# QR GENERATOR & PUSH NOTIFICATIONS
# ─────────────────────────────────────────────────────────────

@app.api_route("/api/qr/{token}", methods=["GET", "HEAD"])
async def get_qr_image(request: Request, token: str, host: str = None):
    base = get_public_base_url(request).rstrip('/')

    if host:
        clean_host = host.strip().rstrip('/')
        # If host already includes the token path, use it directly
        if f"/pager/{token}" in clean_host or f"/order/{token}" in clean_host:
            url = clean_host
        elif clean_host.endswith("/pager") or clean_host.endswith("/order"):
            url = f"{clean_host}/{token}"
        elif token in ("demo", "stand"):
            url = clean_host
        else:
            # Guarantee the customer gets to their specific pager page!
            url = f"{clean_host}/pager/{token}"
    else:
        url = f"{base}/pager/{token}"

    qr = qrcode.QRCode(
        version=1,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=14,
        border=3,
    )
    qr.add_data(url)
    qr.make(fit=True)
    img = qr.make_image(fill_color="#000000", back_color="#ffffff")

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return StreamingResponse(buf, media_type="image/png")

@app.get("/api/push/public_key")
async def get_push_public_key():
    return {"public_key": push_service.get_public_key()}

@app.post("/api/push/subscribe")
async def subscribe_push(req: PushSubscriptionRequest):
    db.save_push_subscription(req.token, req.endpoint, req.p256dh, req.auth)
    return {"status": "ok"}

# ─────────────────────────────────────────────────────────────
# WEBSOCKET CONNECTIONS
# ─────────────────────────────────────────────────────────────

@app.websocket("/ws/pager/{token}")
@app.websocket("/ws/order/{token}")
async def websocket_pager(websocket: WebSocket, token: str):
    await manager.connect_pager(token, websocket)
    order = db.get_order_by_token(token)
    if order:
        q_info = db.get_order_queue_info(order["id"])
        theme = db.get_setting("theme", "light")
        await websocket.send_text(json.dumps({
            "event": "connected",
            "order": order,
            "queue_info": q_info,
            "theme": theme
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

class _AuthSource:
    def __init__(self, cookies, headers):
        self.cookies = cookies
        self.headers = headers


@app.websocket("/ws/cashier")
async def websocket_cashier(websocket: WebSocket):
    source = _AuthSource(websocket.cookies, websocket.headers)
    restaurant = auth.get_current_restaurant(source)
    if restaurant and restaurant.get("sub"):
        scope_id: Optional[int] = int(restaurant["sub"])
    elif auth.get_current_super_admin(source):
        scope_id = None
    else:
        await websocket.close(code=1008)
        return
    await manager.connect_cashier(websocket, scope_id)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect_cashier(websocket)

# ========================================================
# 💳 MYFATOORAH EMBEDDED & IN-APP PAYMENT ROUTERS
# ========================================================
from . import myfatoorah_service as mf

class ProcessPaymentDto(BaseModel):
    order_id: str
    amount: float
    session_id: Optional[str] = None
    payment_method_id: Optional[int] = None
    save_card: bool = False
    customer_name: Optional[str] = "Customer"
    customer_phone: Optional[str] = "97333000000"
    customer_email: Optional[str] = "billing@pager.rahapp.com"

class ChargeTokenDto(BaseModel):
    order_id: str
    amount: float
    token: str
    security_code: Optional[str] = None
    customer_name: Optional[str] = "Customer"
    customer_phone: Optional[str] = "97333000000"

def _require_restaurant_user(request: Request) -> Dict[str, Any]:
    user = auth.get_current_restaurant(request)
    if not user:
        raise HTTPException(status_code=401, detail="Unauthorized")
    return user


@app.get("/api/payment/myfatoorah/config")
async def api_myfatoorah_config():
    """Returns configuration for website / app checkout."""
    return mf.get_payment_config()

@app.post("/api/payment/myfatoorah/session")
async def api_myfatoorah_session(request: Request, amount: float = 1.0, customer_identifier: Optional[str] = None):
    """Initiates an embedded session for in-page payment without external redirect."""
    _require_restaurant_user(request)
    try:
        session_data = await mf.initiate_session(amount=amount, customer_identifier=customer_identifier)
        return {"ok": True, "session": session_data}
    except Exception as ex:
        raise HTTPException(status_code=400, detail=str(ex))

@app.post("/api/payment/myfatoorah/process")
async def api_myfatoorah_process(req: ProcessPaymentDto, request: Request):
    """Processes payment after client-side session submission."""
    _require_restaurant_user(request)
    base_url = get_public_base_url(request)
    callback_url = f"{base_url}/payment/myfatoorah/return"
    error_url = f"{base_url}/payment/myfatoorah/return"

    try:
        data = await mf.execute_payment(
            amount=req.amount,
            customer_name=req.customer_name or "Customer",
            customer_email=req.customer_email or "billing@pager.rahapp.com",
            customer_phone=req.customer_phone or "97333000000",
            order_id=req.order_id,
            session_id=req.session_id,
            payment_method_id=req.payment_method_id,
            callback_url=callback_url,
            error_url=error_url,
            save_token=req.save_card
        )
        return {
            "ok": True,
            "invoice_id": data.get("InvoiceId"),
            "redirect_url": data.get("PaymentURL"),
            "data": data
        }
    except Exception as ex:
        return {"ok": False, "error": str(ex)}

@app.post("/api/payment/myfatoorah/charge-token")
async def api_myfatoorah_charge_token(req: ChargeTokenDto, request: Request):
    """Charges saved card token directly with zero redirection."""
    _require_restaurant_user(request)
    base_url = get_public_base_url(request)
    callback_url = f"{base_url}/payment/myfatoorah/return"
    error_url = f"{base_url}/payment/myfatoorah/return"

    try:
        data = await mf.direct_charge_saved_card(
            amount=req.amount,
            token=req.token,
            order_id=req.order_id,
            customer_name=req.customer_name or "Customer",
            customer_phone=req.customer_phone or "97333000000",
            callback_url=callback_url,
            error_url=error_url,
            security_code=req.security_code
        )
        return {"ok": True, "data": data}
    except Exception as ex:
        return {"ok": False, "error": str(ex)}

@app.get("/checkout/{order_id}", response_class=HTMLResponse)
async def page_embedded_checkout(order_id: str, request: Request, amount: float = 5.0):
    """In-page embedded payment demo without redirect."""
    if not auth.get_current_restaurant(request):
        return _login_redirect(request)
    cfg = mf.get_payment_config()
    session_data = await mf.initiate_session(amount=amount, customer_identifier=f"ORDER_{order_id}")
    return templates.TemplateResponse(
        "payment_checkout.html",
        {
            "request": request,
            "order_id": order_id,
            "amount": amount,
            "payment_config": cfg,
            "session_data": session_data
        }
    )

@app.api_route("/payment/myfatoorah/return", methods=["GET", "POST"], response_class=HTMLResponse)
async def page_myfatoorah_return(request: Request, paymentId: Optional[str] = None):
    """Handles return verification from 3DS / OTP hop."""
    pid = paymentId or request.query_params.get("paymentId")
    is_success = False
    details = {}

    if pid:
        try:
            status_data = await mf.get_payment_status(pid)
            inv_status = (status_data.get("InvoiceStatus") or "").lower()
            if inv_status in ("paid", "success"):
                is_success = True
                details = status_data
        except Exception:
            pass

    return HTMLResponse(content=f"""
    <!DOCTYPE html>
    <html lang="ar" dir="rtl">
    <head>
        <meta charset="utf-8">
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <title>{'تم الدفع بنجاح' if is_success else 'فشلت عملية الدفع'}</title>
        <link href="https://fonts.googleapis.com/css2?family=Readex+Pro:wght@600;800&display=swap" rel="stylesheet">
        <style>
            body {{ background: #0f172a; color: #f8fafc; font-family: 'Readex Pro', sans-serif; display: flex; align-items: center; justify-content: center; min-height: 100vh; margin: 0; }}
            .card {{ background: #1e293b; border-radius: 20px; padding: 40px; text-align: center; max-width: 400px; box-shadow: 0 20px 40px rgba(0,0,0,0.5); }}
            .icon {{ font-size: 50px; margin-bottom: 16px; }}
            .btn {{ display: inline-block; margin-top: 24px; padding: 12px 24px; background: #C05621; color: #fff; text-decoration: none; border-radius: 12px; font-weight: 700; }}
        </style>
    </head>
    <body>
        <div class="card">
            <div class="icon">{'✅' if is_success else '❌'}</div>
            <h2>{'تمت عملية الدفع بنجاح!' if is_success else 'فشلت عملية الدفع'}</h2>
            <p style="color: #94a3b8; font-size: 14px; margin-top: 8px;">معرف العملية: {pid or 'غير متوفر'}</p>
            <a href="/" class="btn">العودة للرئيسية</a>
        </div>
    </body>
    </html>
    """)

@app.post("/api/payment/myfatoorah/webhook")
async def webhook_myfatoorah(request: Request):
    """MyFatoorah V2 Webhook Verification with HMAC-SHA256 signature."""
    raw_body = await request.body()
    signature = request.headers.get("myfatoorah-signature") or request.headers.get("X-MyFatoorah-Signature") or ""

    if not mf.verify_webhook_signature(raw_body, signature):
        raise HTTPException(status_code=400, detail="Invalid webhook signature")

    payload = await request.json()
    event = payload.get("Event")
    data = payload.get("Data", {})

    logger.info("MyFatoorah Webhook received: %s, Data: %s", event, data)
    return {"status": "success", "event": event}
