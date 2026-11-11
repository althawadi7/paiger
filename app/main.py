import io
import os
import re
import csv
import json
import hmac
import hashlib
import logging
import secrets
import shutil
import socket
import asyncio
import subprocess
import time
from collections import defaultdict
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from urllib.parse import quote
from typing import Any, Dict, List, Optional, Tuple
from fastapi import FastAPI, File, UploadFile, WebSocket, WebSocketDisconnect, HTTPException, Request, Form, Depends, status
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
from . import i18n
from . import mailer
from . import cashier_gate
from . import owner_board
from . import billing
from . import viewing
from . import myfatoorah_service as mf
from . import payments as pay

logger = logging.getLogger("pager")
_RESULT_POLL_MS = int(os.getenv("PAYMENT_RESULT_POLL_MS", "3000"))
_RESULT_POLL_MAX_MS = int(os.getenv("PAYMENT_RESULT_POLL_MAX_MS", "60000"))

BASE_DIR = Path(__file__).parent
_enable_docs = os.getenv("ENABLE_DOCS") == "1"
app = FastAPI(
    title="Trings SaaS - جرس الطلبات الذكي للمطاعم",
    docs_url="/docs" if _enable_docs else None,
    redoc_url="/redoc" if _enable_docs else None,
    openapi_url="/openapi.json" if _enable_docs else None,
)

# Static files & templates
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
i18n.install(templates)
templates.env.globals["viewing_as"] = viewing.name


def _hex_luminance(color: str) -> float:
    value = color.lstrip("#")
    if len(value) == 3:
        value = "".join(ch * 2 for ch in value)
    red, green, blue = int(value[0:2], 16) / 255, int(value[2:4], 16) / 255, int(value[4:6], 16) / 255

    def channel(part: float) -> float:
        if part <= 0.04045:
            return part / 12.92
        return ((part + 0.055) / 1.055) ** 2.4

    return 0.2126 * channel(red) + 0.7152 * channel(green) + 0.0722 * channel(blue)


def _contrast_ratio(left: str, right: str) -> float:
    lighter = max(_hex_luminance(left), _hex_luminance(right))
    darker = min(_hex_luminance(left), _hex_luminance(right))
    return (lighter + 0.05) / (darker + 0.05)


def restaurant_brand(restaurant: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Name, logo, initial and contrast ink. Same object on the customer page, cashier and print stand."""
    source = restaurant or {}
    name = str(source.get("name") or "").strip() or "Trings"
    raw_logo = str(source.get("logo_url") or "").strip()
    raw_color = str(source.get("brand_color") or "").strip()
    brand = raw_color if re.fullmatch(r"#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})", raw_color) else "#F05A28"
    ink = "#111" if _contrast_ratio(brand, "#111111") >= _contrast_ratio(brand, "#ffffff") else "#ffffff"
    return {
        "name": name,
        "logo_url": raw_logo or None,
        "initial": name[:1],
        "brand": brand,
        "brand_ink": ink,
    }


def static_version(name: str) -> str:
    """Deploy-wide cache bust id (same as build_version)."""
    return _APP_BUILD


def static_url(rel: str) -> str:
    """Local JS/CSS (and other static) with ?v=build for CDN revalidation."""
    path = (rel or "").lstrip("/")
    if path.startswith("static/"):
        path = path[7:]
    return f"/static/{path}?v={_APP_BUILD}"


def _git_revision() -> str:
    git = shutil.which("git")
    if not git:
        return ""
    try:
        out = subprocess.run(
            [git, "rev-parse", "--short", "HEAD"],
            cwd=str(BASE_DIR.parent),
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    rev = (out.stdout or "").strip()
    if out.returncode != 0 or not rev or any(c not in "0123456789abcdef" for c in rev):
        return ""
    return rev


def _newest_asset_mtime_ns() -> int:
    """Newest template or static JS/CSS. Images and uploads are ignored."""
    newest = 0
    try:
        for path in (BASE_DIR / "templates").rglob("*"):
            if path.is_file():
                newest = max(newest, path.stat().st_mtime_ns)
        static = BASE_DIR / "static"
        for pattern in ("*.js", "*.css"):
            for path in static.rglob(pattern):
                if path.is_file():
                    newest = max(newest, path.stat().st_mtime_ns)
    except OSError:
        return 0
    return newest


def _build_file() -> str:
    try:
        return (BASE_DIR / "BUILD").read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def _compute_app_build() -> str:
    """Deploy id for this process. A newer template, JS, or CSS changes it on the next start."""
    newest = _newest_asset_mtime_ns()
    rev = _git_revision()
    if newest and rev:
        return f"{rev}-{newest}"
    if newest:
        return str(newest)
    if rev:
        return rev
    return _build_file() or "0"


_APP_BUILD = _compute_app_build()


def app_build() -> str:
    return _APP_BUILD


templates.env.globals["restaurant_brand"] = restaurant_brand
templates.env.globals["static_version"] = static_version
templates.env.globals["build_version"] = _APP_BUILD
templates.env.globals["static_url"] = static_url


@app.middleware("http")
async def language_middleware(request: Request, call_next):
    token = i18n.activate(i18n.get_lang(request))
    try:
        response = await call_next(request)
    finally:
        i18n.reset(token)
    query_lang = request.query_params.get("lang")
    if query_lang in ("en", "ar"):
        i18n.apply_lang_cookie(response, query_lang, request)
    return response


@app.middleware("http")
async def admin_robots_header(request: Request, call_next):
    response = await call_next(request)
    path = request.url.path
    if path == "/admin" or path.startswith("/admin/") or path.startswith("/pay/"):
        response.headers["X-Robots-Tag"] = "noindex"
    if path.startswith("/static/js/") or path.startswith("/static/css/"):
        response.headers["Cache-Control"] = "no-cache, must-revalidate"
    elif path == "/static/js/cashier-page.js" or (path.startswith("/r/") and path.rstrip("/").endswith("/cashier")):
        response.headers["Cache-Control"] = "no-cache"
    return response


@app.middleware("http")
async def refresh_cashier_devices(request: Request, call_next):
    response = await call_next(request)
    if request.url.path.startswith("/static"):
        return response
    for name, value in request.cookies.items():
        if not name.startswith(cashier_gate.COOKIE_PREFIX):
            continue
        try:
            restaurant_id = int(name[len(cashier_gate.COOKIE_PREFIX):])
        except ValueError:
            continue
        if cashier_gate.valid_device(restaurant_id, value):
            _stamp_cashier_cookie(response, request, restaurant_id, value)
    return response


_MYFATOORAH_SCRIPT = "https://demo.myfatoorah.com https://portal.myfatoorah.com"
_MYFATOORAH_API = "https://apitest.myfatoorah.com https://api.myfatoorah.com"
_MYFATOORAH_EXTRA = "https://www.myfatoorah.com"
_WALLETS = "https://applepay.cdn-apple.com https://pay.google.com"


@app.middleware("http")
async def payment_csp_report_only(request: Request, call_next):
    """Report-Only CSP for checkout pages — not enforced (Cloudflare may also emit reports)."""
    response = await call_next(request)
    path = request.url.path
    if path.startswith("/dashboard") or path.startswith("/pay/") or path.startswith("/payment/"):
        script = (
            f"'self' 'unsafe-inline' https://fonts.googleapis.com "
            f"https://static.cloudflareinsights.com {_MYFATOORAH_SCRIPT} {_WALLETS}"
        )
        connect = (
            f"'self' https://cloudflareinsights.com {_MYFATOORAH_API} "
            f"{_MYFATOORAH_SCRIPT} {_MYFATOORAH_EXTRA} {_WALLETS}"
        )
        frame = f"'self' {_MYFATOORAH_SCRIPT} {_MYFATOORAH_API} {_MYFATOORAH_EXTRA} {_WALLETS}"
        policy = (
            f"default-src 'self'; script-src {script}; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
            f"font-src 'self' https://fonts.gstatic.com https://applepay.cdn-apple.com; img-src 'self' data: https:; "
            f"connect-src {connect}; frame-src {frame}; child-src {frame}"
        )
        if "Content-Security-Policy-Report-Only" not in response.headers:
            response.headers["Content-Security-Policy-Report-Only"] = policy
    return response


@app.middleware("http")
async def block_impersonated_writes(request: Request, call_next):
    payload = auth.decode_token(request.cookies.get(auth.RESTAURANT_COOKIE) or "")
    viewing_as = payload if payload and payload.get("role") == "restaurant" and payload.get("readonly") else None
    if viewing_as and request.method not in ("GET", "HEAD", "OPTIONS"):
        return JSONResponse({"detail": "Read only"}, status_code=403)
    token = viewing.current.set(viewing_as)
    try:
        return await call_next(request)
    finally:
        viewing.current.reset(token)


def _cookie_secure() -> bool:
    value = (os.getenv("COOKIE_SECURE", "true") or "true").strip().lower()
    return value not in {"0", "false", "no", "off"}


def _stamp_cashier_cookie(response, request: Request, restaurant_id: int, token: str) -> None:
    # Path is / so /api/orders and /ws/cashier receive it. The cookie name
    # carries the restaurant id, so a device paired to A cannot open B.
    response.set_cookie(
        key=cashier_gate.cookie_name(restaurant_id),
        value=token,
        httponly=True,
        max_age=60 * 60 * 24 * 30,
        samesite="lax",
        secure=_cookie_secure(),
        path="/",
    )


def _client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for") or ""
    if forwarded:
        return forwarded.split(",")[0].strip()[:64]
    if request.client and request.client.host:
        return request.client.host[:64]
    return ""


_PLAN_UNIT = {
    "monthly": ("price.mFor", "price.perMonth"),
    "yearly": ("price.yFor", "price.perYear"),
    "points": ("price.pFor", "price.perOrder"),
}


def _bhd3(fils: int) -> str:
    return f"{int(fils) / 1000:.3f}"


def _landing_plans() -> List[Dict[str, Any]]:
    source = owner_board.landing_source()
    rows = list(source["rows"] or [])
    packs = list(source["packs"] or [])
    arabic = i18n.current_lang() == "ar"
    monthly = next((row for row in rows if row.get("type") == "monthly"), None)
    cheapest = min(packs, key=lambda pack: (int(pack.get("price_fils") or 0), int(pack.get("sort") or 0))) if packs else None
    cards = []
    for row in rows:
        kind = row.get("type") or "monthly"
        for_key, per_key = _PLAN_UNIT.get(kind, _PLAN_UNIT["monthly"])
        fils = int(row.get("price_fils") or 0)
        name = (row.get("name_ar") if arabic else row.get("name_en")) or row.get("name_en") or ""
        line = (row.get("blurb_ar") if arabic else row.get("blurb_en")) or ""
        if not str(line).strip():
            line = i18n.t(for_key)
        note = ""
        if kind == "yearly" and monthly is not None:
            saved = int(monthly.get("price_fils") or 0) * 12 - int(row.get("price_fils") or 0)
            if saved > 0:
                note = i18n.t("price.ySave").replace("{amount}", _bhd3(saved))
        elif kind == "points" and cheapest is not None:
            note = i18n.t("price.pNote").replace("{amount}", _bhd3(int(cheapest.get("price_fils") or 0)))
        feats = billing.parse_features(row.get("features"))
        featured = bool(row.get("featured"))
        cards.append({
            "code": row.get("code") or "",
            "name": name,
            "line": line,
            "amount": _bhd3(fils),
            "per_key": per_key,
            "note": note,
            "features": feats["ar" if arabic else "en"],
            "featured": featured,
            "badge": ((row.get("badge_ar") if arabic else row.get("badge_en")) or "") if featured else "",
        })
    return cards

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

    async def connect_cashier(self, websocket: WebSocket, restaurant_id: Optional[int], admin_all: bool = False):
        await websocket.accept()
        self.cashier_connections.append({
            "ws": websocket,
            "restaurant_id": None if admin_all else restaurant_id,
            "admin_all": bool(admin_all),
        })

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
        if restaurant_id is None:
            return
        restaurant_id = int(restaurant_id)
        payload["restaurant_id"] = restaurant_id
        if payload.get("order_id") is None and isinstance(order, dict) and order.get("id") is not None:
            payload["order_id"] = order.get("id")
        message = json.dumps(payload)
        for conn in list(self.cashier_connections):
            if conn.get("admin_all"):
                target = None
            else:
                target = conn.get("restaurant_id")
                if target is None or int(target) != restaurant_id:
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
    lang: Optional[str] = "en"

class ThemeSettingRequest(BaseModel):
    theme: str

class LiveActivityTokenRequest(BaseModel):
    push_token: Optional[str] = None
    pushToken: Optional[str] = None
    device_type: Optional[str] = "ios_live_activity"
    lang: Optional[str] = None

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
    cashier_gate.ensure_pins()

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

@app.api_route("/admin/login", methods=["GET", "HEAD"], response_class=HTMLResponse)
async def super_admin_login_page(request: Request):
    user = auth.get_current_super_admin(request)
    if user:
        return RedirectResponse(url="/admin", status_code=status.HTTP_302_FOUND)
    locked = db.admin_login_failures(_client_ip(request)) >= 5
    return _admin_login_page(request, "admin.locked" if locked else None, "")


@app.post("/admin/login", response_class=HTMLResponse)
async def super_admin_login_submit(
    request: Request,
    identifier: str = Form(""),
    password: str = Form(""),
):
    ip = _client_ip(request)
    tried = (identifier or "").strip()
    if db.admin_login_failures(ip) >= 5:
        db.log_admin_login_attempt(ip, tried, False)
        return _admin_login_page(request, "admin.locked", tried, 429)
    if not tried or not password:
        return _admin_login_page(request, "admin.empty", tried, 400)
    admin = db.get_super_admin_by_identifier(tried)
    if not admin or not auth.verify_password(password, admin["password_hash"]):
        db.log_admin_login_attempt(ip, tried, False)
        return _admin_login_page(request, "admin.wrong", tried, 400)

    db.log_admin_login_attempt(ip, tried, True)
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
        secure=_cookie_secure(),
    )
    return redirect


def _admin_login_page(request: Request, error: Optional[str], identifier: str, status_code: int = 200):
    return templates.TemplateResponse(
        "super_admin_login.html",
        {"request": request, "error": error, "identifier": identifier},
        status_code=status_code,
    )

@app.get("/admin/logout")
async def super_admin_logout():
    redirect = RedirectResponse(url="/admin/login", status_code=status.HTTP_302_FOUND)
    redirect.delete_cookie(auth.SUPER_ADMIN_COOKIE)
    return redirect

# Platform admin pages are registered by admin_panel.install at the bottom of this file.

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

_RATE: Dict[str, List[float]] = defaultdict(list)
_AR_DIGITS = "٠١٢٣٤٥٦٧٨٩"
_EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]{2,}$")
_PLANS = ("monthly", "yearly", "points")
_WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
_MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"]


def _client_ip(request: Request) -> str:
    forwarded = (request.headers.get("x-forwarded-for") or "").split(",")[0].strip()
    if forwarded:
        return forwarded
    return request.client.host if request.client else "local"


def _rate_ok(key: str, limit: int, window: float) -> bool:
    now = time.monotonic()
    hits = [stamp for stamp in _RATE[key] if now - stamp < window]
    if len(hits) >= limit:
        _RATE[key] = hits
        return False
    hits.append(now)
    _RATE[key] = hits
    return True


def _normalize_phone(raw: str) -> Optional[str]:
    text = (raw or "").strip().replace(" ", "").replace("-", "")
    text = "".join(str(_AR_DIGITS.index(ch)) if ch in _AR_DIGITS else ch for ch in text)
    if text.startswith("+973"):
        text = text[4:]
    elif text.startswith("973") and len(text) == 11:
        text = text[3:]
    if re.fullmatch(r"\d{8}", text):
        return "+973" + text
    return None


def _phone_local(stored: str) -> str:
    if stored and stored.startswith("+973"):
        return stored[4:]
    return stored or ""


def _valid_email(value: str) -> bool:
    return bool(_EMAIL_RE.match((value or "").strip()))


def _plan_code(value: Optional[str]) -> str:
    return value if value in _PLANS else "monthly"


def _auth_page(request: Request, screen: str, status_code: int = 200, **extra):
    host = get_public_base_url(request).split("://", 1)[-1]
    context = {
        "request": request,
        "screen": screen,
        "link_host": host,
        "errors": {},
        "values": {},
        "plan": "monthly",
        "next": "",
        "alert": None,
        "rate_limited": False,
        "sent_email": "",
        "token": "",
        "brand": None,
    }
    context.update(extra)
    return templates.TemplateResponse("auth.html", context, status_code=status_code)


def _start_session(response: RedirectResponse, rest: Dict[str, Any]) -> None:
    token = auth.create_access_token({
        "sub": str(rest["id"]),
        "slug": rest["slug"],
        "name": rest["name"],
        "role": "restaurant",
    })
    response.set_cookie(
        key=auth.RESTAURANT_COOKIE,
        value=token,
        httponly=True,
        max_age=86400 * 30,
        samesite="lax",
        secure=_cookie_secure(),
    )


def _bahrain_label(when: Any) -> str:
    moment = when if isinstance(when, datetime) else datetime.fromisoformat(str(when))
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=datetime.now().astimezone().tzinfo)
    local = moment.astimezone(ZoneInfo("Asia/Bahrain"))
    return f"{_WEEKDAYS[local.weekday()]}, {local.day} {_MONTHS[local.month - 1]}"


def _support_email() -> str:
    return (os.getenv("SUPPORT_EMAIL") or "").strip() or "support@paiger.app"


async def _send_welcome(email: str, name: str, slug: str, trial_ends: Any, base: str) -> None:
    try:
        host = base.rstrip("/")
        context = {
            "restaurant_name": name,
            "base_url": host,
            "trial_ends_label": _bahrain_label(trial_ends),
            "cashier_url": f"{host}/r/{slug}/cashier",
            "dashboard_url": f"{host}/dashboard",
            "print_url": f"{host}/r/{slug}/print",
            "customer_link": f"{host}/r/{slug}",
            "support_email": _support_email(),
            "trial_days": owner_board.trial_length(),
        }
        html = templates.env.get_template("emails/welcome.html").render(**context)
        text = (
            f"{name} is ready to page its first order.\n"
            f"Your {context['trial_days']}-day free trial runs until {context['trial_ends_label']}. No card needed until then.\n\n"
            f"Open the cashier: {context['cashier_url']}\n"
            f"Dashboard: {context['dashboard_url']}\n"
            f"Customer link: {context['customer_link']}\n"
            f"Print your QR stand: {context['print_url']}\n"
        )
        await asyncio.to_thread(mailer.send_email, email, "Welcome to Trings", html, text)
    except Exception:
        logger.exception("Could not prepare the welcome email")


async def _send_reset(email: str, name: str, reset_url: str, base: str) -> None:
    try:
        context = {
            "restaurant_name": name,
            "reset_url": reset_url,
            "base_url": base.rstrip("/"),
            "support_email": _support_email(),
        }
        html = templates.env.get_template("emails/reset_password.html").render(**context)
        text = f"Set a new password for {name}: {reset_url}\nThis link expires in 1 hour.\n"
        await asyncio.to_thread(mailer.send_email, email, "Set a new password", html, text)
    except Exception:
        logger.exception("Could not prepare the reset email")


@app.get("/login", response_class=HTMLResponse)
@app.get("/restaurant/login", response_class=HTMLResponse)
async def restaurant_login_page(request: Request, next: Optional[str] = None):
    safe_next = _safe_next(next)
    user = auth.get_current_restaurant(request)
    if user:
        return RedirectResponse(url=_next_for_restaurant(user, safe_next), status_code=status.HTTP_302_FOUND)
    return _auth_page(request, "login", next=safe_next or "")


@app.post("/login", response_class=HTMLResponse)
@app.post("/restaurant/login", response_class=HTMLResponse)
async def restaurant_login_submit(
    request: Request,
    identifier: str = Form(""),
    password: str = Form(""),
    next: Optional[str] = Form(None),
):
    safe_next = _safe_next(next)
    ident = identifier.strip().lower()
    errors = {}
    if not ident or ("@" in ident and not _valid_email(ident)):
        errors["identifier"] = "auth.eEmail"
    if not password:
        errors["password"] = "auth.ePassReq"
    if errors:
        return _auth_page(request, "login", 400, errors=errors, values={"identifier": identifier}, next=safe_next or "")
    ip = _client_ip(request)
    if not _rate_ok(f"login-ip:{ip}", 10, 60) or not _rate_ok(f"login-id:{ident}", 10, 60):
        return _auth_page(request, "login", 429, alert="auth.tooMany", values={"identifier": identifier}, next=safe_next or "", rate_limited=True)
    rest = db.get_restaurant_for_login(ident)
    if not rest or not auth.verify_password(password, rest["password_hash"]):
        return _auth_page(request, "login", 400, alert="auth.eLogin", values={"identifier": identifier}, next=safe_next or "")
    if rest.get("is_active") == 0:
        return _auth_page(request, "login", 403, alert="auth.paused", values={"identifier": identifier}, next=safe_next or "")
    redirect = RedirectResponse(url=_next_for_restaurant(rest, safe_next), status_code=status.HTTP_302_FOUND)
    _start_session(redirect, rest)
    return redirect

@app.get("/logout")
@app.get("/restaurant/logout")
async def restaurant_logout():
    redirect = RedirectResponse(url="/login", status_code=status.HTTP_302_FOUND)
    redirect.delete_cookie(auth.RESTAURANT_COOKIE)
    return redirect

_PILL = {"waiting": "", "ringing": "ring", "onway": "way", "done": "done"}
_SWATCHES = ["#111111", "#7a4b2a", "#b91c1c", "#c2410c", "#166534", "#1d4ed8", "#6d28d9"]
_WEEKDAYS_AR = ["الاثنين", "الثلاثاء", "الأربعاء", "الخميس", "الجمعة", "السبت", "الأحد"]
_NOTICES = {"saved": "dash.saved", "pin": "dash.pinSaved", "devices": "dash.devOut"}


def _dash_csrf(restaurant_id: int) -> str:
    return hmac.new(auth.SECRET_KEY.encode("utf-8"), f"dash:{int(restaurant_id)}".encode("utf-8"), hashlib.sha256).hexdigest()


def _csrf_ok(restaurant_id: int, token: str) -> bool:
    expected = _dash_csrf(restaurant_id)
    offered = token or ""
    return len(expected) == len(offered) and hmac.compare_digest(expected, offered)


def _owner_restaurant(request: Request):
    user = auth.get_current_restaurant(request)
    if not user or not user.get("sub"):
        return None
    return db.get_restaurant_by_id(int(user["sub"]))


def _bahrain_phone(value: str) -> Optional[str]:
    digits = re.sub(r"\D", "", value or "")
    if digits.startswith("973") and len(digits) == 11:
        digits = digits[3:]
    if len(digits) == 8:
        return "+973" + digits
    return None


def _email_taken(email: str, restaurant_id: int) -> bool:
    conn = db.get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id FROM restaurants WHERE email=%s AND id<>%s LIMIT 1",
                (email.strip().lower(), restaurant_id),
            )
            return cur.fetchone() is not None
    finally:
        conn.close()


def _weekday_name() -> str:
    now = owner_board.bahrain_now()
    if i18n.current_lang() == "ar":
        return _WEEKDAYS_AR[now.weekday()]
    return now.strftime("%A")


def _greeting() -> str:
    hour = owner_board.bahrain_now().hour
    if hour < 12:
        key = "dash.helloMorning"
    elif hour < 17:
        key = "dash.helloAfternoon"
    else:
        key = "dash.helloEvening"
    return i18n.t(key)


def _trial_view(rest: Dict[str, Any]) -> Dict[str, Any]:
    state = db.access_state(rest)
    ends = owner_board._as_bahrain(rest.get("trial_ends_at"))
    seconds = (ends - owner_board.bahrain_now()).total_seconds() if ends else 0
    days = max(0, int((seconds + 86399) // 86400)) if seconds > 0 else 0
    return {
        "show": state in ("trialing", "expired"),
        "state": state,
        "days": days,
        "bars": min(3, days) if state == "trialing" else 0,
        "ends_label": ends.strftime("%d %b %Y") if ends else "",
    }


def _customer_link(request: Request, slug: str):
    url = f"{get_public_base_url(request).rstrip('/')}/r/{slug}"
    return url, url.split("://", 1)[-1]


def _form_from(rest: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "name": rest.get("name") or "",
        "phone": rest.get("phone") or "",
        "prep_minutes": int(rest.get("prep_minutes") or 6),
        "brand_color": (rest.get("brand_color") or "#111111").lower(),
        "email": rest.get("email") or "",
    }


def _device_when(value) -> str:
    moment = owner_board._as_bahrain(value)
    if not moment:
        return i18n.t("dash.devNow")
    minutes = int((owner_board.bahrain_now() - moment).total_seconds() // 60)
    if minutes < 1:
        return i18n.t("dash.devNow")
    if minutes < 60:
        return i18n.t("dash.devMin").replace("{n}", str(minutes))
    hours = minutes // 60
    if hours < 48:
        return i18n.t("dash.devHour").replace("{n}", str(hours))
    return i18n.t("dash.devDay").replace("{n}", str(hours // 24))


def _device_rows(restaurant_id: int) -> List[Dict[str, str]]:
    rows = []
    for row in cashier_gate.list_devices(restaurant_id):
        rows.append({
            "label": cashier_gate.describe_agent(row.get("user_agent") or ""),
            "when": _device_when(row.get("last_used_at")),
        })
    return rows


def _present_order(row: Dict[str, Any], prep_limit: int) -> Dict[str, Any]:
    created = owner_board._as_bahrain(row.get("created_at"))
    prep = owner_board._minutes(row.get("created_at"), row.get("called_at"))
    pick = owner_board._minutes(row.get("called_at"), row.get("completed_at"))
    return {
        "number": row.get("order_number"),
        "time": created.strftime("%H:%M") if created else "",
        "date": created.strftime("%Y-%m-%d") if created else "",
        "prep": prep,
        "prep_late": prep is not None and prep > prep_limit,
        "pick": pick,
        "scanned": bool(row.get("scanned_at")),
        "calls": int(row.get("call_count") or 0),
        "status": row.get("status") or "",
        "pill": _PILL.get(row.get("status") or "", ""),
        "ago": int((owner_board.bahrain_now() - created).total_seconds() // 60) if created else 0,
    }


def _subscription_view(rest: Dict[str, Any], requested_plan: str = "") -> Dict[str, Any]:
    state = db.effective_status(rest)
    code = (rest.get("plan_code") or "monthly").strip()
    if code not in ("monthly", "yearly", "points"):
        code = "monthly"
    catalog = owner_board.plans()
    by_code = {row["code"]: row for row in catalog}
    if code not in by_code:
        by_code[code] = billing.plan_row(code) or {}
    lang_key = "ar" if i18n.current_lang() == "ar" else "en"
    for plan in catalog:
        plan["price_label"] = owner_board.fils_label(plan.get("price_fils"))
        plan["feature_lines"] = billing.parse_features(plan.get("features"))[lang_key][:8]
        plan["badge"] = ((plan.get("badge_ar") if lang_key == "ar" else plan.get("badge_en")) or "") if plan.get("featured") else ""
    current = by_code.get(code) or {}
    name_key = "name_ar" if i18n.current_lang() == "ar" else "name_en"
    if state == "trialing":
        title, tag, tag_class = i18n.t("dash.freeTrial"), i18n.t("dash.trialTag"), "tag-trial"
    elif state == "expired":
        title, tag, tag_class = i18n.t("dash.freeTrial"), i18n.t("dash.expiredTag"), "tag-trial"
    else:
        title, tag, tag_class = current.get(name_key) or code, i18n.t("dash.activeTag"), "active"
    ends = owner_board._as_bahrain(rest.get("trial_ends_at"))
    ends_label = ends.strftime("%d %b %Y") if ends else "—"
    cards = pay.list_cards(int(rest["id"]))
    status = billing.plan_status_texts(rest)
    paid_end = pay.paid_period_end(rest)
    periodic = state == "active" and code in ("monthly", "yearly")
    change = status["scheduled"]
    facts = []
    if state in ("trialing", "expired"):
        facts.append({"label": i18n.t("dash.trialEnds"), "value": ends_label})
    elif code == "points":
        facts.append({"label": i18n.t("dash.amount"), "value": "BHD " + owner_board.fils_label(current.get("price_fils"))})
    else:
        facts.append({"label": i18n.t("dash.amount"), "value": "BHD " + owner_board.fils_label(current.get("price_fils"))})
        if change and change["code"] not in ("monthly", "yearly"):
            next_charge = "—"
        elif not status["auto_renew"]:
            next_charge = i18n.t("dash.autoRenewOff")
        elif status["until"]:
            card_text = billing.card_short_label(cards[0]) if cards else i18n.t("dash.noCardShort")
            next_charge = f"{billing.date_label(status['until'])} · {card_text}"
        else:
            next_charge = "—"
        facts.append({"label": i18n.t("dash.nextCharge"), "value": next_charge})
    ends_soon = ""
    if periodic and status["until"]:
        days_left = (status["until"] - owner_board.bahrain_now().date()).days
        if days_left <= 0:
            ends_soon = i18n.t("dash.endsToday")
        elif days_left == 1:
            ends_soon = i18n.t("dash.endsTomorrow")
        elif days_left <= 7:
            ends_soon = i18n.t("dash.endsIn").replace("{n}", str(days_left))
    must_pick_plan = state in ("trialing", "expired", "past_due")
    preselected_checkout = ""
    checkout_summary_name = ""
    checkout_summary_price = ""
    requested = next((plan for plan in catalog if plan.get("code") == requested_plan), None)
    if requested:
        must_pick_plan = False
        preselected_checkout = requested_plan
        checkout_summary_name = requested.get(name_key) or requested_plan
        checkout_summary_price = owner_board.fils_label(requested.get("price_fils"))
    packs = owner_board.point_packs()
    for pack in packs:
        pack["price_label"] = owner_board.fils_label(pack.get("price_fils"))
    invoices = billing.owner_documents(int(rest["id"]))
    wallet = billing.wallet(int(rest["id"])) if code == "points" else {"balance": 0}
    prefs = pay.billing_preferences(int(rest["id"]))
    history = pay.payment_history(int(rest["id"]))
    mf_cfg = mf.get_payment_config()
    paid_end_label = billing.date_label(paid_end)
    plan_checkout = []
    for plan in catalog:
        mode = pay.plan_change_mode(rest, str(plan.get("code") or ""))
        name = (plan.get("name_ar") if lang_key == "ar" else plan.get("name_en")) or plan.get("code")
        summary_name = name
        if mode == "extend":
            ahead_key = "dash.sumAheadYear" if plan.get("code") == "yearly" else "dash.sumAheadMonth"
            summary_name = i18n.t(ahead_key).replace("{plan}", str(name))
            note = i18n.t("dash.extendsTo").replace("{date}", billing.date_label(pay.plan_period_end(plan, paid_end)))
        elif mode == "upgrade":
            note = i18n.t("dash.startsOn").replace("{date}", paid_end_label)
        elif mode == "schedule":
            note = i18n.t("dash.switchesOn").replace("{date}", paid_end_label)
        else:
            note = ""
        plan_checkout.append({
            "code": plan.get("code"),
            "name": name,
            "summary_name": summary_name,
            "price_label": plan.get("price_label"),
            "price_fils": int(plan.get("price_fils") or 0),
            "mode": mode,
            "note": note,
        })
    extend_label = ""
    if periodic and paid_end:
        extend_label = i18n.t("dash.payAheadYear") if code == "yearly" else i18n.t("dash.payAheadMonth")
    return {
        "plan_until_label": status["until_label"] if periodic else "",
        "plan_ends_soon": ends_soon,
        "plan_change_label": status["scheduled_label"] if state == "active" else "",
        "extend_label": extend_label,
        "current_locked": state == "active",
        "plans": catalog,
        "packs": packs,
        "invoices": invoices,
        "plan_title": title,
        "plan_tag": tag,
        "plan_tag_class": tag_class,
        "plan_facts": facts,
        "plan_code": code,
        "sub_state": state,
        "point_price": owner_board.fils_label((by_code.get("points") or {}).get("price_fils")),
        "must_pick_plan": must_pick_plan,
        "preselected_checkout": preselected_checkout,
        "checkout_summary_name": checkout_summary_name,
        "checkout_summary_price": checkout_summary_price,
        "plan_checkout": plan_checkout,
        "points_balance": int(wallet["balance"]),
        "saved_cards": cards,
        "billing_prefs": prefs,
        "payment_history": history,
        "payment_config": mf_cfg,
        "past_due": state == "past_due",
        "checkout_bootstrap": {
            "config": mf_cfg,
            "test_mode": bool(mf_cfg.get("is_test")),
            "plans": plan_checkout,
            "selected": preselected_checkout,
            "must_pick": must_pick_plan,
            "current": code if state == "active" else "",
            "messages": {
                "empty_plan": "—",
                "pick_plan": i18n.t("dash.paymentPickPlan") if state != "active" else i18n.t("dash.chooseToContinue"),
                "no_charge": i18n.t("dash.noChargeNow"),
                "schedule_failed": i18n.t("dash.scheduleFailed"),
                **pay.checkout_messages(),
            },
        },
    }



def _overview_stats(rest: Dict[str, Any]) -> Dict[str, Any]:
    stats = owner_board.overview(int(rest["id"]))
    for row in stats["latest"]:
        row["pill"] = _PILL.get(row.get("status") or "", "")
    stats["weekday"] = _weekday_name()
    return stats


def _render_dashboard(request: Request, rest: Dict[str, Any], section: str, **extra):
    link, host = _customer_link(request, rest["slug"])
    notice_code = request.query_params.get("notice") or ""
    ctx = {
        "request": request,
        "section": section,
        "restaurant": rest,
        "brand": restaurant_brand(rest),
        "trial": _trial_view(rest),
        "greeting": _greeting(),
        "customer_url": link,
        "customer_host": host,
        "notice": i18n.t(_NOTICES[notice_code]) if notice_code in _NOTICES else "",
        "stats": _overview_stats(rest) if section == "overview" else None,
        "errors": {},
        "csrf": _dash_csrf(int(rest["id"])),
        "form": _form_from(rest),
        "swatches": _SWATCHES,
        "pin": cashier_gate.current_pin(int(rest["id"])) if section == "settings" else None,
        "devices": _device_rows(int(rest["id"])) if section == "settings" else [],
        "range_key": "today",
        "status_key": "",
        "query": "",
        "order_rows": [],
        "order_total": 0,
        "page": 1,
        "pages": 1,
    }
    ctx.update(_subscription_view(rest, request.query_params.get("plan") or ""))
    ctx.update(extra)
    minutes = int((ctx.get("form") or {}).get("prep_minutes") or 6)
    choices = [3, 5, 6, 8, 10, 15]
    if minutes not in choices:
        choices = sorted(set(choices + [minutes]))
    ctx["prep_choices"] = choices
    return templates.TemplateResponse("dashboard.html", ctx)


def _order_filters(request: Request):
    range_key = request.query_params.get("range") or "today"
    if range_key not in ("today", "7", "30"):
        range_key = "today"
    status_key = request.query_params.get("status") or ""
    if status_key not in ("waiting", "ringing", "onway", "done"):
        status_key = ""
    query = (request.query_params.get("q") or "").strip()[:20]
    try:
        page = max(1, int(request.query_params.get("page") or "1"))
    except ValueError:
        page = 1
    return range_key, status_key, query, page


@app.get("/dashboard", response_class=HTMLResponse)
@app.get("/merchant", response_class=HTMLResponse)
async def restaurant_dashboard(request: Request):
    rest = _owner_restaurant(request)
    if not rest:
        return _login_redirect(request)
    return _render_dashboard(request, rest, "overview")


@app.get("/dashboard/orders", response_class=HTMLResponse)
async def dashboard_orders(request: Request):
    rest = _owner_restaurant(request)
    if not rest:
        return _login_redirect(request)
    range_key, status_key, query, page = _order_filters(request)
    found = owner_board.list_orders(int(rest["id"]), range_key, status_key, query, page)
    limit = int(rest.get("prep_minutes") or 6)
    return _render_dashboard(
        request, rest, "orders",
        range_key=range_key, status_key=status_key, query=query,
        order_rows=[_present_order(row, limit) for row in found["rows"]],
        order_total=found["total"], page=found["page"], pages=found["pages"],
    )


@app.get("/dashboard/orders.csv")
async def dashboard_orders_csv(request: Request):
    rest = _owner_restaurant(request)
    if not rest:
        return _login_redirect(request)
    range_key, status_key, query, _page = _order_filters(request)
    found = owner_board.list_orders(int(rest["id"]), range_key, status_key, query, 1)
    limit = int(rest.get("prep_minutes") or 6)
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow([
        i18n.t("dash.cOrder"), i18n.t("dash.cTime"), i18n.t("dash.cPrep"),
        i18n.t("dash.cPick"), i18n.t("dash.cScan"), i18n.t("dash.cCalls"), i18n.t("dash.cStatus"),
    ])
    for raw in found["all_rows"]:
        row = _present_order(raw, limit)
        writer.writerow([
            row["number"], f"{row['date']} {row['time']}".strip(),
            "" if row["prep"] is None else f"{row['prep']:.1f}",
            "" if row["pick"] is None else f"{row['pick']:.1f}",
            i18n.t("dash.yes") if row["scanned"] else i18n.t("dash.no"),
            row["calls"], i18n.t("dash.st." + row["status"]) if row["status"] else "",
        ])
    filename = f"orders-{rest['slug']}-{range_key}.csv"
    return Response(
        "\ufeff" + buf.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.get("/dashboard/subscription", response_class=HTMLResponse)
async def dashboard_subscription(request: Request):
    rest = _owner_restaurant(request)
    if not rest:
        return _login_redirect(request)
    return _render_dashboard(request, rest, "subscription")


@app.get("/dashboard/settings", response_class=HTMLResponse)
async def dashboard_settings(request: Request):
    rest = _owner_restaurant(request)
    if not rest:
        return _login_redirect(request)
    return _render_dashboard(request, rest, "settings")


@app.post("/dashboard/settings")
async def dashboard_settings_save(
    request: Request,
    csrf: str = Form(""),
    name: str = Form(""),
    phone: str = Form(""),
    prep_minutes: str = Form("6"),
    brand_color: str = Form("#111111"),
    email: str = Form(""),
    password: str = Form(""),
    password2: str = Form(""),
):
    rest = _owner_restaurant(request)
    if not rest:
        return _login_redirect(request)
    if not _csrf_ok(int(rest["id"]), csrf):
        return RedirectResponse(url="/dashboard/settings", status_code=status.HTTP_303_SEE_OTHER)
    errors = {}
    clean_phone = _bahrain_phone(phone)
    if not clean_phone:
        errors["phone"] = "dash.ePhone"
    clean_email = (email or "").strip().lower()
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", clean_email) or _email_taken(clean_email, int(rest["id"])):
        errors["email"] = "dash.eEmail"
    new_hash = None
    if password or password2:
        if password != password2 or not password.strip():
            errors["password"] = "dash.ePass"
        else:
            new_hash = auth.hash_password(password)
    try:
        minutes = int(prep_minutes)
    except ValueError:
        minutes = 6
    if minutes < 1 or minutes > 180:
        minutes = int(rest.get("prep_minutes") or 6)
    color = (brand_color or "").strip().lower()
    if not re.fullmatch(r"#[0-9a-f]{6}", color):
        color = (rest.get("brand_color") or "#111111").lower()
    clean_name = (name or "").strip()
    if not clean_name:
        clean_name = rest.get("name") or ""
    form = {
        "name": clean_name,
        "phone": phone.strip(),
        "prep_minutes": minutes,
        "brand_color": color,
        "email": email.strip(),
    }
    if errors:
        return _render_dashboard(request, rest, "settings", errors=errors, form=form)
    owner_board.save_profile(int(rest["id"]), clean_name, clean_email, clean_phone, color, minutes, new_hash)
    return RedirectResponse(url="/dashboard/settings?notice=saved", status_code=status.HTTP_303_SEE_OTHER)


@app.post("/dashboard/settings/pin")
async def dashboard_pin_save(request: Request, csrf: str = Form(""), pin: str = Form("")):
    rest = _owner_restaurant(request)
    if not rest:
        return _login_redirect(request)
    if not _csrf_ok(int(rest["id"]), csrf):
        return RedirectResponse(url="/dashboard/settings", status_code=status.HTTP_303_SEE_OTHER)
    offered = (pin or "").strip()
    if not re.fullmatch(r"\d{6}", offered):
        error = "dash.pinShort"
    elif cashier_gate.pin_is_weak(offered):
        error = "dash.pinWeak"
    else:
        cashier_gate.save_pin(int(rest["id"]), offered)
        return RedirectResponse(url="/dashboard/settings?notice=pin", status_code=status.HTTP_303_SEE_OTHER)
    return _render_dashboard(request, rest, "settings", errors={"pin": error})


@app.get("/dashboard/settings/pin/generate")
async def dashboard_pin_generate(request: Request):
    rest = _owner_restaurant(request)
    if not rest:
        raise HTTPException(status_code=401, detail="Unauthorized")
    return {"pin": cashier_gate.generate_pin()}


@app.post("/dashboard/settings/devices/sign-out")
async def dashboard_sign_out_devices(request: Request, csrf: str = Form("")):
    rest = _owner_restaurant(request)
    if not rest:
        return _login_redirect(request)
    if not _csrf_ok(int(rest["id"]), csrf):
        return RedirectResponse(url="/dashboard/settings", status_code=status.HTTP_303_SEE_OTHER)
    cashier_gate.revoke_devices(int(rest["id"]))
    return RedirectResponse(url="/dashboard/settings?notice=devices", status_code=status.HTTP_303_SEE_OTHER)

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


_LOGO_DIR = BASE_DIR / "static" / "uploads" / "logos"
_LOGO_URL = re.compile(r"^/static/uploads/logos/(\d+-[0-9a-f]+\.webp)$")
_LOGO_BYTES = 2 * 1024 * 1024


def _remove_logo_file(logo_url: str) -> None:
    match = _LOGO_URL.match(logo_url or "")
    if not match:
        return
    path = (_LOGO_DIR / match.group(1)).resolve()
    root = _LOGO_DIR.resolve()
    if path.parent != root or not path.is_file():
        return
    path.unlink()


@app.post("/api/merchant/logo")
async def upload_restaurant_logo(request: Request, file: UploadFile = File(...)):
    user = _require_restaurant_user(request)
    rest_id = int(user["sub"])
    rest = db.get_restaurant_by_id(rest_id)
    if not rest:
        raise HTTPException(status_code=404, detail="Restaurant not found")
    raw = await file.read()
    if not raw or len(raw) > _LOGO_BYTES:
        raise HTTPException(status_code=413, detail="Logo must be a PNG, JPG or WEBP under 2 MB")
    try:
        from PIL import Image, ImageOps, UnidentifiedImageError
        Image.MAX_IMAGE_PIXELS = 24_000_000
        try:
            probe = Image.open(io.BytesIO(raw))
            kind = (probe.format or "").upper()
            probe.verify()
        except (UnidentifiedImageError, OSError):
            raise HTTPException(status_code=400, detail="PNG, JPG or WEBP only")
        if kind not in ("PNG", "JPEG", "WEBP"):
            raise HTTPException(status_code=400, detail="PNG, JPG or WEBP only")
        image = ImageOps.exif_transpose(Image.open(io.BytesIO(raw)))
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=400, detail="PNG, JPG or WEBP only")
    width, height = image.size
    max_side = 512
    longest = max(width, height)
    if longest > max_side:
        scale = max_side / float(longest)
        image = image.resize(
            (max(1, int(round(width * scale))), max(1, int(round(height * scale)))),
            Image.Resampling.LANCZOS,
        )
    if image.mode not in ("RGB", "RGBA"):
        image = image.convert("RGBA")
    _LOGO_DIR.mkdir(parents=True, exist_ok=True)
    filename = f"{rest_id}-{secrets.token_hex(6)}.webp"
    image.save(_LOGO_DIR / filename, format="WEBP", quality=82, method=6)
    _remove_logo_file(str(rest.get("logo_url") or ""))
    url = f"/static/uploads/logos/{filename}"
    db.set_restaurant_logo(rest_id, url)
    rest["logo_url"] = url
    return {"logo_url": url, "brand": restaurant_brand(rest)}


@app.delete("/api/merchant/logo")
async def delete_restaurant_logo(request: Request):
    user = _require_restaurant_user(request)
    rest_id = int(user["sub"])
    rest = db.get_restaurant_by_id(rest_id)
    if not rest:
        raise HTTPException(status_code=404, detail="Restaurant not found")
    _remove_logo_file(str(rest.get("logo_url") or ""))
    db.set_restaurant_logo(rest_id, "")
    rest["logo_url"] = ""
    return {"logo_url": None, "brand": restaurant_brand(rest)}

# ─────────────────────────────────────────────────────────────
# CASHIER TERMINALS (PER-RESTAURANT & CENTRAL)
# ─────────────────────────────────────────────────────────────

@app.get("/r/{slug}", response_class=HTMLResponse)
async def restaurant_customer_track(request: Request, slug: str, num: Optional[str] = None):
    rest = db.get_restaurant_by_slug(slug)
    if not rest:
        raise HTTPException(status_code=404, detail="Restaurant not found")

    if num and num.strip():
        matched = db.find_active_order_number(rest["id"], num.strip())
        if matched and matched.get("token"):
            return RedirectResponse(url=f"/pager/{matched['token']}", status_code=status.HTTP_302_FOUND)

    return templates.TemplateResponse(
        "customer_track.html",
        {
            "request": request,
            "restaurant": rest,
            "brand": restaurant_brand(rest),
            "num": num,
            "error": "لم يتم العثور على طلب نشط بهذا الرقم" if num else None
        }
    )

@app.get("/r/{slug}/cashier", response_class=HTMLResponse)
async def restaurant_dedicated_cashier(request: Request, slug: str):
    rest = db.get_restaurant_by_slug(slug)
    if not rest:
        raise HTTPException(status_code=404, detail="Restaurant not found")
    if rest.get("suspended_at"):
        return templates.TemplateResponse("paused.html", {"request": request})
    if _can_open_restaurant(request, rest["id"]) or cashier_gate.device_from_cookies(request.cookies, rest["id"]):
        local_ip = get_local_ip()
        base_url = get_public_base_url(request)
        port = request.url.port or (443 if request.url.scheme == "https" else 80)
        theme = db.get_setting("theme", "light")
        response = templates.TemplateResponse("cashier.html", _cashier_context(request, rest, local_ip, port, base_url, theme))
        response.headers["Cache-Control"] = "no-cache"
        return response
    key = cashier_gate.device_key(_client_ip(request), request.headers.get("user-agent", ""))
    gate = cashier_gate.pin_status(int(rest["id"]), key)
    return templates.TemplateResponse(
        "cashier_pin.html",
        {
            "request": request,
            "restaurant": rest,
            "brand": restaurant_brand(rest),
            "login_next": quote(f"/r/{rest['slug']}/cashier", safe="/"),
            "locked_until": gate.get("locked_until"),
        },
    )


@app.post("/r/{slug}/cashier/pin")
async def restaurant_cashier_pin(request: Request, slug: str):
    rest = db.get_restaurant_by_slug(slug)
    if not rest:
        raise HTTPException(status_code=404, detail="Restaurant not found")
    if rest.get("suspended_at"):
        raise HTTPException(status_code=403, detail="This service is paused")
    try:
        body = await request.json()
    except Exception:
        body = {}
    offered = str((body or {}).get("pin") or "")
    if _can_open_restaurant(request, rest["id"]):
        return {"ok": True, "remaining": 5, "locked_until": None}
    key = cashier_gate.device_key(_client_ip(request), request.headers.get("user-agent", ""))
    result = cashier_gate.check_pin(int(rest["id"]), key, offered, request.headers.get("user-agent", ""), _client_ip(request))
    if not result.get("ok"):
        return JSONResponse(result, status_code=401)
    token = cashier_gate.issue_device(int(rest["id"]), request.headers.get("user-agent", ""), _client_ip(request))
    response = JSONResponse(result)
    _stamp_cashier_cookie(response, request, int(rest["id"]), token)
    return response

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

    response = templates.TemplateResponse("cashier.html", _cashier_context(request, rest, local_ip, port, base_url, theme))
    response.headers["Cache-Control"] = "no-cache"
    return response

@app.get("/lang/{code}")
async def set_language(code: str, request: Request):
    chosen = code if code in ("en", "ar") else "en"
    response = RedirectResponse(i18n.return_path(request), status_code=302)
    i18n.apply_lang_cookie(response, chosen, request)
    return response


@app.get("/api/signup/slug-available")
async def signup_slug_available(slug: str = ""):
    problem = db.slug_problem(slug)
    return {"available": problem is None, "reason": problem or "ok"}


@app.get("/signup", response_class=HTMLResponse)
async def signup_page(request: Request, plan: Optional[str] = None):
    if auth.get_current_restaurant(request):
        return RedirectResponse("/dashboard", status_code=status.HTTP_302_FOUND)
    return _auth_page(request, "signup", plan=_plan_code(plan))


@app.post("/signup", response_class=HTMLResponse)
async def signup_submit(
    request: Request,
    name: str = Form(""),
    slug: str = Form(""),
    email: str = Form(""),
    password: str = Form(""),
    phone: str = Form(""),
    plan: str = Form("monthly"),
):
    chosen = _plan_code(plan)
    values = {"name": name, "slug": slug.strip().lower(), "email": email.strip(), "phone": phone}
    errors: Dict[str, str] = {}
    if not name.strip():
        errors["name"] = "auth.eRest"
    problem = db.slug_problem(values["slug"])
    if problem == "taken":
        errors["slug"] = "auth.slugTaken"
    elif problem:
        errors["slug"] = "auth.eSlug"
    if not _valid_email(email):
        errors["email"] = "auth.eEmail"
    elif db.email_in_use(email):
        errors["email"] = "auth.emailTaken"
    if len(password) < 8:
        errors["password"] = "auth.ePass"
    normalized = _normalize_phone(phone)
    if not normalized:
        errors["phone"] = "auth.ePhone"
    if errors:
        code = 409 if errors.get("email") == "auth.emailTaken" or errors.get("slug") == "auth.slugTaken" else 400
        return _auth_page(request, "signup", code, errors=errors, values=values, plan=chosen)
    if not _rate_ok(f"signup-ip:{_client_ip(request)}", 5, 3600):
        return _auth_page(request, "signup", 429, values=values, plan=chosen, rate_limited=True)
    try:
        rest = db.create_restaurant(
            name=name.strip(),
            slug=values["slug"],
            email=email.strip().lower(),
            password_hash=auth.hash_password(password),
            phone=normalized,
            brand_color="#111111",
        )
    except Exception:
        logger.exception("Could not create restaurant")
        if db.email_in_use(email):
            errors["email"] = "auth.emailTaken"
        else:
            errors["slug"] = "auth.slugTaken"
        return _auth_page(request, "signup", 409, errors=errors, values=values, plan=chosen)
    db.start_restaurant_trial(rest["id"], chosen)
    rest = db.get_restaurant_by_id(rest["id"])
    asyncio.create_task(_send_welcome(rest["email"], rest["name"], rest["slug"], rest.get("trial_ends_at"), get_public_base_url(request)))
    redirect = RedirectResponse("/onboarding/brand", status_code=status.HTTP_302_FOUND)
    _start_session(redirect, rest)
    return redirect


@app.get("/forgot-password", response_class=HTMLResponse)
async def forgot_password_page(request: Request):
    return _auth_page(request, "forgot")


@app.post("/forgot-password", response_class=HTMLResponse)
async def forgot_password_submit(request: Request, email: str = Form("")):
    cleaned = email.strip()
    if not _valid_email(cleaned):
        return _auth_page(request, "forgot", 400, errors={"email": "auth.eEmail"}, values={"email": cleaned})
    if not _rate_ok(f"forgot:{cleaned.lower()}", 3, 3600):
        return _auth_page(request, "forgot", 429, rate_limited=True, values={"email": cleaned})
    rest = db.get_restaurant_for_login(cleaned.lower())
    if rest and rest.get("email", "").lower() == cleaned.lower():
        raw = secrets.token_urlsafe(32)
        db.create_password_reset(int(rest["id"]), raw)
        base = get_public_base_url(request)
        asyncio.create_task(_send_reset(rest["email"], rest["name"], f"{base}/reset-password/{raw}", base))
    return _auth_page(request, "sent", sent_email=cleaned)


@app.get("/reset-password/{token}", response_class=HTMLResponse)
async def reset_password_page(request: Request, token: str):
    if not db.reset_token_restaurant(token):
        return _auth_page(request, "reset_bad", 400)
    return _auth_page(request, "reset", token=token)


@app.post("/reset-password/{token}", response_class=HTMLResponse)
async def reset_password_submit(request: Request, token: str, password: str = Form(""), confirm: str = Form("")):
    if not _rate_ok(f"reset-ip:{_client_ip(request)}", 10, 3600):
        return _auth_page(request, "reset", 429, token=token, rate_limited=True)
    errors = {}
    if len(password) < 8:
        errors["password"] = "auth.ePass"
    elif password != confirm:
        errors["confirm"] = "auth.eMatch"
    if errors:
        return _auth_page(request, "reset", 400, token=token, errors=errors)
    restaurant_id = db.consume_password_reset(token)
    if not restaurant_id:
        return _auth_page(request, "reset_bad", 400)
    db.set_restaurant_password(restaurant_id, auth.hash_password(password))
    rest = db.get_restaurant_by_id(restaurant_id)
    redirect = RedirectResponse("/dashboard", status_code=status.HTTP_302_FOUND)
    _start_session(redirect, rest)
    return redirect


@app.get("/onboarding/brand", response_class=HTMLResponse)
async def brand_setup_page(request: Request):
    user = auth.get_current_restaurant(request)
    if not user:
        return RedirectResponse("/login?next=/onboarding/brand", status_code=status.HTTP_302_FOUND)
    rest = db.get_restaurant_by_id(int(user["sub"]))
    if not rest:
        return RedirectResponse("/logout", status_code=status.HTTP_302_FOUND)
    return _auth_page(request, "brand", brand=restaurant_brand(rest))


@app.post("/onboarding/brand", response_class=HTMLResponse)
async def brand_setup_submit(request: Request, brand_color: str = Form("#111111")):
    user = auth.get_current_restaurant(request)
    if not user:
        return RedirectResponse("/login?next=/onboarding/brand", status_code=status.HTTP_302_FOUND)
    color = brand_color.strip()
    if not re.fullmatch(r"#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})", color):
        color = "#111111"
    db.set_restaurant_brand(int(user["sub"]), color)
    return RedirectResponse("/dashboard", status_code=status.HTTP_302_FOUND)


@app.get("/", response_class=HTMLResponse)
async def index_route(request: Request):
    footer_pages = db.get_all_custom_pages(published_only=True, footer_only=True)
    return templates.TemplateResponse(
        "landing.html",
        {
            "request": request,
            "footer_pages": footer_pages,
            "plans": _landing_plans(),
            "trial_days": owner_board.landing_source()["trial_days"],
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
    if i18n.get_lang(request) == "en":
        page_title = page.get("title_en") or page.get("title") or ""
        raw = page.get("content_en") or page.get("content") or ""
    else:
        page_title = page.get("title") or page.get("title_en") or ""
        raw = page.get("content") or page.get("content_en") or ""
    html_content = markdown.markdown(raw, extensions=['extra', 'nl2br'])

    return templates.TemplateResponse(
        "custom_page.html",
        {
            "request": request,
            "page": page,
            "footer_pages": footer_pages,
            "page_title": page_title,
            "html_content": html_content,
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
                <h1>طلب غير موجود | Order Not Found</h1>
                <p style="color:#8a93a6; margin-top:10px;">الرابط غير صحيح أو قد تم حذف الطلب من الكاشير.</p>
                <a href="/login" style="display:inline-block; margin-top:20px; color:#8b7bff; text-decoration:none;">العودة للرئيسية</a>
            </div>
            """,
            status_code=404,
        )
    if order.get("restaurant_id") and billing.restaurant_suspended(int(order["restaurant_id"])):
        return templates.TemplateResponse("paused.html", {"request": request})
    await _note_customer_view(request, order)
    queue_info = db.get_order_queue_info(order["id"])
    theme = db.get_setting("theme", "light")
    prep_minutes = 6
    if order.get("restaurant_id"):
        rest_row = db.get_restaurant_by_id(int(order["restaurant_id"]))
        raw_prep = (rest_row or {}).get("prep_minutes")
        if raw_prep is not None:
            prep_minutes = int(raw_prep)
    created = order.get("created_at")
    if isinstance(created, str) and created:
        try:
            created = datetime.fromisoformat(created)
        except ValueError:
            created = None
    created_ms = int(created.timestamp() * 1000) if isinstance(created, datetime) else int(datetime.now().timestamp() * 1000)
    return templates.TemplateResponse(
        "pager.html",
        {
            "request": request,
            "order": order,
            "token": token,
            "base_url": get_public_base_url(request),
            "queue_info": queue_info,
            "theme": theme,
            "prep_minutes": prep_minutes,
            "created_ms": created_ms,
        }
    )

def _brand_hex(value: Optional[str]) -> str:
    raw = str(value or "").strip()
    if re.fullmatch(r"#[0-9a-fA-F]{3}", raw):
        raw = "#" + "".join(ch * 2 for ch in raw[1:])
    if re.fullmatch(r"#[0-9a-fA-F]{6}", raw):
        return "#" + raw[1:].upper()
    return "#C05621"


def _iso_tz(value: Any) -> str:
    """Naive database clocks are server-local. Emit seconds and a numeric offset."""
    if isinstance(value, str) and value:
        try:
            value = datetime.fromisoformat(value)
        except ValueError:
            return value
    if not isinstance(value, datetime):
        value = datetime.now().astimezone()
    if value.tzinfo is None:
        value = value.replace(tzinfo=datetime.now().astimezone().tzinfo)
    return value.isoformat(timespec="seconds")


def _push_live_activity(order: Optional[Dict[str, Any]], *, is_ringing: bool, phase: str) -> None:
    if not order:
        return
    try:
        from . import apns_service
        asyncio.create_task(apns_service.notify_order_live_activity(order, is_ringing=is_ringing, phase=phase))
    except Exception as e:
        print("APNs trigger error:", e)


@app.get("/api/pager/{token}/status")
@app.get("/api/orders/{token}/details")
async def get_order_details(token: str, request: Request):
    order = db.get_order_by_token(token)
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    order = await _note_customer_view(request, order)
    q_info = db.get_order_queue_info(order["id"])
    theme = db.get_setting("theme", "light")
    default_rest = db.get_setting("restaurant_name", "بيجر الطلبات")
    rest_row = None
    if order.get("restaurant_id"):
        rest_row = db.get_restaurant_by_id(int(order["restaurant_id"]))
    raw_prep = (rest_row or {}).get("prep_minutes")
    prep_minutes = int(raw_prep) if raw_prep is not None else 6
    logo_raw = str((rest_row or {}).get("logo_url") or "").strip()
    if not logo_raw:
        logo_url = None
    elif logo_raw.startswith("http://") or logo_raw.startswith("https://"):
        logo_url = logo_raw
    else:
        logo_url = get_public_base_url(request).rstrip("/") + "/" + logo_raw.lstrip("/")
    return {
        "id": order["id"],
        "order_number": order["order_number"],
        "customer_name": order["customer_name"],
        "notes": order["notes"],
        "status": order["status"],
        "token": order["token"],
        "restaurant_id": order.get("restaurant_id", 1),
        "restaurant_name": str(order.get("restaurant_name") or (rest_row or {}).get("name") or default_rest),
        "logo_url": logo_url,
        "brand_color": _brand_hex(order.get("brand_color") or (rest_row or {}).get("brand_color")),
        "brand_icon": order.get("brand_icon") or "cup.and.saucer.fill",
        "prep_minutes": prep_minutes,
        "call_count": order.get("call_count", 0),
        "created_at": _iso_tz(order.get("created_at")),
        "called_at": order["called_at"],
        "completed_at": order["completed_at"],
        "orders_ahead": q_info["orders_ahead"],
        "estimated_wait": q_info["estimated_wait"],
        "avg_prep_mins": q_info["avg_prep_mins"],
        "theme": theme,
    }

@app.post("/api/orders/{token}/onway")
async def mobile_onway_order(token: str, request: Request):
    order = db.get_order_by_token(token)
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    updated = db.update_order_status(order["id"], "onway")
    await manager.notify_cashiers({
        "event": "customer_onway",
        "order": updated
    })
    _push_live_activity(updated, is_ringing=False, phase="onway")
    real_token = order.get("token") or token
    await manager.notify_pager(real_token, {
        "event": "order_onway",
        "status": "onway",
        "order": updated
    })
    return updated

@app.post("/api/orders/{token}/live-activity-token")
@app.post("/api/orders/{token}/live-activity")
async def register_live_activity_token(token: str, req: LiveActivityTokenRequest, request: Request):
    order = db.get_order_by_token(token)
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    await _note_customer_view(request, order)
    chosen = (req.lang or "").strip().lower()
    if chosen in ("en", "ar"):
        db.set_order_lang(order["id"], chosen)
        order["lang"] = chosen
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

def _business_since(restaurant_id: int) -> datetime:
    rest = db.get_restaurant_by_id(int(restaurant_id)) or {"id": int(restaurant_id)}
    return owner_board.as_stored(owner_board.business_day_start(rest))


def _picked_before_business_day(order: Dict[str, Any]) -> bool:
    if (order.get("status") or "") != "done":
        return False
    raw = order.get("completed_at")
    if not raw:
        return True
    if isinstance(raw, str):
        raw = datetime.fromisoformat(raw)
    if not isinstance(raw, datetime):
        return True
    if raw.tzinfo is not None:
        raw = raw.astimezone().replace(tzinfo=None)
    return raw < _business_since(int(order.get("restaurant_id") or 0))


def _cashier_context(request: Request, rest: Dict[str, Any], local_ip: str, port: int, base_url: str, theme: str) -> Dict[str, Any]:
    day_start = owner_board.business_day_start(rest)
    stats = db.cashier_today_stats(int(rest["id"]), owner_board.as_stored(day_start))
    offset = datetime.now().astimezone().strftime("%z")
    tz_offset = f"{offset[:3]}:{offset[3:]}" if len(offset) == 5 else "+00:00"
    return {
        "request": request,
        "restaurant": rest,
        "local_ip": local_ip,
        "port": port,
        "base_url": base_url,
        "theme": theme,
        "tz_offset": tz_offset,
        "time_zone": str(owner_board.restaurant_zone(rest)),
        "day_start_hour": owner_board.BUSINESS_DAY_HOUR,
        "business_day_start": day_start.isoformat(),
        "build": app_build(),
        "today_count": stats["today_count"],
        "called_count": stats["called_count"],
        "avg_seconds": stats["avg_seconds"] if stats["avg_seconds"] is not None else "",
    }


def _has_restaurant_session(source) -> bool:
    user = auth.get_current_restaurant(source)
    return bool(user and user.get("sub"))


async def _note_customer_view(source, order: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """First customer view sets scanned_at. A logged-in restaurant session does not count."""
    if not order or _has_restaurant_session(source):
        return order
    try:
        fresh = db.mark_order_scanned(order["id"])
    except Exception:
        logger.exception("Could not record customer scan")
        return order
    if not fresh:
        return order
    await manager.notify_cashiers({
        "event": "customer_connected",
        "order_id": fresh["id"],
        "restaurant_id": fresh.get("restaurant_id"),
    })
    return fresh


def _order_actor(request: Request) -> Dict[str, Any]:
    """Restaurant id comes from the owner session or a paired cashier device.

    Two device cookies in one browser are not collapsed onto the first cookie.
    """
    restaurant = auth.get_current_restaurant(request)
    if restaurant and restaurant.get("sub"):
        return {"role": "restaurant", "restaurant_id": int(restaurant["sub"]), "device_id": None}
    admin = auth.get_current_super_admin(request)
    if admin:
        return {"role": "super_admin", "restaurant_id": None, "device_id": None}
    ids = cashier_gate.device_restaurant_ids(request.cookies)
    if len(ids) == 1:
        device = cashier_gate.device_from_cookies(request.cookies, ids[0])
        if device:
            return {"role": "device", "restaurant_id": int(device["restaurant_id"]), "device_id": int(device["id"])}
    if ids:
        return {"role": "device", "restaurant_id": None, "device_id": None}
    raise HTTPException(status_code=401, detail="Unauthorized")


def _order_for_actor(order_id: int, actor: Dict[str, Any], request: Optional[Request] = None) -> Dict[str, Any]:
    order = db.get_order_by_id(order_id)
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    order_restaurant = int(order.get("restaurant_id") or 0)
    if actor["role"] == "super_admin":
        return order
    if actor["role"] == "restaurant":
        if order_restaurant == actor["restaurant_id"]:
            return order
        if request and auth.get_current_super_admin(request):
            return order
        raise HTTPException(status_code=404, detail="Order not found")
    if actor["role"] == "device":
        if actor.get("restaurant_id") is not None and order_restaurant == int(actor["restaurant_id"]):
            return order
        if request and cashier_gate.device_from_cookies(request.cookies, order_restaurant):
            return order
    raise HTTPException(status_code=404, detail="Order not found")


def _restaurant_for_cashier_write(request: Request, actor: Dict[str, Any], requested_id: Optional[int]) -> tuple:
    """The page's restaurant, accepted only when this session may write it."""
    if actor["role"] == "super_admin":
        return _restaurant_id_for_create(actor, requested_id), None
    if actor["role"] == "restaurant":
        session_id = int(actor["restaurant_id"])
        if requested_id and int(requested_id) != session_id:
            if not auth.get_current_super_admin(request):
                raise HTTPException(status_code=403, detail="Wrong restaurant")
            restaurant = db.get_restaurant_by_id(int(requested_id))
            if not restaurant:
                raise HTTPException(status_code=404, detail="Restaurant not found")
            return int(restaurant["id"]), None
        return session_id, None
    if not requested_id:
        if actor.get("restaurant_id") is None:
            raise HTTPException(status_code=403, detail="Restaurant is ambiguous")
        requested_id = int(actor["restaurant_id"])
    device = cashier_gate.device_from_cookies(request.cookies, int(requested_id))
    if not device:
        raise HTTPException(status_code=403, detail="Wrong restaurant")
    return int(device["restaurant_id"]), int(device["id"])


def _reject_if_paused(restaurant_id: int) -> None:
    if restaurant_id and billing.restaurant_suspended(int(restaurant_id)):
        raise HTTPException(status_code=403, detail="This service is paused")


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


def _staff_orders(restaurant_id: int, board: bool) -> List[Dict[str, Any]]:
    if board:
        return db.get_cashier_board(int(restaurant_id), _business_since(int(restaurant_id)))
    return db.get_all_orders(limit=150, restaurant_id=int(restaurant_id))


@app.get("/api/orders")
async def list_orders(request: Request, restaurant_id: Optional[int] = None, board: Optional[int] = None):
    """Staff sessions keep the Phase 0 scope.

    The shipped Dawrak app calls this with ?restaurant_id= and no cookie, then
    registers the Live Activity token. That public response is the full order
    payload from this morning. Writes stay session-only.

    ?board=1 is the cashier screen: active orders plus picked-up orders from
    the current business day only. Other callers still receive the full list.
    """
    cashier_board = board == 1
    restaurant = auth.get_current_restaurant(request)
    if restaurant and restaurant.get("sub"):
        session_id = int(restaurant["sub"])
        if restaurant_id and int(restaurant_id) != session_id:
            if not auth.get_current_super_admin(request):
                raise HTTPException(status_code=403, detail="Wrong restaurant")
            return _staff_orders(int(restaurant_id), cashier_board)
        return _staff_orders(session_id, cashier_board)
    if auth.get_current_super_admin(request):
        if restaurant_id:
            return _staff_orders(int(restaurant_id), cashier_board)
        return db.get_all_orders(limit=150)
    if restaurant_id:
        device = cashier_gate.device_from_cookies(request.cookies, int(restaurant_id))
        if device:
            return _staff_orders(int(restaurant_id), cashier_board)
        if not cashier_gate.device_restaurant_ids(request.cookies):
            # Public / legacy App Clip fallback: NEVER return completed 'done' orders
            return db.get_all_orders(limit=150, restaurant_id=int(restaurant_id), active_only=True)
        raise HTTPException(status_code=403, detail="Wrong restaurant")
    device_ids = cashier_gate.device_restaurant_ids(request.cookies)
    if len(device_ids) == 1:
        return _staff_orders(device_ids[0], cashier_board)
    raise HTTPException(status_code=401, detail="Unauthorized")


@app.post("/api/orders")
async def create_new_order(order_data: CreateOrderRequest, request: Request):
    actor = _order_actor(request)
    restaurant_id, device_id = _restaurant_for_cashier_write(request, actor, order_data.restaurant_id)
    if device_id and cashier_gate.recent_device_orders(device_id) >= 30:
        raise HTTPException(status_code=429, detail="Too many orders. Wait a moment and try again.")
    _reject_if_paused(restaurant_id)
    blocked = billing.points_block_reason(restaurant_id)
    if blocked:
        raise HTTPException(status_code=402, detail=blocked)
    if db.find_active_order_number(restaurant_id, order_data.order_number):
        raise HTTPException(status_code=409, detail="An active order with this number already exists")
    order = db.create_order(
        order_number=order_data.order_number,
        restaurant_id=restaurant_id,
        customer_name=order_data.customer_name,
        notes=order_data.notes,
        restaurant_name=order_data.restaurant_name or "",
        brand_color=order_data.brand_color or "",
        brand_icon=order_data.brand_icon or "",
        cashier_device_id=device_id,
    )
    billing.charge_order_point(restaurant_id)
    await manager.notify_cashiers({"event": "order_created", "order": order})
    return order

@app.post("/api/orders/{order_id}/ring")
@app.post("/api/orders/{order_id}/call")
async def ring_order(order_id: int, request: Request):
    actor = _order_actor(request)
    order = _order_for_actor(order_id, actor, request)
    _reject_if_paused(int(order.get("restaurant_id") or 0))
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

    _push_live_activity(order, is_ringing=True, phase="calling")

    return order

@app.post("/api/orders/{order_id}/onway")
async def onway_order(order_id: int, request: Request):
    actor = _order_actor(request)
    order = _order_for_actor(order_id, actor, request)
    _reject_if_paused(int(order.get("restaurant_id") or 0))

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
    _push_live_activity(order, is_ringing=False, phase="onway")
    return order

@app.post("/api/orders/{order_id}/done")
@app.post("/api/orders/{order_id}/complete")
async def complete_order(order_id: int, request: Request):
    actor = _order_actor(request)
    order = _order_for_actor(order_id, actor, request)
    _reject_if_paused(int(order.get("restaurant_id") or 0))
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
    order = _order_for_actor(order_id, actor, request)
    _reject_if_paused(int(order.get("restaurant_id") or 0))
    if _picked_before_business_day(order):
        raise HTTPException(status_code=409, detail="That pickup is from a previous business day")
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
    order = _order_for_actor(order_id, actor, request)
    _reject_if_paused(int(order.get("restaurant_id") or 0))
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
            # Guarantee the customer gets to their specific pager page
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
    db.save_push_subscription(req.token, req.endpoint, req.p256dh, req.auth, req.lang or "en")
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
        order = await _note_customer_view(websocket, order)
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
                        _push_live_activity(updated, is_ringing=False, phase="onway")
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


async def _cashier_socket_loop(websocket: WebSocket) -> None:
    try:
        await websocket.send_text(json.dumps({"event": "hello", "build": app_build()}))
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect_cashier(websocket)


@app.websocket("/ws/cashier")
async def websocket_cashier(websocket: WebSocket):
    """Super-admin board only. Cashier pages use /ws/cashier/{slug}.

    A cached cashier page, a device cookie, or a restaurant owner must not
    sit on this socket: it hears every restaurant.
    """
    source = _AuthSource(websocket.cookies, websocket.headers)
    if not auth.get_current_super_admin(source):
        await websocket.accept()
        await websocket.close(code=4403)
        return
    await manager.connect_cashier(websocket, None, admin_all=True)
    await _cashier_socket_loop(websocket)


@app.websocket("/ws/cashier/{slug}")
async def websocket_cashier_room(websocket: WebSocket, slug: str):
    """Cashier page room. The restaurant comes from the slug after the cookie is checked.

    A client-supplied restaurant id is never trusted: the slug is resolved on
    the server, then the session or the device cookie for that restaurant
    must match.
    """
    rest = db.get_restaurant_by_slug(slug)
    if not rest:
        await websocket.accept()
        await websocket.close(code=4404)
        return
    rid = int(rest["id"])
    source = _AuthSource(websocket.cookies, websocket.headers)
    session = auth.get_current_restaurant(source)
    allowed = bool(session and str(session.get("sub")) == str(rid))
    if not allowed and auth.get_current_super_admin(source):
        allowed = True
    if not allowed and cashier_gate.device_from_cookies(websocket.cookies, rid):
        allowed = True
    if not allowed:
        await websocket.accept()
        await websocket.close(code=4401)
        return
    await manager.connect_cashier(websocket, rid, admin_all=False)
    await _cashier_socket_loop(websocket)

# ========================================================
#  MYFATOORAH / BILLING CHECKOUT
# ========================================================
class CheckoutIntentDto(BaseModel):
    purpose: str
    plan_code: Optional[str] = None
    pack_id: Optional[int] = None
    invoice_number: Optional[str] = None


class SessionIntentDto(BaseModel):
    intent_id: str
    save_card: bool = False


class ProcessIntentDto(BaseModel):
    intent_id: str
    session_id: str
    payment_data: str = ""
    redirection_url: str = ""


class ChargeCardDto(BaseModel):
    intent_id: str
    card_id: int


class BillingPrefsDto(BaseModel):
    auto_renew: Optional[bool] = None
    auto_topup: Optional[bool] = None


def _require_owner_payment(request: Request) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    user = auth.get_current_restaurant(request)
    admin = auth.get_current_super_admin(request)
    if admin:
        raise HTTPException(status_code=400, detail="Use restaurant session")
    if not user or not user.get("sub"):
        raise HTTPException(status_code=401, detail="Unauthorized")
    rest = db.get_restaurant_by_id(int(user["sub"]))
    if not rest:
        raise HTTPException(status_code=401, detail="Unauthorized")
    ip = _client_ip(request)
    if not _rate_ok(f"pay:{rest['id']}:{ip}", 40, 300):
        raise HTTPException(status_code=429, detail="Too many requests")
    return user, rest


@app.get("/.well-known/apple-developer-merchantid-domain-association")
async def apple_pay_association():
    blob = pay.apple_association_bytes()
    if not blob:
        raise HTTPException(status_code=404, detail="Not configured")
    return Response(content=blob, media_type="text/plain")


@app.get("/api/payment/myfatoorah/config")
@app.get("/api/billing/checkout/config")
async def api_myfatoorah_config(request: Request):
    _require_owner_payment(request)
    return mf.get_payment_config()


@app.post("/api/billing/checkout/intent")
async def api_checkout_intent(req: CheckoutIntentDto, request: Request):
    _, rest = _require_owner_payment(request)
    try:
        return {"ok": True, "intent": pay.create_checkout_intent(
            int(rest["id"]),
            req.purpose,
            plan_code=req.plan_code,
            pack_id=req.pack_id,
            invoice_number=req.invoice_number,
        )}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


def _gateway_error(exc: Exception) -> HTTPException:
    logger.warning("MyFatoorah checkout error: %s", exc)
    return HTTPException(status_code=502, detail=str(exc) or "Payment gateway error")


@app.post("/api/billing/checkout/session")
async def api_checkout_session(req: SessionIntentDto, request: Request):
    _, rest = _require_owner_payment(request)
    try:
        data = await pay.session_data_for_intent(
            req.intent_id,
            int(rest["id"]),
            save_card=req.save_card,
            base_url=get_public_base_url(request),
            language=i18n.get_lang(request),
        )
        return {"ok": True, **data}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        raise _gateway_error(exc)


@app.post("/api/payment/myfatoorah/process")
@app.post("/api/billing/checkout/process")
async def api_checkout_process(req: ProcessIntentDto, request: Request):
    _, rest = _require_owner_payment(request)
    try:
        return await pay.process_embedded_payment(
            req.intent_id,
            int(rest["id"]),
            session_id=req.session_id,
            payment_data=req.payment_data,
            redirection_url=req.redirection_url,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        raise _gateway_error(exc)


@app.post("/api/billing/checkout/charge-card")
async def api_checkout_charge_card(req: ChargeCardDto, request: Request):
    _, rest = _require_owner_payment(request)
    try:
        return await pay.charge_saved_card(
            req.card_id,
            int(rest["id"]),
            req.intent_id,
            rest,
            get_public_base_url(request),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


@app.get("/api/billing/cards")
async def api_billing_cards(request: Request):
    _, rest = _require_owner_payment(request)
    return {"cards": pay.list_cards(int(rest["id"])), "prefs": pay.billing_preferences(int(rest["id"]))}


@app.delete("/api/billing/cards/{card_id}")
async def api_billing_card_delete(card_id: int, request: Request):
    _, rest = _require_owner_payment(request)
    try:
        pay.remove_card(int(rest["id"]), card_id)
        return {"ok": True}
    except ValueError:
        raise HTTPException(status_code=404, detail="Not found")


@app.post("/api/billing/cards/{card_id}/default")
async def api_billing_card_default(card_id: int, request: Request):
    _, rest = _require_owner_payment(request)
    try:
        pay.set_default_card(int(rest["id"]), card_id)
        return {"ok": True}
    except ValueError:
        raise HTTPException(status_code=404, detail="Not found")


class PayLinkSessionDto(BaseModel):
    number: str
    token: str
    intent_id: str


class PayLinkProcessDto(PayLinkSessionDto):
    session_id: str
    payment_data: str = ""
    redirection_url: str = ""


def _rate_pay_link(request: Request, key: str) -> None:
    if not _rate_ok(f"paylink:{key}:{_client_ip(request)}", 30, 300):
        raise HTTPException(status_code=429, detail="Too many requests")


@app.post("/api/billing/pay-link/session")
async def api_pay_link_session(req: PayLinkSessionDto, request: Request):
    _rate_pay_link(request, req.number)
    try:
        invoice = pay.verify_pay_link(req.number, req.token, req.intent_id)
        data = await pay.session_data_for_intent(
            req.intent_id,
            int(invoice["restaurant_id"]),
            save_card=False,
            base_url=get_public_base_url(request),
            language=i18n.get_lang(request),
        )
        return {"ok": True, **data}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        raise _gateway_error(exc)


@app.post("/api/billing/pay-link/process")
async def api_pay_link_process(req: PayLinkProcessDto, request: Request):
    _rate_pay_link(request, req.number)
    try:
        invoice = pay.verify_pay_link(req.number, req.token, req.intent_id, pending=False)
        return await pay.process_embedded_payment(
            req.intent_id,
            int(invoice["restaurant_id"]),
            session_id=req.session_id,
            payment_data=req.payment_data,
            redirection_url=req.redirection_url,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        raise _gateway_error(exc)


@app.post("/api/billing/preferences")
async def api_billing_preferences(req: BillingPrefsDto, request: Request):
    _, rest = _require_owner_payment(request)
    pay.update_preferences(int(rest["id"]), auto_renew=req.auto_renew, auto_topup=req.auto_topup)
    return {"ok": True, "prefs": pay.billing_preferences(int(rest["id"]))}


class PlanChangeDto(BaseModel):
    plan_code: str


@app.post("/api/billing/plan-change")
async def api_billing_plan_change(req: PlanChangeDto, request: Request):
    _, rest = _require_owner_payment(request)
    try:
        return {"ok": True, "change": pay.schedule_plan_change(int(rest["id"]), (req.plan_code or "").strip())}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.post("/api/billing/plan-change/cancel")
async def api_billing_plan_change_cancel(request: Request):
    _, rest = _require_owner_payment(request)
    pay.cancel_plan_change(int(rest["id"]))
    return {"ok": True}


@app.get("/dashboard/checkout", response_class=HTMLResponse)
async def dashboard_checkout_page(request: Request, intent: str = ""):
    rest = _owner_restaurant(request)
    if not rest:
        return _login_redirect(request)
    if not intent:
        raise HTTPException(status_code=400, detail="intent")
    try:
        row = pay.get_intent(intent, int(rest["id"]))
    except ValueError:
        raise HTTPException(status_code=404, detail="Not found")
    cfg = mf.get_payment_config()
    return templates.TemplateResponse(
        "billing_checkout.html",
        {
            "request": request,
            "restaurant": rest,
            "intent_id": intent,
            "amount_label": billing.fils_fixed(row["amount_fils"]),
            "invoice_number": row.get("invoice_number") or "",
            "purpose": row.get("purpose") or "",
            "payment_config": cfg,
            "csrf": _dash_csrf(int(rest["id"])),
            "billing_checkout_bootstrap": {"intent_id": intent, "config": cfg, "messages": pay.checkout_messages()},
        },
    )


@app.get("/checkout/{order_id}", response_class=HTMLResponse)
async def page_embedded_checkout_demo(order_id: str, request: Request):
    """Super-admin test checkout only."""
    admin = auth.get_current_super_admin(request)
    if not admin or mf.MYFATOORAH_ENV != "test":
        raise HTTPException(status_code=404, detail="Not found")
    raise HTTPException(status_code=404, detail="Use /dashboard/checkout")


@app.api_route("/payment/myfatoorah/return", methods=["GET", "POST"], response_class=HTMLResponse)
async def page_myfatoorah_return(request: Request, paymentId: Optional[str] = None):
    pid = paymentId or request.query_params.get("paymentId") or request.query_params.get("paymentid") or ""
    intent = request.query_params.get("intent") or ""
    result = await pay.resolve_result(pid, intent, "return")
    owner = _owner_restaurant(request)
    response = templates.TemplateResponse(
        "payment_return.html",
        {
            "request": request,
            "state": result["state"],
            "reason": result["reason"],
            "view": pay.result_view(intent, int(owner["id"]) if owner else None),
            "poll": {
                "url": "/api/payment/myfatoorah/result",
                "payment_id": pid,
                "intent": intent,
                "interval_ms": _RESULT_POLL_MS,
                "max_ms": _RESULT_POLL_MAX_MS,
            },
        },
    )
    response.headers["Cache-Control"] = "no-store"
    return response


@app.get("/api/payment/myfatoorah/result")
async def api_myfatoorah_result(paymentId: str = "", intent: str = ""):
    result = await pay.resolve_result(paymentId, intent, "poll")
    return {"state": result["state"]}


@app.post("/api/payment/myfatoorah/webhook")
async def webhook_myfatoorah(request: Request):
    raw_body = await request.body()
    signature = request.headers.get("myfatoorah-signature") or request.headers.get("X-MyFatoorah-Signature") or ""
    if not mf.verify_webhook_signature(raw_body, signature):
        raise HTTPException(status_code=400, detail="Invalid webhook signature")
    try:
        payload = json.loads(raw_body.decode("utf-8"))
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid JSON")
    data = payload.get("Data") or payload.get("data") or {}
    tx = data.get("Transaction") or {}
    pid = str(tx.get("PaymentId") or data.get("PaymentId") or "")
    if pid:
        try:
            await pay.settle_payment(pid, "webhook")
        except ValueError:
            logger.warning("Webhook settlement skipped for %s", pid)
        except Exception:
            logger.exception("Webhook settlement failed for %s", pid)
    return {"status": "success"}


from . import admin_panel
admin_panel.install(app, templates)
