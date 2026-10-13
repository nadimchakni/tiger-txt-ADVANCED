"""
thrill.py — Thrill World EPUB Downloader
Converts the CLI tw.py menu into Telegram inline-keyboard button tools.

Menu (shown via /thrill):
  🔑 Login   📚 Input
  📖 Library 🔄 BR Mode
  ❌ Close
"""
from __future__ import annotations

import asyncio
import contextlib
import io
import sys
from functools import partial
from pathlib import Path

from pyrogram import Client, filters
from pyrogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from config import ADMINS

# ── locate tw_app and add to sys.path so tw imports work ─────────────────────
TW_ROOT = Path(__file__).resolve().parent / "tw_app"
if str(TW_ROOT) not in sys.path:
    sys.path.insert(0, str(TW_ROOT))

import accounts           # tw_app/accounts.py  noqa: E402
import login as tw_login  # tw_app/login.py      noqa: E402
from core import api      # tw_app/core/api.py   noqa: E402
import tw                 # tw_app/tw.py         noqa: E402

# ── in-memory session — mirrors tw._SESSION so do_br also gets creds ─────────
_SESSION: dict[str, str] = {"email": "", "password": ""}

BOOKS: Path = tw.BOOKS
BOOKS.mkdir(parents=True, exist_ok=True)


# ── inline keyboards ──────────────────────────────────────────────────────────
def main_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🔑 Login",    callback_data="tw_login"),
            InlineKeyboardButton("📚 Input",    callback_data="tw_input"),
        ],
        [
            InlineKeyboardButton("📖 Library",  callback_data="tw_lib"),
            InlineKeyboardButton("🔄 BR Mode",  callback_data="tw_br"),
        ],
        [InlineKeyboardButton("❌ Close",       callback_data="tw_exit")],
    ])


MENU_TEXT = (
    "**🌍 Thrill World EPUB Downloader**\n\n"
    "• 🔑 **Login** — authenticate with email + password\n"
    "• 📚 **Input** — paste a book URL → get an EPUB\n"
    "• 📖 **Library** — download all borrowed books as EPUBs\n"
    "• 🔄 **BR Mode** — batch borrow → EPUB → return (auto-cycle)\n\n"
    "👤 Session: `{session}`"
)


# ── helpers ───────────────────────────────────────────────────────────────────
def _capture(fn, *args, **kwargs) -> tuple[int, str]:
    """Run fn(*args, **kwargs) synchronously; capture stdout+stderr.
    Returns (rc: int, output: str)."""
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
        try:
            rc = fn(*args, **kwargs)
        except SystemExit as exc:
            rc = int(exc.code or 0)
        except Exception as exc:
            buf.write(f"\n[Error] {exc}\n")
            rc = 1
    return (rc if isinstance(rc, int) else 0), buf.getvalue()


async def run_sync(fn, *args, **kwargs) -> tuple[int, str]:
    """Run blocking fn in a thread-pool executor."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, partial(_capture, fn, *args, **kwargs))


def _make_client() -> "api.ApiClient | None":
    token = accounts.load_token()
    if not token.get("token"):
        return None
    return api.ApiClient(
        token,
        email=_SESSION["email"] or token.get("email", ""),
        password=_SESSION["password"],
    )


async def _send_epubs(bot_client: Client, chat_id: int, status_msg: Message) -> int:
    """Send every EPUB in BOOKS to chat_id, then delete them. Returns count sent."""
    epubs = sorted(BOOKS.glob("*.epub"))
    if not epubs:
        return 0
    await status_msg.edit(f"📤 Sending {len(epubs)} EPUB file(s)…")
    sent = 0
    for epub in epubs:
        try:
            await bot_client.send_document(
                chat_id=chat_id,
                document=str(epub),
                caption=f"📖 `{epub.name}`",
            )
            epub.unlink(missing_ok=True)
            sent += 1
        except Exception as exc:
            await bot_client.send_message(
                chat_id, f"⚠️ Could not send `{epub.name}`: {exc}"
            )
    return sent


def _trim(text: str, limit: int = 900) -> str:
    """Trim long text to fit Telegram message limits."""
    if len(text) <= limit:
        return text
    return "…(trimmed)…\n" + text[-limit:]


def _session_label() -> str:
    return _SESSION.get("email") or accounts.active_email() or "not logged in"


# ── register all handlers onto a Pyrogram Client ─────────────────────────────
def register(app: Client) -> None:
    """Attach all Thrill World handlers to the given Pyrogram Client."""

    # /thrill — show main menu
    @app.on_message(filters.command(["thrill"]) & filters.user(ADMINS))
    async def cmd_thrill(_, m: Message):
        await m.reply_text(
            MENU_TEXT.format(session=_session_label()),
            reply_markup=main_kb(),
        )

    # ── ❌ Close ──────────────────────────────────────────────────────────────
    @app.on_callback_query(filters.regex("^tw_exit$") & filters.user(ADMINS))
    async def cb_exit(_, cq: CallbackQuery):
        await cq.message.edit("👋 Thrill World closed.")

    # ── 🔑 Login ──────────────────────────────────────────────────────────────
    @app.on_callback_query(filters.regex("^tw_login$") & filters.user(ADMINS))
    async def cb_login(bot_c: Client, cq: CallbackQuery):
        await cq.answer()
        chat_id = cq.message.chat.id

        prompt = await cq.message.reply("📧 Send your **email** (or `cancel`):")
        try:
            em_msg: Message = await bot_c.listen(chat_id, timeout=120)
            email = (em_msg.text or "").strip()
            await em_msg.delete(True)

            if not email or email.lower() in ("cancel", "e", "exit"):
                await prompt.edit("↩️ Login cancelled.", reply_markup=main_kb())
                return

            await prompt.edit("🔒 Send your **password**:")
            pw_msg: Message = await bot_c.listen(chat_id, timeout=120)
            password = (pw_msg.text or "").strip()
            await pw_msg.delete(True)

            if not password:
                await prompt.edit("❌ Password is required.", reply_markup=main_kb())
                return

            await prompt.edit("⏳ Logging in, please wait…")
            rc, out = await run_sync(tw_login.run_login, email=email, password=password)

            if rc == 0:
                # sync into both session stores
                _SESSION["email"] = email
                _SESSION["password"] = password
                tw._SESSION["email"] = email
                tw._SESSION["password"] = password

                await prompt.edit(
                    f"✅ **Login successful!**\n👤 `{email}`\n\n```\n{_trim(out, 400)}\n```",
                    reply_markup=main_kb(),
                )
            else:
                await prompt.edit(
                    f"❌ **Login failed.**\n\n```\n{_trim(out, 500)}\n```",
                    reply_markup=main_kb(),
                )

        except asyncio.TimeoutError:
            await prompt.edit("⏰ Timed out. Use /thrill to try again.")

    # ── 📚 Input (single book URL → EPUB) ────────────────────────────────────
    @app.on_callback_query(filters.regex("^tw_input$") & filters.user(ADMINS))
    async def cb_input(bot_c: Client, cq: CallbackQuery):
        await cq.answer()
        chat_id = cq.message.chat.id

        client = _make_client()
        if client is None:
            await cq.message.reply(
                "⚠️ Not logged in — tap 🔑 Login first.",
                reply_markup=main_kb(),
            )
            return

        prompt = await cq.message.reply("🔗 Send the **book URL** (or `cancel`):")
        try:
            url_msg: Message = await bot_c.listen(chat_id, timeout=180)
            url = (url_msg.text or "").strip()
            await url_msg.delete(True)

            if not url or url.lower() in ("cancel", "e", "exit"):
                await prompt.edit("↩️ Cancelled.", reply_markup=main_kb())
                return

            BOOKS.mkdir(parents=True, exist_ok=True)
            status = await prompt.edit(f"⏳ Looking up book…\n`{url}`")

            rc, out = await run_sync(tw.make_epub, client, url)
            preview = _trim(out)

            if rc == 0:
                sent = await _send_epubs(bot_c, chat_id, status)
                await status.edit(
                    f"✅ Done! Sent **{sent}** EPUB(s).\n\n```\n{preview}\n```",
                    reply_markup=main_kb(),
                )
            else:
                await status.edit(
                    f"❌ Failed.\n\n```\n{preview}\n```",
                    reply_markup=main_kb(),
                )

        except asyncio.TimeoutError:
            await prompt.edit("⏰ Timed out. Use /thrill to try again.")

    # ── 📖 Library ───────────────────────────────────────────────────────────
    @app.on_callback_query(filters.regex("^tw_lib$") & filters.user(ADMINS))
    async def cb_lib(bot_c: Client, cq: CallbackQuery):
        await cq.answer()
        chat_id = cq.message.chat.id

        client = _make_client()
        if client is None:
            await cq.message.reply(
                "⚠️ Not logged in — tap 🔑 Login first.",
                reply_markup=main_kb(),
            )
            return

        BOOKS.mkdir(parents=True, exist_ok=True)
        status = await cq.message.reply("⏳ Fetching your library…")

        rc, out = await run_sync(tw.make_library, client)
        sent = await _send_epubs(bot_c, chat_id, status)
        icon = "✅" if rc == 0 else "⚠️"
        await status.edit(
            f"{icon} Library done — sent **{sent}** EPUB(s).\n\n```\n{_trim(out)}\n```",
            reply_markup=main_kb(),
        )

    # ── 🔄 BR Mode (batch borrow → EPUB → return) ────────────────────────────
    @app.on_callback_query(filters.regex("^tw_br$") & filters.user(ADMINS))
    async def cb_br(bot_c: Client, cq: CallbackQuery):
        await cq.answer()
        chat_id = cq.message.chat.id

        # BR uses tw._SESSION internally; confirm we have creds or a saved token
        if not (tw._SESSION.get("password") or accounts.load_token().get("token")):
            await cq.message.reply(
                "⚠️ Not logged in — tap 🔑 Login first.",
                reply_markup=main_kb(),
            )
            return

        BOOKS.mkdir(parents=True, exist_ok=True)
        status = await cq.message.reply(
            "⏳ **BR Mode** starting (batch borrow → EPUB → return)…\n"
            "This may take several minutes — EPUBs will arrive as they finish."
        )

        rc, out = await run_sync(tw.do_br)
        sent = await _send_epubs(bot_c, chat_id, status)
        icon = "✅" if rc == 0 else "⚠️"
        await status.edit(
            f"{icon} BR done — sent **{sent}** EPUB(s).\n\n```\n{_trim(out, 1000)}\n```",
            reply_markup=main_kb(),
        )
