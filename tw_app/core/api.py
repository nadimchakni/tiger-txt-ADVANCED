#!/usr/bin/env python3
"""
api.py — Thrill World (sahaj-ereader) REST client.

Burp capture se mile endpoints:
  GET  /catalogue                     saari books
  GET  /library                       user ki borrowed books
  GET  /book/{id}/manifest            book metadata
  GET  /book/{id}/chapter/{n}         ek chapter (html)
  POST /book/{id}/progress            reading progress

Token short-lived hai — 401 pe ApiClient apne aap dubara login karke
(in-memory email+password se) retry karta hai. Password kabhi disk pe
nahi likhta.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path
from urllib.parse import urlparse

import requests

ROOT = Path(__file__).resolve().parent.parent   # C:\san\app
sys.path.insert(0, str(ROOT))

import login  # noqa: E402

BASE_URL = "https://santoshpathak.in/wp-json/sahaj-ereader/v1"


class ApiError(Exception):
    pass


class AuthExpired(ApiError):
    """401 aaya aur dubara login bhi nahi ho paya."""


class AccessDenied(ApiError):
    """403 — book borrow/access nahi (retry bekaar)."""


def book_slug(url: str) -> str:
    """
    https://santoshpathak.in/book/bhramjaal-the-web-of-deception/?v=..
      -> 'bhramjaal-the-web-of-deception'
    """
    path = urlparse(url.strip()).path
    parts = [p for p in path.split("/") if p]
    if "book" in parts:
        i = parts.index("book")
        if i + 1 < len(parts):
            return parts[i + 1]
    return parts[-1] if parts else ""


class ApiClient:
    def __init__(self, token_data: dict, *, email: str = "", password: str = ""):
        self.token_data = dict(token_data or {})
        self.token      = self.token_data.get("token", "")
        self.email      = email or self.token_data.get("email", "")
        self.password   = password          # in-memory only, disk pe nahi
        self.device_id  = self.token_data.get("device_id", "")
        self.http       = login.make_session()

    # ---- low level ----

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self.token}"}

    def _relogin(self) -> bool:
        """401 pe in-memory creds se dubara login. True = naya token mila."""
        if not (self.email and self.password):
            return False
        try:
            td, self.http = login.authenticate(
                self.email, self.password,
                device_id=self.device_id, http=login.make_session(),
            )
        except Exception:
            return False
        self.token_data = td
        self.token      = td["token"]
        self.device_id  = td.get("device_id", self.device_id)
        try:
            import accounts
            accounts.save_session(self.email, td)
        except Exception:
            pass
        return True

    def _get(self, path: str, *, params: dict | None = None, _retry: bool = True) -> requests.Response:
        url = path if path.startswith("http") else f"{BASE_URL}{path}"
        r = self.http.get(url, headers=self._headers(), params=params, timeout=40)
        if r.status_code == 401 and _retry and self._relogin():
            return self._get(path, params=params, _retry=False)
        if r.status_code == 401:
            raise AuthExpired("token expire ho gaya — phir se login karo (option 1)")
        return r

    def _post(self, path: str, *, _retry: bool = True) -> requests.Response:
        """POST with empty body (borrow/return) — 401 pe re-login + retry."""
        url = path if path.startswith("http") else f"{BASE_URL}{path}"
        headers = {**self._headers(), "Content-Type": "application/x-www-form-urlencoded"}
        r = self.http.post(url, headers=headers, data="", timeout=40)
        if r.status_code == 401 and _retry and self._relogin():
            return self._post(path, _retry=False)
        if r.status_code == 401:
            raise AuthExpired("token expire ho gaya — phir se login karo (option 1)")
        return r

    # ---- endpoints ----

    def catalogue(self) -> list[dict]:
        r = self._get("/catalogue")
        if r.status_code != 200:
            raise ApiError(f"catalogue fail ({r.status_code})")
        data = r.json()
        return data.get("books", data) if isinstance(data, dict) else data

    def library(self) -> dict:
        r = self._get("/library")
        if r.status_code != 200:
            raise ApiError(f"library fail ({r.status_code})")
        return r.json()

    def find_book(self, url_or_slug: str) -> dict | None:
        """URL/slug se catalogue (phir library) me book dhundo."""
        slug = book_slug(url_or_slug) or url_or_slug.strip().lower()
        slug = slug.lower()

        def _match(books):
            for b in books:
                share = (b.get("share_url") or "")
                if book_slug(share).lower() == slug:
                    return b
            return None

        hit = _match(self.catalogue())
        if hit:
            return hit
        try:
            return _match(self.library().get("books", []))
        except Exception:
            return None

    def manifest(self, book_id: int) -> dict:
        """Poora manifest: {book, chapters:[{index,title,word_count}], ...}.
        chapters list me VALID spine indices hote hain (gaps skip — jaise
        index 1 kisi book me hota hi nahi). Isi se chapters maangna sahi hai."""
        r = self._get(f"/book/{book_id}/manifest")
        if r.status_code != 200:
            raise ApiError(f"manifest fail ({r.status_code})")
        data = r.json()
        return data if isinstance(data, dict) else {"book": data, "chapters": []}

    def chapter_indices(self, book_id: int) -> list[int]:
        """Manifest se valid chapter indices (gaps ke bina)."""
        man = self.manifest(book_id)
        chaps = man.get("chapters") or []
        idx = [c.get("index") for c in chaps if isinstance(c, dict) and c.get("index") is not None]
        return idx

    # ---- borrow / return (v2.26.9+) ----

    def borrow(self, book_id: int) -> bool:
        """POST /borrow — book ko library me lo (can_open=false books ke liye)."""
        r = self._post(f"/book/{book_id}/borrow")
        try:
            return bool(r.json().get("borrowed"))
        except Exception:
            return r.status_code == 200

    def return_book(self, book_id: int) -> bool:
        """POST /return — borrow slot free karo (5 ki limit hai)."""
        r = self._post(f"/book/{book_id}/return")
        try:
            return bool(r.json().get("returned"))
        except Exception:
            return r.status_code == 200

    # ---- chapters ----

    def chapter(self, book_id: int, n: int, *, tries: int = 8, on_retry=None) -> dict:
        """Ek chapter (VALID index). Transient 404/5xx — plain retry se aa jaata
        hai (login ki zaroorat NAHI). 403 = book borrow/access nahi → turant fail
        (retry bekaar). 401 ho to _get khud token refresh."""
        last = None
        for attempt in range(1, tries + 1):
            try:
                r = self._get(f"/book/{book_id}/chapter/{n}")
                if r.status_code == 200:
                    return r.json()
                if r.status_code == 403:
                    raise AccessDenied(f"chapter {n} 403 — book borrow/access nahi")
                last = ApiError(f"chapter {n} status {r.status_code}")
            except (AuthExpired, AccessDenied):
                raise
            except Exception as exc:
                last = exc
            if attempt < tries:
                if on_retry:
                    on_retry(n, attempt, last)
                time.sleep(min(0.6 * attempt, 4.0))
        raise last or ApiError(f"chapter {n} fail")

    def all_chapters(self, book_id: int, count: int = 0, *, on_step=None,
                     on_retry=None) -> list[dict]:
        """Manifest se valid chapter indices lo, sirf wahi maango (gaps nahi).
        Isi tarah app karta hai — blind 0..N loop ka 404 problem khatam.
        Manifest me chapters na ho to is_last loop par fallback."""
        try:
            indices = self.chapter_indices(book_id)
        except Exception:
            indices = []

        out: list[dict] = []
        if indices:
            for n in indices:
                ch = self.chapter(book_id, n, on_retry=on_retry)
                out.append(ch)
                if on_step:
                    on_step(n, ch)
                time.sleep(0.15)
            return out

        # fallback: purana is_last loop (manifest chapters na mile to)
        n = 0
        ceiling = (count + 3) if count else 500
        while n < ceiling:
            try:
                ch = self.chapter(book_id, n, on_retry=on_retry)
            except AuthExpired:
                raise
            except ApiError:
                if out and count and n >= count:
                    break
                raise
            out.append(ch)
            if on_step:
                on_step(n, ch)
            if ch.get("is_last"):
                break
            n += 1
            time.sleep(0.15)
        return out

    def fetch_bytes(self, url: str) -> bytes:
        """Cover/image public URL — no auth."""
        r = self.http.get(url, timeout=40)
        if r.status_code != 200:
            raise ApiError(f"image fail ({r.status_code}) {url}")
        return r.content
