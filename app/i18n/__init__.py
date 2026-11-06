import json
import logging
from contextvars import ContextVar, Token
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlparse

from markupsafe import Markup
from starlette.requests import Request
from starlette.responses import Response

logger = logging.getLogger("pager.i18n")

_DIR = Path(__file__).parent
EN = json.loads((_DIR / "en.json").read_text(encoding="utf-8"))
AR = json.loads((_DIR / "ar.json").read_text(encoding="utf-8"))

_lang: ContextVar[str] = ContextVar("paiger_lang", default="en")
_missing_logged: set = set()


def get_lang(request: Request) -> str:
    query_lang = request.query_params.get("lang")
    if query_lang in ("en", "ar"):
        return query_lang
    cookie_lang = request.cookies.get("lang")
    if cookie_lang in ("en", "ar"):
        return cookie_lang
    accept = request.headers.get("accept-language") or ""
    for part in accept.split(","):
        tag = part.split(";", 1)[0].strip().lower()
        if tag.startswith("ar"):
            return "ar"
        if tag.startswith("en"):
            return "en"
    return "en"


def activate(lang: str) -> Token:
    return _lang.set(lang if lang in ("en", "ar") else "en")


def reset(token: Token) -> None:
    _lang.reset(token)


def current_lang() -> str:
    return _lang.get()


def current_dir() -> str:
    return "rtl" if current_lang() == "ar" else "ltr"


def t(key: str) -> str:
    lang = current_lang()
    if lang == "ar":
        arabic = AR.get(key)
        if arabic:
            return arabic
    english = EN.get(key)
    if english:
        return english
    if key not in _missing_logged:
        _missing_logged.add(key)
        logger.warning("Missing i18n key: %s", key)
    return key


class _LangValue:
    def __str__(self) -> str:
        return current_lang()

    def __html__(self) -> Markup:
        return Markup(current_lang())

    def __eq__(self, other: object) -> bool:
        return current_lang() == other


class _DirValue:
    def __str__(self) -> str:
        return current_dir()

    def __html__(self) -> Markup:
        return Markup(current_dir())

    def __eq__(self, other: object) -> bool:
        return current_dir() == other


def apply_lang_cookie(response: Response, lang: str, request: Request) -> None:
    forwarded = (request.headers.get("x-forwarded-proto") or "").split(",")[0].strip()
    secure = request.url.scheme == "https" or forwarded == "https"
    response.set_cookie(
        "lang",
        lang if lang in ("en", "ar") else "en",
        max_age=60 * 60 * 24 * 365,
        path="/",
        samesite="lax",
        secure=secure,
        httponly=False,
    )


def return_path(request: Request) -> str:
    referer = request.headers.get("referer") or ""
    parsed = urlparse(referer)
    host = request.headers.get("host") or request.url.netloc
    if not parsed.path or (parsed.netloc and parsed.netloc != host):
        return "/"
    if parsed.path.startswith("/lang/"):
        return "/"
    query = [(k, v) for k, v in parse_qsl(parsed.query, keep_blank_values=True) if k != "lang"]
    if not query:
        return parsed.path or "/"
    return f"{parsed.path}?{urlencode(query)}"


def install(templates) -> None:
    templates.env.globals["t"] = t
    templates.env.globals["lang"] = _LangValue()
    templates.env.globals["dir"] = _DirValue()
    templates.env.filters["tojson"] = lambda value: Markup(json.dumps(value, ensure_ascii=False))
