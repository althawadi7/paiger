"""Super-admin pages. Every write is a POST with an HMAC token and an audit row."""
import asyncio
import csv
import hmac
import hashlib
import io
import logging
import os
import re
import secrets
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional
from urllib.parse import quote

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from markupsafe import Markup

from . import auth, billing, cashier_gate, database as db, i18n, mailer, owner_board

_STATUSES = ("all", "active", "trialing", "past_due", "expired", "suspended", "legacy")
_PAY_FILTERS = ("all", "unpaid", "paid", "refunded")
_NOTICES = {
    "saved": "admin.done",
    "created": "admin.notice.created",
    "restaurant": "admin.notice.restaurant",
    "deleted": "admin.notice.deleted",
    "restored": "admin.notice.restored",
    "paid": "admin.markedPaid",
    "plan_hidden": "admin.notice.hidden",
    "email": "admin.notice.email",
    "email_skipped": "admin.notice.emailSkipped",
}

_PLAN_CODE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_ERRORS = {
    "csrf": "admin.err.csrf",
    "trial": "admin.err.trial",
    "plan": "admin.err.plan",
    "points": "admin.err.points",
    "pack": "admin.err.pack",
    "sold": "admin.err.sold",
    "invoice": "admin.err.invoice",
    "confirm": "admin.err.confirm",
    "already": "admin.err.already",
    "paid": "admin.err.paid",
    "settings": "admin.err.settings",
    "price": "admin.err.price",
    "code": "admin.err.codeFormat",
    "format": "admin.err.codeFormat",
    "taken": "admin.err.codeTaken",
    "deleted": "admin.err.codeDeleted",
    "invalid": "admin.err.invalid",
    "used": "admin.err.used",
    "rest": "admin.err.rest",
    "slug": "admin.err.slug",
    "email": "admin.err.email",
    "phone": "admin.err.phone",
    "date": "admin.err.date",
}

_HANDOFFS: Dict[str, Dict[str, Any]] = {}
_log = logging.getLogger("uvicorn.error")


def _flash(code: str, table: Dict[str, str], fallback: str) -> str:
    if not code:
        return ""
    key = table.get(code)
    if not key:
        return i18n.t(fallback)
    text = i18n.t(key)
    if not text or text == key:
        return i18n.t(fallback)
    return text


def _secure() -> bool:
    value = (os.getenv("COOKIE_SECURE", "true") or "true").strip().lower()
    return value not in {"0", "false", "no", "off"}


def _ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for") or ""
    if forwarded:
        return forwarded.split(",")[0].strip()[:64]
    if request.client and request.client.host:
        return request.client.host[:64]
    return ""


def _csrf(admin_id: int) -> str:
    return hmac.new(auth.SECRET_KEY.encode("utf-8"), f"admin:{int(admin_id)}".encode("utf-8"), hashlib.sha256).hexdigest()


def _csrf_ok(admin_id: int, token: str) -> bool:
    expected = _csrf(admin_id)
    offered = token or ""
    return len(expected) == len(offered) and hmac.compare_digest(expected, offered)


def _admin(request: Request):
    user = auth.get_current_super_admin(request)
    if not user or not user.get("sub"):
        return None
    return user


def _gate(request: Request):
    user = _admin(request)
    if not user:
        return None, RedirectResponse(url="/admin/login", status_code=302)
    return user, None


async def _form(request: Request) -> Dict[str, Any]:
    data = await request.form()
    return data


def _long_date(value) -> str:
    when = value if isinstance(value, datetime) else None
    if when is None and value:
        when = db._parse_dt(value)
    if not when:
        return "—"
    return when.strftime("%-d %B %Y")


def _chart(months: List[Dict[str, Any]]) -> Markup:
    width, height = 640, 230
    pad_l, pad_b, pad_t = 48, 24, 12
    peak = max([int(m["fils"]) for m in months] or [0]) / 1000
    axis_max = 1.0 if peak <= 0 else peak * 1.2
    slot = (width - pad_l) / max(len(months), 1)
    bar_w = min(36, slot * 0.46)
    parts = []
    for step in range(5):
        value = axis_max * step / 4
        y = pad_t + (height - pad_b - pad_t) * (1 - value / axis_max)
        label = f"{value:.0f}" if axis_max >= 10 else f"{value:.1f}"
        parts.append(f'<line class="gl" x1="{pad_l}" x2="{width}" y1="{y:.1f}" y2="{y:.1f}"/>')
        parts.append(f'<text class="ax" x="{pad_l - 8}" y="{y + 4:.1f}" text-anchor="end">{label}</text>')
    for index, month in enumerate(months):
        value = int(month["fils"]) / 1000
        bh = (height - pad_b - pad_t) * (value / axis_max)
        x = pad_l + index * slot + (slot - bar_w) / 2
        y = height - pad_b - bh
        cur = " cur" if month["current"] else ""
        label = month["label"].replace("&", "")
        parts.append(
            f'<g data-m="{label}" data-v="{month["bhd"]}">'
            f'<rect class="hit" x="{x - 6:.1f}" y="{pad_t}" width="{bar_w + 12:.1f}" height="{height - pad_t - pad_b}"/>'
            f'<rect class="bar{cur}" x="{x:.1f}" y="{y:.1f}" width="{bar_w:.1f}" height="{max(bh, 0):.1f}" rx="4"/>'
            f"</g>"
        )
        parts.append(f'<text class="ax" x="{x + bar_w / 2:.1f}" y="{height - 6}" text-anchor="middle">{label}</text>')
    svg = f'<svg class="chart" id="chart" viewBox="0 0 {width} {height}" role="img">{"".join(parts)}</svg>'
    return Markup(svg)


def _undo_target(notice: str, kind: str, raw_id: str):
    if notice != "deleted":
        return None
    if kind == "plan" and _PLAN_CODE.fullmatch(raw_id or ""):
        return {"action": f"/admin/plans/{raw_id}/undo", "flash": "admin.notice.deletedPlan"}
    if kind == "pack" and (raw_id or "").isdigit():
        return {"action": f"/admin/packs/{int(raw_id)}/undo", "flash": "admin.notice.deletedPack"}
    return None


def _shell(request: Request, user: dict, page: str, **extra):
    record = db.get_super_admin_by_id(int(user["sub"])) or {}
    name = record.get("full_name") or record.get("username") or "Admin"
    notice = request.query_params.get("notice") or ""
    error = request.query_params.get("error") or ""
    flash = _flash(error, _ERRORS, "admin.errGeneric") if error else _flash(notice, _NOTICES, "admin.done")
    undo = _undo_target(notice, request.query_params.get("undo") or "", request.query_params.get("id") or "")
    if undo:
        flash = i18n.t(undo["flash"])
    ctx = {
        "request": request,
        "page": page,
        "csrf": _csrf(int(user["sub"])),
        "admin_name": name,
        "admin_initial": (name[:1] or "A").upper(),
        "restaurant_count": billing.list_restaurants("all", "", 1)["counts"]["all"],
        "notice": notice,
        "error": error,
        "flash": flash,
        "flash_error": bool(error),
        "undo": undo,
    }
    ctx.update(extra)
    return ctx


def _public_base() -> str:
    domain = (os.getenv("DOMAIN") or "").strip()
    if domain:
        return f"https://{domain}"
    port = (os.getenv("PORT") or "8035").strip()
    return f"http://127.0.0.1:{port}"


def _doc_model(invoice: dict, request: Optional[Request], for_pdf: bool, base: str = "") -> Dict[str, Any]:
    status = invoice.get("status") or "unpaid"
    title = "Credit note" if status == "refunded" else "Receipt" if status == "paid" else "Invoice"
    sign = -1 if status == "refunded" else 1
    cfg = billing.settings()
    lines = []
    for line in invoice.get("line_items") or []:
        lines.append({
            "description": line.get("description") or "",
            "detail": line.get("detail") or "",
            "qty": line.get("qty") or 1,
            "amount": billing.fils_fixed(sign * int(line.get("amount_fils") or 0)),
        })
    if not lines:
        lines.append({
            "description": invoice.get("description") or "",
            "detail": "",
            "qty": 1,
            "amount": billing.fils_fixed(sign * int(invoice.get("total_fils") or invoice.get("amount_fils") or 0)),
        })
    method = invoice.get("paid_method") or ""
    if method == "card" and invoice.get("card_last4"):
        method_label = f"Visa ending {invoice['card_last4']}"
    else:
        method_label = {"cash": "Cash", "bank_transfer": "Bank transfer", "other": "Other", "card": "Card"}.get(method, method or "—")
    if not base:
        base = str(request.base_url).rstrip("/") if request is not None else _public_base()
    else:
        base = base.rstrip("/")
    number = invoice.get("number") or ""
    token = invoice.get("pay_token") or ""
    total = sign * int(invoice.get("total_fils") if invoice.get("total_fils") is not None else invoice.get("amount_fils") or 0)
    sub = sign * int(invoice.get("subtotal_fils") if invoice.get("subtotal_fils") is not None else total)
    return {
        "request": request,
        "for_pdf": for_pdf,
        "invoice": invoice,
        "title": title,
        "lines": lines,
        "amount": billing.fils_fixed(total),
        "subtotal": billing.fils_fixed(sub),
        "issued": _long_date(invoice.get("issued_at") or invoice.get("created_at")),
        "due": _long_date(invoice.get("due_at")),
        "paid_on": _long_date(invoice.get("paid_at")),
        "method": method_label,
        "reference": invoice.get("provider_ref") or "—",
        "related": invoice.get("related_number") or "",
        "second_label": "Paid on" if status == "paid" else "Due",
        "second_value": _long_date(invoice.get("paid_at")) if status == "paid" else _long_date(invoice.get("due_at")),
        "total_label": "Total paid" if status == "paid" else "Amount due" if status == "unpaid" else "Total",
        "pay_url": f"{base}/pay/{number}?t={quote(token)}",
        "pay_host": request.url.netloc if request is not None else base.split("://", 1)[-1],
        "seller": {
            "name": cfg.get("seller_name") or "",
            "cr": cfg.get("seller_cr") or "",
            "address": cfg.get("seller_address") or "",
            "billing_email": cfg.get("billing_email") or "",
            "support_email": cfg.get("support_email") or "",
        },
    }


def _pdf_name(invoice: dict) -> str:
    status = invoice.get("status") or ""
    kind = "Receipt" if status == "paid" else "Credit-note" if status == "refunded" else "Invoice"
    return f"Trings-{kind}-{invoice.get('number')}.pdf"


def invoice_pdf_bytes(invoice: dict) -> bytes:
    """Same WeasyPrint document as the download, with pay links on the public domain."""
    from .main import templates
    from weasyprint import HTML

    html = templates.get_template("invoice_doc.html").render(**_doc_model(invoice, None, True, _public_base()))
    return HTML(string=html).write_pdf()


def _render_pdf(templates, invoice: dict, request: Request) -> Response:
    html = templates.get_template("invoice_doc.html").render(**_doc_model(invoice, request, True))
    from weasyprint import HTML
    pdf = HTML(string=html).write_pdf()
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{_pdf_name(invoice)}"'},
    )


def _gateway() -> Dict[str, str]:
    mode = (os.getenv("MYFATOORAH_PAYMENT_MODE") or "test").strip().lower()
    return {
        "configured": "yes" if (os.getenv("MYFATOORAH_API_KEY") or "").strip() else "no",
        "mode": "live" if mode == "live" else "test",
        "webhook": "/api/payment/myfatoorah/webhook",
    }


def install(app: FastAPI, templates) -> None:
    def page(name: str, **extra):
        async def handler(request: Request):
            user, bounce = _gate(request)
            if bounce:
                return bounce
            return templates.TemplateResponse(f"admin/{name}.html", _shell(request, user, name, **extra(request)))
        return handler

    @app.api_route("/admin", methods=["GET", "HEAD"], response_class=HTMLResponse)
    @app.api_route("/admin/dashboard", methods=["GET", "HEAD"], response_class=HTMLResponse)
    async def admin_overview(request: Request):
        user, bounce = _gate(request)
        if bounce:
            return bounce
        data = billing.overview()
        return templates.TemplateResponse("admin/overview.html", _shell(
            request, user, "overview", overview=data, chart=_chart(data["months"]),
        ))

    @app.api_route("/admin/restaurants", methods=["GET", "HEAD"], response_class=HTMLResponse)
    async def admin_restaurants(request: Request):
        user, bounce = _gate(request)
        if bounce:
            return bounce
        status = request.query_params.get("status") or "all"
        if status not in _STATUSES:
            status = "all"
        query = request.query_params.get("q") or ""
        try:
            page_no = int(request.query_params.get("page") or "1")
        except ValueError:
            page_no = 1
        found = billing.list_restaurants(status, query, page_no)
        return templates.TemplateResponse("admin/restaurants.html", _shell(
            request, user, "restaurants",
            rows=found["rows"], total=found["total"], page_no=found["page"], pages=found["pages"],
            counts=found["counts"], status_key=status, query=query, statuses=_STATUSES,
            open_id=request.query_params.get("open") or "",
            trial_days=owner_board.trial_length(),
            handoff=_HANDOFFS.pop(request.query_params.get("handoff") or "", None),
        ))

    def _forget_restaurant(restaurant_id: int) -> None:
        conn = db.get_db_connection()
        try:
            with conn.cursor() as cur:
                for table in (
                    "invoices", "point_ledger", "point_wallets", "password_reset_tokens",
                    "cashier_devices", "cashier_pin_attempts", "cashier_pin_fail_log", "orders", "email_log",
                ):
                    cur.execute(f"DELETE FROM {table} WHERE restaurant_id=%s", (restaurant_id,))
                cur.execute("DELETE FROM restaurants WHERE id=%s", (restaurant_id,))
                conn.commit()
        finally:
            conn.close()

    async def _ready_email(email: str, name: str, slug: str, base: str) -> None:
        try:
            host = base.rstrip("/")
            text = (
                f"{name} is ready on Trings.\n"
                f"Dashboard: {host}/dashboard\n"
                f"Cashier: {host}/r/{slug}/cashier\n"
                f"Customer link: {host}/r/{slug}\n"
            )
            html = "".join(f"<p>{line}</p>" for line in text.splitlines() if line)
            await asyncio.to_thread(mailer.send_email, email, "Welcome to Trings", html, text)
        except Exception:
            _log.exception("Could not prepare the welcome email")

    @app.post("/admin/restaurants/create")
    async def admin_create_restaurant(request: Request):
        from .main import _normalize_phone, _send_reset, _send_welcome, _valid_email, get_public_base_url

        user = _admin(request)
        if not user:
            return RedirectResponse("/admin/login", status_code=302)
        form = await request.form()
        if not _csrf_ok(int(user["sub"]), str(form.get("csrf") or "")):
            return RedirectResponse("/admin/restaurants?error=csrf", status_code=303)
        name = str(form.get("name") or "").strip()
        slug = str(form.get("slug") or "").strip().lower()
        email = str(form.get("email") or "").strip().lower()
        mode = str(form.get("password_mode") or "link")
        plan = str(form.get("plan") or "trial")
        color = str(form.get("brand_color") or "").strip()
        if not name or len(name) > 120:
            return RedirectResponse("/admin/restaurants?error=rest", status_code=303)
        if db.slug_problem(slug):
            return RedirectResponse("/admin/restaurants?error=slug", status_code=303)
        if not _valid_email(email) or db.email_in_use(email):
            return RedirectResponse("/admin/restaurants?error=email", status_code=303)
        phone = _normalize_phone(str(form.get("phone") or ""))
        if not phone:
            return RedirectResponse("/admin/restaurants?error=phone", status_code=303)
        if mode not in ("link", "now") or plan not in ("trial", "monthly", "yearly", "points", "legacy"):
            return RedirectResponse("/admin/restaurants?error=rest", status_code=303)
        until = str(form.get("until") or "").strip()
        days = owner_board.trial_length()
        if plan == "trial":
            try:
                days = int(str(form.get("days") or days))
            except ValueError:
                return RedirectResponse("/admin/restaurants?error=date", status_code=303)
            if days < 1 or days > 60:
                return RedirectResponse("/admin/restaurants?error=date", status_code=303)
        elif plan in ("monthly", "yearly", "points"):
            try:
                chosen = datetime.strptime(until, "%Y-%m-%d").date()
            except ValueError:
                return RedirectResponse("/admin/restaurants?error=date", status_code=303)
            if chosen < datetime.now().date():
                return RedirectResponse("/admin/restaurants?error=date", status_code=303)
        if color and not re.fullmatch(r"#(?:[0-9a-fA-F]{6})", color):
            color = ""
        shown_password = ""
        if mode == "now":
            alphabet = "abcdefghijkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789"
            shown_password = "".join(secrets.choice(alphabet) for _ in range(12))
            password_hash = auth.hash_password(shown_password)
        else:
            password_hash = auth.hash_password(secrets.token_urlsafe(24))
        try:
            created = db.create_restaurant(
                name=name, slug=slug, email=email, password_hash=password_hash,
                phone=phone, brand_color=color or "#111111",
            )
        except Exception:
            _log.exception("Could not create restaurant")
            reason = "email" if db.email_in_use(email) else "slug"
            return RedirectResponse(f"/admin/restaurants?error={reason}", status_code=303)
        if not created:
            return RedirectResponse("/admin/restaurants?error=rest", status_code=303)
        restaurant_id = int(created["id"])
        try:
            billing.ensure_wallet(restaurant_id)
            pin = cashier_gate.issue_initial_pin(restaurant_id)
            if plan == "trial":
                db.start_restaurant_trial(restaurant_id, "monthly", days)
            elif plan == "legacy":
                billing.set_legacy_plan(restaurant_id)
            else:
                billing.set_manual_plan(restaurant_id, plan, until, int(user["sub"]))
            if color:
                db.set_restaurant_brand(restaurant_id, color)
        except Exception:
            _log.exception("Could not finish the new restaurant")
            _forget_restaurant(restaurant_id)
            return RedirectResponse("/admin/restaurants?error=plan", status_code=303)
        base = get_public_base_url(request)
        if mode == "link":
            raw = secrets.token_urlsafe(32)
            db.create_password_reset(restaurant_id, raw)
            asyncio.create_task(_send_reset(email, name, f"{base}/reset-password/{raw}", base))
        else:
            fresh = db.get_restaurant_by_id(restaurant_id) or {}
            if fresh.get("trial_ends_at"):
                asyncio.create_task(_send_welcome(email, name, slug, fresh.get("trial_ends_at"), base))
            else:
                asyncio.create_task(_ready_email(email, name, slug, base))
        billing.audit(int(user["sub"]), "restaurant_created_by_admin", restaurant_id, {
            "slug": slug, "plan": plan, "password": mode,
        }, _ip(request))
        token = secrets.token_urlsafe(18)
        _HANDOFFS[token] = {
            "id": restaurant_id,
            "pin": pin,
            "email": email,
            "link": f"{base}/r/{slug}",
            "password": shown_password,
        }
        return RedirectResponse(
            f"/admin/restaurants?notice=restaurant&open={restaurant_id}&handoff={token}",
            status_code=303,
        )

    @app.get("/admin/restaurants.csv")
    async def admin_restaurants_csv(request: Request):
        user, bounce = _gate(request)
        if bounce:
            return bounce
        status = request.query_params.get("status") or "all"
        found = billing.list_restaurants(status if status in _STATUSES else "all", request.query_params.get("q") or "", 1)
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(["name", "slug", "email", "status", "plan", "renew", "orders_30d", "joined"])
        for row in found["all_rows"]:
            writer.writerow([row["name"], row["slug"], row["email"], row["status"], row["plan_label"], row["renew"], row["orders_30d"], row["joined"]])
        return Response(buf.getvalue(), media_type="text/csv", headers={"Content-Disposition": 'attachment; filename="restaurants.csv"'})

    @app.get("/admin/restaurants/{restaurant_id}.json")
    async def admin_restaurant_json(restaurant_id: int, request: Request):
        user = _admin(request)
        if not user:
            return JSONResponse({"detail": "Unauthorized"}, status_code=401)
        detail = billing.restaurant_detail(restaurant_id)
        if not detail:
            return JSONResponse({"detail": "Not found"}, status_code=404)
        return detail

    async def _write(request: Request, restaurant_id: int, action: str, details: dict):
        user = _admin(request)
        if not user:
            return None, RedirectResponse("/admin/login", status_code=302)
        form = await request.form()
        if not _csrf_ok(int(user["sub"]), str(form.get("csrf") or "")):
            return None, RedirectResponse(f"/admin/restaurants?open={restaurant_id}&error=csrf", status_code=303)
        return form, None

    @app.post("/admin/restaurants/{restaurant_id}/trial")
    async def admin_extend_trial(restaurant_id: int, request: Request):
        form, bounce = await _write(request, restaurant_id, "extend_trial", {})
        if bounce:
            return bounce
        try:
            days = int(str(form.get("days") or ""))
            billing.extend_trial(restaurant_id, days)
        except ValueError:
            return RedirectResponse(f"/admin/restaurants?open={restaurant_id}&error=trial", status_code=303)
        billing.audit(int(_admin(request)["sub"]), "extend_trial", restaurant_id, {"days": days}, _ip(request))
        return RedirectResponse(f"/admin/restaurants?open={restaurant_id}&notice=saved", status_code=303)

    @app.post("/admin/restaurants/{restaurant_id}/plan")
    async def admin_set_plan(restaurant_id: int, request: Request):
        user = _admin(request)
        if not user:
            return RedirectResponse("/admin/login", status_code=302)
        form = await request.form()
        if not _csrf_ok(int(user["sub"]), str(form.get("csrf") or "")):
            return RedirectResponse(f"/admin/restaurants?open={restaurant_id}&error=csrf", status_code=303)
        try:
            number = billing.set_manual_plan(restaurant_id, str(form.get("code") or ""), str(form.get("until") or ""), int(user["sub"]))
        except ValueError:
            return RedirectResponse(f"/admin/restaurants?open={restaurant_id}&error=plan", status_code=303)
        billing.audit(int(user["sub"]), "set_plan", restaurant_id, {"code": form.get("code"), "until": form.get("until"), "invoice": number}, _ip(request))
        return RedirectResponse(f"/admin/restaurants?open={restaurant_id}&notice=saved", status_code=303)

    @app.post("/admin/restaurants/{restaurant_id}/points")
    async def admin_adjust_points(restaurant_id: int, request: Request):
        user = _admin(request)
        if not user:
            return RedirectResponse("/admin/login", status_code=302)
        form = await request.form()
        if not _csrf_ok(int(user["sub"]), str(form.get("csrf") or "")):
            return RedirectResponse(f"/admin/restaurants?open={restaurant_id}&error=csrf", status_code=303)
        try:
            delta = int(str(form.get("delta") or ""))
            balance = billing.adjust_points(restaurant_id, delta, str(form.get("reason") or ""), int(user["sub"]))
        except ValueError:
            return RedirectResponse(f"/admin/restaurants?open={restaurant_id}&error=points", status_code=303)
        billing.audit(int(user["sub"]), "adjust_points", restaurant_id, {"delta": delta, "balance": balance}, _ip(request))
        return RedirectResponse(f"/admin/restaurants?open={restaurant_id}&notice=saved", status_code=303)

    @app.post("/admin/restaurants/{restaurant_id}/suspend")
    async def admin_suspend(restaurant_id: int, request: Request):
        user = _admin(request)
        if not user:
            return RedirectResponse("/admin/login", status_code=302)
        form = await request.form()
        if not _csrf_ok(int(user["sub"]), str(form.get("csrf") or "")):
            return RedirectResponse(f"/admin/restaurants?open={restaurant_id}&error=csrf", status_code=303)
        on = str(form.get("suspended") or "") == "1"
        billing.set_suspended(restaurant_id, on)
        billing.audit(int(user["sub"]), "suspend" if on else "reactivate", restaurant_id, {"suspended": on}, _ip(request))
        return RedirectResponse(f"/admin/restaurants?open={restaurant_id}&notice=saved", status_code=303)

    @app.post("/admin/restaurants/{restaurant_id}/impersonate")
    async def admin_impersonate(restaurant_id: int, request: Request):
        user = _admin(request)
        if not user:
            return RedirectResponse("/admin/login", status_code=302)
        form = await request.form()
        if not _csrf_ok(int(user["sub"]), str(form.get("csrf") or "")):
            return RedirectResponse(f"/admin/restaurants?open={restaurant_id}&error=csrf", status_code=303)
        rest = db.get_restaurant_by_id(restaurant_id)
        if not rest:
            raise HTTPException(status_code=404, detail="Not found")
        token = auth.create_access_token({
            "sub": str(restaurant_id),
            "role": "restaurant",
            "readonly": True,
            "impersonator": int(user["sub"]),
            "name": rest.get("name") or rest.get("slug") or "",
        }, expires_hours=0.5)
        billing.audit(int(user["sub"]), "impersonate_start", restaurant_id, {}, _ip(request))
        response = RedirectResponse("/dashboard", status_code=303)
        response.set_cookie(auth.RESTAURANT_COOKIE, token, httponly=True, samesite="lax", secure=_secure(), max_age=1800, path="/")
        return response

    @app.get("/admin/view/exit")
    async def admin_exit_view(request: Request):
        payload = auth.decode_token(request.cookies.get(auth.RESTAURANT_COOKIE) or "")
        if payload and payload.get("readonly") and payload.get("impersonator"):
            billing.audit(int(payload["impersonator"]), "impersonate_end", int(payload.get("sub") or 0), {}, _ip(request))
        response = RedirectResponse("/admin/restaurants", status_code=303)
        response.delete_cookie(auth.RESTAURANT_COOKIE, path="/", secure=_secure(), httponly=True, samesite="lax")
        return response

    @app.api_route("/admin/plans", methods=["GET", "HEAD"], response_class=HTMLResponse)
    async def admin_plans(request: Request):
        user, bounce = _gate(request)
        if bounce:
            return bounce
        return templates.TemplateResponse("admin/plans.html", _shell(
            request, user, "plans",
            plans=billing.plans_admin(),
            packs=billing.packs_admin(),
            deleted=billing.recently_deleted(),
            reserved=billing.reserved_plan_codes(),
        ))

    @app.post("/admin/plans/save")
    async def admin_save_plan(request: Request):
        user = _admin(request)
        if not user:
            return RedirectResponse("/admin/login", status_code=302)
        form = await request.form()
        ajax = request.headers.get("x-paiger") == "1"

        def _fail(reason: str):
            if reason not in ("price", "format", "taken", "deleted", "plan", "code", "csrf"):
                reason = "plan"
            if ajax:
                return JSONResponse({"ok": False, "error": reason}, status_code=400)
            return RedirectResponse(f"/admin/plans?error={reason}", status_code=303)

        if not _csrf_ok(int(user["sub"]), str(form.get("csrf") or "")):
            return _fail("csrf")
        code = str(form.get("code") or "").strip().lower()
        creating = str(form.get("editing") or "") != "1"
        try:
            billing.save_plan(
                code, str(form.get("type") or ""), str(form.get("name_en") or ""), str(form.get("name_ar") or ""),
                str(form.get("price") or ""), str(form.get("blurb_en") or ""), str(form.get("blurb_ar") or ""),
                1 if str(form.get("active") or "1") == "1" else 0,
                creating=creating,
                features={"en": form.getlist("feat_en"), "ar": form.getlist("feat_ar")},
                featured=str(form.get("featured") or "") == "1",
                badge_en=str(form.get("badge_en") or ""),
                badge_ar=str(form.get("badge_ar") or ""),
            )
        except ValueError as exc:
            return _fail(str(exc))
        billing.audit(int(user["sub"]), "plan_save", None, {"code": code}, _ip(request))
        if ajax:
            return JSONResponse({"ok": True, "redirect": "/admin/plans?notice=saved"})
        return RedirectResponse("/admin/plans?notice=saved", status_code=303)

    @app.post("/admin/plans/{code}/visible")
    async def admin_plan_visible(code: str, request: Request):
        user = _admin(request)
        if not user:
            return RedirectResponse("/admin/login", status_code=302)
        form = await request.form()
        if not _csrf_ok(int(user["sub"]), str(form.get("csrf") or "")):
            return RedirectResponse("/admin/plans?error=csrf", status_code=303)
        active = 1 if str(form.get("active") or "") == "1" else 0
        try:
            billing.set_plan_visible(code, active)
        except ValueError as exc:
            return RedirectResponse(f"/admin/plans?error={'price' if str(exc) == 'price' else 'plan'}", status_code=303)
        billing.audit(int(user["sub"]), "plan_visible", None, {"code": code, "active": active}, _ip(request))
        return RedirectResponse(f"/admin/plans?notice={'plan_hidden' if not active else 'saved'}", status_code=303)

    @app.post("/admin/plans/{code}/delete")
    async def admin_delete_plan(code: str, request: Request):
        user = _admin(request)
        if not user:
            return RedirectResponse("/admin/login", status_code=302)
        form = await request.form()
        if not _csrf_ok(int(user["sub"]), str(form.get("csrf") or "")):
            return RedirectResponse("/admin/plans?error=csrf", status_code=303)
        try:
            billing.delete_plan(code)
        except ValueError:
            return RedirectResponse("/admin/plans?error=used", status_code=303)
        billing.audit(int(user["sub"]), "plan_delete", None, {"code": code}, _ip(request))
        return RedirectResponse(f"/admin/plans?notice=deleted&undo=plan&id={code}", status_code=303)

    @app.post("/admin/plans/{code}/undo")
    async def admin_undo_plan(code: str, request: Request):
        return await _restore_plan(code, request, "plan_undo")

    @app.post("/admin/plans/{code}/restore")
    async def admin_restore_plan(code: str, request: Request):
        return await _restore_plan(code, request, "plan_restore")

    @app.post("/admin/packs/save")
    async def admin_save_pack(request: Request):
        user = _admin(request)
        if not user:
            return RedirectResponse("/admin/login", status_code=302)
        form = await request.form()
        if not _csrf_ok(int(user["sub"]), str(form.get("csrf") or "")):
            return RedirectResponse("/admin/plans?error=csrf", status_code=303)
        raw_id = str(form.get("pack_id") or "").strip()
        try:
            billing.save_pack(int(raw_id) if raw_id else None, int(str(form.get("points") or "")), str(form.get("price") or ""), 1 if str(form.get("active") or "1") == "1" else 0)
        except ValueError:
            return RedirectResponse("/admin/plans?error=pack", status_code=303)
        billing.audit(int(user["sub"]), "pack_save", None, {"pack_id": raw_id, "points": form.get("points")}, _ip(request))
        return RedirectResponse("/admin/plans?notice=saved", status_code=303)

    @app.post("/admin/packs/{pack_id}/delete")
    async def admin_delete_pack(pack_id: int, request: Request):
        user = _admin(request)
        if not user:
            return RedirectResponse("/admin/login", status_code=302)
        form = await request.form()
        if not _csrf_ok(int(user["sub"]), str(form.get("csrf") or "")):
            return RedirectResponse("/admin/plans?error=csrf", status_code=303)
        try:
            billing.delete_pack(pack_id)
        except ValueError as exc:
            reason = "sold" if str(exc) == "sold" else "pack"
            return RedirectResponse(f"/admin/plans?error={reason}", status_code=303)
        billing.audit(int(user["sub"]), "pack_delete", None, {"pack_id": pack_id}, _ip(request))
        return RedirectResponse(f"/admin/plans?notice=deleted&undo=pack&id={pack_id}", status_code=303)

    async def _restore_plan(code: str, request: Request, action: str):
        user = _admin(request)
        if not user:
            return RedirectResponse("/admin/login", status_code=302)
        form = await request.form()
        if not _csrf_ok(int(user["sub"]), str(form.get("csrf") or "")):
            return RedirectResponse("/admin/plans?error=csrf", status_code=303)
        try:
            billing.restore_plan(code)
        except ValueError:
            return RedirectResponse("/admin/plans?error=plan", status_code=303)
        billing.audit(int(user["sub"]), action, None, {"code": code}, _ip(request))
        return RedirectResponse("/admin/plans?notice=restored", status_code=303)

    @app.post("/admin/packs/{pack_id}/undo")
    async def admin_undo_pack(pack_id: int, request: Request):
        return await _restore_pack(pack_id, request, "pack_undo")

    @app.post("/admin/packs/{pack_id}/restore")
    async def admin_restore_pack(pack_id: int, request: Request):
        return await _restore_pack(pack_id, request, "pack_restore")

    async def _restore_pack(pack_id: int, request: Request, action: str):
        user = _admin(request)
        if not user:
            return RedirectResponse("/admin/login", status_code=302)
        form = await request.form()
        if not _csrf_ok(int(user["sub"]), str(form.get("csrf") or "")):
            return RedirectResponse("/admin/plans?error=csrf", status_code=303)
        try:
            billing.restore_pack(pack_id)
        except ValueError:
            return RedirectResponse("/admin/plans?error=pack", status_code=303)
        billing.audit(int(user["sub"]), action, None, {"pack_id": pack_id}, _ip(request))
        return RedirectResponse("/admin/plans?notice=restored", status_code=303)

    @app.api_route("/admin/payments", methods=["GET", "HEAD"], response_class=HTMLResponse)
    async def admin_payments(request: Request):
        user, bounce = _gate(request)
        if bounce:
            return bounce
        status = request.query_params.get("status") or "all"
        if status not in _PAY_FILTERS:
            status = "all"
        query = request.query_params.get("q") or ""
        rows = billing.list_invoices(status, query)
        restaurants = db.get_all_restaurants(limit=500)
        return templates.TemplateResponse("admin/payments.html", _shell(
            request, user, "payments",
            invoices=rows, kpis=billing.payment_kpis(), status_key=status, query=query,
            pay_filters=_PAY_FILTERS, restaurants=restaurants, today=(datetime.now() + timedelta(days=7)).strftime("%Y-%m-%d"),
            email_enabled=True,
        ))

    @app.get("/admin/payments.csv")
    async def admin_payments_csv(request: Request):
        user, bounce = _gate(request)
        if bounce:
            return bounce
        status = request.query_params.get("status") or "all"
        rows = billing.list_invoices(status if status in _PAY_FILTERS else "all", request.query_params.get("q") or "")
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(["number", "date", "restaurant", "description", "amount_bhd", "status", "reference"])
        for row in rows:
            writer.writerow([
                row.get("number") or "", row.get("created_at"), row.get("restaurant_name"),
                row.get("description"), billing.fils_fixed(row.get("total_fils")), row.get("status"), row.get("provider_ref") or "",
            ])
        return Response(buf.getvalue(), media_type="text/csv", headers={"Content-Disposition": 'attachment; filename="invoices.csv"'})

    @app.post("/admin/invoices")
    async def admin_new_invoice(request: Request):
        user = _admin(request)
        if not user:
            return RedirectResponse("/admin/login", status_code=302)
        form = await request.form()
        if not _csrf_ok(int(user["sub"]), str(form.get("csrf") or "")):
            return RedirectResponse("/admin/payments?error=csrf", status_code=303)
        try:
            restaurant_id = int(str(form.get("restaurant_id") or ""))
            due = datetime.strptime(str(form.get("due") or ""), "%Y-%m-%d")
            kind = str(form.get("kind") or "manual")
            if kind not in ("manual", "topup"):
                raise ValueError("kind")
            descs = form.getlist("desc")
            qtys = form.getlist("qty")
            amounts = form.getlist("amount")
            lines = []
            for desc, qty, amount in zip(descs, qtys, amounts):
                if not str(desc).strip():
                    continue
                lines.append({
                    "description": str(desc).strip()[:180],
                    "detail": "",
                    "qty": max(1, int(str(qty or "1"))),
                    "amount_fils": billing.parse_bhd(str(amount or "")),
                })
            if not lines:
                raise ValueError("lines")
            points = int(str(form.get("points") or "0") or "0") if kind == "topup" else None
            number = billing.create_invoice(
                restaurant_id, kind, lines[0]["description"], lines, due,
                admin_id=int(user["sub"]), points=points,
            )
        except (ValueError, TypeError):
            return RedirectResponse("/admin/payments?error=invoice", status_code=303)
        billing.audit(int(user["sub"]), "invoice_create", restaurant_id, {"number": number, "kind": kind}, _ip(request))
        return RedirectResponse(f"/admin/payments?notice=saved&q={quote(number)}", status_code=303)

    @app.post("/admin/invoices/{number}/paid")
    async def admin_mark_paid(number: str, request: Request):
        user = _admin(request)
        if not user:
            return RedirectResponse("/admin/login", status_code=302)
        form = await request.form()
        if not _csrf_ok(int(user["sub"]), str(form.get("csrf") or "")):
            return RedirectResponse("/admin/payments?error=csrf", status_code=303)
        if str(form.get("confirm") or "") != "1":
            return RedirectResponse(f"/admin/payments?error=confirm&q={quote(number)}", status_code=303)
        invoice = billing.get_invoice(number)
        try:
            billing.mark_paid(number, str(form.get("method") or ""), str(form.get("reference") or ""), int(user["sub"]))
        except ValueError as exc:
            code = "already" if str(exc) == "already" else "paid"
            return RedirectResponse(f"/admin/payments?error={code}&q={quote(number)}", status_code=303)
        billing.audit(int(user["sub"]), "invoice_paid", int(invoice["restaurant_id"]) if invoice else None, {
            "number": number, "method": form.get("method"), "reference": form.get("reference") or "",
        }, _ip(request))
        return RedirectResponse(f"/admin/payments?notice=paid&q={quote(number)}", status_code=303)

    @app.post("/admin/invoices/{number}/email")
    async def admin_resend_invoice(number: str, request: Request):
        user = _admin(request)
        if not user:
            return RedirectResponse("/admin/login", status_code=302)
        form = await request.form()
        if not _csrf_ok(int(user["sub"]), str(form.get("csrf") or "")):
            return RedirectResponse("/admin/payments?error=csrf", status_code=303)
        if str(form.get("confirm") or "") != "1":
            return RedirectResponse(f"/admin/payments?error=confirm&q={quote(number)}", status_code=303)
        invoice = billing.get_invoice(number)
        if not invoice or (invoice.get("status") or "") not in ("unpaid", "paid"):
            return RedirectResponse(f"/admin/payments?error=invoice&q={quote(number)}", status_code=303)
        from . import billing_mail
        result = await asyncio.to_thread(billing_mail.resend_invoice, invoice)
        kind = "payment_received" if invoice.get("status") == "paid" else "invoice_new"
        billing.audit(int(user["sub"]), "billing_email_resend", int(invoice["restaurant_id"]), {
            "number": number, "kind": kind, "status": result.get("status"),
        }, _ip(request))
        notice = "email" if result.get("status") == "sent" else "email_skipped"
        return RedirectResponse(f"/admin/payments?notice={notice}&q={quote(number)}", status_code=303)

    @app.api_route("/admin/settings", methods=["GET", "HEAD"], response_class=HTMLResponse)
    async def admin_settings(request: Request):
        user, bounce = _gate(request)
        if bounce:
            return bounce
        from . import billing_mail
        return templates.TemplateResponse("admin/settings.html", _shell(
            request, user, "settings", settings=billing.settings(), gateway=_gateway(),
            email=billing_mail.admin_status(), email_enabled=True,
        ))

    @app.post("/admin/settings")
    async def admin_save_settings(request: Request):
        user = _admin(request)
        if not user:
            return RedirectResponse("/admin/login", status_code=302)
        form = await request.form()
        if not _csrf_ok(int(user["sub"]), str(form.get("csrf") or "")):
            return RedirectResponse("/admin/settings?error=csrf", status_code=303)
        try:
            trial = int(str(form.get("trial_days") or ""))
            low = int(str(form.get("low_points_at") or ""))
            retry = int(str(form.get("renewal_retry_days") or ""))
            rule = str(form.get("points_empty_rule") or "")
            if trial < 0 or trial > 60 or low < 0 or retry < 0 or retry > 30 or rule not in ("block", "warn"):
                raise ValueError("range")
        except ValueError:
            return RedirectResponse("/admin/settings?error=settings", status_code=303)
        values = {
            "trial_days": str(trial),
            "points_empty_rule": rule,
            "low_points_at": str(low),
            "renewal_retry_days": str(retry),
            "seller_name": str(form.get("seller_name") or "").strip()[:160],
            "seller_cr": str(form.get("seller_cr") or "").strip()[:40],
            "seller_address": str(form.get("seller_address") or "").strip()[:240],
            "billing_email": str(form.get("billing_email") or "").strip()[:160],
            "support_email": str(form.get("support_email") or "").strip()[:160],
        }
        if not values["seller_name"] or "@" not in values["billing_email"] or "@" not in values["support_email"]:
            return RedirectResponse("/admin/settings?error=settings", status_code=303)
        billing.save_settings(values)
        billing.audit(int(user["sub"]), "settings_save", None, {"keys": sorted(values)}, _ip(request))
        return RedirectResponse("/admin/settings?notice=saved", status_code=303)

    @app.post("/admin/settings/test-email")
    async def admin_test_email(request: Request):
        user = _admin(request)
        if not user:
            return RedirectResponse("/admin/login", status_code=302)
        form = await request.form()
        if not _csrf_ok(int(user["sub"]), str(form.get("csrf") or "")):
            return RedirectResponse("/admin/settings?error=csrf", status_code=303)
        admin = db.get_super_admin_by_id(int(user["sub"])) or {}
        target = str(admin.get("email") or "").strip()
        if "@" not in target:
            return RedirectResponse("/admin/settings?error=settings", status_code=303)
        from . import billing_mail
        result = await asyncio.to_thread(billing_mail.send_admin_test, target)
        billing.audit(int(user["sub"]), "billing_email_test", None, {"status": result.get("status")}, _ip(request))
        notice = "email" if result.get("status") == "sent" else "email_skipped"
        return RedirectResponse(f"/admin/settings?notice={notice}", status_code=303)

    @app.get("/admin/invoices/{number}.pdf")
    async def admin_invoice_pdf(number: str, request: Request):
        user, bounce = _gate(request)
        if bounce:
            return bounce
        invoice = billing.get_invoice(number)
        if not invoice:
            raise HTTPException(status_code=404, detail="Not found")
        return _render_pdf(templates, invoice, request)

    @app.get("/admin/invoices/{number}", response_class=HTMLResponse)
    async def admin_invoice_html(number: str, request: Request):
        user, bounce = _gate(request)
        if bounce:
            return bounce
        invoice = billing.get_invoice(number)
        if not invoice:
            raise HTTPException(status_code=404, detail="Not found")
        return templates.TemplateResponse("invoice_doc.html", _doc_model(invoice, request, False))

    @app.get("/dashboard/invoices/{number}.pdf")
    async def owner_invoice_pdf(number: str, request: Request):
        user = auth.get_current_restaurant(request)
        invoice = billing.get_invoice(number) if user else None
        if not user or not invoice or int(invoice["restaurant_id"]) != int(user["sub"]):
            raise HTTPException(status_code=404, detail="Not found")
        return _render_pdf(templates, invoice, request)

    @app.get("/dashboard/invoices/{number}", response_class=HTMLResponse)
    async def owner_invoice_html(number: str, request: Request):
        user = auth.get_current_restaurant(request)
        invoice = billing.get_invoice(number) if user else None
        if not user or not invoice or int(invoice["restaurant_id"]) != int(user["sub"]):
            raise HTTPException(status_code=404, detail="Not found")
        return templates.TemplateResponse("invoice_doc.html", _doc_model(invoice, request, False))

    @app.get("/pay/{number}", response_class=HTMLResponse)
    async def pay_invoice(number: str, request: Request):
        invoice = billing.get_invoice(number)
        token = request.query_params.get("t") or ""
        expected = (invoice or {}).get("pay_token") or ""
        if not invoice or not token or len(token) != len(expected) or not hmac.compare_digest(token, expected):
            raise HTTPException(status_code=404, detail="Not found")
        status = invoice.get("status") or ""
        title = "Receipt" if status == "paid" else "Credit note" if status == "refunded" else "Invoice"
        sign = -1 if status == "refunded" else 1
        response = templates.TemplateResponse("pay.html", {
            "request": request,
            "invoice": invoice,
            "title": title,
            "amount": billing.fils_fixed(sign * int(invoice.get("total_fils") or 0)),
            "due": _long_date(invoice.get("due_at")) if status == "unpaid" else "",
        })
        response.headers["X-Robots-Tag"] = "noindex"
        return response
