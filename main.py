"""
╔══════════════════════════════════════════════════╗
║        𝗔𝗩𝗘𝗥𝗢𝗡 𝗗𝗘𝗔𝗟𝗦 BOT v3.0  —  main.py            ║
║  Extended from bot.py with paginated start menu  ║
║  • Dynamic paginated /start service browse       ║
║  • Auto-reflect admin add/remove/toggle          ║
║  • Out-of-stock badges kept visible              ║
║  • All bot.py systems preserved unchanged        ║
╚══════════════════════════════════════════════════╝
"""

import asyncio
import logging
import sqlite3
import random
import math
import string
import os
import re
import html
import time
import qrcode
from io import BytesIO
from datetime import datetime, timedelta, timezone

from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command, StateFilter
from aiogram.types import (
    InlineKeyboardButton, InlineKeyboardMarkup,
    Message, CallbackQuery
)
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))
except ImportError:
    pass

import supplier_clients
import supplier_store
import sales_store
import mongo_sync

# ══════════════════════════════════════════════════
#  CONFIGURATION
# ══════════════════════════════════════════════════
BOT_TOKEN          = os.getenv("BOT_TOKEN", "").strip()   # set in .env
OWNER_ID           = 8733687681            # Only owner can add/remove admins
CHANNEL_USERNAME   = "AVERON_DEALS_OFFICIAL"
UPI_ID             = "12749702@axl"
DB_NAME            = os.getenv("DB_PATH") or os.path.join(os.path.dirname(os.path.abspath(__file__)), "hind_deals_mini.db")

REFERRAL_REWARD        = 5    # ₹ credited to referrer
WELCOME_BONUS          = 2    # ₹ credited to new user on first referral join
REFERRAL_UNLOCK_COUNT  = 10   # referrals needed to unlock wallet spending

# ── Payment Gateway (GlobalNexPay — AUTOPAY panel) ─
GATEWAY_BASE_DEFAULT  = "https://manage.globalnexpay.site"
GATEWAY_API_KEY       = os.getenv("GATEWAY_API_KEY", "").strip()      # .env   # panel API key (user_token)
GATEWAY_SECRET_KEY    = os.getenv("GATEWAY_SECRET_KEY", "").strip()   # .env   # panel secret key
GATEWAY_TIMEOUT       = 25      # seconds per API call
GATEWAY_POLL_SECONDS  = 15      # auto-verify interval
GATEWAY_EXPIRY_MIN    = 10      # pending payment expires after this

# ── QR / Auto-approval ─────────────────────────────
QR_VALID_MIN   = 5     # ek QR itne minute valid rehta hai (regenerate nahi hota)
AUTO_APPROVAL  = False  # ❌ auto approval OFF — har payment admin manually approve karega

# ── Pagination constants ───────────────────────────
START_MENU_PER_PAGE  = 11   # services shown per page on /start browse menu
STOCK_ADMIN_PER_PAGE = 10   # services shown per page in admin stock/service panels

if not CHANNEL_USERNAME.startswith("@"):
    CHANNEL_USERNAME = "@" + CHANNEL_USERNAME

# ══════════════════════════════════════════════════
#  🕒 DATE / TIME  (always Indian Standard Time)
# ══════════════════════════════════════════════════
IST = timezone(timedelta(hours=5, minutes=30))

def now_dt() -> datetime:
    """Current IST time as a naive datetime (safe for all arithmetic below)."""
    return datetime.now(IST).replace(tzinfo=None)

def now_str() -> str:
    return now_dt().strftime("%Y-%m-%d %H:%M:%S")

def ts_dt(ts: float) -> datetime:
    """Epoch seconds -> naive IST datetime."""
    return datetime.fromtimestamp(ts, IST).replace(tzinfo=None)

def fmt_time(ts: float) -> str:
    return ts_dt(ts).strftime("%I:%M %p")

def fmt_datetime(ts: float) -> str:
    return ts_dt(ts).strftime("%d %b %Y, %I:%M %p")


# ══════════════════════════════════════════════════
#  ✨ STYLISH FONT + BOLD QUOTE FORMATTING
# ══════════════════════════════════════════════════
_BOLD_UPPER = "𝗔𝗕𝗖𝗗𝗘𝗙𝗚𝗛𝗜𝗝𝗞𝗟𝗠𝗡𝗢𝗣𝗤𝗥𝗦𝗧𝗨𝗩𝗪𝗫𝗬𝗭"
_BOLD_LOWER = "𝗮𝗯𝗰𝗱𝗲𝗳𝗴𝗵𝗶𝗷𝗸𝗹𝗺𝗻𝗼𝗽𝗾𝗿𝘀𝘁𝘂𝘃𝘄𝘅𝘆𝘇"
_BOLD_DIGIT = "𝟬𝟭𝟮𝟯𝟰𝟱𝟲𝟳𝟴𝟵"

def sty(text: str) -> str:
    """Convert plain text to stylish bold sans-serif unicode."""
    out = []
    for ch in str(text):
        if "A" <= ch <= "Z":
            out.append(_BOLD_UPPER[ord(ch) - 65])
        elif "a" <= ch <= "z":
            out.append(_BOLD_LOWER[ord(ch) - 97])
        elif "0" <= ch <= "9":
            out.append(_BOLD_DIGIT[ord(ch) - 48])
        else:
            out.append(ch)
    return "".join(out)

_HTML_TAG = re.compile(r"(<[^>]+>)")

def sty_html(text: str) -> str:
    """Stylish font for visible text only — HTML tags stay untouched."""
    return "".join(p if p.startswith("<") else sty(p) for p in _HTML_TAG.split(str(text)))

def bq(text: str, expandable: bool = False) -> str:
    """Wrap text in a Telegram bold quote block."""
    tag = "<blockquote expandable>" if expandable else "<blockquote>"
    return f"{tag}<b>{text}</b></blockquote>"

def styled_quote(title: str, body: str = "", expandable: bool = False) -> str:
    """Stylish heading + bold quote body — used for all money/refund messages."""
    head = sty(title)
    block = bq(body.strip(), expandable) if body.strip() else ""
    return f"{head}\n{block}" if block else head


# ══════════════════════════════════════════════════
#  PREMIUM EMOJI SETUP
# ══════════════════════════════════════════════════
P_EMOJI = {
    "✅": '<tg-emoji emoji-id="6269141158111219856">✅</tg-emoji>',
    "👑": '<tg-emoji emoji-id="6154611916678369390">👑</tg-emoji>',
    "❤️": '<tg-emoji emoji-id="5868508172108435919">❤️</tg-emoji>',
    "🍿": '<tg-emoji emoji-id="6154409593653956339">🍿</tg-emoji>',
    "⭐": '<tg-emoji emoji-id="6154534143410573519">⭐</tg-emoji>',
    "✉️": '<tg-emoji emoji-id="6154247978329576031">✉️</tg-emoji>',
    "💵": '<tg-emoji emoji-id="6154665938777019329">💵</tg-emoji>',
    "💰": '<tg-emoji emoji-id="6327928561629926045">💰</tg-emoji>',
    "🎮": '<tg-emoji emoji-id="5206370578960760792">🎮</tg-emoji>',
    "📞": '<tg-emoji emoji-id="5215532388448216191">📞</tg-emoji>',
    "📺": '<tg-emoji emoji-id="4994708676443899089">📺</tg-emoji>',
    "📷": '<tg-emoji emoji-id="5215685959298853284">📷</tg-emoji>',
    "🔥": '<tg-emoji emoji-id="5049058803220218770">🔥</tg-emoji>',
    "⚡": '<tg-emoji emoji-id="6284971355297290197">⚡</tg-emoji>',
    "✨": '<tg-emoji emoji-id="5350444080084033572">✨</tg-emoji>',
    "⚠️": '<tg-emoji emoji-id="5188463524568926712">⚠️</tg-emoji>',
    "🌟": '<tg-emoji emoji-id="6154220795481559361">🌟</tg-emoji>',
    "📱": '<tg-emoji emoji-id="5206640663684203642">📱</tg-emoji>',
    "💸": '<tg-emoji emoji-id="6154665938777019329">💸</tg-emoji>',
    "✔️": '<tg-emoji emoji-id="6267261061947200606">✔️</tg-emoji>',
    "✈️": '<tg-emoji emoji-id="5350433153687235163">✈️</tg-emoji>',
    "🎁": '<tg-emoji emoji-id="6154314868150243661">🎁</tg-emoji>',
    "🌿": '<tg-emoji emoji-id="5282901450156954572">🌿</tg-emoji>',
    "🦋": '<tg-emoji emoji-id="5281002284338140663">🦋</tg-emoji>',
    "🐝": '<tg-emoji emoji-id="5280763638775305815">🐝</tg-emoji>',
    "🥸": '<tg-emoji emoji-id="5280637761873794347">🥸</tg-emoji>',
    "👹": '<tg-emoji emoji-id="6240047663828439184">👹</tg-emoji>',
    "☠️": '<tg-emoji emoji-id="6307439368604749803">☠️</tg-emoji>',
    "😈": '<tg-emoji emoji-id="6307666795713006953">😈</tg-emoji>',
    "🌹": '<tg-emoji emoji-id="6100380554337721941">🌹</tg-emoji>',
    "💟": '<tg-emoji emoji-id="5215286931067251318">💟</tg-emoji>',
    "💿": '<tg-emoji emoji-id="5215352343419167936">💿</tg-emoji>',
    "⏰": '<tg-emoji emoji-id="5213349767672769194">⏰</tg-emoji>',
    "🎙": '<tg-emoji emoji-id="5206456018745180883">🎙</tg-emoji>',
    "👾": '<tg-emoji emoji-id="5212998216009659081">👾</tg-emoji>',
    "🕐": '<tg-emoji emoji-id="5215484787325676090">🕐</tg-emoji>',
    "🛠": '<tg-emoji emoji-id="5213214428958306222">🛠</tg-emoji>',
    "🍏": '<tg-emoji emoji-id="6267235257783686837">🍏</tg-emoji>',
    "💀": '<tg-emoji emoji-id="6282728866972702273">💀</tg-emoji>',
    "⭕️": '<tg-emoji emoji-id="6282702779341346176">⭕️</tg-emoji>',
    "🌙": '<tg-emoji emoji-id="5350311932530270693">🌙</tg-emoji>',
    "🧭": '<tg-emoji emoji-id="5352858062157783478">🧭</tg-emoji>',
    "🐦": '<tg-emoji emoji-id="5431900484199783449">🐦</tg-emoji>',
    "▶️": '<tg-emoji emoji-id="5429277560557019280">▶️</tg-emoji>',
    "🤍": '<tg-emoji emoji-id="6161210000057309028">🤍</tg-emoji>',
    "♾": '<tg-emoji emoji-id="6161123246012896003">♾</tg-emoji>',
    "🤩": '<tg-emoji emoji-id="6161309428550210746">🤩</tg-emoji>',
    "🫧": '<tg-emoji emoji-id="6163515645875982758">🫧</tg-emoji>',
    "🎀": '<tg-emoji emoji-id="6161414534989877573">🎀</tg-emoji>',
    "🤎": '<tg-emoji emoji-id="6186116665730666759">🤎</tg-emoji>',
    "🍽": '<tg-emoji emoji-id="6152176648746702128">🍽</tg-emoji>',
    "✔": '<tg-emoji emoji-id="6154718341672997377">✔</tg-emoji>',
    "🔖": '<tg-emoji emoji-id="6154668949549092628">🔖</tg-emoji>',
    "🎄": '<tg-emoji emoji-id="6154306875216106278">🎄</tg-emoji>',
    "💥": '<tg-emoji emoji-id="6154203907670151815">💥</tg-emoji>',
    "💧": '<tg-emoji emoji-id="6154388767357538799">💧</tg-emoji>',
    "💕": '<tg-emoji emoji-id="6154409400380429396">💕</tg-emoji>',
    "🩵": '<tg-emoji emoji-id="6154437446516871101">🩵</tg-emoji>',
    "🛜": '<tg-emoji emoji-id="6154664143480689527">🛜</tg-emoji>',
    "🌐": '<tg-emoji emoji-id="5206208353751024833">🌐</tg-emoji>',
    "🅰": '<tg-emoji emoji-id="6154277325841109960">🅰</tg-emoji>',
    "⬛️": '<tg-emoji emoji-id="6154405328751432013">⬛️</tg-emoji>',
    "🍪": '<tg-emoji emoji-id="6154583359440819411">🍪</tg-emoji>',
    "🖼️": '<tg-emoji emoji-id="5350693961281314631">🖼️</tg-emoji>',
    "💳": '<tg-emoji emoji-id="5206507661431947835">💳</tg-emoji>',
    "💲": '<tg-emoji emoji-id="5206391796099204564">💲</tg-emoji>',
    "💬": '<tg-emoji emoji-id="5206384258431599552">💬</tg-emoji>',
    "🎧": '<tg-emoji emoji-id="5206361550939506453">🎧</tg-emoji>',
    "🛒": '<tg-emoji emoji-id="5206690180362158435">🛒</tg-emoji>',
    "🎬": '<tg-emoji emoji-id="5206647346653316175">🎬</tg-emoji>',
    "🚗": '<tg-emoji emoji-id="5206483175823392785">🚗</tg-emoji>',
    "🏀": '<tg-emoji emoji-id="5046452136028669374">🏀</tg-emoji>',
    "📲": '<tg-emoji emoji-id="5899880249620958912">📲</tg-emoji>',
    "🎥": '<tg-emoji emoji-id="5206391615710578229">🎥</tg-emoji>',
    "⭐️": '<tg-emoji emoji-id="5879697188979740680">⭐️</tg-emoji>',
    "🥙": '<tg-emoji emoji-id="5206563105164773389">🥙</tg-emoji>',
    "📦": '<tg-emoji emoji-id="5206209753910365368">📦</tg-emoji>',
    "🌇": '<tg-emoji emoji-id="5206283885045891726">🌇</tg-emoji>',
    "🏚": '<tg-emoji emoji-id="5206520731017431111">🏚</tg-emoji>',
    "🧑‍🎓": '<tg-emoji emoji-id="5203937213699607386">🧑‍🎓</tg-emoji>',
    "✏️": '<tg-emoji emoji-id="5206286797033719693">✏️</tg-emoji>',
    "🗒": '<tg-emoji emoji-id="5204024191082318452">🗒</tg-emoji>',
    "▫️": '<tg-emoji emoji-id="5206675474394141094">▫️</tg-emoji>',
    "©️": '<tg-emoji emoji-id="5467831785461141919">©️</tg-emoji>',
    "😃": '<tg-emoji emoji-id="6330159672586145392">😃</tg-emoji>',
    "😁": '<tg-emoji emoji-id="6330355428605564442">😁</tg-emoji>',
    "🥹": '<tg-emoji emoji-id="6330105732091875959">🥹</tg-emoji>',
    "😅": '<tg-emoji emoji-id="6330003069488598017">😅</tg-emoji>',
    "😂": '<tg-emoji emoji-id="6329889643697278341">😂</tg-emoji>',
    "🤣": '<tg-emoji emoji-id="6327725937957801811">🤣</tg-emoji>',
    "☺️": '<tg-emoji emoji-id="6330161970393649279">☺️</tg-emoji>',
    "😊": '<tg-emoji emoji-id="6329861430057110814">😊</tg-emoji>',
    "🙂": '<tg-emoji emoji-id="6330069705906196240">🙂</tg-emoji>',
    "😉": '<tg-emoji emoji-id="6327648409503142865">😉</tg-emoji>',
    "😍": '<tg-emoji emoji-id="6330317813281984147">😍</tg-emoji>',
}

def pe(text: str) -> str:
    """Polish every bot message, then replace standard emojis with premium ones."""
    if not text:
        return text
    # Keep code/links untouched, but make every explicit heading stylish.
    text = re.sub(
        r"<b>(.*?)</b>",
        lambda match: f"<b>{sty_html(match.group(1))}</b>",
        text,
        flags=re.DOTALL,
    )
    # Give every multi-line screen a clean Telegram quote-card layout. Messages
    # already composed with styled_quote()/bq() are deliberately left alone.
    if "\n" in text and "<blockquote" not in text:
        lines = text.splitlines()
        title = lines[0].strip()
        body_lines = lines[1:]
        while body_lines and (not body_lines[0].strip() or set(body_lines[0].strip()) <= {"━", "─", "═"}):
            body_lines.pop(0)
        body = "\n".join(body_lines).strip()
        if title and body:
            text = f"{title}\n<blockquote><b>{body}</b></blockquote>"
    return premiumize(text)


_EMOJI_KEYS = sorted(P_EMOJI.keys(), key=len, reverse=True)
_TAG_SPLIT = re.compile(r"(<tg-emoji\b[^>]*>.*?</tg-emoji>)", re.DOTALL)

def premiumize(text: str) -> str:
    """Replace plain emojis with premium ones WITHOUT touching existing
    <tg-emoji> tags (double-wrapping made emojis render broken)."""
    parts = _TAG_SPLIT.split(text)
    for idx, part in enumerate(parts):
        if part.startswith("<tg-emoji"):
            continue
        for standard in _EMOJI_KEYS:
            if standard in part:
                part = part.replace(standard, P_EMOJI[standard])
        parts[idx] = part
    return "".join(parts)

# ══════════════════════════════════════════════════
#  LOGGING & CORE
# ══════════════════════════════════════════════════
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
if not BOT_TOKEN:
    raise SystemExit("BOT_TOKEN missing: .env file me BOT_TOKEN=... daalo (see .env.example)")
bot = Bot(token=BOT_TOKEN)
dp  = Dispatcher()

# ══════════════════════════════════════════════════
#  DEFAULT PRODUCTS (used to seed DB on first run)
# ══════════════════════════════════════════════════
DEFAULT_PRODUCTS = {
    "netflix":        {"display_name": "Netflix Premium",          "keywords": ["netflix","nf","netflix premium"],                "description": "🎞️ Unlimited Movies & Series\n• 4K Ultra HD Quality\n• Multiple Screens\n• Netflix Originals Access",                    "prices": {"1 Month": 120, "3 Months": 329, "6 Months": 599, "1 Year": 1099}, "stock": {"1 Month": 100, "3 Months": 50, "6 Months": 30, "1 Year": 20}},
    "youtube":        {"display_name": "YouTube Premium",          "keywords": ["youtube","yt","youtube premium"],                "description": "🎵 No Ads on YouTube\n• Background Play\n• Video Downloads\n• YouTube Music Included",                               "prices": {"1 Month": 19,  "3 Months": 58,  "6 Months": 115, "1 Year": 219},  "stock": {"1 Month": 100, "3 Months": 50, "6 Months": 30, "1 Year": 20}},
    "youtubefamily":  {"display_name": "YouTube Family",           "keywords": ["youtube family","yt family"],                    "description": "👨‍👩‍👧 YouTube Family\n• Premium For Multiple Members\n• Ad-Free Videos\n• Separate Accounts",                            "prices": {"1 Month": 59,  "3 Months": 149, "6 Months": 269, "1 Year": 499},  "stock": {"1 Month": 50,  "3 Months": 30, "6 Months": 20, "1 Year": 10}},
    "spotify":        {"display_name": "Spotify Premium",          "keywords": ["spotify","sp","spotify premium"],                "description": "🎶 Ad-Free Music\n• Offline Downloads\n• Unlimited Skips\n• High Audio Quality",                                       "prices": {"1 Month": 39,  "3 Months": 89,  "6 Months": 149, "1 Year": 249},  "stock": {"1 Month": 100, "3 Months": 50, "6 Months": 30, "1 Year": 20}},
    "jiohotstar":     {"display_name": "JioHotstar Premium",       "keywords": ["jiohotstar","hotstar","jio hotstar"],            "description": "📺 Live IPL & Sports\n• Latest Movies & Shows\n• Ad-Free Streaming\n• 4K Support",                                    "prices": {"1 Month": 39,  "3 Months": 90,  "6 Months": 160, "1 Year": 409},  "stock": {"1 Month": 100, "3 Months": 50, "6 Months": 30, "1 Year": 20}},
    "amazon":         {"display_name": "Amazon Prime Video",       "keywords": ["amazon","prime","amazon prime"],                 "description": "🍿 Prime Movies & Web Series\n• Fast Delivery Benefits\n• Prime Music Included",                                       "prices": {"1 Month": 50,  "3 Months": 99,  "6 Months": 129, "1 Year": 249},  "stock": {"1 Month": 100, "3 Months": 50, "6 Months": 30, "1 Year": 20}},
    "sony":           {"display_name": "Sony LIV Premium",         "keywords": ["sony","sonyliv","sony liv"],                     "description": "🎬 WWE, UEFA & Sports\n• Sony TV Shows Early Access\n• Ad-Free Streaming",                                            "prices": {"1 Month": 49,  "3 Months": 109, "6 Months": 179, "1 Year": 259},  "stock": {"1 Month": 100, "3 Months": 50, "6 Months": 30, "1 Year": 20}},
    "zee5":           {"display_name": "ZEE5 Premium",             "keywords": ["zee5","zee","z5"],                               "description": "📡 Hindi & South Movies\n• TV Shows Before TV\n• Originals & Web Series",                                            "prices": {"1 Month": 39,  "3 Months": 89,  "6 Months": 149, "1 Year": 210},  "stock": {"1 Month": 100, "3 Months": 50, "6 Months": 30, "1 Year": 20}},
    "hoichoi":        {"display_name": "Hoichoi Premium",          "keywords": ["hoichoi","bengali"],                             "description": "❤️ Bengali Movies & Series\n• Exclusive Originals\n• HD Streaming",                                                "prices": {"1 Month": 49,  "3 Months": 99,  "6 Months": 169, "1 Year": 279},  "stock": {"1 Month": 100, "3 Months": 50, "6 Months": 30, "1 Year": 20}},
    "altbalaji":      {"display_name": "ALTBalaji Premium",        "keywords": ["altbalaji","alt balaji","alt"],                  "description": "🎥 Indian Web Series\n• Exclusive Drama Content\n• HD Streaming",                                                    "prices": {"1 Month": 35,  "3 Months": 79,  "6 Months": 129, "1 Year": 219},  "stock": {"1 Month": 100, "3 Months": 50, "6 Months": 30, "1 Year": 20}},
    "discovery":      {"display_name": "Discovery+ Premium",       "keywords": ["discovery","discovery plus"],                    "description": "🌟 Discovery & Animal Planet\n• Science & Reality Shows\n• Ad-Free Viewing",                                          "prices": {"1 Month": 35,  "3 Months": 79,  "6 Months": 129, "1 Year": 219},  "stock": {"1 Month": 100, "3 Months": 50, "6 Months": 30, "1 Year": 20}},
    "lionsgate":      {"display_name": "Lionsgate Play",           "keywords": ["lionsgate","lions gate"],                        "description": "🎭 Hollywood Movies & Series\n• Premium English Content\n• HD Streaming",                                            "prices": {"1 Month": 39,  "3 Months": 89,  "6 Months": 149, "1 Year": 249},  "stock": {"1 Month": 100, "3 Months": 50, "6 Months": 30, "1 Year": 20}},
    "mxplayer":       {"display_name": "MX Player Gold",           "keywords": ["mx","mx player","mxplayer"],                     "description": "📱 Ad-Free Movies\n• Premium MX Originals\n• Live TV Access",                                                       "prices": {"1 Month": 29,  "3 Months": 69,  "6 Months": 119, "1 Year": 199},  "stock": {"1 Month": 100, "3 Months": 50, "6 Months": 30, "1 Year": 20}},
    "chaupal":        {"display_name": "Chaupal Premium",          "keywords": ["chaupal","punjabi"],                             "description": "🎦 Punjabi Movies & Shows\n• Regional Content\n• Exclusive Originals",                                             "prices": {"1 Month": 39,  "3 Months": 89,  "6 Months": 149, "1 Year": 249},  "stock": {"1 Month": 100, "3 Months": 50, "6 Months": 30, "1 Year": 20}},
    "applemusic":     {"display_name": "Apple Music",              "keywords": ["apple music","apple"],                           "description": "🎧 High Quality Audio\n• Millions of Songs\n• Offline Music\n• Dolby Atmos",                                         "prices": {"1 Month": 49,  "3 Months": 109, "6 Months": 189, "1 Year": 299},  "stock": {"1 Month": 100, "3 Months": 50, "6 Months": 30, "1 Year": 20}},
    "gaana":          {"display_name": "Gaana Plus",               "keywords": ["gaana","gaana plus"],                            "description": "📻 Ad-Free Songs\n• HD Music Quality\n• Unlimited Downloads",                                                       "prices": {"1 Month": 25,  "3 Months": 59,  "6 Months": 99,  "1 Year": 179},  "stock": {"1 Month": 100, "3 Months": 50, "6 Months": 30, "1 Year": 20}},
    "wynk":           {"display_name": "Wynk Music Premium",       "keywords": ["wynk","wynk music"],                             "description": "🎼 Caller Tunes\n• Offline Music\n• Ad-Free Streaming",                                                             "prices": {"1 Month": 19,  "3 Months": 49,  "6 Months": 89,  "1 Year": 149},  "stock": {"1 Month": 100, "3 Months": 50, "6 Months": 30, "1 Year": 20}},
    "chatgpt":        {"display_name": "ChatGPT Plus",             "keywords": ["chatgpt","gpt","chat gpt","chatgpt plus"],      "description": "🤖 Faster Responses\n• Advanced AI Access\n• Better Writing & Coding\n• GPT-4 Access",                               "prices": {"1 Month": 149, "3 Months": 399, "6 Months": 699, "1 Year": 1299}, "stock": {"1 Month": 50,  "3 Months": 30, "6 Months": 20, "1 Year": 10}},
    "gemini":         {"display_name": "Gemini Premium + 2TB",     "keywords": ["gemini","google gemini"],                        "description": "🍃 Gemini AI Access\n• 2TB Google Storage\n• AI Writing & Research",                                               "prices": {"1 Month": 39,  "3 Months": 109, "6 Months": 149, "1 Year": 179},  "stock": {"1 Month": 50,  "3 Months": 30, "6 Months": 20, "1 Year": 10}},
    "claudeai":       {"display_name": "Claude AI Pro",            "keywords": ["claude","claude ai","claude pro"],              "description": "🧠 Smart AI Answers\n• Long File Analysis\n• Coding & Writing Help",                                               "prices": {"1 Month": 149, "3 Months": 399, "6 Months": 699, "1 Year": 1199}, "stock": {"1 Month": 50,  "3 Months": 30, "6 Months": 20, "1 Year": 10}},
    "blackbox":       {"display_name": "Blackbox AI Premium",      "keywords": ["blackbox","blackbox ai"],                        "description": "⚡ Coding AI Assistant\n• Error Fixing\n• Code Generation",                                                       "prices": {"1 Month": 69,  "3 Months": 150, "6 Months": 269, "1 Year": 449},  "stock": {"1 Month": 50,  "3 Months": 30, "6 Months": 20, "1 Year": 10}},
    "canva":          {"display_name": "Canva Pro",                "keywords": ["canva","canva pro"],                             "description": "🎨 Premium Templates\n• AI Design Tools\n• Background Remover\n• Unlimited Exports",                                "prices": {"1 Month": 39,  "3 Months": 109, "6 Months": 149, "1 Year": 179},  "stock": {"1 Month": 50,  "3 Months": 30, "6 Months": 20, "1 Year": 10}},
    "picsart":        {"display_name": "PicsArt Premium",          "keywords": ["picsart","picsart premium"],                     "description": "🖌️ Premium Editing Tools\n• AI Photo Effects\n• No Watermark",                                                    "prices": {"1 Month": 60,  "3 Months": 119, "6 Months": 169, "1 Year": 210},  "stock": {"1 Month": 50,  "3 Months": 30, "6 Months": 20, "1 Year": 10}},
    "remini":         {"display_name": "Remini Premium",           "keywords": ["remini","remini premium"],                       "description": "✨ AI Photo Enhance\n• Blur Fix\n• Face Enhancement\n• HD Quality",                                              "prices": {"1 Month": 49,  "3 Months": 119, "6 Months": 199, "1 Year": 349},  "stock": {"1 Month": 50,  "3 Months": 30, "6 Months": 20, "1 Year": 10}},
    "capcut":         {"display_name": "CapCut Pro",               "keywords": ["capcut","capcut pro"],                           "description": "🎬 Premium Video Effects\n• No Watermark\n• AI Editing Tools",                                                  "prices": {"1 Month": 49,  "3 Months": 109, "6 Months": 189, "1 Year": 329},  "stock": {"1 Month": 50,  "3 Months": 30, "6 Months": 20, "1 Year": 10}},
    "kinemaster":     {"display_name": "KineMaster Premium",       "keywords": ["kinemaster","km","kine master"],                 "description": "🎥 Pro Video Editing\n• No Watermark\n• Premium Assets",                                                         "prices": {"1 Month": 39,  "3 Months": 99,  "6 Months": 169, "1 Year": 299},  "stock": {"1 Month": 50,  "3 Months": 30, "6 Months": 20, "1 Year": 10}},
    "lightroom":      {"display_name": "Lightroom Premium",        "keywords": ["lightroom","adobe lightroom"],                   "description": "📷 Professional Photo Editing\n• Premium Filters\n• Cloud Backup",                                               "prices": {"1 Month": 49,  "3 Months": 119, "6 Months": 199, "1 Year": 349},  "stock": {"1 Month": 50,  "3 Months": 30, "6 Months": 20, "1 Year": 10}},
    "adobeexpress":   {"display_name": "Adobe Express Premium",    "keywords": ["adobe express","spark"],                         "description": "🎨 Premium Templates\n• AI Design Features\n• Brand Kit Access",                                                "prices": {"1 Month": 59,  "3 Months": 149, "6 Months": 249, "1 Year": 399},  "stock": {"1 Month": 50,  "3 Months": 30, "6 Months": 20, "1 Year": 10}},
    "grammarly":      {"display_name": "Grammarly Premium",        "keywords": ["grammarly","grammerly"],                         "description": "📝 Grammar Correction\n• AI Writing Help\n• Plagiarism Check",                                                "prices": {"1 Month": 59,  "3 Months": 139, "6 Months": 249, "1 Year": 399},  "stock": {"1 Month": 50,  "3 Months": 30, "6 Months": 20, "1 Year": 10}},
    "duolingo":       {"display_name": "Duolingo Super",           "keywords": ["duolingo","duolingo super"],                     "description": "📚 Ad-Free Learning\n• Unlimited Hearts\n• Offline Lessons",                                                  "prices": {"1 Month": 69,  "3 Months": 149, "6 Months": 249, "1 Year": 399},  "stock": {"1 Month": 50,  "3 Months": 30, "6 Months": 20, "1 Year": 10}},
    "tradingview":    {"display_name": "TradingView Premium",      "keywords": ["tradingview","trading view"],                    "description": "📈 Advanced Trading Charts\n• Multiple Indicators\n• Real-Time Market Data",                                      "prices": {"1 Month": 149, "3 Months": 399, "6 Months": 699, "1 Year": 1299}, "stock": {"1 Month": 50,  "3 Months": 30, "6 Months": 20, "1 Year": 10}},
    "microsoft365":   {"display_name": "Microsoft 365",            "keywords": ["microsoft","office 365","microsoft 365"],       "description": "☁️ Word, Excel & PowerPoint\n• 1TB OneDrive Storage\n• Premium Office Apps",                                    "prices": {"1 Month": 79,  "3 Months": 199, "6 Months": 349, "1 Year": 599},  "stock": {"1 Month": 50,  "3 Months": 30, "6 Months": 20, "1 Year": 10}},
    "googleone":      {"display_name": "Google One Premium",       "keywords": ["google one","google drive premium"],             "description": "💾 Extra Google Storage\n• Backup Photos & Files\n• VPN Benefits",                                             "prices": {"1 Month": 39,  "3 Months": 99,  "6 Months": 169, "1 Year": 299},  "stock": {"1 Month": 50,  "3 Months": 30, "6 Months": 20, "1 Year": 10}},
    "dropbox":        {"display_name": "Dropbox Premium",          "keywords": ["dropbox","dropbox premium"],                     "description": "📂 Large Cloud Storage\n• File Backup\n• Fast File Sharing",                                                "prices": {"1 Month": 69,  "3 Months": 169, "6 Months": 299, "1 Year": 549},  "stock": {"1 Month": 50,  "3 Months": 30, "6 Months": 20, "1 Year": 10}},
    "googleworkspace":{"display_name": "Google Workspace",         "keywords": ["google workspace","gsuite"],                     "description": "📧 Professional Gmail\n• Business Tools\n• Cloud Collaboration",                                              "prices": {"1 Month": 99,  "3 Months": 249, "6 Months": 449, "1 Year": 799},  "stock": {"1 Month": 50,  "3 Months": 30, "6 Months": 20, "1 Year": 10}},
    "telegrampremium":{"display_name": "Telegram Premium",         "keywords": ["telegram premium","tg premium"],                 "description": "📲 Faster Downloads\n• Large Upload Limit\n• Premium Emojis\n• No Ads",                                         "prices": {"1 Month": 39,  "3 Months": 99,  "6 Months": 179, "1 Year": 299},  "stock": {"1 Month": 100, "3 Months": 50, "6 Months": 30, "1 Year": 20}},
    "discord":        {"display_name": "Discord Nitro",            "keywords": ["discord","nitro","discord nitro"],               "description": "💬 HD Streaming\n• Custom Emojis\n• Bigger Upload Size",                                                      "prices": {"1 Month": 79,  "3 Months": 199, "6 Months": 349, "1 Year": 649},  "stock": {"1 Month": 50,  "3 Months": 30, "6 Months": 20, "1 Year": 10}},
    "xbox":           {"display_name": "Xbox Game Pass",           "keywords": ["xbox","game pass","xbox game pass"],             "description": "🎮 Hundreds of Games\n• Online Multiplayer\n• Cloud Gaming",                                               "prices": {"1 Month": 99,  "3 Months": 249, "6 Months": 449, "1 Year": 799},  "stock": {"1 Month": 50,  "3 Months": 30, "6 Months": 20, "1 Year": 10}},
    "playstation":    {"display_name": "PlayStation Plus",         "keywords": ["playstation","ps plus","ps+"],                   "description": "🎮 Online Multiplayer\n• Free Monthly Games\n• Cloud Saves",                                               "prices": {"1 Month": 119, "3 Months": 299, "6 Months": 549, "1 Year": 999},  "stock": {"1 Month": 50,  "3 Months": 30, "6 Months": 20, "1 Year": 10}},
    "steam":          {"display_name": "Steam Wallet Codes",       "keywords": ["steam","steam wallet"],                          "description": "🕹️ Buy Games Easily\n• In-Game Purchases\n• DLC & Premium Access",                                          "prices": {"100 Code": 89, "250 Code": 219, "500 Code": 439, "1000 Code": 879}, "stock": {"100 Code": 50, "250 Code": 30, "500 Code": 20, "1000 Code": 10}},

    # ── NEW SERVICES ───────────────────────────────────────────────────────────────────
    "averontv": {
        "display_name": "𝗔𝗩𝗘𝗥𝗢𝗡 𝗗𝗘𝗔𝗟𝗦 TV",
        "keywords": ["mini app","miniapp","hind tv","averontv","hind deals tv","fifa","movie","movies","live","streaming app","tv app"],
        "description": (
            "📺 <b>𝗔𝗩𝗘𝗥𝗢𝗡 𝗗𝗘𝗔𝗟𝗦 TV — Premium Mini App</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "🎬 Watch Live Sports, Movies & Shows\n"
            "⚽ FIFA Live Matches\n"
            "📱 Works on all devices\n"
            "🔒 Ad-free, buffer-free experience\n"
            "👤 Access linked to your account\n\n"
            "✅ Instant activation after payment"
        ),
        "prices": {"Silver 🥈 1 Month": 59, "Gold 🥇 3 Months": 150, "Platinum 💎 6 Months": 199, "Diamond 💠 1 Year": 299},
        "stock":  {"Silver 🥈 1 Month": 999, "Gold 🥇 3 Months": 999, "Platinum 💎 6 Months": 999, "Diamond 💠 1 Year": 999},
    },
    "pwcourses": {
        "display_name": "Physics Wallah (PW) Courses",
        "keywords": ["pw","physics wallah","pw courses","physics","wallah","pw batch","pw pass"],
        "description": (
            "📚 <b>Physics Wallah — PW Courses</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "🎓 Top-Ranked JEE/NEET Courses\n"
            "📖 Full Batch Access\n"
            "🎥 HD Video Lectures\n"
            "📝 Notes & DPP Included\n"
            "🏆 Study from India's Best Teachers"
        ),
        "prices": {"1 Month": 99, "3 Months": 249, "6 Months": 449, "1 Year": 799},
        "stock":  {"1 Month": 50, "3 Months": 30,  "6 Months": 20,  "1 Year": 10},
    },
    "coupons": {
        "display_name": "Premium Offer Coupons",
        "keywords": ["coupon","coupons","swiggy","zomato","flipkart","amazon","meesho","myntra","pizza hut","offer","discount","deals coupon"],
        "description": (
            "🎟️ <b>Premium Offer Coupons</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "🍔 Swiggy & Zomato Coupons\n"
            "🛍️ Flipkart & Amazon Deals\n"
            "👗 Meesho & Myntra Offers\n"
            "🍕 Pizza Hut Special Codes\n"
            "💸 Save Big on Every Order!"
        ),
        "prices": {"Swiggy/Zomato": 29, "Flipkart/Amazon": 39, "Meesho/Myntra": 29, "All Platforms Bundle": 99},
        "stock":  {"Swiggy/Zomato": 100, "Flipkart/Amazon": 100, "Meesho/Myntra": 100, "All Platforms Bundle": 50},
    },
    "virtualnumber": {
        "display_name": "Virtual Number",
        "keywords": ["virtual number","virtual","vn","temp number","otp number","virtual sim","number","whatsapp","whatsapp account","telegram account","wa account","tg account"],
        "description": (
            "📞 <b>Virtual Number Service</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "🌍 Numbers from Multiple Countries\n"
            "📲 Receive OTP Instantly\n"
            "💬 WhatsApp & Telegram Account Setup\n"
            "✅ For Testing / App Registration\n"
            "🔒 Private & Secure\n"
            "⚡ Instant Delivery"
        ),
        "prices": {"1 Number (India)": 19, "1 Number (US/UK)": 39, "5 Numbers Bundle": 89, "10 Numbers Bundle": 149},
        "stock":  {"1 Number (India)": 200, "1 Number (US/UK)": 100, "5 Numbers Bundle": 50, "10 Numbers Bundle": 30},
    },
}

# In-memory cache loaded from DB at startup and after admin changes
PRODUCTS: dict = {}

# ══════════════════════════════════════════════════
#  STATES
# ══════════════════════════════════════════════════
class OrderState(StatesGroup):
    waiting_for_screenshot = State()
    waiting_for_review     = State()

class AdminState(StatesGroup):
    waiting_for_user_id      = State()
    waiting_for_broadcast    = State()
    waiting_for_stock_update = State()
    waiting_for_ban_reason   = State()

class ServiceAddState(StatesGroup):
    name        = State()
    description = State()
    keywords    = State()
    durations   = State()
    prices      = State()
    stocks      = State()

class ServiceEditState(StatesGroup):
    choose_field = State()
    new_value    = State()

class AdminManageState(StatesGroup):
    waiting_add_id   = State()
    waiting_add_role = State()
    waiting_remove   = State()

class EditSettingState(StatesGroup):
    waiting_value = State()   # admin is typing new value for a setting key

class GatewayState(StatesGroup):
    waiting_amount = State()
    waiting_token  = State()
    waiting_base   = State()
    waiting_min    = State()

class QRState(StatesGroup):
    waiting_amount = State()   # user ne "qr" / "/qr" likha, amount poocha ja raha hai

class DepositState(StatesGroup):
    waiting_for_screenshot = State()

class WalletAdminState(StatesGroup):
    waiting_add    = State()   # admin gives balance
    waiting_deduct = State()   # admin takes balance
    waiting_check  = State()   # admin checks a user's balance

class BroadcastState(StatesGroup):
    waiting_content = State()
    waiting_confirm = State()

# ══════════════════════════════════════════════════
#  DATABASE — INIT & HELPERS
# ══════════════════════════════════════════════════
def db():
    """Return a fresh SQLite connection."""
    return sqlite3.connect(DB_NAME)

def init_db():
    conn = db()
    c = conn.cursor()

    # ── Users ──────────────────────────────────────
    c.execute("""CREATE TABLE IF NOT EXISTS users (
        user_id          INTEGER PRIMARY KEY,
        username         TEXT    DEFAULT '',
        first_name       TEXT    DEFAULT '',
        join_date        TEXT,
        last_active      TEXT,
        is_banned        INTEGER DEFAULT 0,
        ban_reason       TEXT    DEFAULT '',
        total_orders     INTEGER DEFAULT 0,
        total_spent      INTEGER DEFAULT 0,
        wallet_balance   REAL    DEFAULT 0,
        referral_code    TEXT    UNIQUE,
        referred_by      INTEGER DEFAULT NULL,
        referral_count   INTEGER DEFAULT 0,
        wallet_unlocked  INTEGER DEFAULT 1
    )""")

    # ── Orders ─────────────────────────────────────
    c.execute("""CREATE TABLE IF NOT EXISTS orders (
        order_id       TEXT    PRIMARY KEY,
        user_id        INTEGER,
        product_name   TEXT,
        duration       TEXT,
        amount         INTEGER,
        wallet_used    REAL    DEFAULT 0,
        status         TEXT,
        order_date     TEXT,
        delivery_date  TEXT    DEFAULT '',
        rating         INTEGER DEFAULT 0,
        review         TEXT    DEFAULT '',
        refunded       INTEGER DEFAULT 0
    )""")

    # ── Admins ─────────────────────────────────────
    c.execute("""CREATE TABLE IF NOT EXISTS admins (
        user_id    INTEGER PRIMARY KEY,
        role       TEXT    DEFAULT 'admin',
        added_by   INTEGER,
        added_date TEXT
    )""")

    # ── Services ───────────────────────────────────
    c.execute("""CREATE TABLE IF NOT EXISTS services (
        service_key   TEXT PRIMARY KEY,
        display_name  TEXT,
        description   TEXT,
        keywords      TEXT,
        is_active     INTEGER DEFAULT 1
    )""")

    # ── Service prices + stock ──────────────────────
    c.execute("""CREATE TABLE IF NOT EXISTS service_prices (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        service_key TEXT,
        duration    TEXT,
        price       INTEGER,
        stock       INTEGER DEFAULT 0
    )""")

    # ── Wallet logs ────────────────────────────────
    c.execute("""CREATE TABLE IF NOT EXISTS wallet_logs (
        id       INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id  INTEGER,
        amount   REAL,
        type     TEXT,
        reason   TEXT,
        date     TEXT
    )""")

    # ── Referrals ──────────────────────────────────
    c.execute("""CREATE TABLE IF NOT EXISTS referrals (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        referrer_id INTEGER,
        referred_id INTEGER,
        date        TEXT,
        rewarded    INTEGER DEFAULT 0
    )""")

    # ── Bot Settings (admin-editable messages & config) ─
    c.execute("""CREATE TABLE IF NOT EXISTS bot_settings (
        key        TEXT PRIMARY KEY,
        value      TEXT,
        updated_at TEXT
    )""")

    # ── Mini App Subscriptions ──────────────────────
    c.execute("""CREATE TABLE IF NOT EXISTS mini_app_subscriptions (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id     INTEGER,
        plan        TEXT,
        order_id    TEXT,
        start_date  TEXT,
        expiry_date TEXT,
        is_active   INTEGER DEFAULT 1
    )""")

    # ── Auto-Reply Log (one-time welcome reply) ────
    c.execute("""CREATE TABLE IF NOT EXISTS auto_reply_log (
        user_id  INTEGER PRIMARY KEY,
        sent_at  TEXT
    )""")

    # ── Deposits (wallet top-up requests) ──────────
    c.execute("""CREATE TABLE IF NOT EXISTS gw_payments (
        order_id    TEXT PRIMARY KEY,
        user_id     INTEGER,
        amount      REAL,
        purpose     TEXT,
        ref_id      TEXT,
        pay_url     TEXT,
        status      TEXT,
        settled     INTEGER DEFAULT 0,
        created_at  TEXT,
        updated_at  TEXT
    )""")

    # ── Payment Ledger (AUTOPAY + MANUAL history) ──
    c.execute("""CREATE TABLE IF NOT EXISTS payment_ledger (
        id        INTEGER PRIMARY KEY AUTOINCREMENT,
        method    TEXT,
        purpose   TEXT,
        user_id   INTEGER,
        amount    REAL,
        ref_id    TEXT,
        detail    TEXT DEFAULT '',
        actor     INTEGER DEFAULT 0,
        date      TEXT
    )""")
    c.execute("CREATE INDEX IF NOT EXISTS idx_pl_method ON payment_ledger(method)")

    c.execute("""CREATE TABLE IF NOT EXISTS deposits (
        deposit_id  TEXT PRIMARY KEY,
        user_id     INTEGER,
        amount      INTEGER,
        status      TEXT    DEFAULT 'pending',
        created_at  TEXT,
        updated_at  TEXT    DEFAULT ''
    )""")

    # ── Migrations for older databases ─────────────
    try:
        c.execute("ALTER TABLE admins ADD COLUMN can_wallet INTEGER DEFAULT 0")
    except Exception:
        pass
    try:
        c.execute("ALTER TABLE orders ADD COLUMN refunded INTEGER DEFAULT 0")
    except Exception:
        pass
    # Wallet spending is open for every user
    c.execute("UPDATE users SET wallet_unlocked=1 WHERE wallet_unlocked=0")
    # Owner + full admins get wallet permission by default
    c.execute("UPDATE admins SET can_wallet=1 WHERE role IN ('owner','admin')")

    supplier_store.init_tables(conn)
    sales_store.init_tables(conn)
    conn.commit()
    conn.close()

    # Insert owner into admins if not present
    _ensure_owner()
    # Seed default products if DB is empty
    _seed_products()
    # Load products into memory
    reload_products()

def _ensure_owner():
    conn = db()
    c = conn.cursor()
    now = now_str()
    c.execute("INSERT OR IGNORE INTO admins (user_id, role, added_by, added_date) VALUES (?,?,?,?)",
              (OWNER_ID, "owner", OWNER_ID, now))
    conn.commit()
    conn.close()

def _seed_products():
    conn = db()
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM services")
    count = c.fetchone()[0]
    if count == 0:
        for key, data in DEFAULT_PRODUCTS.items():
            c.execute("INSERT OR IGNORE INTO services (service_key, display_name, description, keywords, is_active) VALUES (?,?,?,?,1)",
                      (key, data["display_name"], data["description"], ",".join(data["keywords"])))
            for dur, price in data["prices"].items():
                stock = data["stock"].get(dur, 0)
                c.execute("INSERT INTO service_prices (service_key, duration, price, stock) VALUES (?,?,?,?)",
                          (key, dur, price, stock))
    conn.commit()
    conn.close()

def reload_products():
    """Reload in-memory PRODUCTS dict from DB."""
    global PRODUCTS
    PRODUCTS = {}
    conn = db()
    c = conn.cursor()
    c.execute("SELECT service_key, display_name, description, keywords FROM services WHERE is_active=1")
    services = c.fetchall()
    for sk, dname, desc, kw in services:
        keywords = [k.strip().lower() for k in kw.split(",") if k.strip()]
        c.execute("SELECT duration, price, stock FROM service_prices WHERE service_key=? ORDER BY price", (sk,))
        rows = c.fetchall()
        prices = {}
        stock  = {}
        for dur, price, st in rows:
            prices[dur] = price
            stock[dur]  = st
        PRODUCTS[sk] = {
            "display_name": dname,
            "description":  desc,
            "keywords":     keywords,
            "prices":       prices,
            "stock":        stock,
        }
    conn.close()

# ──────────────────────────────────────────────────
#  Admin helpers
# ──────────────────────────────────────────────────
def get_admin_role(user_id: int) -> str | None:
    """Returns role string or None if not admin."""
    conn = db()
    c = conn.cursor()
    c.execute("SELECT role FROM admins WHERE user_id=?", (user_id,))
    row = c.fetchone()
    conn.close()
    return row[0] if row else None

def is_admin(user_id: int) -> bool:
    return get_admin_role(user_id) is not None

def is_delivery_admin(user_id: int) -> bool:
    role = get_admin_role(user_id)
    return role in ("owner", "super_admin", "admin", "delivery_admin")

def can_manage_services(user_id: int) -> bool:
    role = get_admin_role(user_id)
    return role in ("owner", "super_admin", "admin")

def can_manage_wallet(user_id: int) -> bool:
    """Owner always; other admins only if given wallet permission."""
    if user_id == OWNER_ID:
        return True
    conn = db()
    c = conn.cursor()
    c.execute("SELECT role, COALESCE(can_wallet,0) FROM admins WHERE user_id=?", (user_id,))
    r = c.fetchone()
    conn.close()
    if not r:
        return False
    return r[0] == "owner" or r[1] == 1

def set_wallet_perm(user_id: int, allowed: bool):
    conn = db()
    c = conn.cursor()
    c.execute("UPDATE admins SET can_wallet=? WHERE user_id=?", (1 if allowed else 0, user_id))
    conn.commit()
    conn.close()

def get_admins_with_perm():
    conn = db()
    c = conn.cursor()
    c.execute("SELECT user_id, role, COALESCE(can_wallet,0) FROM admins ORDER BY added_date")
    r = c.fetchall()
    conn.close()
    return r

def get_all_admins():
    conn = db()
    c = conn.cursor()
    c.execute("SELECT user_id, role, added_date FROM admins ORDER BY added_date")
    rows = c.fetchall()
    conn.close()
    return rows

# ──────────────────────────────────────────────────
#  Bot Settings helpers  (admin-editable messages)
# ──────────────────────────────────────────────────
DEFAULT_SETTINGS = {
    "welcome_msg": "",    # Empty = use hardcoded WELCOME_MSG; set from admin panel to override
    "payment_pending_msg": (
        "⏳ <b>Shukriya aapke payment ka!</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "📸 Screenshot successfully received.\n"
        "🔍 Admin abhi verify kar raha hai.\n\n"
        "⏱️ <b>Estimated wait: 5–15 minutes</b>\n\n"
        "📩 Support ke liye: @AVERON_DEALS\n"
        "📢 Reviews: https://t.me/AVERON_DEALS_REVIEWS\n\n"
        "🙏 <i>Thank you for your patience!</i>"
    ),
    "offline_msg": (
        "👋 <b>Thank you very much for messaging 𝗔𝗩𝗘𝗥𝗢𝗡 𝗗𝗘𝗔𝗟𝗦!</b>\n\n"
        "😊 Aapka message mil gaya hai.\n"
        "⚠️ Owner abhi offline hai.\n\n"
        "⏱️ Thoda wait kariye — owner aapko soon reply karega.\n\n"
        "📢 Community: https://t.me/AVERON_DEALS_REVIEWS\n"
        "💬 Update: @AVERON_DEALS_OFFICIAL"
    ),
    "gw_enabled":     "1",                      # 1 = auto payment gateway ON (GlobalNexPay)
    "gw_base":        GATEWAY_BASE_DEFAULT,     # gateway panel base url
    "gw_token":       GATEWAY_API_KEY,          # merchant user_token / api key
    "gw_secret":      GATEWAY_SECRET_KEY,       # merchant secret key
    "gw_min":         "10",                     # min auto-deposit amount
    "mini_app_url":   "",   # Set from admin panel — Telegram WebApp link
    "review_link":    "https://t.me/AVERON_DEALS_REVIEWS",
}

def get_setting(key: str, default: str = "") -> str:
    conn = db()
    c = conn.cursor()
    c.execute("SELECT value FROM bot_settings WHERE key=?", (key,))
    row = c.fetchone()
    conn.close()
    if row:
        return row[0]
    return DEFAULT_SETTINGS.get(key, default)

def set_setting(key: str, value: str):
    conn = db()
    c = conn.cursor()
    now = now_str()
    c.execute("INSERT OR REPLACE INTO bot_settings (key, value, updated_at) VALUES (?,?,?)",
              (key, value, now))
    conn.commit()
    conn.close()

def get_all_settings() -> dict:
    conn = db()
    c = conn.cursor()
    c.execute("SELECT key, value FROM bot_settings")
    rows = {r[0]: r[1] for r in c.fetchall()}
    conn.close()
    merged = dict(DEFAULT_SETTINGS)
    merged.update(rows)
    return merged

# ──────────────────────────────────────────────────
#  Mini App Subscription helpers
# ──────────────────────────────────────────────────
AVERONTV_PLAN_DAYS = {
    "Silver 🥈 1 Month":       30,
    "Gold 🥇 3 Months":       90,
    "Platinum 💎 6 Months":  180,
    "Diamond 💠 1 Year":     365,
}

def activate_mini_app_sub(user_id: int, plan: str, order_id: str):
    """Activate (or extend) a 𝗔𝗩𝗘𝗥𝗢𝗡 𝗗𝗘𝗔𝗟𝗦 TV subscription for a user."""
    days = AVERONTV_PLAN_DAYS.get(plan, 30)
    conn = db()
    c = conn.cursor()
    now = now_dt()
    # Deactivate old subscriptions
    c.execute("UPDATE mini_app_subscriptions SET is_active=0 WHERE user_id=?", (user_id,))
    from datetime import timedelta
    expiry = (now + timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
    c.execute("""INSERT INTO mini_app_subscriptions
                 (user_id, plan, order_id, start_date, expiry_date, is_active)
                 VALUES (?,?,?,?,?,1)""",
              (user_id, plan, order_id, now.strftime("%Y-%m-%d %H:%M:%S"), expiry))
    conn.commit()
    conn.close()

def get_mini_app_sub(user_id: int):
    """Returns (plan, expiry_date) if active, else None."""
    conn = db()
    c = conn.cursor()
    now = now_str()
    c.execute("""SELECT plan, expiry_date FROM mini_app_subscriptions
                 WHERE user_id=? AND is_active=1 AND expiry_date > ?
                 ORDER BY expiry_date DESC LIMIT 1""", (user_id, now))
    row = c.fetchone()
    conn.close()
    return row  # (plan, expiry_date) or None

def get_all_active_subs(limit=50):
    conn = db()
    c = conn.cursor()
    now = now_str()
    c.execute("""SELECT m.user_id, u.username, m.plan, m.expiry_date
                 FROM mini_app_subscriptions m
                 LEFT JOIN users u ON u.user_id = m.user_id
                 WHERE m.is_active=1 AND m.expiry_date > ?
                 ORDER BY m.expiry_date DESC LIMIT ?""", (now, limit))
    rows = c.fetchall()
    conn.close()
    return rows

# ──────────────────────────────────────────────────
#  Deposit helpers  (wallet top-up)
# ──────────────────────────────────────────────────
def generate_deposit_id(user_id: int) -> str:
    return f"DEP{user_id}{''.join(random.choices(string.ascii_uppercase + string.digits, k=4))}"

def save_deposit(deposit_id: str, user_id: int, amount: int):
    conn = db()
    c = conn.cursor()
    now = now_str()
    c.execute("INSERT INTO deposits (deposit_id, user_id, amount, status, created_at) VALUES (?,?,?,?,?)",
              (deposit_id, user_id, amount, "pending", now))
    conn.commit()
    conn.close()

def get_deposit(deposit_id: str):
    conn = db()
    c = conn.cursor()
    c.execute("SELECT * FROM deposits WHERE deposit_id=?", (deposit_id,))
    row = c.fetchone()
    conn.close()
    return row

def update_deposit_status(deposit_id: str, status: str):
    conn = db()
    c = conn.cursor()
    now = now_str()
    c.execute("UPDATE deposits SET status=?, updated_at=? WHERE deposit_id=?", (status, now, deposit_id))
    conn.commit()
    conn.close()

# ──────────────────────────────────────────────────
#  Auto-reply helpers  (one-time offline message)
# ──────────────────────────────────────────────────
def has_auto_replied(user_id: int) -> bool:
    conn = db()
    c = conn.cursor()
    c.execute("SELECT user_id FROM auto_reply_log WHERE user_id=?", (user_id,))
    r = c.fetchone()
    conn.close()
    return r is not None

def mark_auto_replied(user_id: int):
    conn = db()
    c = conn.cursor()
    now = now_str()
    c.execute("INSERT OR IGNORE INTO auto_reply_log (user_id, sent_at) VALUES (?,?)", (user_id, now))
    conn.commit()
    conn.close()

# ──────────────────────────────────────────────────
#  User helpers
# ──────────────────────────────────────────────────
def _gen_ref_code(user_id: int) -> str:
    return f"ref{user_id}"

def update_user(user_id: int, username: str, name: str):
    conn = db()
    c = conn.cursor()
    now = now_str()
    ref_code = _gen_ref_code(user_id)
    c.execute("SELECT user_id FROM users WHERE user_id=?", (user_id,))
    if c.fetchone():
        c.execute("UPDATE users SET username=?, first_name=?, last_active=? WHERE user_id=?",
                  (username or "", name or "", now, user_id))
    else:
        c.execute("""INSERT INTO users
            (user_id, username, first_name, join_date, last_active, referral_code)
            VALUES (?,?,?,?,?,?)""",
            (user_id, username or "", name or "", now, now, ref_code))
    conn.commit()
    conn.close()

def is_user_banned(user_id: int) -> bool:
    conn = db()
    c = conn.cursor()
    c.execute("SELECT is_banned FROM users WHERE user_id=?", (user_id,))
    r = c.fetchone()
    conn.close()
    return bool(r and r[0] == 1)

def get_ban_reason(user_id: int) -> str:
    conn = db()
    c = conn.cursor()
    c.execute("SELECT ban_reason FROM users WHERE user_id=?", (user_id,))
    r = c.fetchone()
    conn.close()
    return r[0] if r else ""

def ban_user(uid: int, reason: str):
    conn = db()
    c = conn.cursor()
    c.execute("UPDATE users SET is_banned=1, ban_reason=? WHERE user_id=?", (reason, uid))
    conn.commit()
    conn.close()

def unban_user(uid: int):
    conn = db()
    c = conn.cursor()
    c.execute("UPDATE users SET is_banned=0, ban_reason='' WHERE user_id=?", (uid,))
    conn.commit()
    conn.close()

def get_user_full(uid: int):
    conn = db()
    c = conn.cursor()
    c.execute("SELECT * FROM users WHERE user_id=?", (uid,))
    u = c.fetchone()
    c.execute("""SELECT order_id, product_name, duration, amount, status
                 FROM orders WHERE user_id=? ORDER BY order_date DESC LIMIT 10""", (uid,))
    o = c.fetchall()
    conn.close()
    return u, o


# ══════════════════════════════════════════════════
#  PAYMENT GATEWAY (auto UPI — iPey.shop style panel)
# ══════════════════════════════════════════════════
def gw_enabled() -> bool:
    """Auto payment gateway ON hai ya nahi. (Manual QR approval se independent —
    gateway khud payment verify karta hai, isliye AUTO_APPROVAL isko block nahi karta.)"""
    return get_setting("gw_enabled", "1") == "1" and bool(gw_token())

def gw_base() -> str:
    return (get_setting("gw_base", GATEWAY_BASE_DEFAULT) or GATEWAY_BASE_DEFAULT).rstrip("/")

def gw_token() -> str:
    return (get_setting("gw_token", GATEWAY_API_KEY) or GATEWAY_API_KEY).strip()

def gw_secret() -> str:
    return (get_setting("gw_secret", GATEWAY_SECRET_KEY) or GATEWAY_SECRET_KEY).strip()

def gw_bootstrap():
    """GlobalNexPay AUTOPAY ko hamesha ready rakhta hai (purane panel ko migrate karta hai)."""
    base = (get_setting("gw_base", "") or "").strip().rstrip("/")
    if (not base) or ("ipey" in base.lower()):
        set_setting("gw_base", GATEWAY_BASE_DEFAULT)
    # .env keys always win at startup (so a new key in .env fixes an old saved one)
    if GATEWAY_API_KEY or not (get_setting("gw_token", "") or "").strip():
        set_setting("gw_token", GATEWAY_API_KEY)
    if GATEWAY_SECRET_KEY or not (get_setting("gw_secret", "") or "").strip():
        set_setting("gw_secret", GATEWAY_SECRET_KEY)
    if get_setting("gw_enabled", "1") != "1":
        set_setting("gw_enabled", "1")

def gw_min() -> int:
    try:
        return max(1, int(float(get_setting("gw_min", "10"))))
    except Exception:
        return 10

def gw_new_id(user_id: int) -> str:
    return f"GW{user_id}{int(datetime.now(IST).timestamp())}{random.randint(10, 99)}"

def gw_save(order_id: str, user_id: int, amount: float, purpose: str, ref_id: str, pay_url: str):
    conn = db(); c = conn.cursor()
    now = now_str()
    c.execute("""INSERT OR REPLACE INTO gw_payments
                 (order_id,user_id,amount,purpose,ref_id,pay_url,status,settled,created_at,updated_at)
                 VALUES (?,?,?,?,?,?,?,0,?,?)""",
              (order_id, user_id, amount, purpose, ref_id, pay_url, "pending", now, now))
    conn.commit(); conn.close()

def gw_get(order_id: str):
    conn = db(); c = conn.cursor()
    c.execute("SELECT order_id,user_id,amount,purpose,ref_id,pay_url,status,settled FROM gw_payments WHERE order_id=?",
              (order_id,))
    r = c.fetchone(); conn.close()
    return r

def gw_set_status(order_id: str, status: str, settled: int | None = None):
    conn = db(); c = conn.cursor()
    now = now_str()
    if settled is None:
        c.execute("UPDATE gw_payments SET status=?, updated_at=? WHERE order_id=?", (status, now, order_id))
    else:
        c.execute("UPDATE gw_payments SET status=?, settled=?, updated_at=? WHERE order_id=?",
                  (status, settled, now, order_id))
    conn.commit(); conn.close()

def gw_pending_rows():
    conn = db(); c = conn.cursor()
    c.execute("""SELECT order_id,user_id,amount,purpose,ref_id,pay_url,status,settled,created_at
                 FROM gw_payments WHERE status='pending' AND settled=0 ORDER BY created_at DESC LIMIT 100""")
    rows = c.fetchall(); conn.close()
    return rows

def gw_stats():
    conn = db(); c = conn.cursor()
    c.execute("SELECT COUNT(*), COALESCE(SUM(amount),0) FROM gw_payments WHERE status='success'")
    ok = c.fetchone()
    c.execute("SELECT COUNT(*) FROM gw_payments WHERE status='pending'")
    pend = c.fetchone()[0]
    conn.close()
    return ok[0], ok[1], pend

async def _gw_post(path: str, payload: dict) -> dict:
    """POST to GlobalNexPay (application/x-www-form-urlencoded, as per /docs). Never raises."""
    import aiohttp, json as _json
    url = f"{gw_base()}{path}"
    fields = {k: ("" if v is None else str(v)) for k, v in payload.items()}
    headers = {"Accept": "application/json", "User-Agent": "Mozilla/5.0 (AveronDealsBot)"}
    timeout = aiohttp.ClientTimeout(total=GATEWAY_TIMEOUT)
    try:
        async with aiohttp.ClientSession(timeout=timeout, headers=headers) as sess:
            async with sess.post(url, data=fields) as resp:
                raw = (await resp.text() or "").strip()
                try:
                    data = _json.loads(raw)
                except Exception:
                    data = None
                if isinstance(data, dict):
                    data.setdefault("_http", resp.status)
                    return data
                return {"status": False, "message": f"HTTP {resp.status}: {raw[:160] or 'blank response'}"}
    except Exception as e:
        return {"status": False, "message": f"Network error: {type(e).__name__}"}

def _truthy(v) -> bool:
    return str(v).strip().lower() in ("1", "true", "success", "yes", "ok")

def _qr_amount(qr_code: str, fallback: float) -> float:
    """Gateway may stagger amount (+₹0.01) — the exact amount is inside the UPI string."""
    m = re.search(r"[?&]am=([0-9]+(?:\.[0-9]+)?)", qr_code or "")
    try:
        return float(m.group(1)) if m else float(fallback)
    except Exception:
        return float(fallback)

async def gw_create_order_full(amount: float, order_id: str, mobile: str = "9999999999",
                               remark: str = "wallet", name: str = "Customer") -> dict:
    """Returns {ok, url, qr, pay_amount, message}."""
    if not gw_enabled():
        return {"ok": False, "url": "", "qr": "", "pay_amount": amount,
                "message": "Gateway band hai (admin ne token set nahi kiya)."}
    payload = {
        "user_token":      gw_token(),
        "amount":          f"{float(amount):.2f}",
        "customer_name":   (name or "Customer")[:40],
        "customer_mobile": re.sub(r"\D", "", str(mobile))[-10:] or "9999999999",
        "order_id":        order_id,
        "remark1":         order_id,
        "remark2":         remark[:60],
        "redirect_url":    get_setting("review_link", "https://t.me/AVERON_DEALS_REVIEWS"),
    }
    wh = (os.getenv("GATEWAY_WEBHOOK_URL") or get_setting("gw_webhook", "")).strip()
    if wh:
        payload["webhook_url"] = wh
    data = await _gw_post("/api/create-order", payload)
    if _truthy(data.get("status")):
        d = data.get("result") or data.get("data") or {}
        if not isinstance(d, dict):
            d = {}
        url = str(d.get("payment_url") or d.get("paymentUrl") or data.get("payment_url") or "")
        qr  = str(d.get("qr_code") or d.get("qrCode") or d.get("upi_intent") or "")
        if url or qr:
            return {"ok": True, "url": url, "qr": qr, "pay_amount": _qr_amount(qr, amount), "message": "ok"}
        return {"ok": False, "url": "", "qr": "", "pay_amount": amount, "message": "Gateway ne QR / link nahi diya."}
    return {"ok": False, "url": "", "qr": "", "pay_amount": amount,
            "message": str(data.get("message") or data.get("msg") or "Gateway error")}

async def gw_create_order(amount: float, order_id: str, mobile: str = "9999999999", remark: str = "wallet"):
    """Backward-compatible wrapper. Returns (ok, payment_url, message)."""
    r = await gw_create_order_full(amount, order_id, mobile, remark)
    return r["ok"], (r["url"] or r["qr"]), r["message"]

async def gw_check_status(order_id: str):
    """Returns (state, message) where state in success|pending|failed."""
    if not gw_enabled():
        return "pending", "Gateway band hai."
    data = await _gw_post("/api/check-order-status", {"user_token": gw_token(), "order_id": order_id})
    res = data.get("result") or data.get("data") or {}
    if isinstance(res, dict):
        txn = str(res.get("txnStatus") or res.get("status") or res.get("payment_status") or "").upper()
    else:
        txn = str(res).upper()
    if txn in ("SUCCESS", "COMPLETED", "PAID", "TRUE"):
        return "success", "paid"
    if txn in ("FAILED", "FAILURE", "CANCELLED", "EXPIRED"):
        return "failed", txn
    return "pending", txn or str(data.get("message") or "PENDING")

async def gw_send_payment(chat_id: int, uid: int, amount: float, purpose: str,
                          title: str, lines: str, remark: str) -> bool:
    """Creates a gateway order and sends the user a ready-to-scan QR for the exact amount.
    Payment is then auto-verified by gw_poller (and webhook if configured)."""
    gw_oid = gw_new_id(uid)
    r = await gw_create_order_full(amount, gw_oid, remark=remark)
    if not r["ok"]:
        await bot.send_message(chat_id, pe(f"⚠️ <b>Auto pay abhi available nahi:</b> {html.escape(r['message'])}\n\n"
                                           f"📷 MANUAL QR se pay kar sakte hain."), parse_mode="HTML")
        return False
    gw_save(gw_oid, uid, amount, purpose, "", r["url"] or r["qr"])
    pay_amt = r["pay_amount"]
    caption = pe(
        f"⚡ <b>{title}</b>\n━━━━━━━━━━━━━━━━━━━━\n\n{lines}"
        f"💰 Pay exactly: <b>₹{pay_amt:.2f}</b>\n\n"
        f"1️⃣ Kisi bhi UPI app se QR scan kijiye\n"
        f"2️⃣ <b>₹{pay_amt:.2f}</b> pay kijiye (exact amount)\n"
        f"3️⃣ Payment hote hi <b>automatic</b> confirm ho jayega ✅\n\n"
        f"🆔 <code>{gw_oid}</code>\n⏱️ QR {min(GATEWAY_EXPIRY_MIN, 5)} min valid hai.")
    kb = gw_pay_kb(gw_oid, r["url"])
    if r["qr"]:
        q = qrcode.QRCode(box_size=10, border=3)
        q.add_data(r["qr"]); q.make(fit=True)
        bio = BytesIO(); q.make_image(fill_color="black", back_color="white").save(bio, "PNG"); bio.seek(0)
        await bot.send_photo(chat_id, types.BufferedInputFile(bio.read(), filename="pay.png"),
                             caption=caption, parse_mode="HTML", reply_markup=kb)
    else:
        await bot.send_message(chat_id, caption, parse_mode="HTML", reply_markup=kb)
    return True

# ══════════════════════════════════════════════════
#  📊 PAYMENT LEDGER — AUTOPAY vs MANUAL history
# ══════════════════════════════════════════════════
PM_AUTO   = "AUTOPAY"
PM_MANUAL = "MANUAL"

def log_payment(method: str, purpose: str, user_id: int, amount: float,
                ref_id: str = "", detail: str = "", actor: int = 0):
    """Record every confirmed payment (autopay gateway or manual QR)."""
    try:
        conn = db(); c = conn.cursor()
        c.execute("""INSERT INTO payment_ledger
            (method, purpose, user_id, amount, ref_id, detail, actor, date)
            VALUES (?,?,?,?,?,?,?,?)""",
            (method, purpose, int(user_id), float(amount), ref_id, detail, int(actor), now_str()))
        conn.commit(); conn.close()
    except Exception as e:
        print(f"[ledger] {e}")

def pay_totals(method: str) -> tuple[int, float, int, float, int, float]:
    """(all_cnt, all_sum, today_cnt, today_sum, week_cnt, week_sum) for a method."""
    conn = db(); c = conn.cursor()
    today = now_str()[:10]
    c.execute("SELECT COUNT(*), COALESCE(SUM(amount),0) FROM payment_ledger WHERE method=?", (method,))
    a = c.fetchone()
    c.execute("SELECT COUNT(*), COALESCE(SUM(amount),0) FROM payment_ledger WHERE method=? AND substr(date,1,10)=?",
              (method, today))
    t = c.fetchone()
    c.execute("""SELECT COUNT(*), COALESCE(SUM(amount),0) FROM payment_ledger
                 WHERE method=? AND date >= datetime('now','-7 day')""", (method,))
    w = c.fetchone()
    conn.close()
    return a[0], a[1], t[0], t[1], w[0], w[1]

def pay_rows(method: str | None = None, limit: int = 12):
    conn = db(); c = conn.cursor()
    if method:
        c.execute("""SELECT method, purpose, user_id, amount, ref_id, detail, actor, date
                     FROM payment_ledger WHERE method=? ORDER BY id DESC LIMIT ?""", (method, limit))
    else:
        c.execute("""SELECT method, purpose, user_id, amount, ref_id, detail, actor, date
                     FROM payment_ledger ORDER BY id DESC LIMIT ?""", (limit,))
    rows = c.fetchall(); conn.close()
    return rows

def pay_history_text(method: str | None = None, limit: int = 12) -> str:
    """Professional payment-history report: AUTOPAY vs MANUAL + latest logs."""
    aa, asum, at, atsum, aw, awsum = pay_totals(PM_AUTO)
    ma, msum, mt, mtsum, mw, mwsum = pay_totals(PM_MANUAL)
    total = asum + msum
    a_pct = (asum / total * 100) if total else 0
    m_pct = (msum / total * 100) if total else 0
    title = {None: "PAYMENT HISTORY", PM_AUTO: "AUTOPAY LOG DETAILS",
             PM_MANUAL: "MANUAL PAYMENT LOG"}[method]
    t = (f"📊 <b>{title}</b>\n"
         f"━━━━━━━━━━━━━━━━━━━━\n\n"
         f"⚡ <b>AUTOPAY (Gateway)</b>\n"
         f"• Total: <b>₹{asum:.0f}</b> ({aa} payments) — {a_pct:.1f}%\n"
         f"• Today: ₹{atsum:.0f} ({at})   • 7 days: ₹{awsum:.0f} ({aw})\n\n"
         f"📷 <b>MANUAL (QR / Screenshot)</b>\n"
         f"• Total: <b>₹{msum:.0f}</b> ({ma} payments) — {m_pct:.1f}%\n"
         f"• Today: ₹{mtsum:.0f} ({mt})   • 7 days: ₹{mwsum:.0f} ({mw})\n\n"
         f"💰 <b>GRAND TOTAL: ₹{total:.0f}</b> ({aa + ma} payments)\n"
         f"━━━━━━━━━━━━━━━━━━━━\n\n")
    rows = pay_rows(method, limit)
    if not rows:
        return t + "<i>Abhi koi payment record nahi hai.</i>"
    t += f"🧾 <b>LATEST {len(rows)} PAYMENTS</b>\n\n"
    for m, purpose, u, amt, ref, detail, actor, date in rows:
        icon = "⚡" if m == PM_AUTO else "📷"
        t += (f"{icon} <b>{m}</b> — ₹{amt:.0f}  <i>{purpose}</i>\n"
              f"   👤 <code>{u}</code>  🆔 <code>{ref or '-'}</code>\n"
              f"   🕒 {date}"
              + (f"  • by <code>{actor}</code>" if actor else "") + "\n\n")
    return t

def pay_history_kb(method: str | None = None) -> InlineKeyboardMarkup:
    smap = get_style_map()
    return InlineKeyboardMarkup(inline_keyboard=[
        [create_btn(f"{sty('ALL PAYMENTS')} 📊", cb="payhist_all", style=smap[1]),
         create_btn(f"{sty('REFRESH')} 🔄", cb=f"payhist_{(method or 'all').lower()}", style=smap[2])],
        [create_btn(f"{sty('AUTOPAY LOG')} ⚡", cb="payhist_autopay", style=ButtonStyle.SUCCESS),
         create_btn(f"{sty('MANUAL LOG')} 📷", cb="payhist_manual", style=smap[3])],
        [create_btn(f"{sty('BACK')} 🔙", cb="admin_back", style=ButtonStyle.DANGER)],
    ])

async def gw_settle(order_id: str) -> bool:
    """Credits wallet / approves order exactly once. Returns True if settled now."""
    row = gw_get(order_id)
    if not row:
        return False
    oid, uid, amount, purpose, ref_id, pay_url, status, settled = row
    if settled:
        return False
    gw_set_status(oid, "success", 1)
    amount = float(amount)

    if purpose.startswith("order:"):
        shop_oid = purpose.split(":", 1)[1]
        credit_wallet(uid, amount, f"Online payment {oid}")
        debit_wallet(uid, amount, f"Purchase {shop_oid} (online)")
        log_payment(PM_AUTO, "Order payment", uid, amount, shop_oid,
                    detail=f"Gateway txn {oid}")
        try:
            update_order_status(shop_oid, "approved")
            add_order_spent(uid, amount)
        except Exception:
            pass
        schedule_autofulfill(shop_oid)
        conn = db(); c = conn.cursor()
        c.execute("SELECT product_name, duration FROM orders WHERE order_id=?", (shop_oid,))
        r = c.fetchone()
        c.execute("SELECT user_id FROM admins")
        admins_list = [x[0] for x in c.fetchall()]
        conn.close()
        pkey, dur = (r[0], r[1]) if r else ("", "")
        if str(pkey).startswith("api:"):
            # Service 1 — supplier auto delivery already scheduled above
            try:
                await bot.send_message(uid, pe(f"✅ <b>Payment Successful!</b>\n━━━━━━━━━━━━━━━━━━━━\n\n"
                                               f"Order ID: <code>{shop_oid}</code>\nAmount: ₹{amount:.0f}\n\n"
                                               f"🤖 Aapka product automatic deliver ho raha hai..."), parse_mode="HTML")
            except Exception:
                pass
            return True
        try:
            decrement_stock(pkey, dur)
        except Exception:
            pass
        pname = PRODUCTS.get(pkey, {}).get("display_name", pkey)
        for adm in admins_list:
            try:
                await bot.send_message(
                    adm,
                    pe(f"⚡ <b>ONLINE PAYMENT RECEIVED — DELIVERY REQUIRED</b>\n"
                       f"━━━━━━━━━━━━━━━━━━━━\n\n"
                       f"Order: <code>{shop_oid}</code>\n"
                       f"User: <code>{uid}</code>\n"
                       f"Product: {pname}\nDuration: {dur}\n"
                       f"Amount: ₹{amount:.0f} (auto verified ✅)\n\n"
                       f"No screenshot needed — payment gateway confirmed."),
                    parse_mode="HTML",
                    reply_markup=deliver_kb(shop_oid, uid, int(amount))
                )
            except Exception:
                pass
        try:
            await bot.send_message(
                uid,
                pe(f"✅ <b>Payment Successful!</b>\n━━━━━━━━━━━━━━━━━━━━\n\n"
                   f"Order ID: <code>{shop_oid}</code>\n"
                   f"Amount: ₹{amount:.0f}\n\n"
                   f"Admin thodi der me deliver karega. Dhanyavaad! 🙏"),
                parse_mode="HTML"
            )
        except Exception:
            pass
    else:
        credit_wallet(uid, amount, f"Auto deposit {oid}")
        log_payment(PM_AUTO, "Wallet top-up", uid, amount, oid, detail="Gateway auto deposit")
        bal, _ = get_wallet(uid)
        try:
            await bot.send_message(
                uid,
                pe(f"✅ <b>Wallet Recharge Successful!</b>\n━━━━━━━━━━━━━━━━━━━━\n\n"
                   f"➕ Added: <b>₹{amount:.0f}</b>\n"
                   f"💰 New Balance: <b>₹{bal:.2f}</b>\n\n"
                   f"Ab aap wallet se direct buy kar sakte hain 🛒"),
                parse_mode="HTML"
            )
        except Exception:
            pass
    return True

async def gw_poller():
    """Background auto-verifier — credits wallet without user pressing anything."""
    await asyncio.sleep(10)
    while True:
        try:
            if gw_enabled():
                for row in gw_pending_rows():
                    oid, uid, amount, purpose, ref_id, pay_url, status, settled, created = row
                    try:
                        age = (now_dt() - datetime.strptime(created, "%Y-%m-%d %H:%M:%S")).total_seconds()
                    except Exception:
                        age = 0
                    if age > GATEWAY_EXPIRY_MIN * 60:
                        gw_set_status(oid, "expired")
                        continue
                    state, _msg = await gw_check_status(oid)
                    if state == "success":
                        await gw_settle(oid)
                    elif state == "failed":
                        gw_set_status(oid, "failed")
                    await asyncio.sleep(0.4)
        except Exception as e:
            logging.warning(f"gw_poller: {e}")
        await asyncio.sleep(GATEWAY_POLL_SECONDS)

def gw_pay_kb(order_id: str, pay_url: str) -> InlineKeyboardMarkup:
    smap = get_style_map()
    rows = [
        *([[create_btn(f"{sty('OPEN PAY PAGE')} 💳", url=pay_url, style=ButtonStyle.SUCCESS)]]
          if str(pay_url).startswith("http") else []),
        [create_btn(f"{sty('CHECK PAYMENT')} 🔄", cb=f"gwchk_{order_id}", style=smap[2])],
        [create_btn(f"{sty('CANCEL PAYMENT')} ❌", cb=f"gwcan_{order_id}", style=ButtonStyle.DANGER)],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)

def gw_admin_kb() -> InlineKeyboardMarkup:
    on = gw_enabled()
    b = InlineKeyboardBuilder()
    b.button(text=("🔴 TURN OFF" if on else "🟢 TURN ON"), callback_data="gwadm_toggle")
    b.button(text="🔑 SET API TOKEN",  callback_data="gwadm_token")
    b.button(text="🌐 SET PANEL URL",  callback_data="gwadm_base")
    b.button(text="💵 SET MIN AMOUNT", callback_data="gwadm_min")
    b.button(text="🧪 TEST GATEWAY",   callback_data="gwadm_test")
    b.button(text="📋 PENDING PAYMENTS", callback_data="gwadm_pending")
    b.button(text="⚡ AUTOPAY LOG DETAILS", callback_data="payhist_autopay")
    b.button(text="📊 AUTO vs MANUAL HISTORY", callback_data="payhist_all")
    b.button(text="🔙 BACK", callback_data="admin_back")
    b.adjust(1)
    return b.as_markup()

# ──────────────────────────────────────────────────
#  Wallet helpers
# ──────────────────────────────────────────────────
def get_wallet(user_id: int) -> tuple[float, int]:
    """Returns (balance, wallet_unlocked)."""
    conn = db()
    c = conn.cursor()
    c.execute("SELECT wallet_balance, wallet_unlocked FROM users WHERE user_id=?", (user_id,))
    r = c.fetchone()
    conn.close()
    return (r[0] if r else 0, r[1] if r else 0)

def credit_wallet(user_id: int, amount: float, reason: str):
    conn = db()
    c = conn.cursor()
    now = now_str()
    c.execute("INSERT OR IGNORE INTO users (user_id, username, first_name, referral_code, join_date, last_active) "
              "VALUES (?,?,?,?,?,?)",
              (user_id, "", "", _gen_ref_code(user_id), now, now))
    c.execute("UPDATE users SET wallet_balance = wallet_balance + ? WHERE user_id=?", (amount, user_id))
    c.execute("INSERT INTO wallet_logs (user_id, amount, type, reason, date) VALUES (?,?,?,?,?)",
              (user_id, amount, "credit", reason, now))
    conn.commit()
    conn.close()

def debit_wallet(user_id: int, amount: float, reason: str) -> bool:
    """Returns True if debit successful."""
    conn = db()
    c = conn.cursor()
    c.execute("SELECT wallet_balance FROM users WHERE user_id=?", (user_id,))
    r = c.fetchone()
    if not r or r[0] < amount:
        conn.close()
        return False
    c.execute("UPDATE users SET wallet_balance = wallet_balance - ? WHERE user_id=?", (amount, user_id))
    now = now_str()
    c.execute("INSERT INTO wallet_logs (user_id, amount, type, reason, date) VALUES (?,?,?,?,?)",
              (user_id, amount, "debit", reason, now))
    conn.commit()
    conn.close()
    return True

def get_wallet_history(user_id: int, limit: int = 10):
    conn = db()
    c = conn.cursor()
    c.execute("SELECT amount, type, reason, date FROM wallet_logs WHERE user_id=? ORDER BY date DESC LIMIT ?",
              (user_id, limit))
    rows = c.fetchall()
    conn.close()
    return rows

def refund_order_wallet(order_id: str, reason: str = "Order cancelled") -> float:
    """Return wallet money used on an order — exactly once. Returns refunded amount."""
    conn = db(); c = conn.cursor()
    c.execute("SELECT user_id, COALESCE(wallet_used,0), COALESCE(refunded,0), status "
              "FROM orders WHERE order_id=?", (order_id,))
    row = c.fetchone(); conn.close()
    if not row:
        return 0.0
    uid, used, refunded, _status = row[0], float(row[1] or 0), int(row[2] or 0), row[3]
    if refunded or used <= 0:
        return 0.0
    conn = db(); c = conn.cursor()
    c.execute("UPDATE orders SET refunded=1 WHERE order_id=?", (order_id,))
    conn.commit(); conn.close()
    credit_wallet(uid, used, f"{reason} — refund for order {order_id}")
    return used

def cancel_order_with_refund(order_id: str, reason: str = "Order cancelled") -> tuple[float, float, int]:
    """Cancel a pending order and refund its wallet part.
    Returns (refunded_amount, new_balance, user_id)."""
    conn = db(); c = conn.cursor()
    c.execute("SELECT user_id, status FROM orders WHERE order_id=?", (order_id,))
    row = c.fetchone(); conn.close()
    if not row:
        return 0.0, 0.0, 0
    uid, status = row[0], (row[1] or "")
    if status in ("delivered", "approved", "cancelled", "rejected"):
        # already finished — only refund if it was never refunded and not delivered
        if status in ("delivered", "approved"):
            return 0.0, get_wallet(uid)[0], uid
    refunded = refund_order_wallet(order_id, reason)
    update_order_status(order_id, "cancelled")
    bal, _ = get_wallet(uid)
    return refunded, bal, uid

def refund_message(order_id: str, refunded: float, balance: float) -> str:
    """Stylish + bold-quote cancel/refund message."""
    if refunded > 0:
        return (
            f"{sty('PAYMENT CANCELLED')} \u274c\n"
            + bq(f"\U0001f4b0 Refund: \u20b9{refunded:.2f} wallet me wapas aa gaya\n"
                 f"\U0001f49a New Wallet Balance: \u20b9{balance:.2f}\n"
                 f"\U0001f194 Order: {order_id}\n"
                 f"\U0001f552 {now_dt().strftime('%d %b %Y, %I:%M %p')} IST")
            + f"\n{sty('Aap dobara order kar sakte hain')} \U0001f6cd\ufe0f"
        )
    return (
        f"{sty('PAYMENT CANCELLED')} \u274c\n"
        + bq(f"\U0001f4b3 Is order me wallet se koi paisa nahi kata tha.\n"
             f"\U0001f49a Wallet Balance: \u20b9{balance:.2f}\n"
             f"\U0001f194 Order: {order_id}\n"
             f"\U0001f552 {now_dt().strftime('%d %b %Y, %I:%M %p')} IST")
    )

def _check_wallet_unlock(user_id: int):
    """Unlock wallet if user has reached referral threshold."""
    conn = db()
    c = conn.cursor()
    c.execute("SELECT referral_count, wallet_unlocked FROM users WHERE user_id=?", (user_id,))
    r = c.fetchone()
    if r and r[0] >= REFERRAL_UNLOCK_COUNT and r[1] == 0:
        c.execute("UPDATE users SET wallet_unlocked=1 WHERE user_id=?", (user_id,))
        conn.commit()
        conn.close()
        return True   # just unlocked
    conn.close()
    return False

# ──────────────────────────────────────────────────
#  Referral helpers
# ──────────────────────────────────────────────────
def get_referral_info(user_id: int):
    """Returns (referral_code, referral_count, wallet_unlocked)."""
    conn = db()
    c = conn.cursor()
    c.execute("SELECT referral_code, referral_count, wallet_unlocked FROM users WHERE user_id=?", (user_id,))
    r = c.fetchone()
    conn.close()
    return r if r else (_gen_ref_code(user_id), 0, 0)

def get_referred_by(user_id: int) -> int | None:
    conn = db()
    c = conn.cursor()
    c.execute("SELECT referred_by FROM users WHERE user_id=?", (user_id,))
    r = c.fetchone()
    conn.close()
    return r[0] if r else None

def set_referred_by(user_id: int, referrer_id: int):
    conn = db()
    c = conn.cursor()
    c.execute("UPDATE users SET referred_by=? WHERE user_id=? AND referred_by IS NULL", (referrer_id, user_id))
    conn.commit()
    conn.close()

def record_referral(referrer_id: int, referred_id: int) -> bool:
    """Returns True if this is a new valid referral (not already recorded)."""
    conn = db()
    c = conn.cursor()
    c.execute("SELECT id FROM referrals WHERE referred_id=?", (referred_id,))
    if c.fetchone():
        conn.close()
        return False   # already referred
    if referrer_id == referred_id:
        conn.close()
        return False   # self-referral
    now = now_str()
    c.execute("INSERT INTO referrals (referrer_id, referred_id, date) VALUES (?,?,?)",
              (referrer_id, referred_id, now))
    conn.commit()
    conn.close()
    return True

def complete_referral_reward(referrer_id: int, referred_id: int):
    """Credits rewards and marks referral as rewarded. Call after channel join confirmed."""
    conn = db()
    c = conn.cursor()
    c.execute("SELECT id, rewarded FROM referrals WHERE referrer_id=? AND referred_id=?",
              (referrer_id, referred_id))
    row = c.fetchone()
    if not row or row[1] == 1:
        conn.close()
        return
    c.execute("UPDATE referrals SET rewarded=1 WHERE id=?", (row[0],))
    # Increment referrer count
    c.execute("UPDATE users SET referral_count = referral_count + 1 WHERE user_id=?", (referrer_id,))
    conn.commit()
    conn.close()
    # Credit wallet rewards
    credit_wallet(referrer_id, REFERRAL_REWARD, f"Referral reward — user {referred_id} joined")
    credit_wallet(referred_id, WELCOME_BONUS,   "Welcome bonus for joining via referral")
    # Check if referrer unlocked wallet
    _check_wallet_unlock(referrer_id)

def get_top_referrers(limit: int = 10):
    conn = db()
    c = conn.cursor()
    c.execute("""SELECT u.user_id, u.username, u.referral_count
                 FROM users u ORDER BY u.referral_count DESC LIMIT ?""", (limit,))
    rows = c.fetchall()
    conn.close()
    return rows

# ──────────────────────────────────────────────────
#  Order helpers
# ──────────────────────────────────────────────────
def generate_order_id(user_id: int) -> str:
    return f"HD{user_id}{''.join(random.choices(string.ascii_uppercase + string.digits, k=4))}"

def save_order(order_id, user_id, product, duration, amount, wallet_used=0):
    conn = db()
    c = conn.cursor()
    now = now_str()
    c.execute("""INSERT INTO orders
        (order_id, user_id, product_name, duration, amount, wallet_used, status, order_date)
        VALUES (?,?,?,?,?,?,?,?)""",
        (order_id, user_id, product, duration, amount, wallet_used, "pending", now))
    conn.commit()
    conn.close()

def update_order_status(order_id, status):
    conn = db()
    c = conn.cursor()
    now = now_str()
    c.execute("UPDATE orders SET status=?, delivery_date=? WHERE order_id=?", (status, now, order_id))
    conn.commit()
    conn.close()
    if status == "delivered":
        try:
            _sale_hook(order_id)      # 🧾 sales logger (idempotent)
        except Exception:
            logging.exception("sales log hook failed for %s", order_id)

def update_rating(order_id, rating, review):
    conn = db()
    c = conn.cursor()
    c.execute("UPDATE orders SET rating=?, review=? WHERE order_id=?", (rating, review, order_id))
    conn.commit()
    conn.close()

def get_order(order_id):
    conn = db()
    c = conn.cursor()
    c.execute("SELECT * FROM orders WHERE order_id=?", (order_id,))
    r = c.fetchone()
    conn.close()
    return r

def get_user_orders(user_id):
    conn = db()
    c = conn.cursor()
    c.execute("""SELECT order_id, product_name, duration, amount, status
                 FROM orders WHERE user_id=? ORDER BY order_date DESC""", (user_id,))
    r = c.fetchall()
    conn.close()
    return r

def get_pending():
    conn = db()
    c = conn.cursor()
    c.execute("SELECT order_id, user_id, product_name, duration, amount FROM orders WHERE status='pending'")
    r = c.fetchall()
    conn.close()
    return r

def add_order_spent(user_id, amount):
    conn = db()
    c = conn.cursor()
    c.execute("UPDATE users SET total_orders=total_orders+1, total_spent=total_spent+? WHERE user_id=?",
              (amount, user_id))
    conn.commit()
    conn.close()

# ──────────────────────────────────────────────────
#  Analytics helpers
# ──────────────────────────────────────────────────
def get_stats():
    conn = db()
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM users")
    users = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM orders")
    total = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM orders WHERE status='pending'")
    pending = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM orders WHERE status='delivered'")
    delivered = c.fetchone()[0]
    c.execute("SELECT COALESCE(SUM(amount),0) FROM orders WHERE status='delivered'")
    revenue = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM referrals WHERE rewarded=1")
    referrals = c.fetchone()[0]
    c.execute("SELECT COALESCE(SUM(wallet_used),0) FROM orders")
    wallet_used = c.fetchone()[0]
    conn.close()
    return users, total, pending, delivered, revenue, referrals, wallet_used

def get_top_services(limit=5):
    conn = db()
    c = conn.cursor()
    c.execute("""SELECT product_name, COUNT(*), COALESCE(SUM(amount),0)
                 FROM orders WHERE status='delivered'
                 GROUP BY product_name ORDER BY COUNT(*) DESC LIMIT ?""", (limit,))
    r = c.fetchall()
    conn.close()
    return r

def get_daily_sales():
    conn = db()
    c = conn.cursor()
    today = now_dt().strftime("%Y-%m-%d")
    c.execute("SELECT COUNT(*), COALESCE(SUM(amount),0) FROM orders WHERE status='delivered' AND order_date LIKE ?",
              (f"{today}%",))
    r = c.fetchone()
    conn.close()
    return r

# ──────────────────────────────────────────────────
#  Stock helper (DB-backed)
# ──────────────────────────────────────────────────
def update_stock_db(service_key, duration, new_stock):
    conn = db()
    c = conn.cursor()
    c.execute("UPDATE service_prices SET stock=? WHERE service_key=? AND duration=?",
              (new_stock, service_key, duration))
    conn.commit()
    conn.close()
    reload_products()

def decrement_stock(service_key, duration):
    conn = db()
    c = conn.cursor()
    c.execute("UPDATE service_prices SET stock = MAX(0, stock-1) WHERE service_key=? AND duration=?",
              (service_key, duration))
    conn.commit()
    conn.close()
    reload_products()

# ──────────────────────────────────────────────────
#  QR helper
# ──────────────────────────────────────────────────
def _upi_link(amount) -> str:
    from urllib.parse import quote
    try:
        amt = float(amount)
    except Exception:
        amt = 0.0
    pa = quote(str(UPI_ID).strip(), safe="@.-_")
    return (f"upi://pay?pa={pa}&pn={quote('AVERON DEALS')}"
            f"&am={amt:.2f}&cu=INR&tn={quote('AVERON DEALS Payment')}")

# ══════════════════════════════════════════════════
#  🎨 DYNAMIC COLOR GENERATOR (random button styles)
# ══════════════════════════════════════════════════
try:                                        # newer clients / forks expose styles
    from aiogram.enums import ButtonStyle   # type: ignore
except Exception:                           # graceful fallback — plain buttons
    class ButtonStyle:                      # type: ignore
        PRIMARY = "primary"
        SUCCESS = "success"
        DANGER  = "danger"

# Premium custom emoji IDs reused for button icons (pulled from P_EMOJI map)
PREMIUM_EMOJIS = sorted({m for m in re.findall(r'emoji-id="(\d+)"', "".join(P_EMOJI.values()))})

_BTN_STYLE_SUPPORTED = True

def get_style_map():
    """Row-wise random colour assignment for inline buttons."""
    styles = [ButtonStyle.PRIMARY, ButtonStyle.SUCCESS, ButtonStyle.DANGER]
    random.shuffle(styles)
    return {1: styles[0], 2: styles[1], 3: styles[2]}

def create_btn(text, cb=None, url=None, user_id=None,
               style=ButtonStyle.PRIMARY, no_emoji=False, **button_kwargs):
    """Smart button creator — random premium emoji + colour, with safe fallback."""
    global _BTN_STYLE_SUPPORTED
    kwargs = {"text": sty(text)}
    if cb:      kwargs["callback_data"] = cb
    if url:     kwargs["url"] = url
    if user_id: kwargs["user_id"] = user_id
    kwargs.update(button_kwargs)
    extra = {"style": style}
    if not no_emoji and PREMIUM_EMOJIS:
        try:
            extra["icon_custom_emoji_id"] = str(random.choice(PREMIUM_EMOJIS))
        except Exception:
            pass
    if _BTN_STYLE_SUPPORTED:
        try:
            return InlineKeyboardButton(**kwargs, **extra)
        except Exception:
            _BTN_STYLE_SUPPORTED = False    # old Bot API — colours unsupported
    return InlineKeyboardButton(**kwargs)

def styled_row(*buttons):
    """Apply a fresh random colour map to a row of (text, callback) pairs."""
    smap = get_style_map()
    row = []
    for i, item in enumerate(buttons, start=1):
        text, cb = item[0], item[1]
        row.append(create_btn(text, cb=cb, style=smap.get(i, ButtonStyle.PRIMARY)))
    return row

def _suitable_button_style(text: str):
    """Use meaningful colours for actions and a random colour for neutral choices."""
    upper = str(text).upper()
    if any(mark in upper for mark in ("❌", "🚫", "🗑", "➖", "DECLINE", "REJECT", "CANCEL", "BAN ", "TURN OFF")):
        return ButtonStyle.DANGER
    if any(mark in upper for mark in ("✅", "💚", "➕", "🚀", "PAY ", "BUY ", "APPROVE", "DELIVERED", "TURN ON")):
        return ButtonStyle.SUCCESS
    return random.choice([ButtonStyle.PRIMARY, ButtonStyle.SUCCESS, ButtonStyle.DANGER])

def smart_add(builder, *, text, callback_data=None, url=None, user_id=None,
              style=None, no_emoji=False, **kwargs):
    """Builder-friendly smart button with stylish text, colour and premium icon."""
    builder.row(create_btn(
        sty(text), cb=callback_data, url=url, user_id=user_id,
        style=style or _suitable_button_style(text), no_emoji=no_emoji, **kwargs
    ))

class DynamicKeyboardBuilder(InlineKeyboardBuilder):
    """Drop-in builder that styles every button without changing keyboard layouts."""
    def button(self, *, text, **kwargs):
        style = kwargs.pop("style", None) or _suitable_button_style(text)
        self.add(create_btn(
            text,
            cb=kwargs.pop("callback_data", None),
            url=kwargs.pop("url", None),
            user_id=kwargs.pop("user_id", None),
            style=style,
            **kwargs,
        ))
        return self

# All existing keyboard factories below now inherit dynamic colours, stylish
# labels and premium emoji icons without changing their carefully tuned rows.
InlineKeyboardBuilder = DynamicKeyboardBuilder


_QR_PNG_CACHE: dict = {}      # amount -> (png_bytes, expires_ts)
_ACTIVE_QR: dict = {}         # user_id -> {amount, dep_id, expires}

def _now_ts() -> float:
    return datetime.now(IST).timestamp()

def active_qr(user_id: int, amount=None):
    """Return the user's still-valid QR record, else None."""
    rec = _ACTIVE_QR.get(user_id)
    if not rec:
        return None
    if _now_ts() >= rec["expires"]:
        _ACTIVE_QR.pop(user_id, None)
        return None
    if amount is not None and int(rec["amount"]) != int(amount):
        return None
    return rec

def remember_qr(user_id: int, amount: int, dep_id: str) -> dict:
    rec = {"amount": int(amount), "dep_id": dep_id,
           "created": _now_ts(),
           "expires": _now_ts() + QR_VALID_MIN * 60}
    _ACTIVE_QR[user_id] = rec
    return rec

def qr_valid_line(rec: dict) -> str:
    left = max(0, int((rec["expires"] - _now_ts()) // 60) + 1)
    till = fmt_time(rec["expires"])
    return (f"⏳ <b>QR valid for {left} min</b> (till {till} IST)\n"
            f"🕒 Generated: {fmt_datetime(rec.get('created', _now_ts()))} IST")

def generate_qr(amount):
    """Return PNG bytes of the UPI QR (cached for QR_VALID_MIN minutes)."""
    key = f"{float(amount):.2f}"
    hit = _QR_PNG_CACHE.get(key)
    if hit and _now_ts() < hit[1]:
        return hit[0]
    try:
        qr = qrcode.QRCode(
            version=None,
            error_correction=qrcode.constants.ERROR_CORRECT_M,
            box_size=10, border=4
        )
        qr.add_data(_upi_link(amount))
        qr.make(fit=True)
        img = qr.make_image(fill_color="black", back_color="white")
        try:
            img = img.convert("RGB")
        except Exception:
            pass
        bio = BytesIO()
        img.save(bio, format="PNG")
        data = bio.getvalue()
        if not data:
            return None
        _QR_PNG_CACHE[key] = (data, _now_ts() + QR_VALID_MIN * 60)
        return data
    except Exception as e:
        logging.exception("QR generation failed: %s", e)
        return None

async def send_payment_qr(target, amount, caption, reply_markup=None):
    """Send QR photo with retries; always falls back to a text payment message."""
    png = generate_qr(amount)
    if png:
        for attempt in range(3):
            try:
                return await target.answer_photo(
                    photo=types.BufferedInputFile(png, filename="qr.png"),
                    caption=pe(caption),
                    parse_mode="HTML",
                    reply_markup=reply_markup
                )
            except Exception as e:
                logging.warning("QR send failed (try %s): %s", attempt + 1, e)
                await asyncio.sleep(1.5)
    # Fallback — never leave the user without payment details
    link = _upi_link(amount)
    try:
        return await target.answer(
            pe(caption + f"\n\n⚠️ QR image load nahi hui.\n"
                      f"UPI ID par direct pay karein: <code>{UPI_ID}</code>\n"
                      f"Ya ye link apne UPI app me kholein:\n<code>{link}</code>"),
            parse_mode="HTML",
            reply_markup=reply_markup,
            disable_web_page_preview=True
        )
    except Exception as e:
        logging.exception("QR fallback send failed: %s", e)
        return None

# ──────────────────────────────────────────────────
#  Force-join helper
# ──────────────────────────────────────────────────
async def is_joined(user_id: int) -> bool:
    try:
        m = await bot.get_chat_member(chat_id=CHANNEL_USERNAME, user_id=user_id)
        return m.status in ("member", "administrator", "creator")
    except Exception:
        return False

# ══════════════════════════════════════════════════
#  ✨ START QUOTES (random, bot-style header)
# ══════════════════════════════════════════════════
START_QUOTES = [
    "Sasta nahi — smart kharidna seekho. 💡",
    "Premium sabke liye hai, sirf jeb badi honi zaroori nahi. 👑",
    "Jo waqt pe deal pakadta hai, wahi paisa bachata hai. ⚡",
    "Trust ek baar banta hai — hum roz kamate hain. 🤝",
    "Bade sapne, chhoti keemat. 🚀",
    "Quality pe compromise nahi, price pe zaroor. 🔥",
    "Aaj ki bachat, kal ki taaqat. 💰",
    "Deal wahi best jo delivery pe khari utre. ✅",
]

START_SPOILERS = [
    "Aaj ka secret: naye users ko wallet cashback milta hai 💰",
    "Psst… best price sirf yahan milega, kahin compare mat karo 🔥",
    "Hidden tip: AUTO PAY se order 15 second me confirm 🚀",
    "Andar ki baat: refer karke free premium bhi mil sakta hai 🎁",
    "Chhupi hui deal: bundle lene par extra discount 💸",
]

def start_header() -> str:
    """Bot-format header + bold quote + very rare spoiler line for the /start screen."""
    q = random.choice(START_QUOTES)
    lines = [
        f"{pe('🅰')} <b>AVERON DEALS BOT</b>  •  <i>{sty('v4.0 ONLINE')}</i>",
        f"━━━━━━━━━━━━━━━━━━━━",
        f"<blockquote><b>{pe('✨')} {sty(q)}</b></blockquote>",
    ]
    # Spoiler sirf 1 in 6 chance par — tab hi jab zaroori lage
    if random.randint(1, 6) == 1:
        sp = random.choice(START_SPOILERS)
        lines.append(f"<b><tg-spoiler>{pe('🎁')} {sty(sp)}</tg-spoiler></b>")
    lines.append(f"{pe('⚡')} <b>{sty('AUTO PAY')}</b> • {pe('📷')} <b>{sty('MANUAL QR')}</b> • "
                 f"{pe('💰')} <b>{sty('WALLET')}</b> • {pe('🤍')} <b>{sty('REFERRAL')}</b>")
    lines.append(f"{pe('⏰')} <b>{sty(now_str())} IST</b>\n")
    return "\n".join(lines)

# ══════════════════════════════════════════════════
#  STATIC MESSAGES
# ══════════════════════════════════════════════════
WELCOME_MSG = """✨ <b>WELCOME TO 𝗔𝗩𝗘𝗥𝗢𝗡 𝗗𝗘𝗔𝗟𝗦 BOT</b> ✨
Your trusted destination for Premium OTT, AI Tools, Cloud Storage &amp; Digital Subscriptions at unbeatable prices 🚀

🎬 <b>OTT &amp; MOVIE STREAMING PREMIUM</b>
• 📺 JioHotstar Premium
• 🎞️ Netflix Premium
• 🍿 Amazon Prime Video
• 🎬 Sony LIV Premium
• 📡 ZEE5 Premium
• ❤️ Hoichoi Premium
• 🎥 ALTBalaji Premium
• 🌟 Discovery+ Premium
• 🎭 Lionsgate Play
• 📱 MX Player Gold
• 🎦 Chaupal Premium
• 🎶 Spotify Premium
• 🎵 YouTube Premium
• 👨‍👩‍👧 YouTube Family
• 🎧 Apple Music
• 📻 Gaana Plus
• 🎼 Wynk Music Premium

🤖 <b>AI TOOLS &amp; PRODUCTIVITY PREMIUM</b>
• 🤖 ChatGPT Plus
• 🍃 Gemini Premium + 2TB
• 🧠 Claude AI Pro
• ⚡ Blackbox AI
• 🎨 Canva Pro
• 🖌️ PicsArt Premium
• ✨ Remini Premium
• 🎬 CapCut Pro
• 🎥 KineMaster Premium
• 📷 Lightroom Premium
• 🎨 Adobe Express Premium
• 📝 Grammarly Premium
• 📚 Duolingo Super
• 📈 TradingView Premium
• ☁️ Microsoft 365
• 💾 Google One Premium
• 📂 Dropbox Premium

📱 <b>GOOGLE &amp; OTHER PREMIUM SERVICES</b>
• ☁️ Google One Storage
• ▶️ YouTube Premium
• 📧 Google Workspace
• 📲 Telegram Premium
• 💬 Discord Nitro
• 🎮 Xbox Game Pass
• 🎮 PlayStation Plus
• 🕹️ Steam Wallet Codes

🆕 <b>EXCLUSIVE SERVICES</b>
• 📺 𝗔𝗩𝗘𝗥𝗢𝗡 𝗗𝗘𝗔𝗟𝗦 TV — Mini App (Live Sports, Movies, FIFA Live)
• 📚 Physics Wallah (PW) Courses — JEE/NEET
• 🎟️ Premium Offer Coupons — Swiggy, Zomato, Flipkart &amp; More
• 📞 Virtual Numbers — WhatsApp &amp; Telegram Accounts, India &amp; International

🔥 <b>WHY CHOOSE 𝗔𝗩𝗘𝗥𝗢𝗡 𝗗𝗘𝗔𝗟𝗦?</b>
⚡ Instant Delivery
🔒 100% Trusted &amp; Safe
💸 Best Market Prices
🛠️ Friendly Support Anytime
✅ Genuine Premium Access
🚀 Fast Response &amp; Smooth Service

👑 <b>JOIN 𝗔𝗩𝗘𝗥𝗢𝗡 𝗗𝗘𝗔𝗟𝗦 TODAY</b> 👑
Best Premium Services at Lowest Prices 💯

📢 Reviews &amp; Community: https://t.me/AVERON_DEALS_REVIEWS
💬 Support &amp; Orders: @AVERON_DEALS

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
💡 Use /help for more clarification"""

HELP_MSG = """❓ <b>HELP & SUPPORT GUIDE</b> ❓

📌 <b>HOW TO BUY:</b>
1️⃣ Type service name (Netflix, Spotify, ChatGPT…)
2️⃣ Select duration
3️⃣ Confirm & proceed to payment
4️⃣ Scan QR → Pay → Click "I HAVE PAID"
5️⃣ Send payment screenshot
6️⃣ Admin verifies → Service delivered ⚡

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

💰 <b>WALLET SYSTEM:</b>
• Earn ₹{rr} for each successful referral
• New users get ₹{wb} welcome bonus
• Wallet unlocks after {ru} referrals
• Use wallet at checkout to reduce payment

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

📋 <b>COMMANDS:</b>
/start   → Main menu
/help    → This guide
/orders  → Your order history
/wallet  → Wallet & balance
/referral→ Referral link & stats
/contact → Contact support
/rules   → Refund & rules policy
/cancel  → Cancel current operation

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
💡 Short forms work! NF=Netflix YT=YouTube GPT=ChatGPT""".format(
    rr=REFERRAL_REWARD, wb=WELCOME_BONUS, ru=REFERRAL_UNLOCK_COUNT
)

PAYMENT_CONFIRMED_MSG = """✅ <b>Dear Customer,</b>
🎉 <b>Your payment has been successfully confirmed!</b>
💎 Thank you for choosing <b>𝗔𝗩𝗘𝗥𝗢𝗡 𝗗𝗘𝗔𝗟𝗦</b>
⚡ Your service/order is now being processed instantly.
📩 For support & updates contact: @AVERON_DEALS_OFFICIAL_BOT
🔥 Trusted Premium Services at Best Prices"""


RULES_MSG = """📜 <b>𝗔𝗩𝗘𝗥𝗢𝗡 𝗗𝗘𝗔𝗟𝗦 — RULES & REFUND POLICY</b>
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

⚖️ <b>GENERAL RULES:</b>

1️⃣ Payment karne ke baad screenshot bhejiye — bina screenshot ke order process nahi hoga.
2️⃣ Ek baar service deliver ho jane ke baad koi refund nahi milega.
3️⃣ Kisi bhi service ke liye genuine screenshots hi bhejein — fake ya edited screenshots strictly banned.
4️⃣ Support ke liye sirf @AVERON_DEALS ya @AVERON_DEALS_OFFICIAL_BOT se contact karein.
5️⃣ Abusive/spam behavior strictly prohibited hai — account ban ho sakta hai.
6️⃣ Ye saari services digital hain — sharing/reselling allowed nahi hai.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

💸 <b>REFUND POLICY (Applicable on All Services):</b>

✅ <b>100% Full Refund:</b>
• Agar hum aapko service provide nahi kar paye (hamare side se issue ho), to aapko <b>100% pura paisa refund</b> kiya jayega — bina kisi deduction ke.

⚠️ <b>82% Partial Refund (18% Deduction):</b>
• Agar aapke phone ya device mein koi technical issue ho jiske wajah se service kaam nahi kar rahi, to <b>18% service/processing fee katke</b> baaki <b>82% refund</b> diya jayega.

❌ <b>No Refund:</b>
• Service successfully deliver ho jane ke baad.
• Galat information dene par (wrong email, wrong ID, etc.).
• Service ka misuse ya policy violation ke case mein.
• Coupon ya virtual number use ho jane ke baad.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

📌 <b>Refund Kaise Claim Karein?</b>
Refund ke liye @AVERON_DEALS ko message karein apna Order ID ke saath.

📢 Reviews: https://t.me/AVERON_DEALS_REVIEWS
💬 Update: @AVERON_DEALS_OFFICIAL"""

REFUND_MSG = """💸 <b>REFUND POLICY — 𝗔𝗩𝗘𝗥𝗢𝗡 𝗗𝗘𝗔𝗟𝗦</b>
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

✅ <b>100% Full Refund:</b>
Agar hum aapko service provide <b>nahi kar paye</b> (hamare side se koi issue), to aapko <b>pura 100% paisa wapas</b> kar diya jayega — zero deduction.

⚠️ <b>82% Partial Refund (18% deduction):</b>
Agar aapke <b>phone/device mein issue</b> hone ki wajah se service kaam nahi kar rahi, to <b>18% cut karke 82% refund</b> diya jayega.

❌ <b>Refund Nahi Milega Agar:</b>
• Service successfully deliver ho chuki ho
• Wrong details di gayi hon
• Coupon ya virtual number use ho chuka ho
• Policy violation/misuse ke case mein

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

📩 Refund claim karne ke liye apna <b>Order ID</b> ke saath contact karein: @AVERON_DEALS

📜 Poori policy dekhne ke liye: /rules"""

# ── Start-menu intro block (shown below service pagination) ──
START_INTRO_MSG = """🔥 <b>𝗔𝗩𝗘𝗥𝗢𝗡 𝗗𝗘𝗔𝗟𝗦 PREMIUM SERVICES</b>
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

⚡ Instant Delivery
💎 Trusted Premium Services
💚 Wallet &amp; Referral System

📢 Reviews:
https://t.me/AVERON_DEALS_REVIEWS

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
💡 Type any service name to buy • /help for guide"""

# ══════════════════════════════════════════════════
#  KEYBOARDS
# ══════════════════════════════════════════════════
# Admin-editable button labels (Admin → ✏️ EDIT MESSAGES → 🎨 BUTTON TEXTS)
BTN_DEFAULTS = {
    "btn_s1":      "SERVICE 1 ⚡",
    "btn_s2":      "SERVICE 2 🛒",
    "btn_wallet":  "WALLET 💰",
    "btn_ref":     "REFERRAL 🤝",
    "btn_orders":  "MY ORDERS 📦",
    "btn_support": "SUPPORT 📞",
}

def btn_text(key: str) -> str:
    return (get_setting(key, "") or BTN_DEFAULTS.get(key, key)).strip()

def _home_rows():
    smap = get_style_map()
    return [
        [create_btn(btn_text("btn_s1"), cb="svc1", style=smap[1]),
         create_btn(btn_text("btn_s2"), cb="buy_now", style=smap[2])],
        [create_btn(btn_text("btn_wallet"), cb="wallet_view", style=smap[2]),
         create_btn(btn_text("btn_ref"), cb="ref_view", style=smap[3])],
        [create_btn(btn_text("btn_orders"), cb="my_orders", style=smap[1]),
         create_btn(btn_text("btn_support"), cb="contact", style=smap[3])],
    ]

def main_kb():
    return InlineKeyboardMarkup(inline_keyboard=_home_rows())

# ══════════════════════════════════════════════════
#  BROWSE SCREEN — paginated service picker
#  (opened only when user taps BUY NOW)
# ══════════════════════════════════════════════════

def _get_active_service_keys() -> list[str]:
    """Always reads live from DB — returns active service keys alphabetically."""
    conn = db()
    c = conn.cursor()
    c.execute("SELECT service_key FROM services WHERE is_active=1 ORDER BY display_name")
    keys = [row[0] for row in c.fetchall()]
    conn.close()
    return keys

def _service_has_any_stock(service_key: str) -> bool:
    """True if at least one duration still has stock > 0."""
    p = PRODUCTS.get(service_key, {})
    return any(v > 0 for v in p.get("stock", {}).values())

def browse_text(page: int = 0) -> str:
    """Header text for the paginated services browse screen."""
    keys        = _get_active_service_keys()
    total       = len(keys)
    total_pages = max(1, (total + START_MENU_PER_PAGE - 1) // START_MENU_PER_PAGE)
    cur         = max(0, min(page, total_pages - 1)) + 1
    return pe(
        f"📦 <b>{html.escape(get_setting('s2_title', '') or 'SERVICE 2 — SERVICES')}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"👇 <b>Select a service to buy:</b>\n\n"
        f"<i>📄 Page {cur} of {total_pages}</i>"
    )

def browse_kb(page: int = 0) -> InlineKeyboardMarkup:
    """
    Keyboard for the paginated services browse screen.
    Contains ONLY:
      • Service buttons (2 per row, START_MENU_PER_PAGE per page)
      • ⬅️ Previous  📄 X/Y  Next ➡️  navigation row
      • 🔙 Back To Menu  (returns user to welcome screen)

    No action buttons here — they live on the welcome screen.
    Reads services live from DB so admin changes appear instantly.
    Out-of-stock services remain visible with ❌ badge.
    """
    keys        = _get_active_service_keys()
    total       = len(keys)
    total_pages = max(1, (total + START_MENU_PER_PAGE - 1) // START_MENU_PER_PAGE)
    page        = max(0, min(page, total_pages - 1))

    start_i = page * START_MENU_PER_PAGE
    end_i   = min(start_i + START_MENU_PER_PAGE, total)

    rows: list[list[InlineKeyboardButton]] = []

    # ── Service grid (2 per row) ────────────────────
    svc_buttons: list[InlineKeyboardButton] = []
    for key in keys[start_i:end_i]:
        p      = PRODUCTS.get(key, {})
        dname  = p.get("display_name", key)
        in_stk = _service_has_any_stock(key)
        label  = f"✅ {dname}" if in_stk else f"❌ {dname}"
        style  = "success" if in_stk else "danger"
        svc_buttons.append(create_btn(label, cb=f"browse_{key}",
                                      style=_suitable_button_style(label)))
    for i in range(0, len(svc_buttons), 2):
        rows.append(svc_buttons[i : i + 2])

    # ── Pagination row ──────────────────────────────
    nav: list[InlineKeyboardButton] = []
    if page > 0:
        nav.append(create_btn("⬅️ Previous", cb=f"startpg_{page - 1}"))
    nav.append(create_btn(f"📄 {page + 1} / {total_pages}", cb="startpg_noop",
                          style=ButtonStyle.PRIMARY, no_emoji=True))
    if page < total_pages - 1:
        nav.append(create_btn("Next ➡️", cb=f"startpg_{page + 1}"))
    rows.append(nav)

    # ── Back button ─────────────────────────────────
    rows.append([create_btn("🔙 Back To Menu", cb="back", style=ButtonStyle.PRIMARY)])

    return InlineKeyboardMarkup(inline_keyboard=rows)

def welcome_kb(joined: bool = True) -> InlineKeyboardMarkup:
    """
    Keyboard for the /start welcome screen.
    Contains ONLY the five action buttons.
    For non-joined users, shows join prompt instead.
    """
    if joined:
        return InlineKeyboardMarkup(inline_keyboard=_home_rows())
    else:
        return InlineKeyboardMarkup(inline_keyboard=[
            [create_btn("📢 Join Channel", url=f"https://t.me/{CHANNEL_USERNAME[1:]}", style=ButtonStyle.SUCCESS)],
            [create_btn("✅ Maine Join Kar Liya - Check Karo", cb="check_join", style=ButtonStyle.SUCCESS)],
        ])

async def send_start_screen(target, joined: bool = True):
    """
    Send the welcome screen — welcome_msg (from settings or WELCOME_MSG fallback) + welcome_kb().
    One message. No pagination here; pagination is opened by BUY NOW.
    target can be Message or CallbackQuery.
    """
    kb   = welcome_kb(joined=joined)
    text = pe(start_header() + (get_setting("welcome_msg") or WELCOME_MSG))
    if isinstance(target, Message):
        await target.answer(text, parse_mode="HTML", reply_markup=kb)
    else:
        try:
            await target.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
        except Exception:
            await target.message.answer(text, parse_mode="HTML", reply_markup=kb)
        await target.answer()

async def send_browse_screen(target, page: int = 0):
    """
    Send or edit-in-place the paginated services browse screen.
    Called by buy_now (new message) and startpg_ (edit in-place).
    target can be Message or CallbackQuery.
    """
    text = browse_text(page)
    kb   = browse_kb(page)
    if isinstance(target, Message):
        await target.answer(text, parse_mode="HTML", reply_markup=kb)
    else:
        try:
            await target.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
        except Exception:
            await target.message.answer(text, parse_mode="HTML", reply_markup=kb)
        await target.answer()


def join_kb():
    """Standalone join prompt used in non-start contexts (e.g. auto-detect gate)."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [create_btn("📢 Join Channel", url=f"https://t.me/{CHANNEL_USERNAME[1:]}", style=ButtonStyle.SUCCESS)],
        [create_btn("✅ Maine Join Kar Liya - Check Karo", cb="check_join", style=ButtonStyle.SUCCESS)]
    ])

def duration_kb(product_key, prices, stock):
    b = InlineKeyboardBuilder()
    for dur, price in prices.items():
        s = stock.get(dur, 0)
        if s > 0:
            b.button(text=f"📺 {dur} — ₹{price}", callback_data=f"dur_{product_key}_{dur}_{price}")
        else:
            b.button(text=f"❌ {dur} — OUT OF STOCK", callback_data="sold_out")
    b.button(text="🔙 BACK", callback_data="back")
    b.adjust(1)
    return b.as_markup()

def confirm_kb(product_key, dur, amt):
    b = InlineKeyboardBuilder()
    b.button(text="✅ YES, PROCEED",  callback_data=f"yes_{product_key}_{dur}_{amt}")
    b.button(text="❌ CANCEL",        callback_data="no")
    b.adjust(2)
    return b.as_markup()

def payment_method_kb(product_key, dur, amt, bal):
    """Payment options — Wallet, ⚡ AUTO PAY (gateway) aur 📷 MANUAL QR: teeno hamesha."""
    smap = get_style_map()
    rows = []
    if bal >= amt and amt > 0:
        rows.append([create_btn(f"{sty(f'PAY WITH WALLET  Rs {amt}')} 💚",
                                cb=f"wuse_{product_key}_{dur}_{amt}", style=ButtonStyle.SUCCESS)])
    elif bal > 0:
        rows.append([create_btn(f"{sty(f'WALLET Rs {bal:.0f} + PAY Rs {amt - int(bal)}')} 💚",
                                cb=f"wuse_{product_key}_{dur}_{amt}", style=ButtonStyle.SUCCESS)])
    else:
        rows.append([create_btn(f"{sty('WALLET Rs 0 — ADD MONEY')} 💰",
                                cb="wallet_empty", style=smap[3])])
    rows.append([create_btn(f"{sty(f'AUTO PAY — INSTANT  Rs {amt}')} ⚡",
                            cb=f"gwbuy_{product_key}_{dur}_{amt}", style=smap[1])])
    rows.append([create_btn(f"{sty(f'MANUAL PAY — QR SCANNER  Rs {amt}')} 📷",
                            cb=f"wskip_{product_key}_{dur}_{amt}", style=smap[2])])
    rows.append([create_btn(f"{sty('CANCEL')} ❌", cb="no", style=ButtonStyle.DANGER)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def pay_choice_kb(product_key, dur, amt, wallet_part: int = 0):
    """Wallet + PAY par click ke baad: AUTO aur MANUAL dono buttons — user koi 1 chunta hai."""
    smap = get_style_map()
    rows = [
        [create_btn(f"{sty(f'AUTO PAY — INSTANT  Rs {amt}')} ⚡",
                    cb=f"gwbuy_{product_key}_{dur}_{amt}", style=smap[1])],
        [create_btn(f"{sty(f'MANUAL PAY — QR SCANNER  Rs {amt}')} 📷",
                    cb=f"wskip_{product_key}_{dur}_{amt}", style=smap[2])],
        [create_btn(f"{sty('CANCEL')} ❌", cb="no", style=ButtonStyle.DANGER)],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


# Backwards-compatible alias
def wallet_confirm_kb(product_key, dur, amt, wallet_amt):
    return payment_method_kb(product_key, dur, amt, wallet_amt)

def paid_kb(product_key, dur, amt, oid):
    smap = get_style_map()
    rows = [
        [create_btn(f"{sty('I HAVE PAID')} 💚",
                    cb=f"paid_{product_key}_{dur}_{amt}_{oid}", style=ButtonStyle.SUCCESS)],
        [create_btn(f"{sty('CANCEL PAYMENT')} ❌",
                    cb=f"ocan_{oid}", style=ButtonStyle.DANGER)],
        [create_btn(f"{sty('SUPPORT')} 📞", cb="contact", style=smap[2])],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)

def rating_kb(oid):
    b = InlineKeyboardBuilder()
    for i in range(1, 6):
        b.button(text=f"⭐{i}", callback_data=f"rate_{oid}_{i}")
    b.button(text="⏭️ SKIP", callback_data=f"skip_{oid}")
    b.adjust(5)
    return b.as_markup()

def admin_kb():
    b = InlineKeyboardBuilder()
    b.button(text="📦 PENDING ORDERS",    callback_data="admin_pending")
    b.button(text="📈 ANALYTICS",         callback_data="admin_analytics")
    b.button(text="🏆 TOP SERVICES",      callback_data="admin_top")
    b.button(text="👥 TOP REFERRERS",     callback_data="admin_referrers")
    b.button(text="👤 USER KUNDALI",      callback_data="admin_kundali")
    b.button(text="🚫 BAN / UNBAN",       callback_data="admin_ban")
    b.button(text="💳 WALLET MGMT",       callback_data="admin_wallet")
    b.button(text="⚡ PAYMENT GATEWAY",   callback_data="admin_gateway")
    b.button(text="🔌 API CONTROL CENTER — ON / OFF", callback_data="apic")
    b.button(text="🧾 SALES LOGGER",      callback_data="slog")
    b.button(text="⚡ SERVICE 1 (API) — PRICE / TEXT", callback_data="s1adm")
    b.button(text="🔌 SUPPLIER API + PROFIT", callback_data="sup_home")
    b.button(text="📊 PAYMENT HISTORY",   callback_data="payhist_all")
    b.button(text="📢 BROADCAST",         callback_data="admin_broadcast")
    b.button(text="📦 STOCK MGMT",        callback_data="admin_stock")
    b.button(text="🛠️ SERVICES MGMT",    callback_data="admin_services")
    b.button(text="📺 MINI APP SUBS",     callback_data="admin_minisubs")
    b.button(text="✏️ EDIT MESSAGES",    callback_data="admin_settings")
    b.button(text="👮 ADMIN MGMT",        callback_data="admin_admins")
    b.button(text="🔙 BACK",              callback_data="back")
    b.adjust(1)
    return b.as_markup()

def settings_kb() -> InlineKeyboardMarkup:
    """Admin: choose which bot message/setting to edit."""
    b = InlineKeyboardBuilder()
    b.button(text="🏠 Welcome Message",        callback_data="editset_welcome_msg")
    b.button(text="⏳ Payment Pending Message", callback_data="editset_payment_pending_msg")
    b.button(text="💤 Offline Auto-Reply",      callback_data="editset_offline_msg")
    b.button(text="📺 Mini App URL",            callback_data="editset_mini_app_url")
    b.button(text="🔗 Review Link",             callback_data="editset_review_link")
    for k, lbl in (("btn_s1", "Service 1 Button"), ("btn_s2", "Service 2 Button"),
                   ("btn_wallet", "Wallet Button"), ("btn_ref", "Referral Button"),
                   ("btn_orders", "My Orders Button"), ("btn_support", "Support Button"),
                   ("s1_title", "Service 1 Heading"), ("s2_title", "Service 2 Heading")):
        b.button(text=f"🎨 {lbl}: {(get_setting(k,'') or BTN_DEFAULTS.get(k,'default'))[:20]}",
                 callback_data=f"editset_{k}")
    b.button(text="🔙 BACK",                    callback_data="admin_back")
    b.adjust(1)
    return b.as_markup()

def minisubs_kb() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="📋 View Active Subs",     callback_data="minisubs_list")
    b.button(text="🔍 Check User Sub",       callback_data="minisubs_check")
    b.button(text="❌ Revoke User Sub",      callback_data="minisubs_revoke")
    b.button(text="🔙 BACK",                 callback_data="admin_back")
    b.adjust(1)
    return b.as_markup()

PRODUCTS_PER_PAGE = STOCK_ADMIN_PER_PAGE   # alias used by stock_kb / services_mgmt_kb

def stock_kb(page=0):
    keys = list(PRODUCTS.keys())
    b = InlineKeyboardBuilder()
    start = page * PRODUCTS_PER_PAGE
    end   = min(start + PRODUCTS_PER_PAGE, len(keys))
    for i in range(start, end):
        key = keys[i]
        b.button(text=PRODUCTS[key]["display_name"], callback_data=f"stk_{key}")
    b.adjust(2)
    nav = []
    if page > 0:
        nav.append(create_btn("⬅️ Prev", cb=f"stkpg_{page-1}"))
    if end < len(keys):
        nav.append(create_btn("Next ➡️", cb=f"stkpg_{page+1}"))
    if nav:
        b.row(*nav)
    b.button(text="🔙 BACK", callback_data="admin_back")
    return b.as_markup()

def services_mgmt_kb(page=0):
    """List all services (active + inactive) for admin management."""
    conn = db()
    c = conn.cursor()
    c.execute("SELECT service_key, display_name, is_active FROM services ORDER BY display_name")
    all_svcs = c.fetchall()
    conn.close()
    b = InlineKeyboardBuilder()
    start = page * PRODUCTS_PER_PAGE
    end   = min(start + PRODUCTS_PER_PAGE, len(all_svcs))
    for sk, dname, active in all_svcs[start:end]:
        prefix = "✅" if active else "🚫"
        b.button(text=f"{prefix} {dname}", callback_data=f"svc_{sk}")
    b.adjust(1)
    nav = []
    if page > 0:
        nav.append(create_btn("⬅️ Prev", cb=f"svcpg_{page-1}"))
    if end < len(all_svcs):
        nav.append(create_btn("Next ➡️", cb=f"svcpg_{page+1}"))
    if nav:
        b.row(*nav)
    b.button(text="➕ Add New Service", callback_data="svc_add")
    b.button(text="🔙 BACK",           callback_data="admin_back")
    b.adjust(1)
    return b.as_markup()

def wallet_mgmt_kb() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="➕ ADD BALANCE (GIVE)",   callback_data="wadm_add")
    b.button(text="➖ DEDUCT BALANCE (TAKE)", callback_data="wadm_deduct")
    b.button(text="🔍 CHECK USER WALLET",    callback_data="wadm_check")
    b.button(text="🏆 TOP WALLETS",          callback_data="wadm_top")
    b.button(text="🧾 RECENT WALLET LOGS",   callback_data="wadm_logs")
    b.button(text="🔙 BACK",                 callback_data="admin_back")
    b.adjust(1)
    return b.as_markup()

def wallet_perm_kb() -> InlineKeyboardMarkup:
    """Owner toggles wallet give/take permission per admin."""
    b = InlineKeyboardBuilder()
    for auid, role, perm in get_admins_with_perm():
        if role == "owner":
            b.button(text=f"👑 {auid} — owner (always)", callback_data="wperm_noop")
        else:
            mark = "✅ HAS" if perm else "🚫 NO"
            b.button(text=f"{mark} wallet — {auid} ({role})", callback_data=f"wperm_{auid}")
    b.button(text="🔙 BACK", callback_data="admin_admins")
    b.adjust(1)
    return b.as_markup()

def broadcast_audience_kb() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="👥 ALL USERS",            callback_data="bcaud_all")
    b.button(text="🛒 BUYERS ONLY",          callback_data="bcaud_buyers")
    b.button(text="🕊️ NEVER BOUGHT",         callback_data="bcaud_nonbuyers")
    b.button(text="🔥 ACTIVE (7 DAYS)",      callback_data="bcaud_active")
    b.button(text="💚 WALLET BALANCE > 0",   callback_data="bcaud_wallet")
    b.button(text="📺 MINI APP SUBSCRIBERS", callback_data="bcaud_subs")
    b.button(text="🔙 BACK",                 callback_data="admin_back")
    b.adjust(1)
    return b.as_markup()

def broadcast_confirm_kb() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="🚀 SEND NOW",  callback_data="bc_send")
    b.button(text="❌ CANCEL",    callback_data="bc_cancel")
    b.adjust(2)
    return b.as_markup()

def service_detail_kb(service_key, is_active):
    b = InlineKeyboardBuilder()
    toggle_text = "🚫 Disable" if is_active else "✅ Enable"
    toggle_cb   = f"svctoggle_{service_key}"
    b.button(text="✏️ Edit Name",        callback_data=f"svcedit_name_{service_key}")
    b.button(text="📝 Edit Description", callback_data=f"svcedit_desc_{service_key}")
    b.button(text="🔑 Edit Keywords",    callback_data=f"svcedit_kw_{service_key}")
    b.button(text="💰 Edit Prices",      callback_data=f"svcedit_prices_{service_key}")
    b.button(text="📦 Edit Stock",       callback_data=f"svcedit_stock_{service_key}")
    b.button(text=toggle_text,        callback_data=toggle_cb)
    b.button(text="🗑️ Delete",       callback_data=f"svcdel_{service_key}")
    b.button(text="🔙 BACK",          callback_data="admin_services")
    b.adjust(2)
    return b.as_markup()

def deliver_kb(order_id, user_id, amount):
    b = InlineKeyboardBuilder()
    b.button(text="✅ DELIVERED",     callback_data=f"deliver_{order_id}_{user_id}_{amount}")
    b.button(text="❌ NOT DELIVERED", callback_data="not_delivered")
    b.adjust(2)
    return b.as_markup()

def approve_kb(oid, amount, uid):
    return InlineKeyboardMarkup(inline_keyboard=[[
        create_btn("✅ APPROVE", cb=f"approve_{oid}_{amount}_{uid}", style=ButtonStyle.SUCCESS),
        create_btn("❌ REJECT", cb=f"reject_{oid}_{uid}", style=ButtonStyle.DANGER),
    ]])

# ══════════════════════════════════════════════════
#  PAYMENT REVIEW CARD (manual admin approval)
# ══════════════════════════════════════════════════
PAY_METHOD_QR = "QR / UPI (Scan & Pay)"

def _fmt_username(user) -> str:
    return f"@{user.username}" if getattr(user, "username", None) else "—"

def _fmt_name(user) -> str:
    name = (getattr(user, "full_name", None) or "").strip()
    return name if name else "—"

def payment_review_card(user, amount, ref_id, *, purpose,
                        product_name=None, duration=None,
                        method=PAY_METHOD_QR, ref_label="Order ID") -> str:
    """Professional approval card shown to admins when a screenshot arrives."""
    lines = [
        "🧾 <b>PAYMENT VERIFICATION REQUIRED</b>",
        "━━━━━━━━━━━━━━━━━━━━",
        "",
        "👤 <b>BUYER DETAILS</b>",
        f"• Name: <b>{_fmt_name(user)}</b>",
        f"• Username: {_fmt_username(user)}",
        f"• Buyer ID: <code>{user.id}</code>",
        "",
        "💳 <b>PAYMENT DETAILS</b>",
        f"• Method: <b>{method}</b>",
        f"• Amount: <b>₹{amount}</b>",
        f"• {ref_label}: <code>{ref_id}</code>",
        "",
        "🎯 <b>PURPOSE</b>",
    ]
    if product_name:
        lines.append(f"• Product: <b>{product_name}</b>")
        if duration:
            lines.append(f"• Plan / Duration: <b>{duration}</b>")
    else:
        lines.append(f"• <b>WALLET TOP-UP</b> — approve karne par ₹{amount} wallet mein add hoga")
    lines += [
        "",
        "📸 Screenshot upar attach hai — verify karke decide kijiye.",
        "━━━━━━━━━━━━━━━━━━━━",
        "⚠️ Auto approval OFF — manual admin decision required.",
    ]
    return pe("\n".join(lines))

def payment_decision_kb(oid, amount, uid):
    return InlineKeyboardMarkup(inline_keyboard=[[
        create_btn("✅ APPROVE PAYMENT", cb=f"approve_{oid}_{amount}_{uid}", style=ButtonStyle.SUCCESS),
        create_btn("❌ DECLINE PAYMENT", cb=f"reject_{oid}_{uid}", style=ButtonStyle.DANGER),
    ]])

def deposit_decision_kb(dep_id, amount, uid):
    return InlineKeyboardMarkup(inline_keyboard=[[
        create_btn("✅ APPROVE TOP-UP", cb=f"dep_approve_{dep_id}_{amount}_{uid}", style=ButtonStyle.SUCCESS),
        create_btn("❌ DECLINE TOP-UP", cb=f"dep_reject_{dep_id}_{uid}", style=ButtonStyle.DANGER),
    ]])

def admin_ids() -> list:
    conn = db()
    c = conn.cursor()
    c.execute("SELECT user_id FROM admins")
    ids = [row[0] for row in c.fetchall()]
    conn.close()
    return ids

def admin_mgmt_kb():
    b = InlineKeyboardBuilder()
    b.button(text="💳 WALLET PERMISSIONS", callback_data="admins_perms")
    b.button(text="➕ Add Admin",    callback_data="admins_add")
    b.button(text="➖ Remove Admin", callback_data="admins_remove")
    b.button(text="📋 List Admins",  callback_data="admins_list")
    b.button(text="🔙 BACK",        callback_data="admin_back")
    b.adjust(1)
    return b.as_markup()

def role_select_kb(target_id: int):
    b = InlineKeyboardBuilder()
    for role in ("super_admin", "admin", "delivery_admin"):
        b.button(text=role.replace("_", " ").title(),
                 callback_data=f"setrole_{target_id}_{role}")
    b.button(text="❌ Cancel", callback_data="admin_back")
    b.adjust(1)
    return b.as_markup()

# ══════════════════════════════════════════════════
#  PRODUCT DISPLAY HELPER
# ══════════════════════════════════════════════════
async def show_product_menu(target, product_key: str):
    """target can be Message or CallbackQuery."""
    p = PRODUCTS[product_key]
    desc = f"🔥 <b>{p['display_name']} DEALS</b>\n━━━━━━━━━━━━━━━━━━━━\n{p['description']}\n\n💰 <b>Available Plans:</b>\n"
    for d, pr in p["prices"].items():
        st = p["stock"].get(d, 0)
        label = "✅ In Stock" if st > 0 else "❌ OUT OF STOCK"
        desc += f"▬ {d} → ₹{pr} ({label})\n"
    desc += "\n📌 <b>Select duration:</b>"
    desc = pe(desc)
    kb = duration_kb(product_key, p["prices"], p["stock"])
    if isinstance(target, Message):
        await target.answer(desc, parse_mode="HTML", reply_markup=kb)
    else:  # CallbackQuery
        await target.message.answer(desc, parse_mode="HTML", reply_markup=kb)
        await target.answer()

# ══════════════════════════════════════════════════
#  SERVICE DETECTION (word-boundary, no spam)
# ══════════════════════════════════════════════════
def detect_service(text: str) -> str | None:
    """
    Returns service key if any keyword matches — else None (silent).
    Uses whole-word regex matching to avoid false positives.
    Only short common codes (2-3 chars) use exact equality.
    """
    t = text.lower().strip()
    for key, data in PRODUCTS.items():
        for kw in data["keywords"]:
            kw = kw.lower()
            if len(kw) <= 3:
                # Short code: require exact word boundary
                if re.search(rf"(?<!\w){re.escape(kw)}(?!\w)", t):
                    return key
            else:
                if kw in t:
                    return key
    return None

# ══════════════════════════════════════════════════
#  COMMAND HANDLERS
# ══════════════════════════════════════════════════
@dp.message(Command("start"))
async def start_cmd(msg: Message, state: FSMContext):
    uid  = msg.from_user.id
    args = msg.text.split(maxsplit=1)[1] if len(msg.text.split()) > 1 else ""
    update_user(uid, msg.from_user.username, msg.from_user.first_name)
    await state.clear()

    # ── Referral processing via deep link ──────────
    if args.startswith("ref") and args[3:].isdigit():
        referrer_id = int(args[3:])
        if referrer_id != uid and get_referred_by(uid) is None:
            set_referred_by(uid, referrer_id)
            await state.update_data(pending_referrer=referrer_id)

    # ── Ban check ──────────────────────────────────
    if is_user_banned(uid):
        reason = get_ban_reason(uid)
        await msg.answer(
            pe(f"🚫 <b>You have been banned.</b>\n\nReason: {reason}\n\nContact: @AVERON_DEALS"),
            parse_mode="HTML"
        )
        return

    # ── Admin gets welcome screen directly ────────
    if is_admin(uid):
        await send_start_screen(msg, joined=True)
        return

    # ── Force join ────────────────────────────────
    if not await is_joined(uid):
        # Show welcome message with join buttons instead of action buttons
        await send_start_screen(msg, joined=False)
    else:
        # Complete any pending referral now that user has joined
        data = await state.get_data()
        rid  = data.get("pending_referrer")
        if rid:
            await _process_referral(uid, rid)
            await state.update_data(pending_referrer=None)
        await send_start_screen(msg, joined=True)

@dp.message(Command("help"))
async def help_cmd(msg: Message):
    await msg.answer(pe(HELP_MSG), parse_mode="HTML", reply_markup=main_kb())

@dp.message(Command("history"))
async def payment_history_cmd(msg: Message):
    """Owner + admins: AUTOPAY vs MANUAL payment history."""
    if not is_admin(msg.from_user.id):
        await msg.answer(pe("⛔ <b>Sirf owner / admin ke liye.</b>"), parse_mode="HTML")
        return
    await msg.answer(pe(pay_history_text(None, limit=12)), parse_mode="HTML",
                     reply_markup=pay_history_kb(None))

@dp.message(Command("orders"))
async def orders_cmd(msg: Message):
    uid    = msg.from_user.id
    orders = get_user_orders(uid)
    if not orders:
        await msg.answer(pe("📭 <b>No orders found.</b>\n\nType a service name to buy!"), parse_mode="HTML", reply_markup=main_kb())
        return
    text = "📦 <b>YOUR ORDERS</b>\n━━━━━━━━━━━━━━━━━━━━\n\n"
    for o in orders:
        em = "✅" if o[4]=="delivered" else ("⏳" if o[4]=="pending" else "❌")
        text += f"{em} <code>{o[0]}</code> — {o[1]} ({o[2]}) — ₹{o[3]}\n"
    await msg.answer(pe(text), parse_mode="HTML", reply_markup=main_kb())

@dp.message(Command("order"))
async def order_cmd(msg: Message):
    parts = msg.text.split()
    if len(parts) < 2:
        await msg.answer(pe("⚠️ Usage: <code>/order ORDER_ID</code>"), parse_mode="HTML")
        return
    o = get_order(parts[1])
    if not o:
        await msg.answer(pe("❌ <b>Order not found</b>"), parse_mode="HTML")
        return
    em   = "✅" if o[6]=="delivered" else ("⏳" if o[6]=="pending" else "❌")
    text = (f"📦 <b>ORDER DETAILS</b>\n━━━━━━━━━━━━━━━━━━━━\n\n"
            f"🆔 <code>{o[0]}</code>\n🛍️ {o[2]}\n⏱️ {o[3]}\n"
            f"💰 ₹{o[4]}")
    if o[5]:
        text += f"  (💚 Wallet used: ₹{o[5]:.0f})"
    text += f"\n📊 {em} {o[6].upper()}\n📅 {o[7]}"
    if o[9] > 0:
        text += f"\n⭐ {'⭐'*o[9]}"
        if o[10]:
            text += f"\n📝 <i>{o[10]}</i>"
    await msg.answer(pe(text), parse_mode="HTML", reply_markup=main_kb())

@dp.message(Command("cancel"))
async def cancel_cmd(msg: Message, state: FSMContext):
    await state.clear()
    await msg.answer(pe("✅ <b>Cancelled.</b> Type a service name to continue!"), parse_mode="HTML", reply_markup=main_kb())

@dp.message(Command("contact"))
async def contact_cmd(msg: Message):
    review = get_setting("review_link", "https://t.me/AVERON_DEALS_REVIEWS")
    await msg.answer(
        pe(f"📞 <b>Contact Support</b>\n━━━━━━━━━━━━━━━━━━━━\n\nMessage directly: @AVERON_DEALS\n\n📢 Reviews: {review}"),
        parse_mode="HTML", reply_markup=main_kb()
    )

@dp.message(Command("rules"))
async def rules_cmd(msg: Message):
    await msg.answer(pe(RULES_MSG), parse_mode="HTML", reply_markup=main_kb())

@dp.message(Command("myapp"))
async def myapp_cmd(msg: Message):
    """User checks their 𝗔𝗩𝗘𝗥𝗢𝗡 𝗗𝗘𝗔𝗟𝗦 TV subscription status."""
    uid = msg.from_user.id
    update_user(uid, msg.from_user.username, msg.from_user.first_name)
    sub = get_mini_app_sub(uid)
    if not sub:
        b = InlineKeyboardBuilder()
        b.button(text="📺 Buy 𝗔𝗩𝗘𝗥𝗢𝗡 𝗗𝗘𝗔𝗟𝗦 TV", callback_data="browse_averontv")
        b.button(text="🔙 Back", callback_data="back")
        b.adjust(1)
        await msg.answer(
            pe("📺 <b>𝗔𝗩𝗘𝗥𝗢𝗡 𝗗𝗘𝗔𝗟𝗦 TV</b>\n━━━━━━━━━━━━━━━━━━━━\n\n"
               "❌ <b>No active subscription found.</b>\n\n"
               "Subscribe to access Live Sports, Movies, FIFA and more!"),
            parse_mode="HTML", reply_markup=b.as_markup()
        )
        return
    plan, expiry = sub
    mini_url = get_setting("mini_app_url")
    rows = []
    if mini_url:
        rows.append([create_btn(
            "▶️ Open 𝗔𝗩𝗘𝗥𝗢𝗡 𝗗𝗘𝗔𝗟𝗦 TV",
            style=ButtonStyle.SUCCESS,
            web_app=types.WebAppInfo(url=mini_url),
        )])
    rows.append([create_btn("🔙 Back", cb="back", style=ButtonStyle.PRIMARY)])
    await msg.answer(
        pe(f"📺 <b>𝗔𝗩𝗘𝗥𝗢𝗡 𝗗𝗘𝗔𝗟𝗦 TV — Active Subscription</b>\n"
           f"━━━━━━━━━━━━━━━━━━━━\n\n"
           f"✅ Plan: <b>{plan}</b>\n"
           f"📅 Expires: <b>{expiry[:10]}</b>\n\n"
           f"{'▶️ Tap button below to open Mini App.' if mini_url else '⏳ Mini App link coming soon — contact @AVERON_DEALS'}"),
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows)
    )


@dp.message(Command("wallet"))
async def wallet_cmd(msg: Message):
    uid = msg.from_user.id
    update_user(uid, msg.from_user.username, msg.from_user.first_name)
    await _show_wallet(msg, uid)

@dp.message(Command("referral"))
async def referral_cmd(msg: Message):
    uid = msg.from_user.id
    update_user(uid, msg.from_user.username, msg.from_user.first_name)
    await _show_referral(msg, uid)

@dp.message(Command("admin"))
async def admin_cmd(msg: Message):
    if not is_admin(msg.from_user.id):
        await msg.answer(pe("⛔ <b>Unauthorized</b>"), parse_mode="HTML")
        return
    await _send_admin_panel(msg)

# Only owner can manage admins
@dp.message(Command("addadmin"))
async def addadmin_cmd(msg: Message, state: FSMContext):
    if msg.from_user.id != OWNER_ID:
        await msg.answer(pe("⛔ <b>Only owner can add admins.</b>"), parse_mode="HTML")
        return
    await msg.answer(pe("👤 <b>Send the user ID to add as admin:</b>"), parse_mode="HTML")
    await state.set_state(AdminManageState.waiting_add_id)

@dp.message(Command("removeadmin"))
async def removeadmin_cmd(msg: Message, state: FSMContext):
    if msg.from_user.id != OWNER_ID:
        await msg.answer(pe("⛔ <b>Only owner can remove admins.</b>"), parse_mode="HTML")
        return
    await msg.answer(pe("👤 <b>Send the admin user ID to remove:</b>"), parse_mode="HTML")
    await state.set_state(AdminManageState.waiting_remove)

@dp.message(Command("admins"))
async def admins_cmd(msg: Message):
    if not is_admin(msg.from_user.id):
        await msg.answer(pe("⛔ <b>Unauthorized</b>"), parse_mode="HTML")
        return
    rows = get_all_admins()
    text = "👮 <b>ADMIN LIST</b>\n━━━━━━━━━━━━━━━━━━━━\n\n"
    for uid_, role, date in rows:
        text += f"🔹 <code>{uid_}</code> — <b>{role}</b> — {date}\n"
    await msg.answer(pe(text), parse_mode="HTML")

# ══════════════════════════════════════════════════
#  QR DEPOSIT  ( /qr  |  "qr"  |  "qr 100" )
# ══════════════════════════════════════════════════
async def send_qr_deposit(msg: Message, amount) -> None:
    """Send (or re-use) a wallet-deposit QR that stays valid for QR_VALID_MIN minutes."""
    uid = msg.from_user.id
    try:
        amount = int(float(amount))
    except Exception:
        amount = 0
    if amount <= 0:
        await msg.answer(pe("❌ <b>Amount galat hai.</b>\n\nJaise: <code>qr 100</code>"), parse_mode="HTML")
        return

    rec = active_qr(uid, amount)
    if rec:
        await msg.answer(
            pe(f"✅ <b>₹{amount} ka QR pehle se active hai</b>\n━━━━━━━━━━━━━━━━━━━━\n\n"
               f"{qr_valid_line(rec)}\n\n"
               f"🆔 Deposit ID: <code>{rec['dep_id']}</code>\n"
               f"UPI: <code>{UPI_ID}</code>\n\n"
               f"Upar bheja hua wahi QR scan kijiye. Expire hone ke baad naya QR milega."),
            parse_mode="HTML")
        return

    dep_id = generate_deposit_id(uid)
    save_deposit(dep_id, uid, amount)
    rec = remember_qr(uid, amount, dep_id)
    dep_paid_kb = InlineKeyboardMarkup(inline_keyboard=[[
        create_btn("I HAVE PAID", cb=f"dep_paid_{dep_id}_{amount}", style=ButtonStyle.SUCCESS)
    ]])
    await send_payment_qr(
        msg, amount,
        (
            f"💳 <b>WALLET DEPOSIT — ₹{amount}</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n\n"
            f"UPI: <code>{UPI_ID}</code>\n"
            f"Amount: <b>₹{amount}</b>\n\n"
            f"{qr_valid_line(rec)}\n\n"
            f"📌 Steps:\n"
            f"1️⃣ Scan QR\n"
            f"2️⃣ Pay ₹{amount}\n"
            f"3️⃣ Click <b>I HAVE PAID</b> below\n\n"
            f"ℹ️ Admin manually verify karke wallet me add karega.\n"
            f"🆔 Deposit ID: <code>{dep_id}</code>"
        ),
        reply_markup=dep_paid_kb
    )

async def ask_qr_amount(msg: Message, state: FSMContext) -> None:
    await state.set_state(QRState.waiting_amount)
    await msg.answer(
        pe("💳 <b>QR BANANA HAI — kitne ka?</b>\n━━━━━━━━━━━━━━━━━━━━\n\n"
           "Sirf amount bhejiye (jaise <code>100</code>).\n\n"
           f"⏳ QR banne ke baad <b>{QR_VALID_MIN} minute</b> valid rahega.\n"
           "❌ Cancel karne ke liye /cancel bhejiye."),
        parse_mode="HTML")

@dp.message(Command("qr"))
async def qr_cmd(msg: Message, state: FSMContext):
    await state.clear()
    parts = (msg.text or "").split()
    if len(parts) > 1:
        m = re.search(r"(\d+(?:\.\d+)?)", parts[1])
        if m:
            await send_qr_deposit(msg, m.group(1))
            return
    await ask_qr_amount(msg, state)

@dp.message(QRState.waiting_amount)
async def qr_amount_handler(msg: Message, state: FSMContext):
    text = (msg.text or "").strip()
    if text.startswith("/"):
        await state.clear()
        await msg.answer(pe("❌ <b>QR cancel ho gaya.</b>"), parse_mode="HTML")
        return
    m = re.search(r"(\d+(?:\.\d+)?)", text)
    if not m:
        await msg.answer(pe("❌ <b>Sirf number bhejiye</b> — jaise <code>100</code>"), parse_mode="HTML")
        return
    amount = int(float(m.group(1)))
    if amount <= 0:
        await msg.answer(pe("❌ <b>Amount ₹1 se zyada hona chahiye.</b>"), parse_mode="HTML")
        return
    await state.clear()
    await send_qr_deposit(msg, amount)

# ══════════════════════════════════════════════════
#  WALLET / REFERRAL DISPLAY HELPERS
# ══════════════════════════════════════════════════
async def _show_wallet(target, uid: int):
    bal, unlocked = get_wallet(uid)
    ref_code, ref_count, _ = get_referral_info(uid)
    history = get_wallet_history(uid, 5)

    text = (f"💚 <b>YOUR WALLET</b>\n━━━━━━━━━━━━━━━━━━━━\n\n"
            f"💰 Balance: <b>₹{bal:.2f}</b>\n"
            f"🤝 Referrals: {ref_count}\n\n"
            f"➕ <b>Money add karne ke liye</b> bas type kijiye:\n"
            f"<code>qr 100</code>  (jitna amount chahiye)\n\n"
            f"🛒 Buy karte waqt wallet se ya QR se — dono option milenge.\n\n"
            f"📋 <b>Recent Transactions:</b>\n")
    if history:
        for amt, typ, reason, date in history:
            em = "➕" if typ=="credit" else "➖"
            text += f"{em} ₹{amt:.2f} — {reason[:30]}\n"
    else:
        text += "No transactions yet.\n"

    smap = get_style_map()
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [create_btn(f"{sty('AUTO PAY — ADD MONEY')} ⚡", cb="gwadd", style=smap[1])],
        [create_btn(f"{sty('MANUAL PAY — QR SCANNER')} 📷", cb="qradd", style=smap[2])],
        [create_btn(f"{sty('BUY NOW')} 🛒", cb="buy_now", style=smap[3])],
        [create_btn(f"{sty('MY REFERRAL')} 🤝", cb="ref_view", style=get_style_map()[1])],
        [create_btn(f"{sty('BACK')} 🔙", cb="back", style=get_style_map()[2])],
    ])
    if isinstance(target, Message):
        await target.answer(pe(text), parse_mode="HTML", reply_markup=kb)
    else:
        await target.message.answer(pe(text), parse_mode="HTML", reply_markup=kb)
        await target.answer()

async def _show_referral(target, uid: int):
    ref_code, ref_count, unlocked = get_referral_info(uid)
    bal, _ = get_wallet(uid)
    me = await bot.get_me()
    ref_link = f"https://t.me/{me.username}?start={ref_code}"

    text = (f"🤝 <b>YOUR REFERRAL PANEL</b>\n━━━━━━━━━━━━━━━━━━━━\n\n"
            f"🔗 Your Referral Link:\n<code>{ref_link}</code>\n\n"
            f"📊 <b>Stats:</b>\n"
            f"• Successful Referrals: <b>{ref_count}</b>\n"
            f"• Wallet Balance: <b>₹{bal:.2f}</b>\n"
            f"• Wallet: ✅ Buy karne ke liye ready\n\n"
            f"💰 <b>Rewards:</b>\n"
            f"• You earn <b>₹{REFERRAL_REWARD}</b> per referral\n"
            f"• New user gets <b>₹{WELCOME_BONUS}</b> welcome bonus\n\n"
            f"📌 Share your link — friends must join channel to activate reward!")

    b = InlineKeyboardBuilder()
    b.button(text="💚 My Wallet", callback_data="wallet_view")
    b.button(text="🔙 BACK",      callback_data="back")
    b.adjust(1)
    if isinstance(target, Message):
        await target.answer(pe(text), parse_mode="HTML", reply_markup=b.as_markup())
    else:
        await target.message.answer(pe(text), parse_mode="HTML", reply_markup=b.as_markup())
        await target.answer()

async def _send_admin_panel(target):
    u, t, p, d, r, refs, wused = get_stats()
    d_cnt, d_rev = get_daily_sales()
    text = (f"🔧 <b>ADMIN PANEL</b>\n━━━━━━━━━━━━━━━━━━━━\n\n"
            f"📊 <b>STATS</b>\n"
            f"👥 Users: {u}  |  📦 Orders: {t}\n"
            f"⏳ Pending: {p}  |  ✅ Done: {d}\n"
            f"💰 Revenue: ₹{r}  |  💚 Wallet Used: ₹{wused:.0f}\n"
            f"🤝 Referrals: {refs}\n\n"
            f"📅 <b>TODAY</b>\n"
            f"✅ Sales: {d_cnt}  |  ₹{d_rev}")
    try:
        _c = db()
        _t = sales_store.summary(_c, *sales_store.day_bounds())
        _a = sales_store.summary(_c)
        _c.close()
        text += (f"\n\n🧾 <b>SALES LOG</b>\n"
                 f"📅 Today: {_t['orders']} sold | ₹{_t['revenue']:,.0f} | 📈 profit ₹{_t['profit']:,.0f}\n"
                 f"♾️ All: {_a['orders']} sold | ₹{_a['revenue']:,.0f} | 📈 profit ₹{_a['profit']:,.0f}")
    except Exception:
        pass
    if isinstance(target, Message):
        await target.answer(pe(text), parse_mode="HTML", reply_markup=admin_kb())
    else:
        try:
            await target.message.edit_text(pe(text), parse_mode="HTML", reply_markup=admin_kb())
        except Exception:
            await target.message.answer(pe(text), parse_mode="HTML", reply_markup=admin_kb())
        await target.answer()

# ══════════════════════════════════════════════════
#  REFERRAL PROCESSING
# ══════════════════════════════════════════════════
async def _process_referral(referred_id: int, referrer_id: int):
    """Called when new user joins channel. Completes referral if valid."""
    ok = record_referral(referrer_id, referred_id)
    if not ok:
        return
    complete_referral_reward(referrer_id, referred_id)
    # Notify referrer
    newly_unlocked = _check_wallet_unlock(referrer_id)
    try:
        msg_to_referrer = (f"🎉 <b>Referral Completed!</b>\n\n"
                           f"User joined via your link.\n"
                           f"💚 You earned <b>₹{REFERRAL_REWARD}</b>!")
        if newly_unlocked:
            msg_to_referrer += "\n\n🔓 <b>WALLET UNLOCKED!</b> You can now use wallet for purchases."
        await bot.send_message(referrer_id, pe(msg_to_referrer), parse_mode="HTML")
    except Exception:
        pass

# ══════════════════════════════════════════════════
#  AUTO SERVICE DETECTION (silent if not found)
# ══════════════════════════════════════════════════
@dp.message(StateFilter(None), lambda m: not (m.text or "").startswith("/"))
async def auto_detect_service(msg: Message, state: FSMContext):
    uid  = msg.from_user.id
    text = msg.text or ""

    # Commands handled by their own handlers — ignore
    if text.startswith("/"):
        return

    # Admins: no auto-detect noise
    if is_admin(uid):
        return

    if is_user_banned(uid):
        return

    # ── One-time offline auto-reply (sent only ONCE per user) ──
    if not has_auto_replied(uid):
        mark_auto_replied(uid)
        offline_txt = get_setting("offline_msg")
        try:
            await msg.answer(pe(offline_txt), parse_mode="HTML")
        except Exception:
            pass

    # Not joined — prompt join
    if not await is_joined(uid):
        await msg.answer(
            pe("🔒 <b>Join our channel first!</b>\n\nRequired to use the bot."),
            parse_mode="HTML",
            reply_markup=join_kb()
        )
        return

    # Try to detect service
    found = detect_service(text)
    if found:
        await show_product_menu(msg, found)
    elif re.search(r"\brefund\b", text.lower()):
        await msg.answer(pe(REFUND_MSG), parse_mode="HTML", reply_markup=main_kb())
    # else: SILENT — do not send "service not found" for normal conversation

    # ── QR Deposit Detection ────────────────────────
    low = text.lower().strip()

    # Bare "qr" / "qr code" / "qr banao" (no amount) -> amount poocho
    if re.fullmatch(r"/?qr(?:\s*code)?(?:\s*(?:banao|bana|do|chahiye|send|dedo))?", low):
        await ask_qr_amount(msg, state)
        return

    # "qr 20", "qr 20rs", "qr20rs", "qr 20 rupees" ...
    qr_match = re.search(r"\bqr\s*(\d+(?:\.\d+)?)\s*(?:rs|₹|rupees|rps|rupe)?\b", low)
    if qr_match:
        await send_qr_deposit(msg, qr_match.group(1))
        return

# ══════════════════════════════════════════════════
#  SCREENSHOT HANDLER
# ══════════════════════════════════════════════════
@dp.message(OrderState.waiting_for_screenshot)
async def screenshot_handler(msg: Message, state: FSMContext):
    if not msg.photo:
        await msg.answer(pe("❌ <b>Please send a screenshot image.</b>"), parse_mode="HTML")
        return

    data         = await state.get_data()
    oid          = data.get("oid")
    product_key  = data.get("product_key")
    duration     = data.get("duration")
    amount       = data.get("amount")
    product_name = PRODUCTS.get(product_key, {}).get("display_name", "Unknown")

    card = payment_review_card(
        msg.from_user, amount, oid,
        purpose="product",
        product_name=product_name,
        duration=duration,
        ref_label="Order ID",
    )
    for admin_id in admin_ids():
        try:
            await bot.send_photo(
                admin_id,
                photo=msg.photo[-1].file_id,
                caption=card,
                parse_mode="HTML",
                reply_markup=payment_decision_kb(oid, amount, msg.from_user.id)
            )
        except Exception:
            pass

    await msg.answer(
        pe(get_setting("payment_pending_msg")),
        parse_mode="HTML", reply_markup=main_kb()
    )
    await state.clear()

# ══════════════════════════════════════════════════
#  DEPOSIT SCREENSHOT HANDLER
# ══════════════════════════════════════════════════
@dp.message(DepositState.waiting_for_screenshot)
async def deposit_screenshot_handler(msg: Message, state: FSMContext):
    if not msg.photo:
        await msg.answer(pe("❌ <b>Screenshot bhejiye (photo).</b>"), parse_mode="HTML")
        return

    data      = await state.get_data()
    dep_id    = data.get("dep_id")
    amount    = data.get("dep_amount")

    card = payment_review_card(
        msg.from_user, amount, dep_id,
        purpose="wallet",
        ref_label="Deposit ID",
    )
    for admin_id in admin_ids():
        try:
            await bot.send_photo(
                admin_id,
                photo=msg.photo[-1].file_id,
                caption=card,
                parse_mode="HTML",
                reply_markup=deposit_decision_kb(dep_id, amount, msg.from_user.id)
            )
        except Exception:
            pass

    await msg.answer(
        pe("⏳ <b>Screenshot mil gaya!</b>\n\n"
           "🔍 Admin verify kar raha hai.\n"
           "✅ Approve hone par wallet mein amount add ho jayega.\n\n"
           "📩 Update: @AVERON_DEALS_OFFICIAL"),
        parse_mode="HTML",
        reply_markup=main_kb()
    )
    await state.clear()

# ══════════════════════════════════════════════════
#  REVIEW HANDLER
# ══════════════════════════════════════════════════
@dp.message(OrderState.waiting_for_review)
async def review_handler(msg: Message, state: FSMContext):
    data   = await state.get_data()
    oid    = data.get("oid")
    rating = data.get("rating", 0)

    if msg.text and msg.text.strip().lower() in ("/skip", "skip"):
        await msg.answer(pe("✅ <b>Thank you! Come back soon!</b> 🎉"), parse_mode="HTML")
    else:
        update_rating(oid, rating, msg.text or "")
        await msg.answer(
            pe(f"✅ <b>Thank you for your feedback!</b> {'⭐'*rating}\n"
               f"━━━━━━━━━━━━━━━━━━━━\n\n<i>\"{msg.text}\"</i>\n\nCome back soon! 🎉"),
            parse_mode="HTML"
        )
    await state.clear()

# ══════════════════════════════════════════════════
#  ADMIN TEXT HANDLERS
# ══════════════════════════════════════════════════
@dp.message(AdminState.waiting_for_user_id)
async def admin_user_handler(msg: Message, state: FSMContext):
    if not is_admin(msg.from_user.id):
        await state.clear()
        return
    try:
        uid = int(msg.text.strip())
    except Exception:
        await msg.answer(pe("❌ <b>Invalid ID</b>"), parse_mode="HTML")
        await state.clear()
        return

    fsm    = await state.get_data()
    action = fsm.get("admin_action")

    # ── Mini app subscription actions ──────────────
    if action == "minisubs_check":
        sub = get_mini_app_sub(uid)
        if sub:
            await msg.answer(pe(f"📺 <b>Active Sub</b>\n\n👤 <code>{uid}</code>\n✅ Plan: {sub[0]}\n📅 Expires: {sub[1][:10]}"), parse_mode="HTML")
        else:
            await msg.answer(pe(f"❌ User <code>{uid}</code> ka koi active subscription nahi hai."), parse_mode="HTML")
        await state.clear()
        return

    if action == "minisubs_revoke":
        conn = db()
        c = conn.cursor()
        c.execute("UPDATE mini_app_subscriptions SET is_active=0 WHERE user_id=?", (uid,))
        changed = c.rowcount
        conn.commit()
        conn.close()
        await msg.answer(
            pe(f"✅ <b>{changed} subscription(s) revoked</b> for <code>{uid}</code>." if changed
               else f"ℹ️ User <code>{uid}</code> ka koi active sub nahi tha."), parse_mode="HTML")
        if changed:
            try:
                await bot.send_message(uid, pe("📺 <b>Aapka 𝗔𝗩𝗘𝗥𝗢𝗡 𝗗𝗘𝗔𝗟𝗦 TV subscription band kar diya gaya hai.</b>"), parse_mode="HTML")
            except Exception:
                pass
        await state.clear()
        return

    user, orders = get_user_full(uid)
    if not user:
        await msg.answer(pe(f"❌ <b>User {uid} not found</b>"), parse_mode="HTML")
        await state.clear()
        return

    total   = len(orders)
    done    = sum(1 for o in orders if o[4]=="delivered")
    rej     = sum(1 for o in orders if o[4]=="rejected")
    pend    = sum(1 for o in orders if o[4]=="pending")
    spent   = user[8] if len(user) > 8 else 0
    bal     = user[9] if len(user) > 9 else 0
    ref_cnt = user[12] if len(user) > 12 else 0

    text  = f"👤 <b>USER KUNDALI</b>\n━━━━━━━━━━━━━━━━━━━━\n"
    text += f"🆔 ID: <code>{user[0]}</code>\n"
    text += f"👤 @{user[1] or 'N/A'}  |  📛 {user[2] or 'N/A'}\n"
    text += f"📅 Joined: {user[3]}\n"
    text += f"🕐 Active: {user[4]}\n"
    text += f"━━━━━━━━━━━━━━━━━━━━\n"
    text += f"📦 Orders: {total}  (✅{done} ❌{rej} ⏳{pend})\n"
    text += f"💰 Spent: ₹{spent}  |  💚 Wallet: ₹{bal:.2f}\n"
    text += f"🤝 Referrals: {ref_cnt}\n"
    text += f"━━━━━━━━━━━━━━━━━━━━\n"
    text += f"📋 LAST ORDERS:\n"
    for o in orders[:5]:
        em = "✅" if o[4]=="delivered" else ("⏳" if o[4]=="pending" else "❌")
        text += f"{em} <code>{o[0]}</code> — {o[1]} ({o[2]}) — ₹{o[3]}\n"
    text += f"━━━━━━━━━━━━━━━━━━━━\n"
    text += f"🚫 STATUS: <b>{'BANNED' if user[5] else 'ACTIVE'}</b>"
    if user[5] and len(user) > 6 and user[6]:
        text += f"\n📌 Reason: {user[6]}"

    rows = []
    if can_manage_wallet(msg.from_user.id):
        rows.append([
            create_btn("➕ ADD BALANCE", cb=f"wquick_add_{uid}", style=ButtonStyle.SUCCESS),
            create_btn("➖ DEDUCT", cb=f"wquick_ded_{uid}", style=ButtonStyle.DANGER),
        ])
    if user[5] == 0:
        rows.append([create_btn("🔨 BAN USER", cb=f"ban_now_{uid}", style=ButtonStyle.DANGER)])
    else:
        rows.append([create_btn("🔓 UNBAN USER", cb=f"unban_{uid}", style=ButtonStyle.SUCCESS)])
    rows.append([create_btn("📞 CONTACT", cb=f"contact_{uid}")])
    kb = InlineKeyboardMarkup(inline_keyboard=rows)

    await msg.answer(pe(text), parse_mode="HTML", reply_markup=kb)
    await state.clear()

# ══════════════════════════════════════════════════
#  BROADCAST ENGINE v2 (audience targeting + any media)
# ══════════════════════════════════════════════════
AUDIENCE_LABELS = {
    "all":       "👥 All users",
    "buyers":    "🛒 Buyers only",
    "nonbuyers": "🕊️ Never bought",
    "active":    "🔥 Active last 7 days",
    "wallet":    "💚 Wallet balance > 0",
    "subs":      "📺 Mini app subscribers",
}

def get_broadcast_targets(audience: str) -> list[int]:
    """Return user IDs for the chosen audience (banned users always excluded)."""
    conn = db()
    c = conn.cursor()
    if audience == "buyers":
        q = """SELECT DISTINCT u.user_id FROM users u
               JOIN orders o ON o.user_id = u.user_id
               WHERE u.is_banned=0"""
    elif audience == "nonbuyers":
        q = """SELECT u.user_id FROM users u
               WHERE u.is_banned=0
                 AND u.user_id NOT IN (SELECT user_id FROM orders)"""
    elif audience == "active":
        q = """SELECT user_id FROM users
               WHERE is_banned=0 AND last_active >= datetime('now','-7 days')"""
    elif audience == "wallet":
        q = "SELECT user_id FROM users WHERE is_banned=0 AND wallet_balance > 0"
    elif audience == "subs":
        q = """SELECT DISTINCT u.user_id FROM users u
               JOIN mini_app_subscriptions m ON m.user_id = u.user_id
               WHERE u.is_banned=0 AND m.is_active=1"""
    else:
        q = "SELECT user_id FROM users WHERE is_banned=0"
    try:
        c.execute(q)
        rows = [r[0] for r in c.fetchall()]
    except Exception:
        rows = []
    conn.close()
    return rows

@dp.message(BroadcastState.waiting_content)
async def broadcast_content_handler(msg: Message, state: FSMContext):
    """Accepts ANY message (text, photo, video, document, sticker…) as broadcast content."""
    if not is_admin(msg.from_user.id):
        await state.clear()
        return
    data     = await state.get_data()
    audience = data.get("bc_audience", "all")
    targets  = get_broadcast_targets(audience)
    await state.update_data(bc_chat_id=msg.chat.id, bc_msg_id=msg.message_id, bc_count=len(targets))

    await msg.answer(
        pe(f"👀 <b>BROADCAST PREVIEW</b>\n━━━━━━━━━━━━━━━━━━━━\n\n"
           f"🎯 Audience: <b>{AUDIENCE_LABELS.get(audience, audience)}</b>\n"
           f"👥 Recipients: <b>{len(targets)}</b>\n"
           f"📨 Content: the message you just sent (upar wala)\n\n"
           f"Bhejne ke liye <b>SEND NOW</b> dabaiye."),
        parse_mode="HTML",
        reply_markup=broadcast_confirm_kb()
    )
    await state.set_state(BroadcastState.waiting_confirm)

async def run_broadcast(admin_msg: Message, chat_id: int, msg_id: int, targets: list[int], audience: str):
    sent = failed = blocked = 0
    status = await admin_msg.answer(
        pe(f"📢 <b>Broadcasting…</b>\n0 / {len(targets)}"), parse_mode="HTML"
    )
    for i, u in enumerate(targets, 1):
        try:
            await bot.copy_message(chat_id=u, from_chat_id=chat_id, message_id=msg_id)
            sent += 1
        except Exception as e:
            txt = str(e).lower()
            if "blocked" in txt or "chat not found" in txt or "deactivated" in txt:
                blocked += 1
            else:
                failed += 1
        await asyncio.sleep(0.05)
        if i % 25 == 0 or i == len(targets):
            try:
                await status.edit_text(
                    pe(f"📢 <b>Broadcasting…</b>\n{i} / {len(targets)}\n"
                       f"✅ {sent}  🚫 {blocked}  ⚠️ {failed}"),
                    parse_mode="HTML"
                )
            except Exception:
                pass
    try:
        await status.edit_text(
            pe(f"✅ <b>BROADCAST COMPLETE</b>\n━━━━━━━━━━━━━━━━━━━━\n\n"
               f"🎯 Audience: <b>{AUDIENCE_LABELS.get(audience, audience)}</b>\n"
               f"👥 Total: {len(targets)}\n"
               f"✅ Delivered: <b>{sent}</b>\n"
               f"🚫 Blocked bot: {blocked}\n"
               f"⚠️ Failed: {failed}"),
            parse_mode="HTML"
        )
    except Exception:
        pass

# ══════════════════════════════════════════════════
#  ADMIN WALLET MANAGEMENT (give / take balance)
# ══════════════════════════════════════════════════
def _parse_wallet_input(text: str):
    """Parse '123456789 50 optional reason' -> (uid, amount, reason)."""
    parts = (text or "").strip().split()
    if len(parts) < 2:
        return None, None, ""
    try:
        uid = int(parts[0])
        amount = float(parts[1])
    except Exception:
        return None, None, ""
    if amount <= 0:
        return None, None, ""
    reason = " ".join(parts[2:]).strip()
    return uid, amount, reason

@dp.message(WalletAdminState.waiting_add)
async def wadm_add_handler(msg: Message, state: FSMContext):
    if not can_manage_wallet(msg.from_user.id):
        await state.clear()
        return
    uid, amount, reason = _parse_wallet_input(msg.text)
    if not uid:
        await msg.answer(pe("❌ Format galat.\n\nUse: <code>USER_ID AMOUNT reason</code>\nExample: <code>123456789 50 bonus</code>"), parse_mode="HTML")
        return
    u, _ = get_user_full(uid)
    if not u:
        await msg.answer(pe(f"❌ User <code>{uid}</code> bot me nahi mila."), parse_mode="HTML")
        await state.clear()
        return
    credit_wallet(uid, amount, reason or f"Admin credit by {msg.from_user.id}")
    bal, _ = get_wallet(uid)
    await msg.answer(
        pe(f"✅ <b>₹{amount:.0f} ADDED</b>\n━━━━━━━━━━━━━━━━━━━━\n\n"
           f"👤 User: <code>{uid}</code>\n💚 New Balance: <b>₹{bal:.2f}</b>\n"
           f"📝 Reason: {reason or 'Admin credit'}"),
        parse_mode="HTML"
    )
    try:
        await bot.send_message(
            uid,
            pe(f"💚 <b>WALLET CREDITED</b>\n━━━━━━━━━━━━━━━━━━━━\n\n"
               f"➕ ₹{amount:.0f} aapke wallet me add kiya gaya.\n"
               f"💰 New Balance: <b>₹{bal:.2f}</b>\n"
               f"📝 {reason or 'Admin credit'}"),
            parse_mode="HTML"
        )
    except Exception:
        pass
    await state.clear()

@dp.message(WalletAdminState.waiting_deduct)
async def wadm_deduct_handler(msg: Message, state: FSMContext):
    if not can_manage_wallet(msg.from_user.id):
        await state.clear()
        return
    uid, amount, reason = _parse_wallet_input(msg.text)
    if not uid:
        await msg.answer(pe("❌ Format galat.\n\nUse: <code>USER_ID AMOUNT reason</code>"), parse_mode="HTML")
        return
    ok = debit_wallet(uid, amount, reason or f"Admin debit by {msg.from_user.id}")
    bal, _ = get_wallet(uid)
    if not ok:
        await msg.answer(pe(f"❌ <b>Insufficient balance.</b>\n\n👤 <code>{uid}</code>\n💚 Current: ₹{bal:.2f}"), parse_mode="HTML")
        await state.clear()
        return
    await msg.answer(
        pe(f"✅ <b>₹{amount:.0f} DEDUCTED</b>\n━━━━━━━━━━━━━━━━━━━━\n\n"
           f"👤 User: <code>{uid}</code>\n💚 New Balance: <b>₹{bal:.2f}</b>\n"
           f"📝 Reason: {reason or 'Admin debit'}"),
        parse_mode="HTML"
    )
    try:
        await bot.send_message(
            uid,
            pe(f"➖ <b>WALLET DEBITED</b>\n━━━━━━━━━━━━━━━━━━━━\n\n"
               f"₹{amount:.0f} wallet se kaata gaya.\n"
               f"💰 New Balance: <b>₹{bal:.2f}</b>\n"
               f"📝 {reason or 'Admin debit'}"),
            parse_mode="HTML"
        )
    except Exception:
        pass
    await state.clear()

@dp.message(WalletAdminState.waiting_check)
async def wadm_check_handler(msg: Message, state: FSMContext):
    if not can_manage_wallet(msg.from_user.id):
        await state.clear()
        return
    try:
        uid = int((msg.text or "").strip())
    except Exception:
        await msg.answer(pe("❌ Sirf user ID bhejiye."), parse_mode="HTML")
        return
    u, _ = get_user_full(uid)
    if not u:
        await msg.answer(pe(f"❌ User <code>{uid}</code> not found."), parse_mode="HTML")
        await state.clear()
        return
    bal, _ = get_wallet(uid)
    hist = get_wallet_history(uid, 8)
    text = (f"💳 <b>USER WALLET</b>\n━━━━━━━━━━━━━━━━━━━━\n\n"
            f"👤 <code>{uid}</code> @{u[1] or 'N/A'}\n"
            f"💰 Balance: <b>₹{bal:.2f}</b>\n\n🧾 <b>Last transactions:</b>\n")
    if hist:
        for amt, typ, reason, date in hist:
            em = "➕" if typ == "credit" else "➖"
            text += f"{em} ₹{amt:.2f} — {reason[:28]} ({date[5:16]})\n"
    else:
        text += "No transactions yet.\n"
    b = InlineKeyboardBuilder()
    b.button(text="➕ ADD BALANCE", callback_data=f"wquick_add_{uid}")
    b.button(text="➖ DEDUCT",      callback_data=f"wquick_ded_{uid}")
    b.button(text="🔙 BACK",        callback_data="admin_wallet")
    b.adjust(2)
    await msg.answer(pe(text), parse_mode="HTML", reply_markup=b.as_markup())
    await state.clear()

@dp.message(AdminState.waiting_for_stock_update)
async def stock_update_handler(msg: Message, state: FSMContext):
    data        = await state.get_data()
    product_key = data.get("stock_product_key")
    try:
        parts    = msg.text.split("|")
        duration = parts[0].strip()
        new_stk  = int(parts[1].strip())
        update_stock_db(product_key, duration, new_stk)
        pname = PRODUCTS.get(product_key, {}).get("display_name", product_key)
        await msg.answer(pe(f"✅ <b>{pname}</b>\n• {duration}: {new_stk} units"), parse_mode="HTML")
    except Exception:
        await msg.answer(pe("❌ <b>Invalid format.</b>\n\nUse: <code>duration|new_stock</code>\nExample: <code>1 Month|50</code>"), parse_mode="HTML")
    await state.clear()

@dp.message(AdminState.waiting_for_ban_reason)
async def ban_reason_handler(msg: Message, state: FSMContext):
    data   = await state.get_data()
    uid    = data.get("ban_user_id")
    reason = msg.text.strip()
    if uid:
        ban_user(uid, reason)
        await msg.answer(pe(f"✅ <b>User {uid} BANNED!</b>\n\nReason: {reason}"), parse_mode="HTML")
        try:
            await bot.send_message(uid, pe(f"🚫 <b>You have been banned.</b>\n\nReason: {reason}\n\nContact: @AVERON_DEALS"), parse_mode="HTML")
        except Exception:
            pass
    await state.clear()

# ── Admin-manage states ────────────────────────────
@dp.message(AdminManageState.waiting_add_id)
async def admin_add_id_handler(msg: Message, state: FSMContext):
    if msg.from_user.id != OWNER_ID:
        await state.clear()
        return
    try:
        target = int(msg.text.strip())
    except Exception:
        await msg.answer(pe("❌ <b>Invalid ID</b>"), parse_mode="HTML")
        await state.clear()
        return
    if get_admin_role(target):
        await msg.answer(pe(f"ℹ️ User <code>{target}</code> is already an admin."), parse_mode="HTML")
        await state.clear()
        return
    await state.update_data(new_admin_id=target)
    await msg.answer(pe(f"👤 <b>User {target}</b>\n\nSelect role:"), parse_mode="HTML", reply_markup=role_select_kb(target))
    await state.clear()  # role is handled via callback

@dp.message(AdminManageState.waiting_remove)
async def admin_remove_handler(msg: Message, state: FSMContext):
    if msg.from_user.id != OWNER_ID:
        await state.clear()
        return
    try:
        target = int(msg.text.strip())
    except Exception:
        await msg.answer(pe("❌ <b>Invalid ID</b>"), parse_mode="HTML")
        await state.clear()
        return
    if target == OWNER_ID:
        await msg.answer(pe("⛔ Cannot remove owner."), parse_mode="HTML")
        await state.clear()
        return
    conn = db()
    c = conn.cursor()
    c.execute("DELETE FROM admins WHERE user_id=?", (target,))
    conn.commit()
    conn.close()
    await msg.answer(pe(f"✅ <b>Admin {target} removed.</b>"), parse_mode="HTML")
    await state.clear()

# ── Service-add FSM ────────────────────────────────
@dp.message(ServiceAddState.name)
async def svc_add_name(msg: Message, state: FSMContext):
    await state.update_data(svc_name=msg.text.strip())
    await msg.answer(pe("📝 <b>Send service description:</b>"), parse_mode="HTML")
    await state.set_state(ServiceAddState.description)

@dp.message(ServiceAddState.description)
async def svc_add_desc(msg: Message, state: FSMContext):
    await state.update_data(svc_desc=msg.text.strip())
    await msg.answer(pe("🔑 <b>Send keywords (comma-separated):</b>\n\nExample: <code>cooler,cool,clr</code>"), parse_mode="HTML")
    await state.set_state(ServiceAddState.keywords)

@dp.message(ServiceAddState.keywords)
async def svc_add_kw(msg: Message, state: FSMContext):
    await state.update_data(svc_kw=msg.text.strip())
    await msg.answer(pe("⏱️ <b>Send durations (one per line):</b>\n\nExample:\n<code>1 Month\n3 Months\n6 Months</code>"), parse_mode="HTML")
    await state.set_state(ServiceAddState.durations)

@dp.message(ServiceAddState.durations)
async def svc_add_dur(msg: Message, state: FSMContext):
    durs = [d.strip() for d in msg.text.strip().split("\n") if d.strip()]
    await state.update_data(svc_durs=durs)
    await msg.answer(
        pe(f"💰 <b>Send prices for {len(durs)} durations (one per line):</b>\n\n"
           + "\n".join(f"  {d}:" for d in durs)),
        parse_mode="HTML"
    )
    await state.set_state(ServiceAddState.prices)

@dp.message(ServiceAddState.prices)
async def svc_add_prices(msg: Message, state: FSMContext):
    data = await state.get_data()
    durs = data.get("svc_durs", [])
    price_lines = [p.strip() for p in msg.text.strip().split("\n") if p.strip()]
    if len(price_lines) != len(durs):
        await msg.answer(pe(f"❌ Need exactly {len(durs)} prices."), parse_mode="HTML")
        return
    try:
        prices = [int(p) for p in price_lines]
    except Exception:
        await msg.answer(pe("❌ Invalid prices. Enter numbers only."), parse_mode="HTML")
        return
    await state.update_data(svc_prices=prices)
    await msg.answer(
        pe(f"📦 <b>Send stocks for {len(durs)} durations (one per line):</b>"),
        parse_mode="HTML"
    )
    await state.set_state(ServiceAddState.stocks)

@dp.message(ServiceAddState.stocks)
async def svc_add_stocks(msg: Message, state: FSMContext):
    data = await state.get_data()
    durs   = data.get("svc_durs", [])
    prices = data.get("svc_prices", [])
    stock_lines = [s.strip() for s in msg.text.strip().split("\n") if s.strip()]
    if len(stock_lines) != len(durs):
        await msg.answer(pe(f"❌ Need exactly {len(durs)} stock values."), parse_mode="HTML")
        return
    try:
        stocks = [int(s) for s in stock_lines]
    except Exception:
        await msg.answer(pe("❌ Invalid stocks. Enter numbers only."), parse_mode="HTML")
        return

    # Build service key from name (lowercase, no spaces/special chars)
    svc_name = data.get("svc_name", "")
    svc_key  = re.sub(r"[^a-z0-9]", "", svc_name.lower())[:20]
    if not svc_key:
        svc_key = f"svc{random.randint(1000,9999)}"

    conn = db()
    c = conn.cursor()
    # Ensure unique key
    orig_key = svc_key
    i = 1
    while True:
        c.execute("SELECT service_key FROM services WHERE service_key=?", (svc_key,))
        if not c.fetchone():
            break
        svc_key = f"{orig_key}{i}"
        i += 1

    c.execute("INSERT INTO services (service_key, display_name, description, keywords, is_active) VALUES (?,?,?,?,1)",
              (svc_key, svc_name, data.get("svc_desc",""), data.get("svc_kw","")))
    for dur, price, stk in zip(durs, prices, stocks):
        c.execute("INSERT INTO service_prices (service_key, duration, price, stock) VALUES (?,?,?,?)",
                  (svc_key, dur, price, stk))
    conn.commit()
    conn.close()
    reload_products()

    await msg.answer(
        pe(f"✅ <b>Service Added!</b>\n━━━━━━━━━━━━━━━━━━━━\n\n"
           f"🔑 Key: <code>{svc_key}</code>\n"
           f"📛 Name: {svc_name}\n"
           f"⏱️ Durations: {len(durs)}\n\n"
           f"Service is now live!"),
        parse_mode="HTML"
    )
    await state.clear()

# ══════════════════════════════════════════════════
#  CALLBACK QUERY HANDLER
# ══════════════════════════════════════════════════
# ══════════════════════════════════════════════════
#  🔌 SUPPLIER API PANEL (CouponShops / Acczone / EliteToolz)
#  Admin: API ON/OFF, balance, catalog, product mapping,
#  cost + markup (profit), bulk price change, auto-delivery, profit report.
# ══════════════════════════════════════════════════
class SupplierState(StatesGroup):
    add_map  = State()
    cost     = State()
    markup   = State()
    bulk     = State()
    rate     = State()

SUP_CAT_PER_PAGE = 15

def sup_on(provider: str) -> bool:
    return get_setting(f"sup_on_{provider}", "1") == "1"

def sup_auto_global() -> bool:
    return get_setting("sup_auto_global", "0") == "1"

def sup_usd_rate() -> float:
    try:
        return float(get_setting("sup_usd_rate", "88") or 88)
    except ValueError:
        return 88.0

def _sup_back(cb_data="sup_home"):
    b = InlineKeyboardBuilder()
    b.button(text="🔙 BACK", callback_data=cb_data)
    return b.as_markup()

def sup_home_kb():
    b = InlineKeyboardBuilder()
    for p in supplier_store.PROVIDER_IDS:
        st = "🟢 ON" if sup_on(p) else "🔴 OFF"
        key = "" if supplier_clients.key_configured(p) else " ⚠️ no key"
        b.button(text=f"{st} — {supplier_clients.PROVIDER_LABELS[p]}{key}", callback_data=f"sup_p_{p}")
    b.button(text=("🤖 AUTO DELIVERY: ON" if sup_auto_global() else "🤖 AUTO DELIVERY: OFF"),
             callback_data="sup_autoglobal")
    b.button(text="🔗 PRODUCT MAPPING + PROFIT", callback_data="sup_maps")
    b.button(text="💹 BULK PRICE + / -",         callback_data="sup_bulk")
    b.button(text=f"💱 USD RATE: ₹{sup_usd_rate():g}", callback_data="sup_rate")
    b.button(text="📊 PROFIT REPORT",            callback_data="sup_profit")
    b.button(text="🧾 SUPPLIER ORDERS",          callback_data="sup_orders")
    b.button(text="🔙 BACK",                     callback_data="admin_back")
    b.adjust(1)
    return b.as_markup()

def sup_home_text():
    return ("🔌 <b>SUPPLIER API PANEL</b>\n━━━━━━━━━━━━━━━━━━━━\n\n"
            "• Har API ko alag ON/OFF karo\n"
            "• Product mapping me supplier cost + aapka profit (₹ ya %) set karo\n"
            "• AUTO DELIVERY ON + mapping AUTO ON ho tabhi payment ke baad supplier se order lagega\n"
            "• Loss guard: selling price cost se kam ho to auto order nahi lagega\n\n"
            f"🤖 Auto delivery: <b>{'ON' if sup_auto_global() else 'OFF'}</b>")

def sup_provider_kb(p):
    b = InlineKeyboardBuilder()
    b.button(text=("🔴 TURN OFF" if sup_on(p) else "🟢 TURN ON"), callback_data=f"sup_tog_{p}")
    b.button(text="💰 CHECK BALANCE", callback_data=f"sup_bal_{p}")
    b.button(text="📚 VIEW CATALOG",  callback_data=f"sup_cat_{p}_0")
    b.button(text="🔙 BACK",          callback_data="sup_home")
    b.adjust(1)
    return b.as_markup()

def _map_line(m):
    price = PRODUCTS.get(m["service_key"], {}).get("prices", {}).get(m["duration"])
    cost_inr = supplier_store.cost_in_inr(m["cost"], m["cost_currency"], sup_usd_rate())
    profit = (price - cost_inr) if price is not None else None
    mk = f"{m['markup_value']:g}%" if m["markup_type"] == "percent" else f"₹{m['markup_value']:g}"
    return (f"{'🤖' if m['auto_fulfill'] else '✋'} {m['service_key']} / {m['duration']} → "
            f"{supplier_clients.PROVIDER_LABELS.get(m['provider'], m['provider'])} #{m['product_id']}\n"
            f"   cost ₹{cost_inr:.2f} | markup {mk} | price {('₹'+str(price)) if price is not None else 'n/a'}"
            f" | profit {('₹%.2f' % profit) if profit is not None else 'n/a'}")

def sup_maps_kb():
    conn = db(); maps = supplier_store.list_mappings(conn); conn.close()
    b = InlineKeyboardBuilder()
    for m in maps[:40]:
        b.button(text=f"{'🤖' if m['auto_fulfill'] else '✋'} {m['service_key']} / {m['duration']}",
                 callback_data=f"sup_m_{m['id']}")
    b.button(text="➕ ADD / UPDATE MAPPING", callback_data="sup_madd")
    b.button(text="🔙 BACK", callback_data="sup_home")
    b.adjust(1)
    return b.as_markup(), maps

def sup_map_kb(m):
    i = m["id"]
    b = InlineKeyboardBuilder()
    b.button(text=("🤖 AUTO: ON (tap OFF)" if m["auto_fulfill"] else "✋ AUTO: OFF (tap ON)"),
             callback_data=f"sup_mauto_{i}")
    b.button(text="💵 SET SUPPLIER COST", callback_data=f"sup_mcost_{i}")
    b.button(text="📈 SET PROFIT / MARKUP", callback_data=f"sup_mmk_{i}")
    b.button(text="✅ APPLY PRICE = COST + PROFIT", callback_data=f"sup_mapply_{i}")
    b.button(text="🗑️ DELETE MAPPING", callback_data=f"sup_mdel_{i}")
    b.button(text="🔙 BACK", callback_data="sup_maps")
    b.adjust(1)
    return b.as_markup()

async def _sup_edit(cb: CallbackQuery, text: str, kb=None):
    try:
        await cb.message.edit_text(pe(text), parse_mode="HTML", reply_markup=kb)
    except Exception:
        await cb.message.answer(pe(text), parse_mode="HTML", reply_markup=kb)

def _admin_ids():
    conn = db(); c = conn.cursor()
    c.execute("SELECT user_id FROM admins")
    ids = [r[0] for r in c.fetchall()]
    conn.close()
    return ids

async def _notify_admins(text: str):
    for adm in _admin_ids():
        try:
            await bot.send_message(adm, pe(text), parse_mode="HTML")
        except Exception:
            pass

async def supplier_cb(cb: CallbackQuery, state: FSMContext):
    data, uid = cb.data, cb.from_user.id
    if not can_manage_services(uid):
        await cb.answer("⛔ No permission", show_alert=True)
        return
    await cb.answer()

    if data == "sup_home":
        await state.clear()
        await _sup_edit(cb, sup_home_text(), sup_home_kb()); return

    if data == "sup_autoglobal":
        set_setting("sup_auto_global", "0" if sup_auto_global() else "1")
        await _sup_edit(cb, sup_home_text(), sup_home_kb()); return

    if data.startswith("sup_p_"):
        p = data[6:]
        if p not in supplier_store.PROVIDER_IDS: return
        env = supplier_clients.PROVIDERS[p].API_KEY_ENV
        txt = (f"🔌 <b>{supplier_clients.PROVIDER_LABELS[p]}</b>\n━━━━━━━━━━━━━━━━━━━━\n\n"
               f"Status: <b>{'ON' if sup_on(p) else 'OFF'}</b>\n"
               f"API key: <b>{'configured ✅' if supplier_clients.key_configured(p) else f'missing ⚠️ (.env me {env} daalo)'}</b>")
        await _sup_edit(cb, txt, sup_provider_kb(p)); return

    if data.startswith("sup_tog_"):
        p = data[8:]
        if p not in supplier_store.PROVIDER_IDS: return
        set_setting(f"sup_on_{p}", "0" if sup_on(p) else "1")
        cb.data = f"sup_p_{p}"
        await supplier_cb(cb, state); return

    if data.startswith("sup_bal_"):
        p = data[8:]
        if p not in supplier_store.PROVIDER_IDS: return
        try:
            resp = await supplier_clients.fetch_balance(p)
            txt = f"💰 <b>{supplier_clients.PROVIDER_LABELS[p]} balance:</b> <code>{html.escape(supplier_store.balance_text(resp))}</code>"
        except (supplier_clients.SupplierAPIError, ValueError) as e:
            txt = f"❌ {html.escape(str(e))}"
        await _sup_edit(cb, txt, _sup_back(f"sup_p_{p}")); return

    if data.startswith("sup_cat_"):
        p, _, pg = data[8:].rpartition("_")
        if p not in supplier_store.PROVIDER_IDS: return
        page = int(pg) if pg.isdigit() else 0
        try:
            items = supplier_store.catalog_items(await supplier_clients.fetch_catalog(p))
        except (supplier_clients.SupplierAPIError, ValueError) as e:
            await _sup_edit(cb, f"❌ {html.escape(str(e))}", _sup_back(f"sup_p_{p}")); return
        chunk = items[page*SUP_CAT_PER_PAGE:(page+1)*SUP_CAT_PER_PAGE]
        lines = [f"<code>{html.escape(i['id'])}</code> — {html.escape(i['name'])}"
                 f" | {html.escape(str(i['price']))} | stock {html.escape(str(i['stock']))}" for i in chunk]
        txt = (f"📚 <b>{supplier_clients.PROVIDER_LABELS[p]} CATALOG</b> ({len(items)} items, page {page+1})\n"
               "id — name | price | stock\n━━━━━━━━━━━━━━━━━━━━\n" + ("\n".join(lines) or "Koi item nahi mila"))
        b = InlineKeyboardBuilder()
        if page > 0: b.button(text="⬅️ Prev", callback_data=f"sup_cat_{p}_{page-1}")
        if (page+1)*SUP_CAT_PER_PAGE < len(items): b.button(text="Next ➡️", callback_data=f"sup_cat_{p}_{page+1}")
        b.button(text="🔙 BACK", callback_data=f"sup_p_{p}")
        b.adjust(2, 1)
        await _sup_edit(cb, txt[:4000], b.as_markup()); return

    if data == "sup_maps":
        await state.clear()
        kb, maps = sup_maps_kb()
        body = "\n\n".join(html.escape(_map_line(m)) for m in maps[:25]) or "Abhi koi mapping nahi hai."
        await _sup_edit(cb, f"🔗 <b>PRODUCT MAPPING</b>\n━━━━━━━━━━━━━━━━━━━━\n\n{body}"[:4000], kb); return

    if data == "sup_madd":
        await state.set_state(SupplierState.add_map)
        keys = ", ".join(f"<code>{html.escape(k)}</code>" for k in list(PRODUCTS.keys())[:30])
        await _sup_edit(cb,
            "➕ <b>ADD / UPDATE MAPPING</b>\n\nEk line bhejo:\n"
            "<code>service_key | duration | provider | product_id | cost | INR/USD</code>\n\n"
            "Example:\n<code>netflix | 1 Month | couponshops | 123 | 150 | INR</code>\n\n"
            "provider: couponshops / acczone / elitetoolz\n"
            f"Service keys: {keys}\n\n/cancel se band karo", _sup_back("sup_maps")); return

    if data.startswith(("sup_m_", "sup_mauto_", "sup_mcost_", "sup_mmk_", "sup_mapply_", "sup_mdel_")):
        action, _, sid = data.rpartition("_")
        if not sid.isdigit(): return
        mid = int(sid)
        conn = db(); m = supplier_store.get_mapping(conn, mid)
        if not m:
            conn.close(); await _sup_edit(cb, "⚠️ Mapping nahi mila", _sup_back("sup_maps")); return
        if action == "sup_mauto":
            supplier_store.update_mapping(conn, mid, auto_fulfill=0 if m["auto_fulfill"] else 1)
        elif action == "sup_mdel":
            supplier_store.delete_mapping(conn, mid); conn.close()
            await _sup_edit(cb, "🗑️ Mapping delete ho gaya", _sup_back("sup_maps")); return
        elif action == "sup_mapply":
            price = supplier_store.apply_mapping_price(conn, m, sup_usd_rate())
            conn.close(); reload_products()
            await _sup_edit(cb, f"✅ {html.escape(m['service_key'])} / {html.escape(m['duration'])} ka naya price: <b>₹{price}</b>",
                            _sup_back(f"sup_m_{mid}")); return
        elif action in ("sup_mcost", "sup_mmk"):
            conn.close()
            await state.set_state(SupplierState.cost if action == "sup_mcost" else SupplierState.markup)
            await state.update_data(sup_map_id=mid)
            prompt = ("💵 Supplier cost bhejo, jaise <code>150</code> ya <code>1.5 USD</code>"
                      if action == "sup_mcost" else
                      "📈 Apna profit bhejo: <code>50</code> = ₹50 fixed, <code>20%</code> = 20% upar")
            await _sup_edit(cb, prompt + "\n\n/cancel se band karo", _sup_back(f"sup_m_{mid}")); return
        m = supplier_store.get_mapping(conn, mid); conn.close()
        await _sup_edit(cb, f"🔗 <b>MAPPING #{mid}</b>\n━━━━━━━━━━━━━━━━━━━━\n\n{html.escape(_map_line(m))}", sup_map_kb(m)); return

    if data == "sup_bulk":
        await state.set_state(SupplierState.bulk)
        await _sup_edit(cb,
            "💹 <b>BULK PRICE CHANGE</b>\n\n"
            "Sab services: <code>+10%</code>  <code>-5%</code>  <code>+20</code>  <code>-15</code>\n"
            "Ek service: <code>netflix +10%</code>\n\n/cancel se band karo", _sup_back()); return

    if data == "sup_rate":
        await state.set_state(SupplierState.rate)
        await _sup_edit(cb, f"💱 Abhi 1 USD = ₹{sup_usd_rate():g}\nNaya rate bhejo (jaise <code>88.5</code>)", _sup_back()); return

    if data == "sup_profit":
        conn = db()
        now = time.time()
        today = supplier_store.profit_summary(conn, now_dt().replace(hour=0, minute=0, second=0).timestamp())
        week = supplier_store.profit_summary(conn, now - 7*86400)
        allt = supplier_store.profit_summary(conn)
        counts = supplier_store.counts_by_status(conn)
        conn.close()
        def fmt(s): t = s["total"]; return f"{t['orders']} orders | sale ₹{t['sales']:.0f} | cost ₹{t['cost']:.0f} | <b>profit ₹{t['profit']:.0f}</b>"
        per = "\n".join(f"• {supplier_clients.PROVIDER_LABELS.get(p,p)}: {v['orders']} | profit ₹{v['profit']:.0f}"
                        for p, v in allt["providers"].items()) or "• abhi koi auto order nahi"
        st = ", ".join(f"{k}: {v}" for k, v in counts.items()) or "-"
        await _sup_edit(cb,
            "📊 <b>SUPPLIER PROFIT REPORT</b>\n━━━━━━━━━━━━━━━━━━━━\n\n"
            f"📅 Today: {fmt(today)}\n🗓️ 7 days: {fmt(week)}\n♾️ All time: {fmt(allt)}\n\n{per}\n\n"
            f"Status: {html.escape(st)}\n\n<i>Gateway fees / refunds isme minus nahi hain.</i>", _sup_back()); return

    if data == "sup_orders":
        conn = db(); c = conn.cursor()
        c.execute("SELECT order_id, provider, status, sale_amount, cost FROM supplier_orders ORDER BY created_at DESC LIMIT 15")
        rows = c.fetchall(); conn.close()
        b = InlineKeyboardBuilder()
        lines = []
        for oid, p, st, s, k in rows:
            lines.append(f"<code>{html.escape(oid)}</code> {supplier_clients.PROVIDER_LABELS.get(p,p)} — {html.escape(st)} | ₹{s:.0f}-₹{k:.0f}")
            if st == "delivered":
                b.button(text=f"📤 Resend {oid}", callback_data=f"sup_resend_{oid}")
        b.button(text="🔙 BACK", callback_data="sup_home"); b.adjust(1)
        await _sup_edit(cb, "🧾 <b>SUPPLIER ORDERS</b> (latest 15)\n━━━━━━━━━━━━━━━━━━━━\n\n" + ("\n".join(lines) or "Koi order nahi"), b.as_markup()); return

    if data.startswith("sup_resend_"):
        oid = data[len("sup_resend_"):]
        conn = db(); so = supplier_store.get_supplier_order(conn, oid)
        o = get_order(oid); conn.close()
        if so and so["status"] == "delivered" and o:
            await _send_supplier_delivery(o[1], oid, so["delivery"])
            await cb.message.answer(pe(f"📤 Order <code>{html.escape(oid)}</code> delivery dobara bhej di (supplier se naya order NAHI lagaya)."), parse_mode="HTML")
        return

async def _send_supplier_delivery(user_id: int, order_id: str, delivery: str):
    await bot.send_message(
        user_id,
        f"✅ <b>Order {html.escape(order_id)} DELIVERED!</b> 🎉\n━━━━━━━━━━━━━━━━━━━━\n\n"
        f"<pre>{html.escape(delivery)[:3500]}</pre>\n\n⭐ <b>Rate your experience!</b>",
        parse_mode="HTML", reply_markup=rating_kb(order_id))

async def supplier_autofulfill(order_id: str):
    """Called after payment is confirmed. Places ONE supplier order (never retried)."""
    try:
        if not sup_auto_global():
            return
        conn = db(); c = conn.cursor()
        c.execute("SELECT user_id, product_name, duration, amount, status FROM orders WHERE order_id=?", (order_id,))
        row = c.fetchone()
        if not row:
            conn.close(); return
        user_id, skey, dur, amount, status = row
        m = supplier_store.find_mapping(conn, skey, dur)
        if not m or not m["auto_fulfill"] or status != "approved":
            conn.close(); return
        p = m["provider"]
        if not sup_on(p) or not supplier_clients.key_configured(p):
            conn.close(); return
        cost_inr = supplier_store.cost_in_inr(m["cost"], m["cost_currency"], sup_usd_rate())
        if float(amount or 0) < cost_inr:
            conn.close()
            await _notify_admins(f"⚠️ <b>LOSS GUARD</b>\nOrder <code>{order_id}</code>: price ₹{amount} &lt; cost ₹{cost_inr:.2f}. Auto order nahi lagaya, manually deliver karo.")
            return
        if not supplier_store.claim_order(conn, order_id, p, m["product_id"], amount, cost_inr):
            conn.close(); return   # already sent once — never double-buy
        conn.close()
        label = supplier_clients.PROVIDER_LABELS[p]
        try:
            resp = await supplier_clients.place_order(p, m["product_id"], 1, order_id)
        except supplier_clients.SupplierAPIError as e:
            uncertain = e.status_code is None
            conn = db(); supplier_store.finish_order(conn, order_id, "uncertain" if uncertain else "failed", error=str(e)); conn.close()
            await _notify_admins(
                f"❌ <b>AUTO DELIVERY {'UNCERTAIN' if uncertain else 'FAILED'}</b>\nOrder <code>{order_id}</code> ({label})\n{html.escape(str(e))}\n\n"
                + ("⚠️ Supplier dashboard me check karo order laga ya nahi — dobara mat lagao.\n" if uncertain else "")
                + "Manually deliver karo ya refund do.")
            return
        except Exception as e:
            conn = db(); supplier_store.finish_order(conn, order_id, "failed", error=type(e).__name__); conn.close()
            await _notify_admins(f"❌ <b>AUTO DELIVERY FAILED</b>\nOrder <code>{order_id}</code> ({label}). Manually deliver karo.")
            return
        err = supplier_store.is_error_response(resp)
        ref = supplier_store.extract_ref(resp)
        if err:
            conn = db(); supplier_store.finish_order(conn, order_id, "failed", supplier_ref=ref, error=err); conn.close()
            await _notify_admins(f"❌ <b>SUPPLIER REJECTED</b>\nOrder <code>{order_id}</code> ({label}): {html.escape(err)}\nManually deliver karo ya refund do.")
            return
        delivery = supplier_store.extract_delivery(resp)
        conn = db()
        if not delivery:
            supplier_store.finish_order(conn, order_id, "placed", supplier_ref=ref); conn.close()
            await _notify_admins(f"⏳ <b>SUPPLIER ORDER PLACED</b>\nOrder <code>{order_id}</code> ({label}) ref <code>{html.escape(ref)}</code>\nDelivery details response me nahi aaye — supplier dashboard se le kar manually deliver karo.")
            return
        supplier_store.finish_order(conn, order_id, "delivered", supplier_ref=ref, delivery=delivery); conn.close()
        update_order_status(order_id, "delivered")
        try:
            maybe_activate_averontv(order_id, user_id)
        except Exception:
            pass
        try:
            await _send_supplier_delivery(user_id, order_id, delivery)
        except Exception:
            await _notify_admins(f"⚠️ Order <code>{order_id}</code> supplier se mil gaya par user ko message nahi gaya. 🧾 SUPPLIER ORDERS se Resend karo.")
            return
        await _notify_admins(f"🤖 <b>AUTO DELIVERED</b> ✅\nOrder <code>{order_id}</code> via {label} | profit ₹{float(amount) - cost_inr:.2f}\nIs order ke liye DELIVERED dabane ki zarurat nahi.")
    except Exception:
        logging.exception("supplier_autofulfill crashed for %s", order_id)

def schedule_autofulfill(order_id: str):
    try:
        coro = s1_autofulfill(order_id) if s1_order_info(order_id) else supplier_autofulfill(order_id)
        asyncio.get_running_loop().create_task(coro)
    except RuntimeError:
        pass

def _sup_admin_ok(msg: Message) -> bool:
    return can_manage_services(msg.from_user.id)

@dp.message(SupplierState.add_map)
async def sup_add_map_msg(msg: Message, state: FSMContext):
    if not _sup_admin_ok(msg): await state.clear(); return
    if (msg.text or "").startswith("/cancel"): await state.clear(); await msg.answer("❌ Cancelled"); return
    try:
        m = supplier_store.parse_mapping_line(msg.text or "")
        if m["service_key"] not in PRODUCTS:
            raise ValueError("service_key nahi mila (active services me)")
        if m["duration"] not in PRODUCTS[m["service_key"]]["prices"]:
            raise ValueError("duration nahi mila. Options: " + ", ".join(PRODUCTS[m["service_key"]]["prices"].keys()))
        conn = db(); mid = supplier_store.upsert_mapping(conn, m); mm = supplier_store.get_mapping(conn, mid); conn.close()
    except ValueError as e:
        await msg.answer(pe(f"❌ {html.escape(str(e))}\nDobara bhejo ya /cancel"), parse_mode="HTML"); return
    await state.clear()
    await msg.answer(pe(f"✅ Mapping saved\n\n{html.escape(_map_line(mm))}"), parse_mode="HTML", reply_markup=sup_map_kb(mm))

@dp.message(SupplierState.cost)
async def sup_cost_msg(msg: Message, state: FSMContext):
    if not _sup_admin_ok(msg): await state.clear(); return
    if (msg.text or "").startswith("/cancel"): await state.clear(); await msg.answer("❌ Cancelled"); return
    parts = (msg.text or "").replace("₹", "").split()
    try:
        cost = float(parts[0]); cur = (parts[1].upper() if len(parts) > 1 else "INR")
        if cost < 0 or cur not in ("INR", "USD"): raise ValueError
    except (ValueError, IndexError):
        await msg.answer("❌ Jaise 150 ya 1.5 USD bhejo"); return
    mid = (await state.get_data()).get("sup_map_id"); await state.clear()
    conn = db(); supplier_store.update_mapping(conn, mid, cost=cost, cost_currency=cur); mm = supplier_store.get_mapping(conn, mid); conn.close()
    await msg.answer(pe(f"✅ Cost updated\n\n{html.escape(_map_line(mm))}"), parse_mode="HTML", reply_markup=sup_map_kb(mm))

@dp.message(SupplierState.markup)
async def sup_markup_msg(msg: Message, state: FSMContext):
    if not _sup_admin_ok(msg): await state.clear(); return
    if (msg.text or "").startswith("/cancel"): await state.clear(); await msg.answer("❌ Cancelled"); return
    try:
        kind, val = supplier_store.parse_markup(msg.text or "")
    except ValueError as e:
        await msg.answer(f"❌ {e}"); return
    mid = (await state.get_data()).get("sup_map_id"); await state.clear()
    conn = db(); supplier_store.update_mapping(conn, mid, markup_type=kind, markup_value=val)
    mm = supplier_store.get_mapping(conn, mid); conn.close()
    newp = supplier_store.compute_price(mm["cost"], mm["cost_currency"], sup_usd_rate(), kind, val)
    await msg.answer(pe(f"✅ Profit set\n\n{html.escape(_map_line(mm))}\n\nSuggested price: <b>₹{newp}</b> — ✅ APPLY dabao to lagu ho"),
                     parse_mode="HTML", reply_markup=sup_map_kb(mm))

@dp.message(SupplierState.bulk)
async def sup_bulk_msg(msg: Message, state: FSMContext):
    if not _sup_admin_ok(msg): await state.clear(); return
    if (msg.text or "").startswith("/cancel"): await state.clear(); await msg.answer("❌ Cancelled"); return
    parts = (msg.text or "").strip().split()
    skey, expr = (parts[0], " ".join(parts[1:])) if len(parts) > 1 and not parts[0][0] in "+-" else (None, msg.text or "")
    if skey and skey not in PRODUCTS:
        await msg.answer("❌ service_key nahi mila"); return
    try:
        conn = db(); n = supplier_store.bulk_adjust(conn, expr, skey); conn.close()
    except ValueError as e:
        await msg.answer(f"❌ {e}"); return
    await state.clear(); reload_products()
    await msg.answer(pe(f"✅ {n} prices updated ({html.escape(expr.strip())}{' — ' + html.escape(skey) if skey else ' — all services'})"),
                     parse_mode="HTML", reply_markup=_sup_back())

@dp.message(SupplierState.rate)
async def sup_rate_msg(msg: Message, state: FSMContext):
    if not _sup_admin_ok(msg): await state.clear(); return
    if (msg.text or "").startswith("/cancel"): await state.clear(); await msg.answer("❌ Cancelled"); return
    try:
        r = float((msg.text or "").replace("₹", "").strip())
        if not 1 <= r <= 1000: raise ValueError
    except ValueError:
        await msg.answer("❌ Valid rate bhejo, jaise 88.5"); return
    set_setting("sup_usd_rate", f"{r:g}"); await state.clear()
    await msg.answer(pe(f"✅ 1 USD = ₹{r:g}"), parse_mode="HTML", reply_markup=_sup_back())

# ══════════════════════════════════════════════════
#  ⚡ SERVICE 1 — LIVE API PRODUCTS (CouponShops + Acczone + EliteToolz)
#  Sab supplier products automatic list hote hain. Price = supplier cost + profit
#  (ya admin ka custom price). Payment confirm hote hi supplier se auto delivery.
# ══════════════════════════════════════════════════
S1_PER_PAGE  = 10
S1_CACHE_SEC = 180
S1_PCODE = {"couponshops": "c", "acczone": "a", "elitetoolz": "e"}
S1_PFULL = {v: k for k, v in S1_PCODE.items()}
_S1_CACHE: dict = {}      # provider -> {"ts": float, "items": [...]}

class S1AdminState(StatesGroup):
    cmd = State()

def s1_init_tables():
    conn = db(); c = conn.cursor()
    c.execute("""CREATE TABLE IF NOT EXISTS s1_overrides (
        pkey TEXT PRIMARY KEY, price INTEGER DEFAULT 0, name TEXT DEFAULT '', hidden INTEGER DEFAULT 0)""")
    c.execute("""CREATE TABLE IF NOT EXISTS s1_orders (
        order_id TEXT PRIMARY KEY, provider TEXT, product_id TEXT, name TEXT,
        cost_inr REAL DEFAULT 0, price INTEGER DEFAULT 0, created_at TEXT)""")
    conn.commit(); conn.close()

def s1_overrides() -> dict:
    conn = db(); c = conn.cursor()
    c.execute("SELECT pkey, price, name, hidden FROM s1_overrides")
    out = {r[0]: {"price": r[1] or 0, "name": r[2] or "", "hidden": r[3] or 0} for r in c.fetchall()}
    conn.close(); return out

def s1_set_override(pkey: str, **f):
    conn = db(); c = conn.cursor()
    c.execute("INSERT OR IGNORE INTO s1_overrides (pkey) VALUES (?)", (pkey,))
    for k, v in f.items():
        if k in ("price", "name", "hidden"):
            c.execute(f"UPDATE s1_overrides SET {k}=? WHERE pkey=?", (v, pkey))
    conn.commit(); conn.close()

def s1_currency(p: str) -> str:
    return (get_setting(f"s1_cur_{p}", "") or ("INR" if p == "couponshops" else "USD")).upper()

def s1_profit() -> str:
    return get_setting("s1_profit", "") or "+20%"

def s1_auto() -> bool:
    return get_setting("s1_auto", "1") == "1"

def s1_sell_price(cost_inr: float, override: int = 0) -> int:
    if override and override > 0:
        return int(override)
    adj = supplier_store.parse_adjust(s1_profit()) or ("percent", 20.0)
    kind, val = adj
    price = cost_inr * (1 + val / 100) if kind == "percent" else cost_inr + val
    return max(1, math.ceil(round(price, 6)))

async def _s1_provider_items(p: str, force: bool = False) -> list[dict]:
    """Live products of ONE supplier (cached a few minutes; stale copy kept if the API fails)."""
    ent = _S1_CACHE.get(p)
    if not force and ent and time.time() - ent["ts"] < S1_CACHE_SEC:
        return ent["items"]
    if not supplier_clients.key_configured(p):
        return []
    try:
        raw = await supplier_clients.fetch_catalog(p)
    except Exception as e:
        logging.warning("s1 catalog %s: %s", p, type(e).__name__)
        return ent["items"] if ent else []
    items = []
    for it in supplier_store.catalog_items(raw):
        if it["id"] in ("?", ""):
            continue
        if it.get("price_inr") is not None:
            cost_inr = float(it["price_inr"])
        else:
            try:
                cost_inr = supplier_store.cost_in_inr(float(it["price"] or 0), s1_currency(p), sup_usd_rate())
            except Exception:
                continue
        try:
            stock = int(float(it.get("stock") if it.get("stock") is not None else 1))
        except Exception:
            stock = 1
        items.append({"pkey": f"{S1_PCODE[p]}:{it['id']}", "provider": p, "id": it["id"],
                      "name": it["name"], "desc": it.get("desc", ""), "cost_inr": cost_inr,
                      "stock": stock})
    if not items and ent:
        return ent["items"]          # empty answer = probably a glitch, keep old list
    _S1_CACHE[p] = {"ts": time.time(), "items": items}
    return items

async def s1_catalog(force: bool = False, admin: bool = False) -> list[dict]:
    """Products for Service 1.
    customers (admin=False): only suppliers that are ON and products that are not hidden.
    admin=True: everything (every supplier, hidden ones flagged) for the API Control Center."""
    provs = [p for p in supplier_store.PROVIDER_IDS if admin or sup_on(p)]
    lists = await asyncio.gather(*[_s1_provider_items(p, force) for p in provs])
    ov = s1_overrides()
    out = []
    for p, items in zip(provs, lists):
        for it in items:
            o = ov.get(it["pkey"], {})
            hidden = bool(o.get("hidden"))
            if hidden and not admin:
                continue
            d = dict(it)
            d["name"] = o.get("name") or it["name"]
            d["price"] = s1_sell_price(it["cost_inr"], o.get("price", 0))
            d["hidden"] = hidden
            out.append(d)
    if admin:
        out.sort(key=lambda x: (x["provider"], x["name"].lower()))
    else:
        out.sort(key=lambda x: (x["stock"] <= 0, x["name"].lower()))
    return out

async def s1_find(pkey: str):
    for it in await s1_catalog():
        if it["pkey"] == pkey:
            return it
    return None

def _s1_cb(prefix: str, pkey: str) -> str:
    return f"{prefix}{pkey}"[:64]

async def s1_list_screen(cb: CallbackQuery, page: int = 0):
    items = await s1_catalog()
    total_pages = max(1, (len(items) + S1_PER_PAGE - 1) // S1_PER_PAGE)
    page = max(0, min(page, total_pages - 1))
    rows = []
    try:
        _c = db(); best = sales_store.bestseller_names(_c); _c.close()
    except Exception:
        best = set()
    for it in items[page * S1_PER_PAGE:(page + 1) * S1_PER_PAGE]:
        ok = it["stock"] > 0
        mark = "❌" if not ok else ("🔥" if it["name"] in best else "✅")
        label = f"{mark} {it['name'][:28]} — ₹{it['price']}"
        rows.append([create_btn(label, cb=_s1_cb("s1p_", it["pkey"]) if ok else "sold_out",
                                style=ButtonStyle.SUCCESS if ok else ButtonStyle.DANGER, no_emoji=True)])
    nav = []
    if page > 0:
        nav.append(create_btn("⬅️ Previous", cb=f"s1pg_{page - 1}", no_emoji=True))
    nav.append(create_btn(f"📄 {page + 1} / {total_pages}", cb="startpg_noop", style=ButtonStyle.PRIMARY, no_emoji=True))
    if page < total_pages - 1:
        nav.append(create_btn("Next ➡️", cb=f"s1pg_{page + 1}", no_emoji=True))
    rows.append(nav)
    rows.append([create_btn("🔙 Back To Menu", cb="back", style=ButtonStyle.PRIMARY)])
    title = get_setting("s1_title", "") or "SERVICE 1 — INSTANT DELIVERY"
    text = pe(f"⚡ <b>{html.escape(title)}</b>\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
              + ("👇 <b>Product select kijiye</b> — payment ke baad <b>auto delivery</b> 🤖\n\n"
                 if items else "😔 Abhi koi product available nahi. Thodi der baad try kijiye.\n\n")
              + f"<i>📄 Page {page + 1} of {total_pages}</i>")
    kb = InlineKeyboardMarkup(inline_keyboard=rows)
    try:
        await cb.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
    except Exception:
        await cb.message.answer(text, parse_mode="HTML", reply_markup=kb)

def _s1_new_order(uid: int, it: dict) -> str:
    oid = generate_order_id(uid)
    save_order(oid, uid, f"api:{it['pkey']}", "API", it["price"])
    conn = db(); c = conn.cursor()
    c.execute("INSERT OR REPLACE INTO s1_orders (order_id,provider,product_id,name,cost_inr,price,created_at) VALUES (?,?,?,?,?,?,?)",
              (oid, it["provider"], it["id"], it["name"], it["cost_inr"], it["price"], now_str()))
    conn.commit(); conn.close()
    return oid

def s1_order_info(order_id: str):
    conn = db(); c = conn.cursor()
    c.execute("SELECT provider, product_id, name, cost_inr, price FROM s1_orders WHERE order_id=?", (order_id,))
    r = c.fetchone(); conn.close()
    return r

@dp.callback_query(lambda c: (c.data or "").startswith(("svc1", "s1p_", "s1pg_", "s1w_", "s1q_", "s1adm")))
async def s1_cb(cb: CallbackQuery, state: FSMContext):
    data, uid = cb.data, cb.from_user.id
    if is_user_banned(uid):
        await cb.answer("🚫 Banned", show_alert=True); return

    if data == "svc1":
        await cb.answer("⏳ Products load ho rahe hain...")
        await s1_list_screen(cb, 0); return
    if data.startswith("s1pg_"):
        await cb.answer()
        await s1_list_screen(cb, int(data[5:] or 0)); return

    if data.startswith("s1p_"):
        it = await s1_find(data[4:])
        if not it:
            await cb.answer("❌ Product ab available nahi.", show_alert=True); return
        bal, _ = get_wallet(uid)
        rows = []
        if bal >= it["price"]:
            rows.append([create_btn(f"PAY WITH WALLET ₹{it['price']} 💚", cb=_s1_cb("s1w_", it["pkey"]), style=ButtonStyle.SUCCESS)])
        rows.append([create_btn(f"PAY BY QR — ₹{it['price']} ⚡", cb=_s1_cb("s1q_", it["pkey"]), style=ButtonStyle.PRIMARY)])
        rows.append([create_btn("🔙 BACK", cb="svc1", style=ButtonStyle.DANGER)])
        desc = html.escape(it["desc"])[:600]
        await cb.message.answer(
            pe(f"🛍️ <b>{html.escape(it['name'])}</b>\n━━━━━━━━━━━━━━━━━━━━\n\n"
               + (f"<blockquote expandable>{desc}</blockquote>\n" if desc else "")
               + f"💰 Price: <b>₹{it['price']}</b>\n📦 Stock: <b>{it['stock']}</b>\n"
               f"💚 Wallet: ₹{bal:.2f}\n\n🤖 Payment ke baad <b>automatic delivery</b>."),
            parse_mode="HTML", reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
        await cb.answer(); return

    if data.startswith(("s1w_", "s1q_")):
        it = await s1_find(data[4:])
        if not it or it["stock"] <= 0:
            await cb.answer("❌ Out of stock.", show_alert=True); return
        if data.startswith("s1w_"):
            oid = _s1_new_order(uid, it)
            if not debit_wallet(uid, it["price"], f"Purchase {oid} (Service 1)"):
                update_order_status(oid, "cancelled")
                await cb.answer("❌ Wallet balance kam hai.", show_alert=True); return
            log_payment("WALLET", "Service 1 order", uid, it["price"], oid, detail=it["name"])
            update_order_status(oid, "approved"); add_order_spent(uid, it["price"])
            await cb.message.answer(pe(f"✅ <b>Payment done from wallet</b>\n🆔 <code>{oid}</code>\n\n🤖 Delivery ho rahi hai..."), parse_mode="HTML")
            schedule_autofulfill(oid)
            await cb.answer(); return
        if not gw_enabled():
            await cb.answer("⚡ Online payment abhi band hai — wallet use kijiye.", show_alert=True); return
        oid = _s1_new_order(uid, it)
        await cb.answer("⚡ QR ban raha hai...")
        await gw_send_payment(cb.message.chat.id, uid, it["price"], f"order:{oid}", "SERVICE 1 PAYMENT",
                              f"🛍️ {html.escape(it['name'])}\n🆔 Order: <code>{oid}</code>\n", remark=f"order {oid}")
        return

    # ── Admin panel for Service 1 ─────────────────
    if data.startswith("s1adm"):
        if not can_manage_services(uid):
            await cb.answer("⛔ No permission", show_alert=True); return
        if data == "s1adm_auto":
            set_setting("s1_auto", "0" if s1_auto() else "1")
        if data == "s1adm_refresh":
            await s1_catalog(force=True)
        if data == "s1adm_cmd":
            await state.set_state(S1AdminState.cmd)
            await cb.message.answer(pe(
                "✏️ <b>SERVICE 1 — command bhejiye</b>\n━━━━━━━━━━━━━━━━━━━━\n\n"
                "<code>profit +20%</code> — sab products par profit (ya <code>profit +15</code> ₹ fixed)\n"
                "<code>price c:service_5 149</code> — 1 product ka fixed price (0 = auto)\n"
                "<code>name e:13173444 Lovable Pro 1 Year</code> — naam badlo\n"
                "<code>hide a:DUOLINGO</code> / <code>show a:DUOLINGO</code>\n"
                "<code>rate 88</code> — USD → ₹ rate\n"
                "<code>currency elitetoolz USD</code> — supplier price currency (INR/USD)\n\n"
                "Product code (c:/a:/e:…) <b>📋 PRODUCT LIST</b> me milega."), parse_mode="HTML")
            await cb.answer(); return
        if data == "s1adm_list":
            items = await s1_catalog()
            lines = [f"<code>{html.escape(i['pkey'])}</code> {html.escape(i['name'][:30])} | cost ₹{i['cost_inr']:.1f} → <b>₹{i['price']}</b> | stk {i['stock']}"
                     for i in items[:60]]
            hidden = [k for k, v in s1_overrides().items() if v.get("hidden")]
            txt = "📋 <b>SERVICE 1 PRODUCTS</b>\n\n" + ("\n".join(lines) or "Koi product nahi")
            if hidden:
                txt += "\n\n🙈 Hidden: " + ", ".join(f"<code>{html.escape(h)}</code>" for h in hidden)
            await cb.message.answer(txt[:4000], parse_mode="HTML"); await cb.answer(); return
        b = InlineKeyboardBuilder()
        b.button(text="🔌 API CONTROL CENTER (ON / OFF)", callback_data="apic")
        b.button(text="📋 PRODUCT LIST + CODES", callback_data="s1adm_list")
        b.button(text="✏️ SET PRICE / PROFIT / NAME / HIDE", callback_data="s1adm_cmd")
        b.button(text=("🔴 AUTO DELIVERY OFF karo" if s1_auto() else "🟢 AUTO DELIVERY ON karo"), callback_data="s1adm_auto")
        b.button(text="🔄 REFRESH PRODUCTS", callback_data="s1adm_refresh")
        b.button(text="🎨 BUTTON / HEADING TEXT", callback_data="admin_settings")
        b.button(text="🔙 BACK", callback_data="admin_back")
        b.adjust(1)
        txt = pe(f"⚡ <b>SERVICE 1 (API) SETTINGS</b>\n━━━━━━━━━━━━━━━━━━━━\n\n"
                 f"📈 Profit: <b>{html.escape(s1_profit())}</b>\n💱 USD rate: ₹{sup_usd_rate()}\n"
                 f"🤖 Auto delivery: <b>{'ON' if s1_auto() else 'OFF'}</b>\n"
                 f"🔌 Suppliers: " + ", ".join(f"{supplier_clients.PROVIDER_LABELS[p]} {'🟢' if sup_on(p) and supplier_clients.key_configured(p) else '🔴'}"
                                              for p in supplier_store.PROVIDER_IDS))
        try:
            await cb.message.edit_text(txt, parse_mode="HTML", reply_markup=b.as_markup())
        except Exception:
            await cb.message.answer(txt, parse_mode="HTML", reply_markup=b.as_markup())
        await cb.answer(); return

@dp.message(S1AdminState.cmd)
async def s1_admin_cmd(msg: Message, state: FSMContext):
    if not can_manage_services(msg.from_user.id):
        await state.clear(); return
    t = (msg.text or "").strip()
    parts = t.split(maxsplit=2)
    cmd = parts[0].lower() if parts else ""
    try:
        if cmd == "profit" and len(parts) >= 2 and supplier_store.parse_adjust(parts[1]):
            set_setting("s1_profit", parts[1]); res = f"Profit = {parts[1]}"
        elif cmd == "price" and len(parts) == 3:
            s1_set_override(parts[1], price=max(0, int(float(parts[2])))); res = f"{parts[1]} price = ₹{parts[2]}"
        elif cmd == "name" and len(parts) == 3:
            s1_set_override(parts[1], name=parts[2][:60]); res = f"{parts[1]} naam = {parts[2]}"
        elif cmd in ("hide", "show") and len(parts) == 2:
            s1_set_override(parts[1], hidden=1 if cmd == "hide" else 0); res = f"{parts[1]} {cmd}"
        elif cmd == "rate" and len(parts) == 2:
            set_setting("sup_usd_rate", str(float(parts[1]))); res = f"USD rate = ₹{parts[1]}"
        elif cmd == "currency" and len(parts) == 3 and parts[1] in supplier_store.PROVIDER_IDS and parts[2].upper() in ("INR", "USD"):
            set_setting(f"s1_cur_{parts[1]}", parts[2].upper()); res = f"{parts[1]} currency = {parts[2].upper()}"
        else:
            await msg.answer(pe("❌ Command samajh nahi aaya. Example: <code>price c:service_5 149</code>"), parse_mode="HTML")
            return
    except ValueError:
        await msg.answer(pe("❌ Number galat hai."), parse_mode="HTML"); return
    await state.clear()
    await msg.answer(pe(f"✅ <b>Saved:</b> {html.escape(res)}\n\nAur badalna ho to Admin → ⚡ SERVICE 1 → ✏️ dobara dabaiye."),
                     parse_mode="HTML")

async def s1_autofulfill(order_id: str):
    """Service 1 order: payment confirmed → buy 1 unit from supplier → send to user."""
    info = s1_order_info(order_id)
    o = get_order(order_id)
    if not info or not o:
        return
    provider, pid, name, cost_inr, price = info
    user_id = o[1]
    conn = db(); c = conn.cursor()
    c.execute("SELECT status FROM orders WHERE order_id=?", (order_id,)); st = (c.fetchone() or [""])[0]
    conn.close()
    if st != "approved":
        return
    if not s1_auto():
        await _notify_admins(f"🛍️ <b>SERVICE 1 ORDER (auto OFF)</b>\n<code>{order_id}</code> — {html.escape(name)}\nUser <code>{user_id}</code> — manually deliver karo.")
        return
    if float(price) < float(cost_inr):
        await _notify_admins(f"⚠️ <b>LOSS GUARD</b> <code>{order_id}</code>: price ₹{price} &lt; cost ₹{cost_inr:.2f}. Manually deliver karo.")
        return
    conn = db()
    claimed = supplier_store.claim_order(conn, order_id, provider, pid, price, cost_inr)
    conn.close()
    if not claimed:
        return
    label = supplier_clients.PROVIDER_LABELS[provider]
    try:
        resp = await supplier_clients.place_order(provider, pid, 1, order_id)
    except supplier_clients.SupplierAPIError as e:
        uncertain = e.status_code is None
        conn = db(); supplier_store.finish_order(conn, order_id, "uncertain" if uncertain else "failed", error=str(e)); conn.close()
        await _notify_admins(f"❌ <b>SERVICE 1 DELIVERY {'UNCERTAIN' if uncertain else 'FAILED'}</b>\n<code>{order_id}</code> ({label})\n{html.escape(str(e))}\nManually deliver ya refund karo.")
        try:
            await bot.send_message(user_id, pe(f"⏳ Order <code>{order_id}</code> me thoda time lag raha hai — admin jaldi deliver karega 🙏"), parse_mode="HTML")
        except Exception:
            pass
        return
    err = supplier_store.is_error_response(resp)
    ref = supplier_store.extract_ref(resp)
    delivery = "" if err else supplier_store.extract_delivery(resp)
    conn = db()
    if err or not delivery:
        supplier_store.finish_order(conn, order_id, "failed" if err else "placed", supplier_ref=ref, error=err or "")
        conn.close()
        await _notify_admins(f"⚠️ <b>SERVICE 1</b> <code>{order_id}</code> ({label}): {html.escape(err or 'delivery details nahi aaye — dashboard check karo')}")
        try:
            await bot.send_message(user_id, pe(f"⏳ Order <code>{order_id}</code> process ho raha hai — admin jaldi deliver karega 🙏"), parse_mode="HTML")
        except Exception:
            pass
        return
    supplier_store.finish_order(conn, order_id, "delivered", supplier_ref=ref, delivery=delivery); conn.close()
    update_order_status(order_id, "delivered")
    try:
        await _send_supplier_delivery(user_id, order_id, delivery)
    except Exception:
        await _notify_admins(f"⚠️ <code>{order_id}</code> deliver hua par user ko message nahi gaya — SUPPLIER ORDERS se Resend karo.")
        return
    await _notify_admins(f"🤖 <b>SERVICE 1 AUTO DELIVERED</b> ✅ <code>{order_id}</code> {html.escape(name)} via {label} | profit ₹{float(price) - float(cost_inr):.2f}")

# ══════════════════════════════════════════════════
#  🧾 SALES LOGGER  — har sale (API / Service 2 / manual) ka professional log
#  • Order DELIVERED hote hi sales_log me entry (sirf 1 baar) + channel post
#  • Public channel: "New Purchase!" card (masked ID)   • Private: cost + profit
#  • Manual sale: admin panel ya /sold command
# ══════════════════════════════════════════════════
_DELIVERED_BY: dict[str, int] = {}      # order_id -> admin who pressed DELIVERED
_BG_TASKS: set = set()
SLOG_MASKS = ("full", "partial", "none")
SLOG_MASK_LABEL = {"full": "FULL ********", "partial": "PARTIAL 87****81", "none": "SHOW ID"}
SLOG_PER_PAGE = 8
PAY_LABEL = {"AUTOPAY": "⚡ AutoPay", "MANUAL": "📷 Manual QR", "WALLET": "💚 Wallet", "Manual": "✍️ Manual"}

class SlogState(StatesGroup):
    setpub  = State()
    setpriv = State()
    addsale = State()

def slog_on(key: str, default: str = "1") -> bool:
    return get_setting(key, default) == "1"

def slog_mask() -> str:
    v = get_setting("slog_mask", "full")
    return v if v in SLOG_MASKS else "full"

def _chat_target(v):
    v = str(v or "").strip()
    if not v:
        return None
    try:
        return int(v)
    except ValueError:
        return v if v.startswith("@") else "@" + v

def _spawn(coro):
    try:
        t = asyncio.get_running_loop().create_task(coro)
    except RuntimeError:
        coro.close()
        return
    _BG_TASKS.add(t)
    t.add_done_callback(_BG_TASKS.discard)

def _schedule_post(rid: int):
    """Post right now; if it fails (or there is no loop) the retry loop will do it."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return
    _spawn(post_sale(rid))

def _sale_hook(order_id: str):
    """Order became 'delivered' -> write ONE sales_log row (never twice)."""
    conn = db()
    rec = None
    try:
        if sales_store.get_by_sale_id(conn, order_id):
            return
        c = conn.cursor()
        c.execute("SELECT user_id, product_name, duration, amount, wallet_used FROM orders WHERE order_id=?", (order_id,))
        o = c.fetchone()
        if not o:
            return
        uid, pname, dur, amount, wallet_used = o
        source, provider, cost = "service2", "", None
        s1 = None
        try:
            c.execute("SELECT provider, name, cost_inr FROM s1_orders WHERE order_id=?", (order_id,))
            s1 = c.fetchone()
        except sqlite3.Error:
            pass
        if s1:
            source = "api"
            provider = supplier_clients.PROVIDER_LABELS.get(s1[0], s1[0])
            product = s1[1] or "API product"
            cost = float(s1[2] or 0)
        else:
            base = PRODUCTS.get(pname, {}).get("display_name", pname)
            product = f"{base} — {dur}" if dur else str(base)
            so = supplier_store.get_supplier_order(conn, order_id)
            if so and so["status"] == "delivered":
                source = "service2_api"
                provider = supplier_clients.PROVIDER_LABELS.get(so["provider"], so["provider"])
                cost = float(so["cost"] or 0)
            else:
                m = supplier_store.find_mapping(conn, pname, dur)
                if m:     # reference cost from mapping, even if delivered by hand
                    cost = supplier_store.cost_in_inr(m["cost"], m["cost_currency"], sup_usd_rate())
        c.execute("SELECT method FROM payment_ledger WHERE ref_id=? ORDER BY id DESC LIMIT 1", (order_id,))
        r = c.fetchone()
        method = PAY_LABEL.get(r[0], r[0]) if r else ("💚 Wallet" if (wallet_used or 0) > 0 else "")
        c.execute("SELECT username, first_name FROM users WHERE user_id=?", (uid,))
        u = c.fetchone()
        buyer = ""
        if u:
            buyer = f"@{u[0]}" if u[0] else (u[1] or "")
        rec = sales_store.record_sale(
            conn, sale_id=order_id, source=source, product=product, amount=float(amount or 0),
            quantity=1, cost=cost, provider=provider, user_id=uid, buyer=buyer, method=method,
            admin_id=_DELIVERED_BY.pop(order_id, 0))
    finally:
        conn.close()
    if rec:
        _schedule_post(rec["id"])

def slog_record_manual(m: dict, admin_id: int):
    sale_id = f"MS{int(time.time())}{random.randint(100, 999)}"
    conn = db()
    try:
        rec = sales_store.record_sale(
            conn, sale_id=sale_id, source="manual", product=m["product"], amount=m["amount"],
            quantity=m["quantity"], cost=m["cost"], user_id=m["user_id"], buyer=m["buyer"],
            method="✍️ Manual", admin_id=admin_id)
    finally:
        conn.close()
    if rec:
        _schedule_post(rec["id"])
    return rec

async def _slog_send(kind: str, text: str):
    """kind 'pub' | 'priv'. Raises on Telegram error."""
    chat = _chat_target(get_setting(f"slog_{kind}_chat", ""))
    if kind == "priv" and not chat:
        chat = OWNER_ID
    await bot.send_message(chat, pe(text), parse_mode="HTML")

async def post_sale(rid: int):
    """Send one sale to the public channel and/or private log (each only once)."""
    conn = db()
    try:
        s = sales_store.get_by_id(conn, rid)
        if not s:
            return
        err = None
        if s["pub_state"] == 0:
            if not slog_on("slog_pub_on") or not _chat_target(get_setting("slog_pub_chat", "")):
                sales_store.set_state(conn, rid, pub=2)
            else:
                try:
                    await _slog_send("pub", sales_store.format_public(
                        s, show_price=slog_on("slog_price"), mask_mode=slog_mask()))
                    sales_store.set_state(conn, rid, pub=1)
                except Exception as e:
                    err = e
        if s["priv_state"] == 0:
            if not slog_on("slog_priv_on"):
                sales_store.set_state(conn, rid, priv=2)
            else:
                try:
                    await _slog_send("priv", sales_store.format_private(s))
                    sales_store.set_state(conn, rid, priv=1)
                except Exception as e:
                    err = err or e
        if err:
            sales_store.set_state(conn, rid, bump=True)
            set_setting("slog_last_error", f"{type(err).__name__}: {str(err)[:150]}")
            if s["attempts"] + 1 >= sales_store.MAX_ATTEMPTS:
                try:
                    await bot.send_message(OWNER_ID, pe(
                        f"⚠️ <b>SALES LOG FAIL</b>\nSale #{s['id']} channel/log me post nahi ho paya.\n"
                        f"{html.escape(str(err))[:200]}\n\nBot ko channel me admin banao → Admin → 🧾 SALES LOGGER."),
                        parse_mode="HTML")
                except Exception:
                    pass
        else:
            set_setting("slog_last_error", "")
    finally:
        conn.close()

async def _slog_daily_tick():
    if not slog_on("slog_daily"):
        return
    n = now_dt()
    if n.hour != 23 or n.minute < 55:
        return
    today = n.strftime("%Y-%m-%d")
    if get_setting("slog_daily_last", "") == today:
        return
    set_setting("slog_daily_last", today)
    conn = db(); txt = sales_store.format_report(conn); conn.close()
    try:
        await _slog_send("priv", "🌙 <b>DAILY CLOSING REPORT</b>\n━━━━━━━━━━━━━━━━━━━━\n\n" + txt.split("\n", 3)[-1])
    except Exception:
        logging.exception("daily sales report failed")

async def sales_poster_loop():
    """Background: retries unsent sale logs + posts the daily closing report."""
    await asyncio.sleep(15)
    while True:
        try:
            conn = db(); rows = sales_store.pending(conn, 20); conn.close()
            for s in rows:
                await post_sale(s["id"])
            await _slog_daily_tick()
        except Exception:
            logging.exception("sales poster loop")
        await asyncio.sleep(45)

# ── admin UI ───────────────────────────────────────
def _onoff(v: bool) -> str:
    return "🟢 ON" if v else "🔴 OFF"

def slog_home():
    conn = db()
    today = sales_store.summary(conn, *sales_store.day_bounds())
    total = sales_store.count(conn)
    conn.close()
    pub = get_setting("slog_pub_chat", "") or "❌ set nahi hai"
    priv = get_setting("slog_priv_chat", "") or f"Owner DM ({OWNER_ID})"
    err = get_setting("slog_last_error", "")
    txt = ("🧾 <b>SALES LOGGER</b>\n━━━━━━━━━━━━━━━━━━━━\n\n"
           f"📅 Today: <b>{today['orders']}</b> sales | ₹{today['revenue']:,.0f} | profit ₹{today['profit']:,.0f}\n"
           f"📚 Total logged: <b>{total}</b>\n\n"
           f"📢 Public channel: <b>{_onoff(slog_on('slog_pub_on'))}</b> → <code>{html.escape(pub)}</code>\n"
           f"🔒 Private log: <b>{_onoff(slog_on('slog_priv_on'))}</b> → <code>{html.escape(priv)}</code>\n"
           f"💰 Price channel me: <b>{_onoff(slog_on('slog_price'))}</b>\n"
           f"🙈 User ID: <b>{SLOG_MASK_LABEL[slog_mask()]}</b>\n"
           f"🌙 Daily report (11:55 PM): <b>{_onoff(slog_on('slog_daily'))}</b>\n"
           + (f"\n⚠️ Last error: <code>{html.escape(err)}</code>\n" if err else "")
           + "\n<i>Sale tab log hoti hai jab order DELIVERED ho (auto ya admin ke button se).\n"
             "Manual sale: ➕ button ya <code>/sold product | qty | price | cost | buyer</code></i>")
    b = InlineKeyboardBuilder()
    b.button(text="📊 SALES REPORT",            callback_data="slog_rep")
    b.button(text="🧾 RECENT SALES",            callback_data="slog_rec_0")
    b.button(text="➕ ADD MANUAL SALE",         callback_data="slog_add")
    b.button(text=f"📢 CHANNEL LOG: {_onoff(slog_on('slog_pub_on'))}", callback_data="slog_pubtog")
    b.button(text="🔗 SET PUBLIC CHANNEL",      callback_data="slog_setpub")
    b.button(text=f"🔒 PRIVATE LOG: {_onoff(slog_on('slog_priv_on'))}", callback_data="slog_privtog")
    b.button(text="🔗 SET PRIVATE CHAT",        callback_data="slog_setpriv")
    b.button(text=f"💰 SHOW PRICE: {_onoff(slog_on('slog_price'))}", callback_data="slog_price")
    b.button(text=f"🙈 ID MASK: {SLOG_MASK_LABEL[slog_mask()]}", callback_data="slog_mask")
    b.button(text=f"🌙 DAILY REPORT: {_onoff(slog_on('slog_daily'))}", callback_data="slog_daily")
    b.button(text="🧪 SEND TEST LOG",           callback_data="slog_test")
    b.button(text="📤 EXPORT CSV",              callback_data="slog_csv")
    b.button(text="🔙 BACK",                    callback_data="admin_back")
    b.adjust(2, 1, 2, 2, 2, 2, 1, 1)
    return txt, b.as_markup()

def _slog_back(cb="slog"):
    b = InlineKeyboardBuilder()
    b.button(text="🔙 BACK", callback_data=cb)
    return b.as_markup()

@dp.callback_query(lambda c: (c.data or "").startswith("slog"))
async def slog_cb(cb: CallbackQuery, state: FSMContext):
    data, uid = cb.data, cb.from_user.id
    if not can_manage_services(uid):
        await cb.answer("⛔ No permission", show_alert=True); return
    await cb.answer()

    if data == "slog":
        await state.clear()
        t, kb = slog_home(); await _sup_edit(cb, t, kb); return

    toggles = {"slog_pubtog": "slog_pub_on", "slog_privtog": "slog_priv_on",
               "slog_price": "slog_price", "slog_daily": "slog_daily"}
    if data in toggles:
        k = toggles[data]; set_setting(k, "0" if slog_on(k) else "1")
        t, kb = slog_home(); await _sup_edit(cb, t, kb); return
    if data == "slog_mask":
        nxt = SLOG_MASKS[(SLOG_MASKS.index(slog_mask()) + 1) % len(SLOG_MASKS)]
        set_setting("slog_mask", nxt)
        t, kb = slog_home(); await _sup_edit(cb, t, kb); return

    if data == "slog_rep":
        conn = db(); txt = sales_store.format_report(conn); conn.close()
        await _sup_edit(cb, txt[:4000], _slog_back()); return

    if data.startswith("slog_rec_"):
        page = int(data[9:] or 0) if data[9:].isdigit() else 0
        conn = db()
        total = sales_store.count(conn)
        rows = sales_store.recent(conn, SLOG_PER_PAGE, page * SLOG_PER_PAGE)
        conn.close()
        pages = max(1, (total + SLOG_PER_PAGE - 1) // SLOG_PER_PAGE)
        b = InlineKeyboardBuilder()
        for s in rows:
            b.button(text=f"#{s['id']} {s['product'][:22]} — ₹{s['amount']:,.0f}", callback_data=f"slog_v_{s['id']}_{page}")
        if page > 0:
            b.button(text="⬅️ Prev", callback_data=f"slog_rec_{page - 1}")
        if (page + 1) * SLOG_PER_PAGE < total:
            b.button(text="Next ➡️", callback_data=f"slog_rec_{page + 1}")
        b.button(text="🔙 BACK", callback_data="slog")
        b.adjust(*([1] * len(rows)), 2, 1) if (page > 0 and (page + 1) * SLOG_PER_PAGE < total) else b.adjust(1)
        await _sup_edit(cb, f"🧾 <b>RECENT SALES</b>\n━━━━━━━━━━━━━━━━━━━━\n\n"
                            f"Total {total} • page {page + 1}/{pages}\nSale par tap karo → detail / delete / repost.", b.as_markup()); return

    if data.startswith(("slog_v_", "slog_d_", "slog_dy_", "slog_rp_")):
        parts = data.split("_")
        act, rid = parts[1], int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 0
        page = parts[3] if len(parts) > 3 else "0"
        conn = db(); s = sales_store.get_by_id(conn, rid)
        if not s:
            conn.close(); await _sup_edit(cb, "⚠️ Sale nahi mili (delete ho chuki hogi)", _slog_back(f"slog_rec_{page}")); return
        if act == "dy":
            sales_store.delete_sale(conn, rid); conn.close()
            await _sup_edit(cb, f"🗑️ Sale #{rid} delete ho gayi — reports me ab count nahi hogi.", _slog_back(f"slog_rec_{page}")); return
        if act == "d":
            conn.close()
            b = InlineKeyboardBuilder()
            b.button(text="🗑️ HAAN, DELETE KARO", callback_data=f"slog_dy_{rid}_{page}")
            b.button(text="❌ NAHI", callback_data=f"slog_v_{rid}_{page}")
            b.adjust(1)
            await _sup_edit(cb, f"⚠️ <b>Sale #{rid} delete karni hai?</b>\nChannel me jo post gaya hai wo delete nahi hoga.", b.as_markup()); return
        if act == "rp":
            sales_store.set_state(conn, rid, pub=0); conn.close()
            await post_sale(rid)
            conn = db(); s = sales_store.get_by_id(conn, rid); conn.close()
            ok = s and s["pub_state"] == 1
            await cb.message.answer(pe("📣 Channel me dobara post ho gaya ✅" if ok else
                                       f"❌ Post nahi hua. Channel set hai? Bot admin hai?\n{html.escape(get_setting('slog_last_error', ''))}"),
                                    parse_mode="HTML")
            return
        conn.close()
        b = InlineKeyboardBuilder()
        b.button(text="📣 REPOST TO CHANNEL", callback_data=f"slog_rp_{rid}_{page}")
        b.button(text="🗑️ DELETE SALE", callback_data=f"slog_d_{rid}_{page}")
        b.button(text="🔙 BACK", callback_data=f"slog_rec_{page}")
        b.adjust(1)
        await _sup_edit(cb, sales_store.format_private(s), b.as_markup()); return

    if data in ("slog_setpub", "slog_setpriv"):
        kind = "pub" if data == "slog_setpub" else "priv"
        await state.set_state(SlogState.setpub if kind == "pub" else SlogState.setpriv)
        hint = ("Channel/group ka <code>@username</code> bhejo, ya <code>-100…</code> chat id, ya channel ka koi post <b>forward</b> karo.\n"
                "⚠️ Pehle bot ko us channel me <b>Admin</b> banao (Post messages permission).") if kind == "pub" else \
               ("Private log ka chat id / @username bhejo (ya forward). Khali chhodna ho to <code>off</code> likho — tab log Owner DM me aayega.")
        await _sup_edit(cb, f"🔗 <b>SET {'PUBLIC CHANNEL' if kind == 'pub' else 'PRIVATE LOG CHAT'}</b>\n\n{hint}\n\n"
                            "<code>off</code> = hata do • /cancel = band", _slog_back()); return

    if data == "slog_add":
        await state.set_state(SlogState.addsale)
        await _sup_edit(cb,
            "➕ <b>MANUAL SALE</b>\n━━━━━━━━━━━━━━━━━━━━\n\nEk line bhejo:\n"
            "<code>product | quantity | total price | cost | buyer</code>\n\n"
            "Example:\n<code>Apple Music 5M | 1 | 149 | 100 | Rahul 123456789</code>\n\n"
            "• cost aur buyer optional hain (<code>Netflix | 1 | 120</code>)\n"
            "• buyer me user id likhoge to channel me masked ID dikhegi\n/cancel se band karo", _slog_back()); return

    if data == "slog_addok":
        m = (await state.get_data()).get("slog_manual")
        await state.clear()
        if not m:
            await _sup_edit(cb, "⚠️ Data nahi mila, dobara ➕ dabao", _slog_back()); return
        rec = slog_record_manual(m, uid)
        await _sup_edit(cb, f"✅ <b>Manual sale #{rec['id']} log ho gayi</b>\nChannel / private log me bhej raha hu…", _slog_back())
        return

    if data == "slog_test":
        sample = {"id": 0, "sale_id": "TEST", "source": "api", "provider": "TestAPI", "user_id": 1234567890,
                  "buyer": "", "product": "Apple Music 5M", "quantity": 1, "amount": 149.0, "cost": 100.0,
                  "profit": 49.0, "method": "⚡ AutoPay", "admin_id": 0, "note": "", "created_at": time.time()}
        res = []
        for kind, label in (("pub", "📢 Public channel"), ("priv", "🔒 Private log")):
            if kind == "pub" and not _chat_target(get_setting("slog_pub_chat", "")):
                res.append(f"{label}: ❌ set nahi hai"); continue
            try:
                await _slog_send(kind, ("🧪 <b>TEST</b>\n" + (sales_store.format_public(sample, show_price=slog_on("slog_price"), mask_mode=slog_mask())
                                        if kind == "pub" else sales_store.format_private(sample))))
                res.append(f"{label}: ✅ bhej diya")
            except Exception as e:
                res.append(f"{label}: ❌ {html.escape(type(e).__name__)} — {html.escape(str(e))[:120]}")
        await _sup_edit(cb, "🧪 <b>TEST RESULT</b>\n━━━━━━━━━━━━━━━━━━━━\n\n" + "\n".join(res), _slog_back()); return

    if data == "slog_csv":
        from aiogram.types import BufferedInputFile
        conn = db(); raw = sales_store.export_csv(conn); conn.close()
        await cb.message.answer_document(BufferedInputFile(raw, filename=f"sales_log_{now_dt().strftime('%Y%m%d')}.csv"),
                                         caption="📤 Sales log export")
        return

async def _slog_set_chat(msg: Message, state: FSMContext, kind: str):
    if not can_manage_services(msg.from_user.id):
        await state.clear(); return
    txt = (msg.text or "").strip()
    if txt.startswith("/cancel"):
        await state.clear(); await msg.answer("❌ Cancelled"); return
    key = f"slog_{kind}_chat"
    if txt.lower() in ("off", "none", "clear", "remove"):
        set_setting(key, ""); await state.clear()
        t, kb = slog_home(); await msg.answer(pe(t), parse_mode="HTML", reply_markup=kb); return
    fo = getattr(msg, "forward_origin", None)
    fchat = getattr(fo, "chat", None) or getattr(msg, "forward_from_chat", None)
    try:
        if fchat:
            chat_id = fchat.id
        elif txt:
            chat_id = (await bot.get_chat(_chat_target(txt))).id
        else:
            await msg.answer("❌ @username, chat id ya forwarded post bhejo"); return
        await bot.send_message(chat_id, pe("✅ <b>AVERON sales logger connected</b>\nYahan ab sales log aayenge."), parse_mode="HTML")
    except Exception as e:
        await msg.answer(pe(f"❌ Connect nahi hua: <code>{html.escape(type(e).__name__)}</code>\n"
                            "Bot ko channel/group me <b>Admin</b> banao (Post messages), phir dobara bhejo ya /cancel."),
                         parse_mode="HTML"); return
    set_setting(key, str(chat_id)); await state.clear()
    t, kb = slog_home()
    await msg.answer(pe("✅ <b>Connected!</b> Test message bhej diya.\n\n" + t), parse_mode="HTML", reply_markup=kb)

@dp.message(SlogState.setpub)
async def slog_setpub_msg(msg: Message, state: FSMContext):
    await _slog_set_chat(msg, state, "pub")

@dp.message(SlogState.setpriv)
async def slog_setpriv_msg(msg: Message, state: FSMContext):
    await _slog_set_chat(msg, state, "priv")

@dp.message(SlogState.addsale)
async def slog_addsale_msg(msg: Message, state: FSMContext):
    if not can_manage_services(msg.from_user.id):
        await state.clear(); return
    if (msg.text or "").startswith("/cancel"):
        await state.clear(); await msg.answer("❌ Cancelled"); return
    try:
        m = sales_store.parse_manual_line(msg.text or "")
    except ValueError as e:
        await msg.answer(pe(f"❌ {html.escape(str(e))}\nDobara bhejo ya /cancel"), parse_mode="HTML"); return
    await state.update_data(slog_manual=m)
    cost_txt = "unknown" if m["cost"] is None else f"₹{m['cost']:,.0f}"
    b = InlineKeyboardBuilder()
    b.button(text="✅ CONFIRM & LOG", callback_data="slog_addok")
    b.button(text="❌ CANCEL", callback_data="slog")
    b.adjust(1)
    await msg.answer(pe("✍️ <b>CONFIRM MANUAL SALE</b>\n━━━━━━━━━━━━━━━━━━━━\n\n"
                        f"🛍️ {html.escape(m['product'])}\n🔢 Qty: {m['quantity']}\n💰 Price: ₹{m['amount']:,.0f}\n"
                        f"🏷️ Cost: {cost_txt}\n👤 Buyer: {html.escape(m['buyer']) or '—'}"),
                     parse_mode="HTML", reply_markup=b.as_markup())

@dp.message(Command("sold"))
async def sold_cmd(msg: Message, state: FSMContext):
    if not can_manage_services(msg.from_user.id):
        return
    args = (msg.text or "").split(maxsplit=1)
    if len(args) < 2:
        await msg.answer(pe("✍️ <b>MANUAL SALE</b>\n\n<code>/sold product | quantity | price | cost | buyer</code>\n\n"
                            "Example:\n<code>/sold Apple Music 5M | 1 | 149 | 100 | Rahul</code>"), parse_mode="HTML"); return
    try:
        m = sales_store.parse_manual_line(args[1])
    except ValueError as e:
        await msg.answer(pe(f"❌ {html.escape(str(e))}"), parse_mode="HTML"); return
    await state.clear()
    rec = slog_record_manual(m, msg.from_user.id)
    b = InlineKeyboardBuilder()
    b.button(text="🗑️ UNDO / DELETE", callback_data=f"slog_d_{rec['id']}_0")
    await msg.answer(pe(f"✅ <b>Sale #{rec['id']} logged</b>\n{html.escape(m['product'])} × {m['quantity']} = ₹{m['amount']:,.0f}\n"
                        "Channel / private log me ja raha hai."), parse_mode="HTML", reply_markup=b.as_markup())

@dp.message(Command("sales"))
async def sales_cmd(msg: Message):
    if not can_manage_services(msg.from_user.id):
        return
    conn = db(); txt = sales_store.format_report(conn); conn.close()
    await msg.answer(pe(txt[:4000]), parse_mode="HTML")

# ══════════════════════════════════════════════════
#  🔌 API CONTROL CENTER — kaun si API ON/OFF + us API ka kaun sa premium ON/OFF
#  ON  = customers ko Service 1 me dikhta hai & kharid sakte hain
#  OFF = chhup jata hai (purchase bhi block)
# ══════════════════════════════════════════════════
APIC_PER_PAGE = 8
_APIC_Q: dict[int, tuple[str, str]] = {}      # admin id -> (provider, search text)

class ApicState(StatesGroup):
    search = State()

def s1_set_hidden_many(pkeys, hidden: bool):
    conn = db(); c = conn.cursor()
    for k in pkeys:
        c.execute("INSERT OR IGNORE INTO s1_overrides (pkey) VALUES (?)", (k,))
        c.execute("UPDATE s1_overrides SET hidden=? WHERE pkey=?", (1 if hidden else 0, k))
    conn.commit(); conn.close()

def _apic_row(it: dict, cb: str):
    if it["hidden"]:
        mark, style = "🔴", ButtonStyle.DANGER
    elif it["stock"] > 0:
        mark, style = "🟢", ButtonStyle.SUCCESS
    else:
        mark, style = "🟡", ButtonStyle.PRIMARY
    return [create_btn(f"{mark} {it['name'][:26]} — ₹{it['price']}", cb=cb[:64], style=style, no_emoji=True)]

async def apic_home():
    items = await s1_catalog(admin=True)
    lines, rows = [], []
    for p in supplier_store.PROVIDER_IDS:
        its = [i for i in items if i["provider"] == p]
        key = supplier_clients.key_configured(p)
        live = sup_on(p) and key
        shown = sum(1 for i in its if not i["hidden"])
        label = supplier_clients.PROVIDER_LABELS[p]
        lines.append(f"{'🟢' if live else '🔴'} <b>{label}</b> — API {'ON' if sup_on(p) else 'OFF'}"
                     f"{'' if key else ' ⚠️ key missing'} | products ON {shown}/{len(its)}")
        rows.append([create_btn(f"{'🟢' if live else '🔴'} {label} — {shown}/{len(its)} ON", cb=f"apicp_{p}_0",
                                style=ButtonStyle.SUCCESS if live else ButtonStyle.DANGER, no_emoji=True)])
    rows.append([create_btn(f"🤖 SERVICE 1 AUTO DELIVERY: {'ON' if s1_auto() else 'OFF'}", cb="apic_s1auto",
                            style=ButtonStyle.SUCCESS if s1_auto() else ButtonStyle.DANGER, no_emoji=True)])
    rows.append([create_btn(f"🤖 SERVICE 2 AUTO DELIVERY: {'ON' if sup_auto_global() else 'OFF'}", cb="apic_s2auto",
                            style=ButtonStyle.SUCCESS if sup_auto_global() else ButtonStyle.DANGER, no_emoji=True)])
    rows.append([create_btn("💰 ALL BALANCES", cb="apic_bal", style=ButtonStyle.PRIMARY, no_emoji=True),
                 create_btn("🔄 REFRESH", cb="apic_ref", style=ButtonStyle.PRIMARY, no_emoji=True)])
    rows.append([create_btn("⚡ PRICE / PROFIT", cb="s1adm", style=ButtonStyle.PRIMARY, no_emoji=True),
                 create_btn("🔗 MAPPING", cb="sup_home", style=ButtonStyle.PRIMARY, no_emoji=True)])
    rows.append([create_btn("🔙 BACK", cb="admin_back", style=ButtonStyle.DANGER, no_emoji=True)])
    txt = ("🔌 <b>API CONTROL CENTER</b>\n━━━━━━━━━━━━━━━━━━━━\n\n" + "\n".join(lines) +
           "\n\n👉 API par tap karo → uske <b>premium products</b> ON/OFF karo.\n"
           "🟢 ON = customer ko dikhega | 🔴 OFF = chhup jayega (purchase block)")
    return txt, InlineKeyboardMarkup(inline_keyboard=rows)

async def apic_provider(p: str, page: int = 0):
    items = [i for i in await s1_catalog(admin=True) if i["provider"] == p]
    pages = max(1, (len(items) + APIC_PER_PAGE - 1) // APIC_PER_PAGE)
    page = max(0, min(page, pages - 1))
    rows = [_apic_row(it, f"apict_{page}_{it['pkey']}") for it in items[page * APIC_PER_PAGE:(page + 1) * APIC_PER_PAGE]]
    nav = []
    if page > 0:
        nav.append(create_btn("⬅️", cb=f"apicp_{p}_{page - 1}", no_emoji=True))
    nav.append(create_btn(f"📄 {page + 1}/{pages}", cb="startpg_noop", style=ButtonStyle.PRIMARY, no_emoji=True))
    if page < pages - 1:
        nav.append(create_btn("➡️", cb=f"apicp_{p}_{page + 1}", no_emoji=True))
    rows.append(nav)
    rows.append([create_btn("✅ ALL ON", cb=f"apic_all_{p}_1", style=ButtonStyle.SUCCESS, no_emoji=True),
                 create_btn("⛔ ALL OFF", cb=f"apic_all_{p}_0", style=ButtonStyle.DANGER, no_emoji=True)])
    rows.append([create_btn("🔎 SEARCH", cb=f"apic_srch_{p}", style=ButtonStyle.PRIMARY, no_emoji=True),
                 create_btn("💰 BALANCE", cb=f"apic_bal_{p}", style=ButtonStyle.PRIMARY, no_emoji=True)])
    on = sup_on(p)
    rows.append([create_btn("🔴 TURN THIS API OFF" if on else "🟢 TURN THIS API ON", cb=f"apic_ptog_{p}",
                            style=ButtonStyle.DANGER if on else ButtonStyle.SUCCESS, no_emoji=True)])
    rows.append([create_btn("🔙 BACK", cb="apic", style=ButtonStyle.DANGER, no_emoji=True)])
    shown = sum(1 for i in items if not i["hidden"])
    label = supplier_clients.PROVIDER_LABELS[p]
    key = supplier_clients.key_configured(p)
    txt = (f"🔌 <b>{label}</b> — {'API ON 🟢' if on else 'API OFF 🔴'}{'' if key else ' ⚠️ key missing'}\n"
           f"━━━━━━━━━━━━━━━━━━━━\n\nProducts ON: <b>{shown}/{len(items)}</b>\n"
           "Product par tap = ON ↔ OFF\n🟢 ON | 🔴 OFF (hidden) | 🟡 ON par stock 0"
           + ("" if items else "\n\n😔 Catalog khali / load nahi hua — 🔄 REFRESH karo ya key check karo."))
    return txt, InlineKeyboardMarkup(inline_keyboard=rows)

async def _apic_search_view(uid: int):
    p, q = _APIC_Q.get(uid, ("", ""))
    items = [i for i in await s1_catalog(admin=True)
             if i["provider"] == p and (q in i["name"].lower() or q in i["id"].lower())]
    rows = [_apic_row(it, f"apict_q_{it['pkey']}") for it in items[:12]]
    if items:
        rows.append([create_btn(f"✅ ALL {len(items)} ON", cb="apic_q1", style=ButtonStyle.SUCCESS, no_emoji=True),
                     create_btn(f"⛔ ALL {len(items)} OFF", cb="apic_q0", style=ButtonStyle.DANGER, no_emoji=True)])
    rows.append([create_btn("🔎 NEW SEARCH", cb=f"apic_srch_{p}", style=ButtonStyle.PRIMARY, no_emoji=True)])
    rows.append([create_btn("🔙 BACK", cb=f"apicp_{p}_0", style=ButtonStyle.DANGER, no_emoji=True)])
    txt = (f"🔎 <b>{html.escape(supplier_clients.PROVIDER_LABELS.get(p, p))} — “{html.escape(q)}”</b>\n"
           f"━━━━━━━━━━━━━━━━━━━━\n\n{len(items)} match mile"
           + (f" (pehle 12 dikh rahe)" if len(items) > 12 else "") + "\nTap = ON ↔ OFF, ya neeche bulk button.")
    return txt, InlineKeyboardMarkup(inline_keyboard=rows), items

async def _apic_balances(providers):
    async def one(p):
        try:
            return supplier_store.balance_text(await supplier_clients.fetch_balance(p))
        except (supplier_clients.SupplierAPIError, ValueError) as e:
            return f"❌ {e}"
        except Exception as e:
            return f"❌ {type(e).__name__}"
    res = await asyncio.gather(*[one(p) for p in providers])
    return "\n".join(f"• <b>{supplier_clients.PROVIDER_LABELS[p]}</b>: <code>{html.escape(str(r))}</code>"
                     for p, r in zip(providers, res)) or "Kisi API ki key set nahi hai."

@dp.callback_query(lambda c: (c.data or "").startswith("apic"))
async def apic_cb(cb: CallbackQuery, state: FSMContext):
    data, uid = cb.data, cb.from_user.id
    if not can_manage_services(uid):
        await cb.answer("⛔ No permission", show_alert=True); return

    if data == "apic":
        await state.clear(); await cb.answer("⏳ Loading…")
        t, kb = await apic_home(); await _sup_edit(cb, t, kb); return

    if data.startswith("apicp_"):
        p, _, pg = data[6:].rpartition("_")
        if p not in supplier_store.PROVIDER_IDS:
            await cb.answer(); return
        await cb.answer("⏳ Loading…")
        t, kb = await apic_provider(p, int(pg) if pg.isdigit() else 0); await _sup_edit(cb, t, kb); return

    if data.startswith("apict_"):
        pg, _, pk = data[6:].partition("_")
        items = await s1_catalog(admin=True)
        it = next((i for i in items if i["pkey"] == pk), None) or \
             next((i for i in items if len(pk) >= 50 and i["pkey"].startswith(pk)), None)
        if not it:
            await cb.answer("❌ Product nahi mila — 🔄 REFRESH karo", show_alert=True); return
        s1_set_override(it["pkey"], hidden=0 if it["hidden"] else 1)
        await cb.answer(f"{'🟢 ON' if it['hidden'] else '🔴 OFF'} — {it['name'][:40]}")
        if pg == "q":
            t, kb, _ = await _apic_search_view(uid)
        else:
            t, kb = await apic_provider(it["provider"], int(pg) if pg.isdigit() else 0)
        await _sup_edit(cb, t, kb); return

    if data.startswith("apic_all_"):
        p, _, flag = data[9:].rpartition("_")
        if p not in supplier_store.PROVIDER_IDS or flag not in ("0", "1"):
            await cb.answer(); return
        items = [i for i in await s1_catalog(admin=True) if i["provider"] == p]
        s1_set_hidden_many([i["pkey"] for i in items], hidden=(flag == "0"))
        await cb.answer(f"{'✅ Sab ON' if flag == '1' else '⛔ Sab OFF'} ({len(items)})")
        t, kb = await apic_provider(p, 0); await _sup_edit(cb, t, kb); return

    if data.startswith("apic_ptog_"):
        p = data[10:]
        if p not in supplier_store.PROVIDER_IDS:
            await cb.answer(); return
        set_setting(f"sup_on_{p}", "0" if sup_on(p) else "1")
        await cb.answer(f"{supplier_clients.PROVIDER_LABELS[p]} API {'ON' if sup_on(p) else 'OFF'}")
        t, kb = await apic_provider(p, 0); await _sup_edit(cb, t, kb); return

    if data in ("apic_s1auto", "apic_s2auto"):
        if data == "apic_s1auto":
            set_setting("s1_auto", "0" if s1_auto() else "1")
        else:
            set_setting("sup_auto_global", "0" if sup_auto_global() else "1")
        await cb.answer("✅ Updated")
        t, kb = await apic_home(); await _sup_edit(cb, t, kb); return

    if data == "apic_ref":
        await cb.answer("🔄 API se naya catalog la raha hu…")
        await s1_catalog(force=True, admin=True)
        t, kb = await apic_home(); await _sup_edit(cb, t, kb); return

    if data == "apic_bal" or data.startswith("apic_bal_"):
        await cb.answer("💰 Balance check…")
        p = data[9:]
        provs = [p] if p in supplier_store.PROVIDER_IDS else [x for x in supplier_store.PROVIDER_IDS if supplier_clients.key_configured(x)]
        back = f"apicp_{p}_0" if p in supplier_store.PROVIDER_IDS else "apic"
        await _sup_edit(cb, "💰 <b>SUPPLIER BALANCE</b>\n━━━━━━━━━━━━━━━━━━━━\n\n" + await _apic_balances(provs), _slog_back(back)); return

    if data.startswith("apic_srch_"):
        p = data[10:]
        if p not in supplier_store.PROVIDER_IDS:
            await cb.answer(); return
        await cb.answer()
        await state.set_state(ApicState.search); await state.update_data(apic_p=p)
        await _sup_edit(cb, f"🔎 <b>SEARCH — {supplier_clients.PROVIDER_LABELS[p]}</b>\n\n"
                            "Product ka naam (ya id) ka kuch hissa bhejo, jaise <code>netflix</code> ya <code>spotify</code>.\n/cancel = band",
                        _slog_back(f"apicp_{p}_0")); return

    if data in ("apic_q1", "apic_q0"):
        _, _, items = await _apic_search_view(uid)
        s1_set_hidden_many([i["pkey"] for i in items], hidden=(data == "apic_q0"))
        await cb.answer(f"{'✅ ON' if data == 'apic_q1' else '⛔ OFF'} — {len(items)} products")
        t, kb, _ = await _apic_search_view(uid); await _sup_edit(cb, t, kb); return

    await cb.answer()

@dp.message(ApicState.search)
async def apic_search_msg(msg: Message, state: FSMContext):
    if not can_manage_services(msg.from_user.id):
        await state.clear(); return
    t = (msg.text or "").strip()
    if t.startswith("/cancel"):
        await state.clear(); await msg.answer("❌ Cancelled"); return
    if not t:
        await msg.answer("❌ Text bhejo"); return
    p = (await state.get_data()).get("apic_p", "")
    await state.clear()
    _APIC_Q[msg.from_user.id] = (p, t.lower()[:40])
    txt, kb, _ = await _apic_search_view(msg.from_user.id)
    await msg.answer(pe(txt), parse_mode="HTML", reply_markup=kb)

@dp.callback_query()
async def handle_cb(cb: CallbackQuery, state: FSMContext):
    data = cb.data
    uid  = cb.from_user.id

    if data and data.startswith("sup_"):
        await supplier_cb(cb, state)
        return

    # ── Channel join check ─────────────────────────
    if data == "check_join":
        if await is_joined(uid):
            # Complete pending referral if any
            fsm_data = await state.get_data()
            rid = fsm_data.get("pending_referrer")
            if rid:
                await _process_referral(uid, rid)
                await state.update_data(pending_referrer=None)
            # Edit the join-button message into the full welcome screen
            wtext = pe(start_header() + (get_setting("welcome_msg") or WELCOME_MSG))
            try:
                await cb.message.edit_text(
                    wtext,
                    parse_mode="HTML",
                    reply_markup=welcome_kb(joined=True)
                )
            except Exception:
                await cb.message.answer(
                    wtext,
                    parse_mode="HTML",
                    reply_markup=welcome_kb(joined=True)
                )
            await cb.answer("✅ Welcome! Bot is now unlocked!", show_alert=True)
        else:
            await cb.answer("❌ Please join the channel first!", show_alert=True)
        return

    # ── Navigation ─────────────────────────────────
    if data == "buy_now":
        # Open the paginated services browse screen (new message or edit-in-place)
        text = browse_text(0)
        kb   = browse_kb(0)
        try:
            await cb.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
        except Exception:
            await cb.message.answer(text, parse_mode="HTML", reply_markup=kb)
        await cb.answer()
        return

    if data == "back":
        # Return to the welcome screen with action buttons — edit in-place
        wtext = pe(start_header() + (get_setting("welcome_msg") or WELCOME_MSG))
        try:
            await cb.message.edit_text(
                wtext,
                parse_mode="HTML",
                reply_markup=welcome_kb(joined=True)
            )
        except Exception:
            await cb.message.answer(
                wtext,
                parse_mode="HTML",
                reply_markup=welcome_kb(joined=True)
            )
        await cb.answer()
        return

    if data == "no":
        try:
            await cb.message.edit_text(pe("❌ <b>Cancelled.</b> Type a service name to start!"), parse_mode="HTML")
        except Exception:
            pass
        await cb.answer()
        return

    if data == "sold_out":
        await cb.answer("❌ Sold out! Choose another duration.", show_alert=True)
        return

    if data == "not_delivered":
        await cb.answer("⚠️ Contact admin to resolve.", show_alert=True)
        return

    if data == "contact":
        await cb.message.answer(
            pe("📞 <b>Contact Support</b>\n━━━━━━━━━━━━━━━━━━━━\n\nMessage: @AVERON_DEALS\n\n📢 Reviews: https://t.me/AVERON_DEALS_REVIEWS"),
            parse_mode="HTML", reply_markup=main_kb()
        )
        await cb.answer()
        return

    if data == "my_orders":
        orders = get_user_orders(uid)
        if not orders:
            await cb.message.answer(pe("📭 <b>No orders found</b>"), parse_mode="HTML")
        else:
            text = "📦 <b>YOUR ORDERS</b>\n━━━━━━━━━━━━━━━━━━━━\n\n"
            for o in orders:
                em = "✅" if o[4]=="delivered" else ("⏳" if o[4]=="pending" else "❌")
                text += f"{em} <code>{o[0]}</code> — {o[1]} ({o[2]}) — ₹{o[3]}\n"
            await cb.message.answer(pe(text), parse_mode="HTML")
        await cb.answer()
        return

    if data == "wallet_view":
        await _show_wallet(cb, uid)
        return

    if data == "ref_view":
        await _show_referral(cb, uid)
        return

    # ── BROWSE SCREEN PAGINATION ───────────────────
    if data == "startpg_noop":
        # Page indicator button — acknowledge tap silently, no action
        await cb.answer()
        return

    if data.startswith("startpg_"):
        # Navigate between pages — always edit-in-place (single message)
        try:
            page = int(data.split("_")[1])
        except (IndexError, ValueError):
            page = 0
        text = browse_text(page)
        kb   = browse_kb(page)
        try:
            await cb.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
        except Exception:
            await cb.message.answer(text, parse_mode="HTML", reply_markup=kb)
        await cb.answer()
        return

    if data.startswith("browse_"):
        # User tapped a service tile from the browse grid
        service_key = data[7:]
        if not is_admin(uid) and not await is_joined(uid):
            await cb.answer("❌ Join the channel first!", show_alert=True)
            return
        if service_key not in PRODUCTS:
            await cb.answer("⚠️ Service not available right now.", show_alert=True)
            return
        # show_product_menu sends a NEW message (duration picker)
        await show_product_menu(cb, service_key)
        return

    # ── Duration selection ─────────────────────────
    if data.startswith("dur_"):
        if not await is_joined(uid):
            await cb.answer("❌ Join channel first!", show_alert=True)
            return
        parts       = data.split("_", 3)
        product_key = parts[1]
        duration    = parts[2]
        amount      = int(parts[3])

        # Payment method screen — wallet AND QR always offered
        bal, _unlocked = get_wallet(uid)
        text = (f"🛒 <b>YOUR SELECTION</b>\n━━━━━━━━━━━━━━━━━━━━\n\n"
                f"📺 {PRODUCTS[product_key]['display_name']}\n"
                f"⏱️ {duration}\n"
                f"💰 Amount: <b>₹{amount}</b>\n"
                f"💚 Wallet Balance: <b>₹{bal:.2f}</b>\n\n"
                f"👇 <b>Payment method choose kijiye:</b>")
        try:
            await cb.message.edit_text(pe(text), parse_mode="HTML",
                                       reply_markup=payment_method_kb(product_key, duration, amount, bal))
        except Exception:
            await cb.message.answer(pe(text), parse_mode="HTML",
                                    reply_markup=payment_method_kb(product_key, duration, amount, bal))
        await cb.answer()
        return

    # ══ MANUAL PAY (QR SCANNER) — ADD MONEY ════════
    if data == "qradd":
        await ask_qr_amount(cb.message, state)
        await cb.answer()
        return

    # ══ PAYMENT GATEWAY — USER ═════════════════════
    if data == "gwadd":
        if not gw_enabled():
            await cb.answer("⚡ Auto payment abhi band hai. 'qr 100' use kijiye.", show_alert=True)
            return
        await state.set_state(GatewayState.waiting_amount)
        await state.update_data(gw_purpose="deposit")
        await cb.message.answer(
            pe(f"⚡ <b>INSTANT WALLET RECHARGE</b>\n━━━━━━━━━━━━━━━━━━━━\n\n"
               f"Kitna amount add karna hai? Sirf number bhejiye.\n"
               f"Minimum: <b>₹{gw_min()}</b>\n\n"
               f"Payment hote hi wallet me <b>automatic</b> add ho jayega."),
            parse_mode="HTML"
        )
        await cb.answer()
        return

    if data.startswith("gwbuy_"):
        parts       = data.split("_", 3)
        product_key = parts[1]
        duration    = parts[2]
        amount      = int(parts[3])
        if not gw_enabled():
            await cb.answer("⚡ Auto payment abhi band hai — QR use kijiye.", show_alert=True)
            return
        shop_oid = generate_order_id(uid)
        save_order(shop_oid, uid, product_key, duration, amount)
        await cb.answer("⚡ QR ban raha hai...")
        await gw_send_payment(
            cb.message.chat.id, uid, amount, f"order:{shop_oid}", "ONLINE PAYMENT",
            f"📺 {PRODUCTS.get(product_key,{}).get('display_name', product_key)}\n⏱️ {duration}\n"
            f"🆔 Order: <code>{shop_oid}</code>\n", remark=f"order {shop_oid}")
        return

    if data.startswith("gwo_"):
        _p = data.split("_")
        shop_oid = _p[1]
        amount   = int(_p[2])
        if not gw_enabled():
            await cb.answer("⚡ Auto pay abhi band hai — MANUAL QR use kijiye.", show_alert=True)
            return
        await cb.answer("⚡ QR ban raha hai...")
        await gw_send_payment(cb.message.chat.id, uid, amount, f"order:{shop_oid}", f"AUTO PAY — Rs {amount}",
                              f"🆔 Order: <code>{shop_oid}</code>\n", remark=f"order {shop_oid}")
        return

    if data.startswith("qro_"):
        _p = data.split("_")
        product_key, duration, amount, shop_oid = _p[1], _p[2], int(_p[3]), _p[4]
        try:
            await cb.message.delete()
        except Exception:
            pass
        await send_payment_qr(
            cb.message, amount,
            (f"{sty('MANUAL PAY — SCAN QR')} 📷\n"
             + bq(f"🏦 UPI: {UPI_ID}\n"
                  f"💰 Amount: Rs {amount}\n"
                  f"⏳ QR valid: {QR_VALID_MIN} minutes\n"
                  f"🆔 Order: {shop_oid}")
             + f"\n{sty('1 Scan QR  2 Pay  3 Click I HAVE PAID')}"),
            reply_markup=paid_kb(product_key, duration, amount, shop_oid))
        await cb.answer()
        return

    if data.startswith("gwchk_"):
        gw_oid = data.split("_", 1)[1]
        row = gw_get(gw_oid)
        if not row:
            await cb.answer("❌ Payment record nahi mila.", show_alert=True)
            return
        if row[7]:
            await cb.answer("✅ Ye payment already confirm ho chuka hai.", show_alert=True)
            return
        state_, msg_ = await gw_check_status(gw_oid)
        if state_ == "success":
            await gw_settle(gw_oid)
            await cb.answer("✅ Payment mil gaya!", show_alert=True)
            try:
                await cb.message.edit_reply_markup(reply_markup=None)
            except Exception:
                pass
        elif state_ == "failed":
            gw_set_status(gw_oid, "failed")
            purpose = row[3] or ""
            if purpose.startswith("order:"):
                shop_oid = purpose.split(":", 1)[1]
                refunded, bal, _u = cancel_order_with_refund(shop_oid, "Online payment failed")
                await cb.message.answer(pe(refund_message(shop_oid, refunded, bal)), parse_mode="HTML")
            await cb.answer("❌ Payment fail/cancel ho gaya. Dobara try kijiye.", show_alert=True)
        else:
            await cb.answer("⏳ Abhi payment nahi mila. Pay karke 10-20 sec baad check kijiye.", show_alert=True)
        return

    if data.startswith("gwcan_"):
        gw_oid = data.split("_", 1)[1]
        row = gw_get(gw_oid)
        refunded, bal = 0.0, get_wallet(uid)[0]
        shop_oid = gw_oid
        if row and not row[7]:
            gw_set_status(gw_oid, "cancelled")
            purpose = row[3] or ""
            if purpose.startswith("order:"):
                shop_oid = purpose.split(":", 1)[1]
                refunded, bal, _u = cancel_order_with_refund(
                    shop_oid, "Online payment cancelled")
        try:
            await cb.message.edit_reply_markup(reply_markup=None)
        except Exception:
            pass
        await cb.message.answer(pe(refund_message(shop_oid, refunded, bal)), parse_mode="HTML")
        await cb.answer("❌ Cancel — wallet safe.")
        return

    # ── ORDER / QR PAYMENT CANCEL (wallet refund) ──
    if data.startswith("ocan_"):
        oid = data.split("_", 1)[1]
        row = get_order(oid)
        if not row:
            await cb.answer("❌ Order record nahi mila.", show_alert=True)
            return
        refunded, bal, owner = cancel_order_with_refund(oid, "User ne payment cancel kiya")
        if owner and owner != uid and not is_admin(uid):
            await cb.answer("⛔ Ye order aapka nahi hai.", show_alert=True)
            return
        try:
            await cb.message.edit_reply_markup(reply_markup=None)
        except Exception:
            pass
        await cb.message.answer(pe(refund_message(oid, refunded, bal)), parse_mode="HTML")
        if refunded > 0:
            for adm in [r[0] for r in get_all_admins()]:
                try:
                    await bot.send_message(
                        adm,
                        pe(f"{sty('WALLET REFUND ISSUED')} 💸\n"
                           + bq(f"Order: {oid}\nUser: {uid}\n"
                                f"Refund: ₹{refunded:.2f}\n"
                                f"Time: {now_str()} IST")),
                        parse_mode="HTML")
                except Exception:
                    pass
        await cb.answer("💚 Wallet balance wapas aa gaya.")
        return

    # ══ PAYMENT HISTORY (owner + admins) ═══════════
    if data.startswith("payhist_"):
        if not is_admin(uid):
            await cb.answer("⛔ Unauthorized", show_alert=True)
            return
        which  = data.split("_", 1)[1]
        method = {"autopay": PM_AUTO, "manual": PM_MANUAL}.get(which)
        txt    = pay_history_text(method, limit=12)
        kb     = pay_history_kb(method)
        try:
            await cb.message.edit_text(pe(txt), parse_mode="HTML", reply_markup=kb)
        except Exception:
            await cb.message.answer(pe(txt), parse_mode="HTML", reply_markup=kb)
        await cb.answer()
        return

    # ══ PAYMENT GATEWAY — ADMIN ════════════════════
    if data == "admin_gateway":
        if not is_admin(uid):
            await cb.answer("⛔ Unauthorized", show_alert=True)
            return
        ok_cnt, ok_sum, pend = gw_stats()
        tok = gw_token()
        masked = (tok[:4] + "•" * 6 + tok[-4:]) if len(tok) > 8 else ("set" if tok else "❌ not set")
        await cb.message.edit_text(
            pe(f"⚡ <b>PAYMENT GATEWAY</b>\n━━━━━━━━━━━━━━━━━━━━\n\n"
               f"Status: <b>{'🟢 ON' if gw_enabled() else '🔴 OFF'}</b>\n"
               f"Panel: <code>{gw_base()}</code>\n"
               f"Token: <code>{masked}</code>\n"
               f"Min amount: ₹{gw_min()}\n\n"
               f"✅ Successful: {ok_cnt} — ₹{ok_sum:.0f}\n"
               f"⏳ Pending: {pend}\n\n"
               f"<i>Payment aate hi wallet/order auto confirm ho jata hai (har "
               f"{GATEWAY_POLL_SECONDS}s auto-check).</i>"),
            parse_mode="HTML",
            reply_markup=gw_admin_kb()
        )
        await cb.answer()
        return

    if data.startswith("gwadm_"):
        if not is_admin(uid):
            await cb.answer("⛔ Unauthorized", show_alert=True)
            return
        act = data.split("_", 1)[1]
        if act == "toggle":
            if not gw_token():
                await cb.answer("🔑 Pehle API token set kijiye.", show_alert=True)
                return
            set_setting("gw_enabled", "0" if get_setting("gw_enabled", "0") == "1" else "1")
            await cb.answer("✅ Updated")
            try:
                await cb.message.edit_reply_markup(reply_markup=gw_admin_kb())
            except Exception:
                pass
            return
        if act == "token":
            await state.set_state(GatewayState.waiting_token)
            await cb.message.answer(
                pe("🔑 <b>Gateway API token bhejiye</b>\n\n"
                   "iPey.shop panel → login → <b>API / Developer</b> section → "
                   "<code>user_token</code> copy karke yahan paste kijiye."),
                parse_mode="HTML")
            await cb.answer(); return
        if act == "base":
            await state.set_state(GatewayState.waiting_base)
            await cb.message.answer(
                pe(f"🌐 <b>Panel URL bhejiye</b>\n\nAbhi: <code>{gw_base()}</code>\n"
                   f"Example: <code>https://manage.globalnexpay.site</code>"), parse_mode="HTML")
            await cb.answer(); return
        if act == "min":
            await state.set_state(GatewayState.waiting_min)
            await cb.message.answer(pe("💵 <b>Minimum auto-deposit amount bhejiye (number):</b>"), parse_mode="HTML")
            await cb.answer(); return
        if act == "test":
            await cb.answer("🧪 Testing…")
            test_id = gw_new_id(uid)
            ok, url, err = await gw_create_order(gw_min(), test_id, remark="test")
            if ok:
                gw_save(test_id, uid, gw_min(), "deposit", "", url)
                await cb.message.answer(
                    pe(f"✅ <b>Gateway working!</b>\n\nTest link bana:\n{url}\n\n"
                       f"Pay karoge to ₹{gw_min()} wallet me auto add ho jayega."),
                    parse_mode="HTML")
            else:
                await cb.message.answer(
                    pe(f"❌ <b>Gateway error</b>\n\n<code>{err}</code>\n\n"
                       f"Token / panel URL check kijiye."), parse_mode="HTML")
            return
        if act == "pending":
            rows = gw_pending_rows()[:15]
            txt = "📋 <b>PENDING ONLINE PAYMENTS</b>\n━━━━━━━━━━━━━━━━━━━━\n\n"
            if not rows:
                txt += "Koi pending payment nahi."
            for r in rows:
                txt += f"🆔 <code>{r[0]}</code>\n👤 {r[1]} — ₹{r[2]:.0f} — {r[3]}\n🕒 {r[8]}\n\n"
            await cb.message.answer(pe(txt), parse_mode="HTML")
            await cb.answer(); return
        await cb.answer()
        return

    if data == "wallet_empty":
        await cb.answer("💚 Wallet khali hai. 'qr 100' type karke money add kijiye.", show_alert=True)
        return

    # ── Wallet USE ─────────────────────────────────
    if data.startswith("wuse_"):
        parts       = data.split("_", 3)
        product_key = parts[1]
        duration    = parts[2]
        amount      = int(parts[3])
        bal, _unlocked = get_wallet(uid)
        use_amt     = min(bal, amount)
        remaining   = int(amount - use_amt)
        if use_amt <= 0:
            await cb.answer("💚 Wallet balance ₹0 — QR se pay kijiye.", show_alert=True)
            return

        if remaining == 0:
            # Full wallet payment
            oid = generate_order_id(uid)
            save_order(oid, uid, product_key, duration, amount, wallet_used=use_amt)
            debit_wallet(uid, use_amt, f"Purchase {oid} — {PRODUCTS.get(product_key,{}).get('display_name','')}")
            update_order_status(oid, "approved")
            add_order_spent(uid, amount)
            decrement_stock(product_key, duration)
            schedule_autofulfill(oid)

            # Notify admins for delivery
            conn = db()
            c = conn.cursor()
            c.execute("SELECT user_id FROM admins")
            admins_list = [r[0] for r in c.fetchall()]
            conn.close()
            for adm in admins_list:
                try:
                    await bot.send_message(
                        adm,
                        pe(f"💚 <b>WALLET PAYMENT — DELIVERY REQUIRED</b>\n━━━━━━━━━━━━━━━━━━━━\n\n"
                           f"Order: <code>{oid}</code>\n"
                           f"User: <code>{uid}</code>\n"
                           f"Product: {PRODUCTS.get(product_key,{}).get('display_name','')}\n"
                           f"Duration: {duration}\n"
                           f"Amount: ₹{amount} (paid via wallet)\n\n"
                           f"No screenshot required — wallet payment confirmed."),
                        parse_mode="HTML",
                        reply_markup=deliver_kb(oid, uid, amount)
                    )
                except Exception:
                    pass

            await cb.message.edit_text(
                pe(f"{sty('WALLET PAYMENT SUCCESSFUL')} 💚\n"
                   + bq(f"🆔 Order: {oid}\n"
                        f"💸 Deducted: ₹{use_amt:.0f}\n"
                        f"🕒 {now_str()} IST")
                   + f"\n{sty('Admin thodi der me deliver karega')} 🚀"),
                parse_mode="HTML"
            )
        else:
            # Partial wallet payment — generate QR for remaining
            oid = generate_order_id(uid)
            save_order(oid, uid, product_key, duration, amount, wallet_used=use_amt)
            debit_wallet(uid, use_amt, f"Partial wallet for {oid}")
            smap = get_style_map()
            rest_kb = InlineKeyboardMarkup(inline_keyboard=[
                [create_btn(f"{sty(f'AUTO PAY — INSTANT  Rs {remaining}')} ⚡",
                            cb=f"gwo_{oid}_{remaining}", style=smap[1])],
                [create_btn(f"{sty(f'MANUAL PAY — QR SCANNER  Rs {remaining}')} 📷",
                            cb=f"qro_{product_key}_{duration}_{remaining}_{oid}", style=smap[2])],
                [create_btn(f"{sty('CANCEL PAYMENT')} ❌", cb=f"ocan_{oid}", style=ButtonStyle.DANGER)],
            ])
            try:
                await cb.message.edit_text(
                    pe(f"{sty('PARTIAL WALLET PAYMENT')} 💳\n"
                       + bq(f"💚 Wallet deducted: Rs {use_amt:.0f}\n"
                            f"💳 Baki dena hai: Rs {remaining}\n"
                            f"🆔 Order: {oid}")
                       + f"\n{sty('Ab payment ka tarika chuniye — AUTO ya MANUAL')} ✨"),
                    parse_mode="HTML", reply_markup=rest_kb)
            except Exception:
                await cb.message.answer(
                    pe(f"{sty('PARTIAL WALLET PAYMENT')} 💳\n"
                       + bq(f"Baki dena hai: Rs {remaining}\nOrder: {oid}")),
                    parse_mode="HTML", reply_markup=rest_kb)
            await cb.answer()
            return

        await cb.answer()
        return

    # ── Wallet SKIP ────────────────────────────────
    if data.startswith("wskip_"):
        parts       = data.split("_", 3)
        product_key = parts[1]
        duration    = parts[2]
        amount      = int(parts[3])
        await cb.message.edit_text(
            pe(f"🛒 <b>YOUR SELECTION</b>\n━━━━━━━━━━━━━━━━━━━━\n\n"
               f"📺 {PRODUCTS[product_key]['display_name']}\n⏱️ {duration}\n💰 ₹{amount}\n\nProceed to payment?"),
            parse_mode="HTML",
            reply_markup=confirm_kb(product_key, duration, amount)
        )
        await cb.answer()
        return

    # ── Confirm YES ────────────────────────────────
    if data.startswith("yes_"):
        parts       = data.split("_", 3)
        product_key = parts[1]
        duration    = parts[2]
        amount      = int(parts[3])
        oid         = generate_order_id(uid)
        save_order(oid, uid, product_key, duration, amount)
        try:
            await cb.message.delete()
        except Exception:
            pass
        await send_payment_qr(
            cb.message,
            amount,
            (f"{sty('PAYMENT DETAILS')} 💳\n"
             + bq(f"🏦 UPI: {UPI_ID}\n"
                  f"💰 Amount: ₹{amount}\n"
                  f"⏳ QR valid: {QR_VALID_MIN} minutes "
                  f"(till {fmt_time(_now_ts() + QR_VALID_MIN * 60)} IST)\n"
                  f"🆔 Order: {oid}\n"
                  f"🕒 {now_str()} IST")
             + f"\n{sty('1 Scan QR  2 Pay  3 Click I HAVE PAID')}"),
            reply_markup=paid_kb(product_key, duration, amount, oid)
        )
        await cb.answer()
        return

    # ── I Have Paid ────────────────────────────────
    if data.startswith("paid_"):
        parts       = data.split("_", 4)
        product_key = parts[1]
        duration    = parts[2]
        amount      = int(parts[3])
        oid         = parts[4]
        await state.update_data(oid=oid, product_key=product_key, duration=duration, amount=amount)
        await cb.message.edit_caption(
            caption=pe(f"📸 <b>SEND SCREENSHOT</b>\n━━━━━━━━━━━━━━━━━━━━\n\n"
                     f"Send your payment screenshot now.\n\n"
                     f"Order ID: <code>{oid}</code>\nAmount: ₹{amount}"),
            parse_mode="HTML"
        )
        await state.set_state(OrderState.waiting_for_screenshot)
        await cb.answer()
        return

    # ── Rating ─────────────────────────────────────
    if data.startswith("rate_"):
        parts  = data.split("_")
        oid    = parts[1]
        rating = int(parts[2])
        await state.update_data(oid=oid, rating=rating)
        await cb.message.edit_text(
            pe(f"⭐ <b>Rating: {'⭐'*rating}</b>\n\n📝 Write a review (or type /skip):"),
            parse_mode="HTML"
        )
        await state.set_state(OrderState.waiting_for_review)
        await cb.answer()
        return

    if data.startswith("skip_"):
        await cb.message.edit_text(pe("✅ <b>Thank you for your order! Come back soon!</b> 🎉"), parse_mode="HTML")
        await cb.answer()
        return

    # ── Deposit I Have Paid ────────────────────────
    if data.startswith("dep_paid_"):
        parts  = data.split("_", 3)
        dep_id = parts[2]
        amount = int(parts[3])
        await state.update_data(dep_id=dep_id, dep_amount=amount)
        try:
            await cb.message.edit_caption(
                caption=pe(
                    f"📸 <b>DEPOSIT SCREENSHOT BHEJIYE</b>\n"
                    f"━━━━━━━━━━━━━━━━━━━━\n\n"
                    f"Payment ka screenshot ab bhejiye.\n\n"
                    f"💰 Amount: ₹{amount}\n"
                    f"🆔 Deposit ID: <code>{dep_id}</code>"
                ),
                parse_mode="HTML"
            )
        except Exception:
            await cb.message.answer(
                pe(f"📸 <b>DEPOSIT SCREENSHOT BHEJIYE</b>\n\nPayment ka screenshot bhejiye.\n\n💰 Amount: ₹{amount}"),
                parse_mode="HTML"
            )
        await state.set_state(DepositState.waiting_for_screenshot)
        await cb.answer()
        return

    # ══════════════════════════════════════════════
    #  ADMIN-ONLY CALLBACKS (role-gated)
    # ══════════════════════════════════════════════
    if not is_admin(uid):
        await cb.answer("⛔ Unauthorized", show_alert=True)
        return

    # ── Admin navigation ───────────────────────────
    if data == "admin_back":
        await _send_admin_panel(cb)
        return

    # ── Pending orders ─────────────────────────────
    if data == "admin_pending":
        pending = get_pending()
        if not pending:
            await cb.message.answer(pe("📭 <b>No pending orders</b>"), parse_mode="HTML")
        else:
            text = "📦 <b>PENDING ORDERS</b>\n━━━━━━━━━━━━━━━━━━━━\n\n"
            for p in pending:
                text += f"🔹 <code>{p[0]}</code> — {p[2]} ({p[3]}) — ₹{p[4]}\n"
            await cb.message.answer(pe(text), parse_mode="HTML")
        await cb.answer()
        return

    # ── Analytics ──────────────────────────────────
    if data == "admin_analytics":
        u, t, p, d, r, refs, wused = get_stats()
        d_cnt, d_rev = get_daily_sales()
        text = (f"📈 <b>FULL ANALYTICS</b>\n━━━━━━━━━━━━━━━━━━━━\n\n"
                f"👥 Total Users: {u}\n"
                f"📦 Total Orders: {t}\n"
                f"⏳ Pending: {p}\n"
                f"✅ Completed: {d}\n"
                f"💰 Total Revenue: ₹{r}\n"
                f"💚 Wallet Used: ₹{wused:.0f}\n"
                f"🤝 Total Referrals: {refs}\n\n"
                f"📅 <b>TODAY</b>\n"
                f"✅ Sales: {d_cnt}  |  Revenue: ₹{d_rev}")
        await cb.message.answer(pe(text), parse_mode="HTML")
        await cb.answer()
        return

    # ── Top services ───────────────────────────────
    if data == "admin_top":
        top = get_top_services()
        if not top:
            await cb.message.answer(pe("📊 <b>No data yet</b>"), parse_mode="HTML")
        else:
            text = "🏆 <b>TOP SELLING SERVICES</b>\n━━━━━━━━━━━━━━━━━━━━\n\n"
            for i, (nm, cnt, rev) in enumerate(top, 1):
                text += f"{i}. {nm} — {cnt} orders — ₹{rev}\n"
            await cb.message.answer(pe(text), parse_mode="HTML")
        await cb.answer()
        return

    # ── Top referrers ──────────────────────────────
    if data == "admin_referrers":
        rows = get_top_referrers()
        if not rows:
            await cb.message.answer(pe("📊 <b>No referrals yet</b>"), parse_mode="HTML")
        else:
            text = "👑 <b>TOP REFERRERS</b>\n━━━━━━━━━━━━━━━━━━━━\n\n"
            for i, (uid_, uname, cnt) in enumerate(rows, 1):
                text += f"{i}. @{uname or uid_} — {cnt} referrals\n"
            await cb.message.answer(pe(text), parse_mode="HTML")
        await cb.answer()
        return

    # ── User Kundali ───────────────────────────────
    if data == "admin_kundali":
        await cb.message.answer(pe("👤 <b>Send user ID:</b>\n\nExample: <code>8598847348</code>"), parse_mode="HTML")
        await state.set_state(AdminState.waiting_for_user_id)
        await cb.answer()
        return

    # ── Ban/Unban ──────────────────────────────────
    if data == "admin_ban":
        await cb.message.answer(pe("🚫 <b>Send user ID to ban:</b>"), parse_mode="HTML")
        await state.set_state(AdminState.waiting_for_user_id)
        await cb.answer()
        return

    if data.startswith("ban_now_"):
        target_uid = int(data.split("_")[2])
        await state.update_data(ban_user_id=target_uid)
        await cb.message.answer(pe("📝 <b>Send ban reason:</b>\n\nExample: Fraud, Spam, etc."), parse_mode="HTML")
        await state.set_state(AdminState.waiting_for_ban_reason)
        await cb.answer()
        return

    if data.startswith("unban_"):
        target_uid = int(data.split("_")[1])
        unban_user(target_uid)
        try:
            await cb.message.edit_text(pe(cb.message.text + "\n\n✅ <b>USER UNBANNED</b>"), parse_mode="HTML")
        except Exception:
            await cb.message.answer(pe(f"✅ <b>User {target_uid} UNBANNED!</b>"), parse_mode="HTML")
        try:
            await bot.send_message(target_uid, pe("✅ <b>You have been UNBANNED!</b>\n\nType /start to continue."), parse_mode="HTML")
        except Exception:
            pass
        await cb.answer("✅ Unbanned!", show_alert=True)
        return

    if data.startswith("contact_"):
        target_uid = int(data.split("_")[1])
        await cb.message.answer(pe(f"📞 User ID: <code>{target_uid}</code>\n\nMessage them directly on Telegram."), parse_mode="HTML")
        await cb.answer()
        return

    # ── Broadcast ──────────────────────────────────
    if data == "admin_broadcast":
        await cb.message.answer(
            pe("📢 <b>BROADCAST</b>\n━━━━━━━━━━━━━━━━━━━━\n\n"
               "🎯 Pehle audience choose kijiye:"),
            parse_mode="HTML", reply_markup=broadcast_audience_kb()
        )
        await cb.answer()
        return

    if data.startswith("bcaud_"):
        audience = data.split("_", 1)[1]
        targets  = get_broadcast_targets(audience)
        await state.clear()
        await state.update_data(bc_audience=audience)
        await cb.message.answer(
            pe(f"📢 <b>{AUDIENCE_LABELS.get(audience, audience)}</b>\n"
               f"👥 Recipients: <b>{len(targets)}</b>\n"
               f"━━━━━━━━━━━━━━━━━━━━\n\n"
               f"Ab wo message bhejiye jo broadcast karna hai.\n"
               f"📝 Text, 🖼️ photo, 🎥 video, 📄 file, 🔊 voice — kuch bhi chalega.\n\n"
               f"❌ Rokne ke liye /cancel"),
            parse_mode="HTML"
        )
        await state.set_state(BroadcastState.waiting_content)
        await cb.answer()
        return

    if data == "bc_cancel":
        await state.clear()
        await cb.message.edit_text(pe("❌ <b>Broadcast cancelled.</b>"), parse_mode="HTML")
        await cb.answer()
        return

    if data == "bc_send":
        bdata    = await state.get_data()
        chat_id  = bdata.get("bc_chat_id")
        msg_id   = bdata.get("bc_msg_id")
        audience = bdata.get("bc_audience", "all")
        await state.clear()
        if not chat_id or not msg_id:
            await cb.answer("⚠️ Broadcast content missing. Dobara try kijiye.", show_alert=True)
            return
        targets = get_broadcast_targets(audience)
        await cb.answer("🚀 Sending…")
        try:
            await cb.message.edit_reply_markup(reply_markup=None)
        except Exception:
            pass
        await run_broadcast(cb.message, chat_id, msg_id, targets, audience)
        return

    # ── Wallet management (give / take balance) ────
    if data == "admin_wallet":
        if not can_manage_wallet(uid):
            await cb.answer("⛔ Aapko wallet permission nahi hai.", show_alert=True)
            return
        conn = db()
        c = conn.cursor()
        c.execute("SELECT COALESCE(SUM(wallet_balance),0), COUNT(*) FROM users WHERE wallet_balance > 0")
        total, holders = c.fetchone()
        conn.close()
        await cb.message.answer(
            pe(f"💳 <b>WALLET MANAGEMENT</b>\n━━━━━━━━━━━━━━━━━━━━\n\n"
               f"💰 Total user balance: <b>₹{total:.2f}</b>\n"
               f"👥 Wallet holders: <b>{holders}</b>\n\n"
               f"Kisi bhi user ki ID par balance add ya deduct kijiye."),
            parse_mode="HTML", reply_markup=wallet_mgmt_kb()
        )
        await cb.answer()
        return

    if data in ("wadm_add", "wadm_deduct", "wadm_check"):
        if not can_manage_wallet(uid):
            await cb.answer("⛔ Aapko wallet permission nahi hai.", show_alert=True)
            return
        if data == "wadm_add":
            await cb.message.answer(
                pe("➕ <b>ADD BALANCE</b>\n━━━━━━━━━━━━━━━━━━━━\n\n"
                   "Format: <code>USER_ID AMOUNT reason</code>\n"
                   "Example: <code>123456789 100 diwali bonus</code>"), parse_mode="HTML")
            await state.set_state(WalletAdminState.waiting_add)
        elif data == "wadm_deduct":
            await cb.message.answer(
                pe("➖ <b>DEDUCT BALANCE</b>\n━━━━━━━━━━━━━━━━━━━━\n\n"
                   "Format: <code>USER_ID AMOUNT reason</code>\n"
                   "Example: <code>123456789 50 refund reverse</code>"), parse_mode="HTML")
            await state.set_state(WalletAdminState.waiting_deduct)
        else:
            await cb.message.answer(pe("🔍 <b>User ID bhejiye:</b>"), parse_mode="HTML")
            await state.set_state(WalletAdminState.waiting_check)
        await cb.answer()
        return

    if data.startswith("wquick_"):
        if not can_manage_wallet(uid):
            await cb.answer("⛔ No permission", show_alert=True)
            return
        _, action, target_id = data.split("_", 2)
        if action == "add":
            await cb.message.answer(pe(f"➕ Send: <code>{target_id} AMOUNT reason</code>"), parse_mode="HTML")
            await state.set_state(WalletAdminState.waiting_add)
        else:
            await cb.message.answer(pe(f"➖ Send: <code>{target_id} AMOUNT reason</code>"), parse_mode="HTML")
            await state.set_state(WalletAdminState.waiting_deduct)
        await cb.answer()
        return

    if data == "wadm_top":
        if not can_manage_wallet(uid):
            await cb.answer("⛔ No permission", show_alert=True)
            return
        conn = db()
        c = conn.cursor()
        c.execute("SELECT user_id, username, wallet_balance FROM users WHERE wallet_balance>0 ORDER BY wallet_balance DESC LIMIT 15")
        rows = c.fetchall()
        conn.close()
        text = "🏆 <b>TOP WALLETS</b>\n━━━━━━━━━━━━━━━━━━━━\n\n"
        if rows:
            for i, (u_, un, b_) in enumerate(rows, 1):
                text += f"{i}. <code>{u_}</code> @{un or 'N/A'} — ₹{b_:.2f}\n"
        else:
            text += "Koi wallet balance nahi hai."
        await cb.message.answer(pe(text), parse_mode="HTML")
        await cb.answer()
        return

    if data == "wadm_logs":
        if not can_manage_wallet(uid):
            await cb.answer("⛔ No permission", show_alert=True)
            return
        conn = db()
        c = conn.cursor()
        c.execute("SELECT user_id, amount, type, reason, date FROM wallet_logs ORDER BY id DESC LIMIT 15")
        rows = c.fetchall()
        conn.close()
        text = "🧾 <b>RECENT WALLET LOGS</b>\n━━━━━━━━━━━━━━━━━━━━\n\n"
        if rows:
            for u_, amt, typ, reason, date in rows:
                em = "➕" if typ == "credit" else "➖"
                text += f"{em} <code>{u_}</code> ₹{amt:.2f} — {reason[:25]} ({date[5:16]})\n"
        else:
            text += "No wallet activity yet."
        await cb.message.answer(pe(text), parse_mode="HTML")
        await cb.answer()
        return

    # ── Owner: wallet permission give/take ─────────
    if data == "admins_perms":
        if uid != OWNER_ID:
            await cb.answer("⛔ Only owner", show_alert=True)
            return
        await cb.message.answer(
            pe("💳 <b>WALLET PERMISSIONS</b>\n━━━━━━━━━━━━━━━━━━━━\n\n"
               "Tap karke kisi admin ko wallet give/take ki power do ya wapas lo."),
            parse_mode="HTML", reply_markup=wallet_perm_kb()
        )
        await cb.answer()
        return

    if data == "wperm_noop":
        await cb.answer("Owner ke paas hamesha permission hai.")
        return

    if data.startswith("wperm_"):
        if uid != OWNER_ID:
            await cb.answer("⛔ Only owner", show_alert=True)
            return
        try:
            target_id = int(data.split("_", 1)[1])
        except ValueError:
            await cb.answer()
            return
        current = can_manage_wallet(target_id)
        set_wallet_perm(target_id, not current)
        try:
            await cb.message.edit_reply_markup(reply_markup=wallet_perm_kb())
        except Exception:
            pass
        try:
            await bot.send_message(
                target_id,
                pe(("💳 <b>Wallet permission mil gayi!</b>\n\nAb aap /admin → WALLET MGMT se users ka balance add/deduct kar sakte ho."
                 if not current else
                 "🚫 <b>Wallet permission hata di gayi hai.</b>")),
                parse_mode="HTML"
            )
        except Exception:
            pass
        await cb.answer("✅ Permission updated", show_alert=True)
        return

    # ── Stock management ───────────────────────────
    if data == "admin_stock":
        await state.update_data(stock_page=0)
        await cb.message.answer(pe("📦 <b>Select product to manage stock:</b>"), parse_mode="HTML", reply_markup=stock_kb(0))
        await cb.answer()
        return

    if data.startswith("stkpg_"):
        page = int(data.split("_")[1])
        try:
            await cb.message.edit_text(pe("📦 <b>Select product to manage stock:</b>"), parse_mode="HTML", reply_markup=stock_kb(page))
        except Exception:
            pass
        await cb.answer()
        return

    if data.startswith("stk_"):
        product_key = data[4:]
        p = PRODUCTS.get(product_key)
        if not p:
            await cb.answer("Service not found", show_alert=True)
            return
        await state.update_data(stock_product_key=product_key)
        text = f"📦 <b>{p['display_name']} — Stock</b>\n━━━━━━━━━━━━━━━━━━━━\n\n"
        for d, s in p["stock"].items():
            text += f"• {d}: {s} units\n"
        text += "\n━━━━━━━━━━━━━━━━━━━━\nFormat: <code>duration|new_stock</code>\nExample: <code>1 Month|50</code>"
        await cb.message.answer(pe(text), parse_mode="HTML")
        await state.set_state(AdminState.waiting_for_stock_update)
        await cb.answer()
        return

    # ── Services management ────────────────────────
    if data == "admin_services":
        if not can_manage_services(uid):
            await cb.answer("⛔ No permission", show_alert=True)
            return
        await cb.message.answer(pe("🛠️ <b>SERVICES MANAGEMENT</b>\n\n✅ = Active  🚫 = Disabled"), parse_mode="HTML", reply_markup=services_mgmt_kb(0))
        await cb.answer()
        return

    if data.startswith("svcpg_"):
        page = int(data.split("_")[1])
        try:
            await cb.message.edit_reply_markup(reply_markup=services_mgmt_kb(page))
        except Exception:
            pass
        await cb.answer()
        return

    if data == "svc_add":
        if not can_manage_services(uid):
            await cb.answer("⛔ No permission", show_alert=True)
            return
        await cb.message.answer(pe("📛 <b>Enter service name:</b>\n\nExample: <code>Cooler Premium</code>"), parse_mode="HTML")
        await state.set_state(ServiceAddState.name)
        await cb.answer()
        return

    if data.startswith("svc_"):
        svc_key = data[4:]
        conn = db()
        c = conn.cursor()
        c.execute("SELECT display_name, description, keywords, is_active FROM services WHERE service_key=?", (svc_key,))
        row = c.fetchone()
        c.execute("SELECT duration, price, stock FROM service_prices WHERE service_key=? ORDER BY price", (svc_key,))
        prices_rows = c.fetchall()
        conn.close()
        if not row:
            await cb.answer("Service not found", show_alert=True)
            return
        dname, desc, kw, active = row
        text  = f"🛠️ <b>{dname}</b>\n━━━━━━━━━━━━━━━━━━━━\n\n"
        text += f"📝 {desc[:100]}…\n🔑 Keywords: {kw}\n\n"
        text += "💰 Plans:\n"
        for dur, price, stk in prices_rows:
            text += f"• {dur}: ₹{price} ({stk} stock)\n"
        text += f"\n🔘 Status: {'✅ Active' if active else '🚫 Disabled'}"
        await cb.message.answer(pe(text), parse_mode="HTML", reply_markup=service_detail_kb(svc_key, active))
        await cb.answer()
        return

    if data.startswith("svctoggle_"):
        svc_key = data[10:]
        conn = db()
        c = conn.cursor()
        c.execute("SELECT is_active FROM services WHERE service_key=?", (svc_key,))
        row = c.fetchone()
        new_active = 0
        if row:
            new_active = 0 if row[0] else 1
            c.execute("UPDATE services SET is_active=? WHERE service_key=?", (new_active, svc_key))
            conn.commit()
        conn.close()
        reload_products()
        await cb.answer(f"{'✅ Enabled' if new_active else '🚫 Disabled'} service.", show_alert=True)
        # Refresh view
        conn2 = db()
        c2 = conn2.cursor()
        c2.execute("SELECT display_name, description, keywords, is_active FROM services WHERE service_key=?", (svc_key,))
        row2 = c2.fetchone()
        conn2.close()
        if row2:
            try:
                await cb.message.edit_reply_markup(reply_markup=service_detail_kb(svc_key, row2[3]))
            except Exception:
                pass
        return

    if data.startswith("svcdel_"):
        svc_key = data[7:]
        conn = db()
        c = conn.cursor()
        c.execute("DELETE FROM service_prices WHERE service_key=?", (svc_key,))
        c.execute("DELETE FROM services WHERE service_key=?", (svc_key,))
        conn.commit()
        conn.close()
        reload_products()
        await cb.message.edit_text(pe("🗑️ <b>Service deleted.</b>"), parse_mode="HTML", reply_markup=services_mgmt_kb())
        await cb.answer("Deleted!", show_alert=True)
        return

    if data.startswith("svcedit_"):
        parts   = data.split("_", 2)
        field   = parts[1]
        svc_key = parts[2]
        await state.update_data(edit_svc_key=svc_key, edit_svc_field=field)
        prompts = {
            "name":   "📛 Send new service name:",
            "desc":   "📝 Send new description (HTML tags supported like b, i):",
            "kw":     "🔑 Send new keywords (comma-separated):",
            "prices": "💰 Send new prices — format:\n<code>duration|price</code> one per line\nExample:\n<code>1 Month|49\n3 Months|99</code>",
            "stock":  "📦 Send new stock — format:\n<code>duration|stock</code> one per line",
        }
        await cb.message.answer(pe(prompts.get(field, "Send new value:")), parse_mode="HTML")
        await state.set_state(ServiceEditState.new_value)
        await cb.answer()
        return

    # ── Admin management ───────────────────────────
    if data == "admin_admins":
        if uid != OWNER_ID:
            await cb.answer("⛔ Only owner can manage admins.", show_alert=True)
            return
        await cb.message.answer(pe("👮 <b>ADMIN MANAGEMENT</b>\n\n⚠️ Only owner can add/remove admins."), parse_mode="HTML", reply_markup=admin_mgmt_kb())
        await cb.answer()
        return

    if data == "admins_list":
        rows = get_all_admins()
        text = "👮 <b>ADMIN LIST</b>\n━━━━━━━━━━━━━━━━━━━━\n\n"
        for auid, role, date in rows:
            text += f"🔹 <code>{auid}</code> — <b>{role}</b>\n"
        await cb.message.answer(pe(text), parse_mode="HTML")
        await cb.answer()
        return

    if data == "admins_add":
        if uid != OWNER_ID:
            await cb.answer("⛔ Only owner", show_alert=True)
            return
        await cb.message.answer(pe("👤 <b>Send user ID to add as admin:</b>"), parse_mode="HTML")
        await state.set_state(AdminManageState.waiting_add_id)
        await cb.answer()
        return

    if data == "admins_remove":
        if uid != OWNER_ID:
            await cb.answer("⛔ Only owner", show_alert=True)
            return
        await cb.message.answer(pe("👤 <b>Send admin user ID to remove:</b>"), parse_mode="HTML")
        await state.set_state(AdminManageState.waiting_remove)
        await cb.answer()
        return

    if data.startswith("setrole_"):
        if uid != OWNER_ID:
            await cb.answer("⛔ Only owner", show_alert=True)
            return
        _, target_id_str, role = data.split("_", 2)
        target_id = int(target_id_str)
        now = now_str()
        conn = db()
        c = conn.cursor()
        c.execute("INSERT OR REPLACE INTO admins (user_id, role, added_by, added_date) VALUES (?,?,?,?)",
                  (target_id, role, uid, now))
        conn.commit()
        conn.close()
        await cb.message.answer(pe(f"✅ <b>Admin added!</b>\n\nID: <code>{target_id}</code>\nRole: <b>{role}</b>"), parse_mode="HTML")
        try:
            await bot.send_message(target_id, pe(f"👮 <b>You have been added as admin!</b>\n\nRole: <b>{role}</b>\n\nUse /admin to access admin panel."), parse_mode="HTML")
        except Exception:
            pass
        await cb.answer("Admin added!", show_alert=True)
        return

    # ── Approve / Reject ───────────────────────────
    if data.startswith("approve_"):
        parts    = data.split("_")
        oid      = parts[1]
        amount   = int(parts[2])
        uid_user = int(parts[3])
        update_order_status(oid, "approved")
        add_order_spent(uid_user, amount)
        decrement_stock_for_order(oid)
        schedule_autofulfill(oid)
        log_payment(PM_MANUAL, "Order payment", uid_user, amount, oid,
                    detail="QR payment approved", actor=uid)
        msg_text = (f"✅ <b>PAYMENT APPROVED</b>\n━━━━━━━━━━━━━━━━━━━━\n\n"
                    f"• Order ID: <code>{oid}</code>\n"
                    f"• Buyer ID: <code>{uid_user}</code>\n"
                    f"• Amount: <b>₹{amount}</b>\n"
                    f"• Approved by: <code>{uid}</code>\n\n"
                    f"➡️ Account details bhejne ke baad DELIVERED dabaiye.")
        try:
            await cb.message.edit_caption(caption=pe(msg_text), parse_mode="HTML")
        except Exception:
            await cb.message.edit_text(pe(msg_text), parse_mode="HTML")

        # Send confirmation to all admins for delivery
        conn = db()
        c = conn.cursor()
        c.execute("SELECT user_id FROM admins")
        admins_list = [r[0] for r in c.fetchall()]
        conn.close()
        for adm in admins_list:
            try:
                await bot.send_message(
                    adm,
                    pe(f"🔔 <b>DELIVERY REQUIRED</b>\n━━━━━━━━━━━━━━━━━━━━\n\n"
                       f"Order: <code>{oid}</code>\nUser: <code>{uid_user}</code>\nAmount: ₹{amount}\n\n"
                       f"Send account details to user."),
                    parse_mode="HTML",
                    reply_markup=deliver_kb(oid, uid_user, amount)
                )
            except Exception:
                pass

        await bot.send_message(
            uid_user,
            pe(f"{PAYMENT_CONFIRMED_MSG}\n\n🆔 Order: <code>{oid}</code>"),
            parse_mode="HTML"
        )
        await cb.answer()
        return

    if data.startswith("reject_"):
        parts    = data.split("_")
        oid      = parts[1]
        uid_user = int(parts[2])
        refunded_amt = refund_order_wallet(oid, "Order rejected by admin")
        update_order_status(oid, "rejected")
        msg_text = (f"❌ <b>PAYMENT DECLINED</b>\n━━━━━━━━━━━━━━━━━━━━\n\n"
                    f"• Order ID: <code>{oid}</code>\n"
                    f"• Buyer ID: <code>{uid_user}</code>\n"
                    f"• Declined by: <code>{uid}</code>")
        try:
            await cb.message.edit_caption(caption=pe(msg_text), parse_mode="HTML")
        except Exception:
            await cb.message.edit_text(pe(msg_text), parse_mode="HTML")
        _bal, _ = get_wallet(uid_user)
        await bot.send_message(
            uid_user,
            pe(f"{sty('ORDER REJECTED')} ❌\n"
               + bq(f"🆔 Order: {oid}\n"
                    + (f"💸 Refund: ₹{refunded_amt:.2f} wallet me wapas\n" if refunded_amt > 0 else "")
                    + f"💚 Wallet Balance: ₹{_bal:.2f}\n"
                      f"🕒 {now_str()} IST")
               + f"\n{sty('Help')}: @AVERON_DEALS"),
            parse_mode="HTML")
        await cb.answer()
        return

    # ── Deposit Approve ────────────────────────────
    if data.startswith("dep_approve_"):
        parts    = data.split("_", 4)
        dep_id   = parts[2]
        amount   = int(parts[3])
        uid_user = int(parts[4])
        dep = get_deposit(dep_id)
        if not dep or dep[3] != "pending":
            await cb.answer("⚠️ Deposit already processed.", show_alert=True)
            return
        update_deposit_status(dep_id, "approved")
        credit_wallet(uid_user, amount, f"Deposit approved — {dep_id}")
        log_payment(PM_MANUAL, "Wallet top-up", uid_user, amount, dep_id,
                    detail="QR deposit approved", actor=uid)
        msg_text = (f"✅ <b>WALLET TOP-UP APPROVED</b>\n━━━━━━━━━━━━━━━━━━━━\n\n"
                    f"• Deposit ID: <code>{dep_id}</code>\n"
                    f"• Buyer ID: <code>{uid_user}</code>\n"
                    f"• Amount: <b>₹{amount}</b> wallet mein add ho gaya\n"
                    f"• Approved by: <code>{uid}</code>")
        try:
            await cb.message.edit_caption(caption=pe(msg_text), parse_mode="HTML")
        except Exception:
            await cb.message.edit_text(pe(msg_text), parse_mode="HTML")
        try:
            await bot.send_message(
                uid_user,
                pe(f"✅ <b>Deposit Approved!</b>\n\n"
                   f"━━━━━━━━━━━━━━━━━━━━\n\n"
                   f"💚 ₹{amount} aapke wallet mein add ho gaya!\n"
                   f"🆔 Deposit ID: <code>{dep_id}</code>\n\n"
                   f"Ab aap wallet se koi bhi service khareed sakte hain. 🎉"),
                parse_mode="HTML",
                reply_markup=main_kb()
            )
        except Exception:
            pass
        await cb.answer("✅ Deposit Approved!", show_alert=True)
        return

    # ── Deposit Reject ─────────────────────────────
    if data.startswith("dep_reject_"):
        parts    = data.split("_", 3)
        dep_id   = parts[2]
        uid_user = int(parts[3])
        dep = get_deposit(dep_id)
        if not dep or dep[3] != "pending":
            await cb.answer("⚠️ Deposit already processed.", show_alert=True)
            return
        update_deposit_status(dep_id, "rejected")
        msg_text = (f"❌ <b>WALLET TOP-UP DECLINED</b>\n━━━━━━━━━━━━━━━━━━━━\n\n"
                    f"• Deposit ID: <code>{dep_id}</code>\n"
                    f"• Buyer ID: <code>{uid_user}</code>\n"
                    f"• Declined by: <code>{uid}</code>")
        try:
            await cb.message.edit_caption(caption=pe(msg_text), parse_mode="HTML")
        except Exception:
            await cb.message.edit_text(pe(msg_text), parse_mode="HTML")
        try:
            await bot.send_message(
                uid_user,
                pe(f"❌ <b>Deposit Rejected.</b>\n\n"
                   f"🆔 Deposit ID: <code>{dep_id}</code>\n\n"
                   f"Koi issue hai? Contact karein: @AVERON_DEALS"),
                parse_mode="HTML",
                reply_markup=main_kb()
            )
        except Exception:
            pass
        await cb.answer("❌ Deposit Rejected!", show_alert=True)
        return

    # ── Deliver ────────────────────────────────────
    if data.startswith("deliver_"):
        if not is_delivery_admin(uid):
            await cb.answer("⛔ No permission", show_alert=True)
            return
        parts    = data.split("_")
        oid      = parts[1]
        uid_user = int(parts[2])
        amount   = int(parts[3])
        _DELIVERED_BY[oid] = uid
        update_order_status(oid, "delivered")
        # Activate 𝗔𝗩𝗘𝗥𝗢𝗡 𝗗𝗘𝗔𝗟𝗦 TV subscription if this is a TV order
        maybe_activate_averontv(oid, uid_user)
        msg_text = f"✅ <b>Order {oid} — DELIVERED!</b>\n\nRating request sent."
        try:
            await cb.message.edit_caption(caption=pe(msg_text), parse_mode="HTML")
        except Exception:
            await cb.message.edit_text(pe(msg_text), parse_mode="HTML")

        # Build delivery message; if TV order, add myapp button
        sub = get_mini_app_sub(uid_user)
        delivery_text = (
            f"✅ <b>Order {oid} has been DELIVERED!</b> 🎉\n"
            f"━━━━━━━━━━━━━━━━━━━━\n\n"
        )
        deliver_kb_user = rating_kb(oid)
        if sub:
            delivery_text += "📺 Your <b>𝗔𝗩𝗘𝗥𝗢𝗡 𝗗𝗘𝗔𝗟𝗦 TV</b> subscription is now active!\nUse /myapp to access.\n\n"
        delivery_text += "⭐ <b>Rate your experience!</b> (Tap a star or SKIP)"
        await bot.send_message(uid_user, pe(delivery_text), parse_mode="HTML", reply_markup=deliver_kb_user)
        await cb.answer()
        return

    # ── Admin Settings (Edit Messages) ─────────────
    if data == "admin_settings":
        if not can_manage_services(uid):
            await cb.answer("⛔ No permission", show_alert=True)
            return
        settings = get_all_settings()
        text = (
            "✏️ <b>EDIT BOT MESSAGES</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n\n"
            "Select which message/setting to edit:\n\n"
            f"🔗 Review Link: <code>{settings.get('review_link','')[:50]}</code>\n"
            f"📺 Mini App URL: <code>{settings.get('mini_app_url','') or 'Not set'}</code>\n\n"
            "<i>All changes take effect immediately.</i>"
        )
        await cb.message.answer(pe(text), parse_mode="HTML", reply_markup=settings_kb())
        await cb.answer()
        return

    if data.startswith("editset_"):
        if not can_manage_services(uid):
            await cb.answer("⛔ No permission", show_alert=True)
            return
        setting_key = data[8:]   # e.g., "welcome_msg"
        prompts = {
            "welcome_msg":         "✏️ Send the new <b>Welcome Message</b> text:\n\n<i>HTML tags supported (b, i, code, etc.)</i>",
            "payment_pending_msg": "✏️ Send the new <b>Payment Pending Message</b>:\n\n<i>Sent to users after they send payment screenshot.</i>",
            "offline_msg":         "✏️ Send the new <b>Offline Auto-Reply</b> message:\n\n<i>Sent once to new users when they first message the bot.</i>",
            "mini_app_url":        "✏️ Send the <b>Telegram Mini App URL</b>:\n\n<i>Must be a valid HTTPS URL for a Telegram WebApp.</i>",
            "review_link":         "✏️ Send the new <b>Review/Community Link</b>:\n\n<i>Example: https://t.me/AVERON_DEALS_REVIEWS</i>",
        }
        if setting_key.startswith(("btn_", "s1_title", "s2_title")):
            prompts[setting_key] = ("✏️ Naya <b>button / heading text</b> bhejiye (emoji allowed).\n"
                                    "<i>Default wapas lane ke liye</i> <code>reset</code> <i>bhejiye.</i>")
        prompt = prompts.get(setting_key, "✏️ Send the new value:")
        await cb.message.answer(pe(prompt), parse_mode="HTML")
        await state.update_data(setting_key=setting_key)
        await state.set_state(EditSettingState.waiting_value)
        await cb.answer()
        return

    # ── Mini App Subscriptions management ──────────
    if data == "admin_minisubs":
        if not can_manage_services(uid):
            await cb.answer("⛔ No permission", show_alert=True)
            return
        active = get_all_active_subs(50)
        text = f"📺 <b>𝗔𝗩𝗘𝗥𝗢𝗡 𝗗𝗘𝗔𝗟𝗦 TV — Active Subscriptions</b>\n━━━━━━━━━━━━━━━━━━━━\n\nTotal active: <b>{len(active)}</b>\n"
        await cb.message.answer(pe(text), parse_mode="HTML", reply_markup=minisubs_kb())
        await cb.answer()
        return

    if data == "minisubs_list":
        active = get_all_active_subs(30)
        if not active:
            await cb.message.answer(pe("📭 <b>No active subscriptions</b>"), parse_mode="HTML")
        else:
            text = "📺 <b>ACTIVE MINI APP SUBS</b>\n━━━━━━━━━━━━━━━━━━━━\n\n"
            for uid_, uname, plan, expiry in active:
                text += f"👤 @{uname or uid_} (<code>{uid_}</code>)\n   {plan} → {expiry[:10]}\n\n"
            await cb.message.answer(pe(text), parse_mode="HTML")
        await cb.answer()
        return

    if data == "minisubs_check":
        await cb.message.answer(pe("👤 <b>Send user ID to check subscription:</b>"), parse_mode="HTML")
        await state.set_state(AdminState.waiting_for_user_id)
        await state.update_data(admin_action="minisubs_check")
        await cb.answer()
        return

    if data == "minisubs_revoke":
        await cb.message.answer(pe("👤 <b>Send user ID to revoke subscription:</b>"), parse_mode="HTML")
        await state.set_state(AdminState.waiting_for_user_id)
        await state.update_data(admin_action="minisubs_revoke")
        await cb.answer()
        return

    # Fallback for unhandled callbacks
    await cb.answer()


# ── Service edit FSM (new_value state) ────────────
@dp.message(ServiceEditState.new_value)
async def svc_edit_value_handler(msg: Message, state: FSMContext):
    data    = await state.get_data()
    svc_key = data.get("edit_svc_key")
    field   = data.get("edit_svc_field")

    conn = db()
    c = conn.cursor()

    if field == "name":
        c.execute("UPDATE services SET display_name=? WHERE service_key=?", (msg.text.strip(), svc_key))
        conn.commit()
        conn.close()
        reload_products()
        await msg.answer(pe(f"✅ <b>Name updated!</b>"), parse_mode="HTML")

    elif field == "desc":
        c.execute("UPDATE services SET description=? WHERE service_key=?", (msg.text.strip(), svc_key))
        conn.commit()
        conn.close()
        reload_products()
        await msg.answer(pe("✅ <b>Description updated!</b>"), parse_mode="HTML")

    elif field == "kw":
        c.execute("UPDATE services SET keywords=? WHERE service_key=?", (msg.text.strip(), svc_key))
        conn.commit()
        conn.close()
        reload_products()
        await msg.answer(pe(f"✅ <b>Keywords updated!</b>"), parse_mode="HTML")

    elif field == "prices":
        lines = [l.strip() for l in msg.text.strip().split("\n") if l.strip()]
        errors = []
        for line in lines:
            try:
                dur, price = line.split("|")
                c.execute("UPDATE service_prices SET price=? WHERE service_key=? AND duration=?",
                          (int(price.strip()), svc_key, dur.strip()))
            except Exception:
                errors.append(line)
        conn.commit()
        conn.close()
        reload_products()
        if errors:
            await msg.answer(pe(f"⚠️ Updated with errors on: {', '.join(errors)}"), parse_mode="HTML")
        else:
            await msg.answer(pe("✅ <b>Prices updated!</b>"), parse_mode="HTML")

    elif field == "stock":
        lines = [l.strip() for l in msg.text.strip().split("\n") if l.strip()]
        errors = []
        for line in lines:
            try:
                dur, stk = line.split("|")
                c.execute("UPDATE service_prices SET stock=? WHERE service_key=? AND duration=?",
                          (int(stk.strip()), svc_key, dur.strip()))
            except Exception:
                errors.append(line)
        conn.commit()
        conn.close()
        reload_products()
        if errors:
            await msg.answer(pe(f"⚠️ Updated with errors on: {', '.join(errors)}"), parse_mode="HTML")
        else:
            await msg.answer(pe("✅ <b>Stock updated!</b>"), parse_mode="HTML")
    else:
        conn.close()
        await msg.answer(pe("❌ Unknown field."), parse_mode="HTML")

    await state.clear()

# ══════════════════════════════════════════════════
#  HELPER — decrement stock for an order
# ══════════════════════════════════════════════════
def decrement_stock_for_order(order_id: str):
    """Safely decrement stock when an order is approved."""
    conn = db()
    c = conn.cursor()
    c.execute("SELECT product_name, duration FROM orders WHERE order_id=?", (order_id,))
    row = c.fetchone()
    conn.close()
    if row:
        decrement_stock(row[0], row[1])

def maybe_activate_averontv(order_id: str, user_id: int):
    """If this order is for 𝗔𝗩𝗘𝗥𝗢𝗡 𝗗𝗘𝗔𝗟𝗦 TV, activate the mini-app subscription."""
    o = get_order(order_id)
    if not o:
        return
    product_key = o[2]   # product_name column
    duration    = o[3]
    if product_key == "averontv":
        activate_mini_app_sub(user_id, duration, order_id)

# ──────────────────────────────────────────────────
#  Edit Setting FSM handler
# ──────────────────────────────────────────────────
@dp.message(EditSettingState.waiting_value)
async def edit_setting_value_handler(msg: Message, state: FSMContext):
    if not is_admin(msg.from_user.id):
        await state.clear()
        return
    data = await state.get_data()
    key  = data.get("setting_key", "")
    val  = msg.text or ""
    if not key or not val.strip():
        await msg.answer(pe("❌ <b>Empty value — not saved.</b>"), parse_mode="HTML")
        await state.clear()
        return
    if val.strip().lower() == "reset" and key.startswith(("btn_", "s1_title", "s2_title")):
        val = ""
    set_setting(key, val.strip())
    friendly = {
        "welcome_msg":         "Welcome Message",
        "payment_pending_msg": "Payment Pending Message",
        "offline_msg":         "Offline Auto-Reply",
        "mini_app_url":        "Mini App URL",
        "review_link":         "Review Link",
    }.get(key, key)
    await msg.answer(
        pe(f"✅ <b>{friendly} updated successfully!</b>\n\n"
           f"<i>New value saved. It will be used from now on.</i>"),
        parse_mode="HTML"
    )
    await state.clear()


# ══════════════════════════════════════════════════
#  PAYMENT GATEWAY — INPUT HANDLERS
# ══════════════════════════════════════════════════
@dp.message(GatewayState.waiting_amount)
async def gw_amount_handler(msg: Message, state: FSMContext):
    raw = (msg.text or "").strip().replace("₹", "")
    try:
        amount = int(float(raw))
    except Exception:
        await msg.answer(pe("❌ <b>Sirf number bhejiye</b> jaise <code>100</code>"), parse_mode="HTML")
        return
    if amount < gw_min():
        await msg.answer(pe(f"❌ Minimum ₹{gw_min()} hai."), parse_mode="HTML")
        return
    await state.clear()
    uid = msg.from_user.id
    await gw_send_payment(msg.chat.id, uid, amount, "deposit", f"WALLET RECHARGE — ₹{amount}",
                          "Paisa aate hi wallet me <b>automatic</b> add ho jayega.\n\n", remark="wallet recharge")

@dp.message(GatewayState.waiting_token)
async def gw_token_handler(msg: Message, state: FSMContext):
    if not is_admin(msg.from_user.id):
        await state.clear(); return
    tok = (msg.text or "").strip()
    if len(tok) < 6:
        await msg.answer(pe("❌ Token bahut chhota lag raha hai. Dobara bhejiye."), parse_mode="HTML")
        return
    set_setting("gw_token", tok)
    set_setting("gw_enabled", "1")
    await state.clear()
    await msg.answer(pe("✅ <b>Token save ho gaya aur gateway ON hai.</b>\n\n"
                        "Ab admin panel → ⚡ PAYMENT GATEWAY → 🧪 TEST GATEWAY se check kijiye."),
                     parse_mode="HTML")

@dp.message(GatewayState.waiting_base)
async def gw_base_handler(msg: Message, state: FSMContext):
    if not is_admin(msg.from_user.id):
        await state.clear(); return
    url = (msg.text or "").strip().rstrip("/")
    if not url.startswith("http"):
        await msg.answer(pe("❌ URL <code>https://</code> se start hona chahiye."), parse_mode="HTML")
        return
    set_setting("gw_base", url)
    await state.clear()
    await msg.answer(pe(f"✅ Panel URL set: <code>{url}</code>"), parse_mode="HTML")

@dp.message(GatewayState.waiting_min)
async def gw_min_handler(msg: Message, state: FSMContext):
    if not is_admin(msg.from_user.id):
        await state.clear(); return
    try:
        val = max(1, int(float((msg.text or "").strip())))
    except Exception:
        await msg.answer(pe("❌ Sirf number bhejiye."), parse_mode="HTML")
        return
    set_setting("gw_min", str(val))
    await state.clear()
    await msg.answer(pe(f"✅ Minimum amount ₹{val} set."), parse_mode="HTML")

@dp.message(Command("gateway"))
async def gateway_cmd(msg: Message, state: FSMContext):
    if not is_admin(msg.from_user.id):
        await msg.answer(pe("⛔ <b>Unauthorized</b>"), parse_mode="HTML")
        return
    ok_cnt, ok_sum, pend = gw_stats()
    tok = gw_token()
    masked = (tok[:4] + "•" * 6 + tok[-4:]) if len(tok) > 8 else ("set" if tok else "❌ not set")
    await msg.answer(
        pe(f"⚡ <b>PAYMENT GATEWAY</b>\n━━━━━━━━━━━━━━━━━━━━\n\n"
           f"Status: <b>{'🟢 ON' if gw_enabled() else '🔴 OFF'}</b>\n"
           f"Panel: <code>{gw_base()}</code>\nToken: <code>{masked}</code>\n"
           f"Min: ₹{gw_min()}\n\n✅ Success: {ok_cnt} — ₹{ok_sum:.0f}\n⏳ Pending: {pend}"),
        parse_mode="HTML", reply_markup=gw_admin_kb())

@dp.message(Command("addmoney"))
async def addmoney_cmd(msg: Message, state: FSMContext):
    if not gw_enabled():
        await msg.answer(pe("⚡ Auto payment abhi band hai.\n\n<code>qr 100</code> type karke QR se add kijiye."),
                         parse_mode="HTML")
        return
    await state.set_state(GatewayState.waiting_amount)
    await msg.answer(pe(f"⚡ <b>Kitna amount add karna hai?</b> (min ₹{gw_min()})"), parse_mode="HTML")

# ══════════════════════════════════════════════════
#  MAIN
# ══════════════════════════════════════════════════
async def start_keepalive_server():
    """Render free Web Service / Heroku web dyno ke liye chhota HTTP server.
    PORT env set ho tabhi chalta hai (VPS par skip)."""
    port = os.getenv("PORT")
    if not port:
        return
    from aiohttp import web
    async def ok(_req):
        return web.Response(text="Bot is running ✅")
    app = web.Application()
    app.router.add_get("/", ok)
    app.router.add_get("/health", ok)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", int(port)).start()
    print(f"🌐 Keep-alive web server on port {port}")

async def main():
    await start_keepalive_server()
    mongo_state = "off"
    try:
        if mongo_sync.connect():
            mongo_state = mongo_sync.restore_if_needed(DB_NAME)
    except Exception as e:
        logging.error("MongoDB connect failed (%s) — check MONGODB_URI", type(e).__name__)
    init_db()
    s1_init_tables()
    reload_products()
    gw_bootstrap()
    if mongo_sync.enabled():
        mongo_state += " | " + mongo_sync.install(DB_NAME)
        asyncio.create_task(mongo_sync.loop(DB_NAME))
    print(f"🍃 MongoDB:        {mongo_state}")
    me = await bot.get_me()
    print("=" * 55)
    print("🤖 𝗔𝗩𝗘𝗥𝗢𝗡 𝗗𝗘𝗔𝗟𝗦 BOT v3.0 STARTED  —  main.py")
    print(f"✅ Bot:            @{me.username}")
    print(f"✅ Channel:        {CHANNEL_USERNAME}")
    print(f"✅ Owner:          {OWNER_ID}")
    print(f"✅ Active Services:{len(PRODUCTS)}")
    print(f"✅ Start page size:{START_MENU_PER_PAGE}")
    print(f"✅ Database:       {DB_NAME}")
    print(f"✅ Referral:       ₹{REFERRAL_REWARD} / Welcome ₹{WELCOME_BONUS}")
    print(f"✅ Wallet:         open for all users (QR + Wallet checkout)")
    print(f"✅ QR validity:    {QR_VALID_MIN} minutes (no re-generate)")
    print(f"✅ Admin wallet:   give/take balance enabled")
    print("=" * 55)
    # Gateway payments always need status polling. AUTO_APPROVAL only controls
    # manual QR screenshots; it must never disable the autopay gateway.
    print(f"⚡ AUTOPAY gateway:  {gw_base()}  ({'ON' if gw_enabled() else 'OFF'})")
    asyncio.create_task(gw_poller())
    asyncio.create_task(sales_poster_loop())      # 🧾 sales log retry + daily report
    if not AUTO_APPROVAL:
        print("⛔ QR auto approval: OFF (manual screenshot approval only)")
    await bot.delete_webhook(drop_pending_updates=False)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
