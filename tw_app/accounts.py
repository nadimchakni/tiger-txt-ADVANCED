#!/usr/bin/env python3
"""
Multi-account support — Thrill World ke liye.

  accounts.json              list: email, id, enabled + default + active
  store/sessions/<id>.bin    har account ka apna token JSON
  store/auth.bin             ACTIVE account ki copy — core yahi padhta hai

Active account: pehle `active`, phir `default`, phir pehla enabled.
Switch karne par us account ka token store/auth.bin me copy ho jaata hai.
"""
from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

ROOT     = Path(__file__).resolve().parent       # C:\san\app
ACCOUNTS = ROOT / "accounts.json"
SESSIONS = ROOT / "store" / "sessions"
AUTH     = ROOT / "store" / "auth.bin"           # active ki copy

_UNSAFE = re.compile(r"[^a-z0-9._-]+")


def make_id(email: str) -> str:
    t = _UNSAFE.sub("_", str(email).strip().lower())
    return t.strip("_") or "account"


def _load() -> dict:
    if ACCOUNTS.is_file():
        try:
            d = json.loads(ACCOUNTS.read_text(encoding="utf-8"))
            if isinstance(d, dict):
                d.setdefault("default", "")
                d.setdefault("active", "")
                d.setdefault("accounts", [])
                return d
        except Exception:
            pass
    return {"default": "", "active": "", "accounts": []}


def _save(data: dict) -> None:
    ACCOUNTS.write_text(
        json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def load_all() -> list[dict]:
    rows = _load().get("accounts")
    return [r for r in rows if isinstance(r, dict)] if isinstance(rows, list) else []


def get(name: str) -> dict | None:
    want = str(name).strip().lower()
    if not want:
        return None
    for a in load_all():
        if a.get("id", "").lower() == want or a.get("email", "").lower() == want:
            return a
    return None


def active_id() -> str:
    d = _load()
    for cand in (d.get("active"), d.get("default")):
        a = get(cand or "")
        if a and a.get("enabled", True):
            return a["id"]
    live = [a for a in load_all() if a.get("enabled", True)]
    return live[0]["id"] if live else ""


def active_email() -> str:
    a = get(active_id())
    return a.get("email", "") if a else ""


def session_path(acc_id: str) -> Path:
    return SESSIONS / f"{acc_id}.bin"


def has_session(acc_id: str) -> bool:
    return session_path(acc_id).is_file()


def load_token(acc_id: str = "") -> dict:
    """Active ya given account ka token dict return karo. {} agar na mile."""
    aid = acc_id or active_id()
    sp = session_path(aid)
    if sp.is_file():
        try:
            return json.loads(sp.read_text(encoding="utf-8"))
        except Exception:
            pass
    if AUTH.is_file():
        try:
            return json.loads(AUTH.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def add(email: str) -> dict:
    clean = str(email).strip()
    have = get(clean)
    if have:
        return have
    d = _load()
    row = {"id": make_id(clean), "email": clean, "enabled": True}
    d.setdefault("accounts", []).append(row)
    if not d.get("default"):
        d["default"] = clean
    if not d.get("active"):
        d["active"] = clean
    _save(d)
    return row


def activate(acc_id: str) -> bool:
    """Us account ka token store/auth.bin me copy — core yahi use karta hai."""
    sp = session_path(acc_id)
    if not sp.is_file():
        return False
    AUTH.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(sp, AUTH)
    return True


def set_active(name: str) -> dict:
    a = get(name)
    if not a:
        raise ValueError(f"account nahi mila: {name}")
    d = _load()
    d["active"] = a["email"]
    _save(d)
    activate(a["id"])
    return a


def set_default(name: str) -> dict:
    a = get(name)
    if not a:
        raise ValueError(f"account nahi mila: {name}")
    d = _load()
    d["default"] = a["email"]
    _save(d)
    return a


def save_session(email: str, token_data: dict) -> dict:
    """Login ke baad: account register + session file + auth.bin (active set)."""
    acc = add(email)
    SESSIONS.mkdir(parents=True, exist_ok=True)
    session_path(acc["id"]).write_text(
        json.dumps(token_data, indent=2, ensure_ascii=False), encoding="utf-8")
    set_active(email)
    set_default(email)
    return acc
