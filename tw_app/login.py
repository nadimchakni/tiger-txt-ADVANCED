#!/usr/bin/env python3
"""
login.py — Thrill World (santoshpathak.in) login → store/auth.bin

Sahaj-ereader WordPress plugin REST API. Simple Bearer-token auth —
koi browser/SSO nahi.

Flow:
  POST /wp-json/sahaj-ereader/v1/auth
    body: username, password, device_id, device_name, platform=android
  → token, refresh_token, expires_at, user_id, display_name, subscribed

Token short-lived (~8 min) hota hai — isliye EPUB banane se thodi der
pehle login karo. api.py 401 pe apne aap dubara login kar leta hai.

Usage:
  python login.py
  python login.py --email x@y.com --password secret
"""
from __future__ import annotations

import argparse
import getpass
import json
import secrets
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

ROOT     = Path(__file__).resolve().parent       # C:\san\app
AUTH_BIN = ROOT / "store" / "auth.bin"

BASE_URL   = "https://santoshpathak.in/wp-json/sahaj-ereader/v1"
AUTH_URL   = f"{BASE_URL}/auth"
VERIFY_URL = f"{BASE_URL}/library"

UA = "okhttp/4.12.0"

_DEVICE_NAME = "samsung SM-G973F"
_PLATFORM    = "android"
RETRIES = (0.0, 2.0, 5.0)

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass


def _out(msg: str = "") -> None:
    print(msg, flush=True)


class AuthError(Exception):
    """Login galat / server ne mana kiya."""


# ---------- device ----------

DEVICE_ID_FILE = ROOT / "store" / "device.id"


def random_device_id() -> str:
    return "android:" + secrets.token_hex(8)


def get_device_id() -> str:
    """Stable device_id — ek baar bana ke reuse (account pe naye device na banein)."""
    try:
        if DEVICE_ID_FILE.is_file():
            val = DEVICE_ID_FILE.read_text(encoding="utf-8").strip()
            if val:
                return val
    except Exception:
        pass
    val = random_device_id()
    try:
        DEVICE_ID_FILE.parent.mkdir(parents=True, exist_ok=True)
        DEVICE_ID_FILE.write_text(val, encoding="utf-8")
    except Exception:
        pass
    return val


# ---------- HTTP ----------

def make_session() -> requests.Session:
    s = requests.Session()
    # 'br' (brotli) jaan-boojh ke nahi — requests br auto-decode nahi karta
    # (brotli module ke bina garbled body aata hai). gzip/deflate native hai.
    s.headers.update({
        "User-Agent":      UA,
        "Accept-Encoding": "gzip, deflate",
        "Connection":      "keep-alive",
    })
    return s


def _post_auth(http, email, password, device_id) -> tuple[int, dict]:
    data = {
        "username":    email,
        "password":    password,
        "device_id":   device_id,
        "device_name": _DEVICE_NAME,
        "platform":    _PLATFORM,
    }
    resp = http.post(
        AUTH_URL, data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=30,
    )
    try:
        body = resp.json()
    except Exception:
        body = {"_raw": resp.text[:200]}
    return resp.status_code, body


def authenticate(
    email: str,
    password: str,
    *,
    device_id: str = "",
    http: requests.Session | None = None,
    retries: tuple = RETRIES,
) -> tuple[dict, requests.Session]:
    """Pure login. Token_data dict + session return karta hai. AuthError raise."""
    device_id = device_id or get_device_id()
    http = http or make_session()

    last = None
    for attempt, delay in enumerate(retries, 1):
        if delay:
            time.sleep(delay)
        code, body = _post_auth(http, email, password, device_id)
        last = (code, body)
        if code == 200 and isinstance(body, dict) and "token" in body:
            td = {
                "token":         body["token"],
                "refresh_token": body.get("refresh_token", ""),
                "expires_at":    body.get("expires_at", ""),
                "user_id":       body.get("user_id"),
                "email":         email,
                "display_name":  body.get("display_name", ""),
                "subscribed":    body.get("subscribed", False),
                "device_id":     device_id,
                "saved_at":      datetime.now(timezone.utc).isoformat(),
            }
            return td, http
        if code in (400, 401, 403):
            msg = ""
            if isinstance(body, dict):
                msg = body.get("message") or body.get("code") or ""
            low = str(msg).lower()
            if "password" in low or "credential" in low or "incorrect" in low or code == 401:
                raise AuthError("email ya password galat hai")
            raise AuthError(f"login fail ({code}): {msg or body}")
        if code and code < 500:
            raise AuthError(f"auth fail ({code}): {body}")
        # 5xx → retry
    raise AuthError(f"server error, retries khatam: {last}")


def verify(http, token: str) -> bool:
    """Library endpoint se token verify karo."""
    try:
        r = http.get(VERIFY_URL, headers={"Authorization": f"Bearer {token}"}, timeout=20)
        return r.status_code == 200
    except Exception:
        return False


# ---------- save ----------

def save_auth(token_data: dict, email: str) -> None:
    AUTH_BIN.parent.mkdir(parents=True, exist_ok=True)
    if AUTH_BIN.is_file():
        try:
            (AUTH_BIN.parent / "auth.bin.bak").write_bytes(AUTH_BIN.read_bytes())
        except Exception:
            pass

    saved = False
    try:
        import accounts
        acc = accounts.save_session(email, token_data)
        saved = True
        _out(f"\n  account  : {acc['email']}  (id: {acc['id']})")
        _out(f"  session  → {accounts.session_path(acc['id'])}")
    except Exception as exc:
        _out(f"  (accounts module skip: {exc})")
    if not saved:
        AUTH_BIN.write_text(json.dumps(token_data, indent=2, ensure_ascii=False),
                            encoding="utf-8")

    _out(f"  auth.bin → {AUTH_BIN}")
    _out(f"  user_id  : {token_data.get('user_id')}")
    _out(f"  display  : {token_data.get('display_name')}")
    _out(f"  subscribed: {token_data.get('subscribed')}")
    _out(f"  expires  : {token_data.get('expires_at')}")


# ---------- prompts ----------

class LoginCancel(Exception):
    pass


_CANCEL = {"e", "exit", "q", "b", "back", "cancel"}


def _prompt(label, default="", *, secret=False) -> str:
    try:
        raw = getpass.getpass(f"{label} ") if secret else input(f"{label} ")
        return raw.lstrip("\ufeff").strip() or default
    except EOFError:
        return default


def prompt_email() -> str:
    _out("\nEmail / username (e = cancel):")
    while True:
        val = _prompt("Email>")
        if val.lower() in _CANCEL:
            raise LoginCancel
        if val:
            return val
        _out("  Email zaroori hai (ya 'e' se cancel).")


def prompt_password() -> str:
    _out("\nPassword:")
    while True:
        val = _prompt("Password>", secret=True)
        if val:
            return val
        _out("  Password zaroori hai.")


# ---------- menu-driven login ----------

def run_login(email: str = "", password: str = "") -> int:
    """Menu se login. 0 = OK, 1 = fail/cancel."""
    try:
        email    = email    or prompt_email()
        password = password or prompt_password()
    except LoginCancel:
        _out("\n  Login cancel — wapas menu.")
        return 1

    _out("\n=== Thrill World Login ===")
    _out(f"  endpoint : {AUTH_URL}")
    try:
        http = make_session()
        _out("  POST /auth ...")
        td, http = authenticate(email, password, http=http)
    except AuthError as exc:
        _out(f"  FAIL — {exc}")
        return 1
    except Exception as exc:
        _out(f"  network error: {exc}")
        return 1

    _out(f"  OK — token mila (user_id={td.get('user_id')})")
    _out("  verify (GET /library)...")
    ok = verify(http, td["token"])
    _out(f"  verify: {'OK' if ok else 'WARN (token mila par library 200 nahi)'}")

    save_auth(td, email)
    _out("\n  LOGIN OK")
    return 0


def main(argv=None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    ap = argparse.ArgumentParser(description="Thrill World login → store/auth.bin")
    ap.add_argument("--email",    default="")
    ap.add_argument("--password", default="")
    args = ap.parse_args(argv)
    return run_login(email=args.email, password=args.password)


if __name__ == "__main__":
    raise SystemExit(main())
