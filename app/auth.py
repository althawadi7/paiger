import os
import bcrypt
import jwt
from datetime import datetime, timedelta
from typing import Optional, Dict, Any
from fastapi import Request, HTTPException, status
from pathlib import Path
from dotenv import load_dotenv

ENV_FILE = Path(__file__).parent.parent / ".env"
load_dotenv(ENV_FILE)

SECRET_KEY = os.getenv("JWT_SECRET", "super-secret-pager-jwt-key-2026-secure-random")
ALGORITHM = "HS256"
SUPER_ADMIN_COOKIE = "sa_pager_session"
RESTAURANT_COOKIE = "rest_pager_session"

def hash_password(password: str) -> str:
    pwd_bytes = password.encode('utf-8')
    salt = bcrypt.gensalt()
    return bcrypt.hashpw(pwd_bytes, salt).decode('utf-8')

def verify_password(plain_password: str, hashed_password: str) -> bool:
    try:
        return bcrypt.checkpw(plain_password.encode('utf-8'), hashed_password.encode('utf-8'))
    except Exception:
        return False

def create_access_token(data: Dict[str, Any], expires_hours: int = 72) -> str:
    to_encode = data.copy()
    expire = datetime.utcnow() + timedelta(hours=expires_hours)
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt

def decode_token(token: str) -> Optional[Dict[str, Any]]:
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        return payload
    except Exception:
        return None

def get_current_super_admin(request: Request) -> Optional[Dict[str, Any]]:
    token = request.cookies.get(SUPER_ADMIN_COOKIE)
    if not token:
        # Check header
        auth_header = request.headers.get("Authorization")
        if auth_header and auth_header.startswith("Bearer "):
            token = auth_header.split(" ")[1]
    if not token:
        return None
    payload = decode_token(token)
    if not payload or payload.get("role") != "super_admin":
        return None
    return payload

def get_current_restaurant(request: Request) -> Optional[Dict[str, Any]]:
    token = request.cookies.get(RESTAURANT_COOKIE)
    if not token:
        auth_header = request.headers.get("Authorization")
        if auth_header and auth_header.startswith("Bearer "):
            token = auth_header.split(" ")[1]
    if not token:
        return None
    payload = decode_token(token)
    if not payload or payload.get("role") != "restaurant":
        return None
    return payload

