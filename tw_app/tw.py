#!/usr/bin/env python3
"""
tw.py — Thrill World menu (C:\\san\\app). Design C:\\au / KINCM 3.0 jaisa.

  1. Login    -> email + password
  2. Input    -> book link -> EPUB  (loop: ek banne ke baad agle ka wait)
  e. Exit

Input mode ek loop hai: book ka link daalo -> EPUB ban jaayegi -> wahi
ruka rahega, agla link maango. 'b' = wapas menu, 'e' = exit.

Login ke baad password sirf RAM me rehta hai (disk pe nahi) taaki token
expire ho to api apne aap dubara login kar le.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent   # C:\san\app
sys.path.insert(0, str(ROOT))

import accounts
import login
from core import api, epubgen

BOOKS = ROOT / "books"

WIDTH    = 60
NAME_W   = 10
TAIL_PAD = " " * 24
EXIT_ALL = 9
BACK     = 0

# login ke baad in-memory creds (disk pe nahi) — 401 pe auto re-login
_SESSION = {"email": "", "password": ""}

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass


# ---------- helpers ----------

def _clean(choice: str) -> str:
    return (choice or "").strip().lstrip("\ufeff").strip().lower()


def _ask(sawaal: str, *, upar: str = "", chhupa: bool = False) -> str:
    import getpass
    if upar:
        print()
        for line in upar.splitlines():
            print(f"  {line}" if line else "")
    try:
        raw = getpass.getpass(f"  {sawaal}> ") if chhupa else input(f"  {sawaal}> ")
    except (EOFError, KeyboardInterrupt):
        return ""
    return raw.lstrip("\ufeff").strip()


def _session_line() -> str:
    email = _SESSION["email"] or accounts.active_email()
    return f"  Session: {email}" if email else "  Session: login nahi hai"


# ---------- 1. Login ----------

def do_login() -> int:
    email = _ask("email", upar=_session_line())
    email = email or _SESSION["email"] or accounts.active_email()
    if not email:
        print("  email zaroori hai")
        return 1
    password = _ask("password", chhupa=True)
    if not password:
        print("  password zaroori hai")
        return 1

    rc = login.run_login(email=email, password=password)
    if rc == 0:
        _SESSION["email"]    = email
        _SESSION["password"] = password   # RAM only
    return rc


# ---------- 2. Input (EPUB loop) ----------

def _client() -> api.ApiClient | None:
    token = accounts.load_token()
    if not token.get("token"):
        print("  login nahi hai — pehle 1 se login karo")
        return None
    return api.ApiClient(
        token,
        email=_SESSION["email"] or token.get("email", ""),
        password=_SESSION["password"],
    )


def make_epub(client: api.ApiClient, url: str) -> int:
    print(f"\n  book dhundh raha hoon...")
    book = client.find_book(url)
    if not book:
        print("  ye book catalogue/library me nahi mili (ya galat link).")
        return 1
    return _build_from_book(client, book)


def make_library(client: api.ApiClient) -> int:
    """Library ki saari books (max ~5) ek-ek karke EPUB."""
    print(f"\n  library kheench raha hoon...")
    try:
        lib = client.library()
    except api.AuthExpired as exc:
        print(f"  {exc}")
        return 1
    except api.ApiError as exc:
        print(f"  library fail: {exc}")
        return 1

    books = lib.get("books", []) if isinstance(lib, dict) else []
    if not books:
        print("  library khali hai.")
        return 0

    print(f"  {len(books)} book(s) mili  (borrows: {lib.get('borrows_out','?')}/"
          f"{lib.get('borrow_limit','?')})")
    ok = fail = 0
    for i, book in enumerate(books, 1):
        print(f"\n  ===== [{i}/{len(books)}] {book.get('title','?')} =====")
        try:
            rc = _build_from_book(client, book)
        except api.AuthExpired as exc:
            print(f"  {exc}")
            return 1
        except KeyboardInterrupt:
            print("\n  ruk gaye — library loop chhoda")
            break
        except Exception as exc:
            print(f"  error: {exc}")
            rc = 1
        ok += (rc == 0)
        fail += (rc != 0)
    print(f"\n  library ho gayi — {ok} ban gayi, {fail} fail")
    return 0


def _build_from_book(client: api.ApiClient, book: dict, *, manage_borrow: bool = True) -> int:
    """manage_borrow=True → khud borrow-if-needed + return-after. BR mode isse
    False deta hai (wo batch-level pe 5 ek saath borrow/return karta hai)."""
    bid   = book.get("id")
    title = book.get("title", f"book-{bid}")
    count = int(book.get("chapter_count") or 0)
    print(f"  id={bid}  {title}")
    print(f"  author: {book.get('author','')}  chapters: {count or '?'}")

    # v2.26.9: can_open=false matlab abhi borrow nahi — pehle borrow karo
    we_borrowed = False
    if manage_borrow and not book.get("can_open", True):
        print("  book borrow nahi hai — borrow kar raha hoon...")
        try:
            we_borrowed = client.borrow(bid)
        except api.AuthExpired as exc:
            print(f"  {exc}")
            return 1
        except Exception as exc:
            print(f"  borrow fail: {exc}")
        print("  borrowed OK" if we_borrowed else "  borrow nahi hua — phir bhi try karta hoon")

    builder = epubgen.EpubBuilder(book)

    def _step(n, ch):
        t = ch.get("title", f"Section {n+1}")
        chars = len(ch.get("html") or "")
        print(f"    chapter {n:>2}: {t[:30]:<30} ({chars} chars)"
              + ("  [last]" if ch.get("is_last") else ""))

    def _retry(n, attempt, exc):
        print(f"    chapter {n:>2}: retry {attempt} ({exc}) — ruk ke phir...")

    print("  chapters kheench raha hoon...")
    try:
        chapters = client.all_chapters(bid, count, on_step=_step, on_retry=_retry)
    except api.AuthExpired as exc:
        print(f"  {exc}")
        return 1
    except api.ApiError as exc:
        print(f"  chapter fail: {exc}")
        return 1

    for ch in chapters:
        builder.add_chapter(ch.get("title", ""), ch.get("html", ""))

    cover_url = book.get("cover_url") or ""
    if cover_url:
        print(f"  cover download...")
        try:
            builder.add_cover(client.fetch_bytes(cover_url), cover_url)
        except Exception as exc:
            print(f"  cover skip ({exc})")

    out = BOOKS / f"{epubgen.safe_filename(title)}.epub"
    print(f"  EPUB bana raha hoon → {out.name}")
    try:
        info = builder.build(out)
    except Exception as exc:
        print(f"  EPUB build fail: {exc}")
        return 1

    print(f"\n  ho gaya → {out}")
    print(f"    chapters: {info['chapters']}  cover: {'haan' if info['cover'] else 'nahi'}"
          f"  devanagari: {info['devanagari_chars']:,}  size: {info['size_kb']} KB")

    # jo humne borrow kiya tha wahi return karo — slot free (5 ki limit).
    # jo book pehle se library me thi usse haath nahi lagate.
    if manage_borrow and we_borrowed:
        try:
            if client.return_book(bid):
                print("    (borrow slot free kar diya — EPUB tumhare paas hai)")
        except Exception:
            pass
    return 0


def do_input() -> int:
    client = _client()
    if client is None:
        return 1
    BOOKS.mkdir(exist_ok=True)

    print()
    print(_session_line())
    print("\n  book ka link daalo — EPUB ban jaayegi. (b = back, e = exit)")
    while True:
        url = _ask("link")
        low = _clean(url)
        if low in ("e", "q", "exit"):
            return EXIT_ALL
        if low in ("b", "back", ""):
            return BACK
        try:
            make_epub(client, url)
        except KeyboardInterrupt:
            print("\n  ruk gaye — agla link daalo (b = back)")
        except Exception as exc:
            print(f"  error: {exc}")
        # loop jaari — agle link ka wait


def do_lib() -> int:
    client = _client()
    if client is None:
        return 1
    BOOKS.mkdir(exist_ok=True)
    print()
    print(_session_line())
    try:
        make_library(client)
    except KeyboardInterrupt:
        print("\n  ruk gaye — wapas menu")
    except Exception as exc:
        print(f"  error: {exc}")
    return 0


# ---------- 4. BR (borrow 5 -> EPUB -> return, cycle; S = stop) ----------

SAN_JSON   = ROOT.parent / "san.json"   # C:\san\san.json
BATCH      = 5                           # borrow_limit = 5 concurrent


def _stop_pressed() -> bool:
    """'S' dabaya? (Windows non-blocking keyboard check). Buffer saaf karke
    True agar 's'/'S' mila."""
    try:
        import msvcrt
    except Exception:
        return False
    hit = False
    while msvcrt.kbhit():
        ch = msvcrt.getwch()
        if ch and ch.lower() == "s":
            hit = True
    return hit


def _book_list(client: api.ApiClient) -> list[dict]:
    """san.json se book list; na mile to catalogue API se."""
    if SAN_JSON.is_file():
        try:
            import json
            data = json.loads(SAN_JSON.read_text(encoding="utf-8"))
            books = data.get("books", data) if isinstance(data, dict) else data
            if books:
                return books
        except Exception:
            pass
    try:
        return client.catalogue()
    except Exception:
        return []


def _already_done(book: dict) -> bool:
    out = BOOKS / f"{epubgen.safe_filename(book.get('title',''))}.epub"
    return out.is_file()


def _return_all(client: api.ApiClient, ids: list) -> None:
    for bid in ids:
        try:
            client.return_book(bid)
        except Exception:
            pass
    ids.clear()


def do_br() -> int:
    client = _client()
    if client is None:
        return 1
    BOOKS.mkdir(exist_ok=True)

    books = _book_list(client)
    if not books:
        print("  book list nahi mili (san.json / catalogue)")
        return 1
    queue = [b for b in books if not _already_done(b)]
    total_pending = len(queue)

    print()
    print(_session_line())
    print(f"  BR: total {len(books)} books, {total_pending} pending "
          f"(baaki {len(books)-total_pending} already ban chuki)")

    # Monthly borrow cap hoti hai (borrows_30_days / period) — return se concurrent
    # slot free hota hai par mahine ka borrow credit WAPAS NAHI aata. Isliye:
    #   1. jo PEHLE SE borrowed hain unhe pehle download karo (FREE, credit nahi)
    #   2. phir naye borrow karo (har borrow 1 credit) jab tak cap na lage
    try:
        lib = client.library()
        now_borrowed = {b.get("id") for b in lib.get("books", []) if b.get("id")}
        bd = lib.get("borrows_30_days"); plim = lib.get("period_limit")
        if bd is not None:
            print(f"  borrows_30_days={bd}  period={plim}d  "
                  f"(abhi borrowed: {len(now_borrowed)})")
    except api.AuthExpired as exc:
        print(f"  {exc}")
        return 1
    except Exception:
        now_borrowed = set()

    have = [b for b in queue if b.get("id") in now_borrowed]   # free
    need = [b for b in queue if b.get("id") not in now_borrowed]
    print(f"  already-borrowed (free): {len(have)}   naye borrow karne honge: {len(need)}")
    print(f"  cycle: {BATCH} borrow → EPUB → return.  'S' = stop.\n")

    done = fail = 0
    stopped = False
    limit_hit = False

    # ---- STEP 1: already-borrowed books (free — koi credit nahi) ----
    for b in have:
        if _stop_pressed():
            stopped = True; break
        print(f"  ===== already-borrowed: {b['id']} {str(b.get('title',''))[:30]} =====")
        try:
            rc = _build_from_book(client, b, manage_borrow=False)
            done += (rc == 0); fail += (rc != 0)
            if rc == 0:
                try:
                    client.return_book(b["id"])   # download ho gaya → slot free
                    print("    returned → slot free")
                except Exception:
                    pass
                if b in queue:
                    queue.remove(b)
        except api.AuthExpired as exc:
            print(f"  {exc}"); return 1
        except Exception as exc:
            print(f"  error {b.get('id')}: {exc}"); fail += 1

    # ---- STEP 2: naye borrow (har borrow 1 credit) → EPUB → return, batch of 5 ----
    batch_no = 0
    while need and not stopped and not limit_hit:
        batch = need[:BATCH]
        batch_no += 1
        borrowed: list = []
        print(f"  ===== batch {batch_no}: {len(batch)} book(s) =====")
        try:
            # borrow — fail hua to MONTHLY cap lag gaya, aage borrow band
            for b in batch:
                if _stop_pressed():
                    stopped = True; break
                try:
                    if client.borrow(b["id"]):
                        borrowed.append(b["id"])
                        print(f"  borrowed: {b['id']} {str(b.get('title',''))[:34]}")
                    else:
                        print(f"  borrow nahi hua: {b['id']} {str(b.get('title',''))[:30]}"
                              f"  → borrow limit lag gaya")
                        limit_hit = True; break
                except api.AuthExpired as exc:
                    print(f"  {exc}"); _return_all(client, borrowed); return 1
                except Exception as exc:
                    print(f"  borrow fail {b['id']}: {exc}")
                    limit_hit = True; break

            # download SIRF jo borrow hui (un-borrowed pe 403 nahi maarte)
            processed: list = []
            for b in batch:
                if b["id"] not in borrowed:
                    continue
                if _stop_pressed():
                    stopped = True; break
                try:
                    rc = _build_from_book(client, b, manage_borrow=False)
                    done += (rc == 0); fail += (rc != 0)
                except api.AuthExpired as exc:
                    print(f"  {exc}"); return 1
                except api.AccessDenied as exc:
                    print(f"  access nahi {b.get('id')}: {exc}"); fail += 1
                except Exception as exc:
                    print(f"  error {b.get('id')}: {exc}"); fail += 1
                processed.append(b)
        finally:
            back = len(borrowed)
            _return_all(client, borrowed)
            if back:
                print(f"  returned: {back}  → slot free")

        for b in processed:
            if b in need:
                need.remove(b)
            if b in queue:
                queue.remove(b)

        if _stop_pressed():
            stopped = True

    remaining = len(queue)
    if limit_hit:
        print(f"\n  BORROW LIMIT khatam (is period ka, ~{BATCH}-kuch per 30 days) — "
              f"ab naya borrow nahi ho raha.")
        print(f"  {done} books ban gayi. baaki {remaining} agle period mein.")
    elif stopped:
        print(f"\n  S — ruk gaya.  ({done} ban gayi, {fail} fail, {remaining} baaki)")
    else:
        print(f"\n  BR poora — {done} ban gayi, {fail} fail")
    return 0


# ---------- menu render (3.0 style) ----------

ITEMS = (
    ("1", "Login", "-> email + password",        do_login),
    ("2", "Input", "-> book link -> EPUB (loop)", do_input),
    ("3", "Lib",   "-> library ki saari books",   do_lib),
    ("4", "BR",    "-> borrow5->EPUB->return (S=stop)", do_br),
)


def render() -> str:
    rows = ["", "=" * WIDTH, "  THRILL WORLD", "=" * WIDTH]
    for key, title, hint, _ in ITEMS:
        rows.append(f"  {key}. {title:<{NAME_W}} {hint}".rstrip())
    rows.append("")
    rows.append(f"{TAIL_PAD}e. Exit")
    return "\n".join(rows)


def dispatch(choice: str) -> int:
    key = _clean(choice)
    if key in ("e", "q", "exit"):
        return EXIT_ALL
    fn = next((f for k, _, _, f in ITEMS if k == key), None)
    if fn is None:
        print("  " + " / ".join([*(k for k, _, _, _ in ITEMS), "e"]))
        return 1
    return fn()


def menu() -> int:
    while True:
        print(render())
        try:
            choice = _clean(input("  > "))
        except (EOFError, KeyboardInterrupt):
            print("\n  Bye.")
            return 0
        if not choice:
            continue
        try:
            got = dispatch(choice)
        except KeyboardInterrupt:
            print("\n  beech me ruk gaye — wapas menu")
            got = 1
        if got == EXIT_ALL:
            print("  Bye.")
            return 0


if __name__ == "__main__":
    sys.exit(menu())
