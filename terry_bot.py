import os
import re
import asyncio
import threading
import logging
import time
import random
from flask import Flask, request, jsonify
from telethon import TelegramClient, events
from telethon.sessions import StringSession

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)
import requests

# ══════════════════════════════════════════════════════
# ENV
# ══════════════════════════════════════════════════════
API_ID = int(os.environ.get("API_ID", "0"))
API_HASH = os.environ.get("API_HASH", "")

TERRY_SESSION_STRING = os.environ.get("TERRY_SESSION_STRING", "")
TERRY_PHONE = os.environ.get("TERRY_PHONE", "")

TARGET_GROUP_ID = int(os.environ.get("TARGET_GROUP_ID", "0"))
TARGET_TOPIC_ID = int(os.environ.get("TARGET_TOPIC_ID", "0"))

SOURCE_CHANNEL_ID = int(os.environ.get("SOURCE_CHANNEL_ID", "0"))
OANDA_API_KEY = os.environ.get("OANDA_API_KEY", "")
OANDA_ACCOUNT = os.environ.get("OANDA_ACCOUNT", "001-011-8842842-001")
OANDA_HOST = os.environ.get("OANDA_HOST", "https://api-fxpractice.oanda.com")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
TWELVE_DATA_KEY = os.environ.get("TWELVE_DATA_KEY", "")

# Test mode. Set LIVE_MODE=true in Railway variables to go live permanently.
# /switch_mode still flips it for this session, but a restart falls back to
# whatever LIVE_MODE says, so the bot cannot silently drop out of live mode.
LIVE_MODE = os.environ.get("LIVE_MODE", "false").strip().lower() in ("1", "true", "yes")
SEND_TO_SAVED = not LIVE_MODE

# ══════════════════════════════════════════════════════
# TRADE MATHS
# 1 point = 1.00 price move on gold = 10 pips
# TP  = 20 points = 200 pips
# SL  =  6 points =  60 pips
# ══════════════════════════════════════════════════════
ENTRY_WIDEN = 2.0     # entry band width in points
TP_POINTS = 20.0
SL_POINTS = 6.0
PIP_SIZE = {"XAUUSD": 0.10, "BTCUSD": 1.0}

ALERT_LEVELS = [20, 60, 100, 200]   # 200 = TP hit, closes the trade

TRADE_EXPIRY_SECONDS = 10800        # auto clear after 3h with no result

# ══════════════════════════════════════════════════════
# STATE
# ══════════════════════════════════════════════════════
client = None
loop = asyncio.new_event_loop()
active_trades = {}
reported_levels = {}
trade_lock = threading.Lock()
last_channel_msgs = []
last_sent_texts = []      # anti repeat guard


def run_loop():
    asyncio.set_event_loop(loop)
    loop.run_forever()


threading.Thread(target=run_loop, daemon=True).start()


# ══════════════════════════════════════════════════════
# MESSAGE WORDING
# Every message is first person, personal, no promos,
# no buttons, no images. Headings rotate as well as the
# body line so nothing reads the same twice.
# ══════════════════════════════════════════════════════

HEADINGS = {
    20: [
        "✅ <b>20 PIPS IN PROFIT</b>",
        "✅ <b>20 PIPS UP ALREADY</b>",
        "✅ <b>20 PIPS GREEN ALREADY</b>",
    ],
    60: [
        "✅ <b>60 PIPS IN PROFIT</b>",
        "✅ <b>60 PIPS GREEN NOW</b>",
        "✅ <b>60 PIPS UP NOW</b>",
        "✅ <b>60 PIPS ONSIDE</b>",
    ],
    100: [
        "✅ <b>100 PIPS IN PROFIT</b>",
        "✅ <b>100 PIPS UP</b>",
        "✅ <b>100 PIPS GREEN</b>",
        "✅ <b>100 PIPS AND CLIMBING</b>",
    ],
    200: [
        "✅ <b>200 PIPS, TP HIT</b>",
        "✅ <b>TP DONE, 200 PIPS</b>",
        "✅ <b>FULL TARGET, 200 PIPS</b>",
        "✅ <b>200 PIPS SECURED</b>",
    ],
    "SL": [
        "❌ <b>STOPPED OUT</b>",
        "❌ <b>SL HIT</b>",
        "❌ <b>TOOK THE LOSS</b>",
    ],
    "BE": [
        "⚠️ <b>BACK TO ENTRY</b>",
        "⚠️ <b>BREAKEVEN</b>",
        "⚠️ <b>CAME BACK TO ENTRY</b>",
    ],
}

LEVEL_BRIEF = {
    20: ("price is 20 pips your way. You are just talking to the group about "
         "it. Say you are holding, or thinking about the stop, and see where "
         "it goes. Casual thinking out loud, not instructions."),
    60: ("price is 60 pips your way and still moving. Just talking about it. "
         "Say it is going well and you are staying in, or watching it."),
    100: ("price is 100 pips your way, halfway to your target. Just talking "
          "about it. Sound pleased and say you are letting it run on."),
    200: ("price hit your 200 pip target. You are closing it. Sound pleased "
          "but normal about it, like a good day at work, not a celebration."),
    "SL": ("your stop got hit. Take it on the chin, no excuses, say you will "
           "get it back on the next one. Short and unbothered."),
    "BE": ("price came back to your entry so you are out at nothing. Shrug it "
           "off, nothing lost, you are looking for the next one."),
}

FALLBACK_LINE = {
    20: [
        "20 pips bro, holding this one, lets see where it goes",
        "20 up already, happy with that start",
        "20 pips in, staying in this for now",
        "Nice, 20 up bro, letting it breathe a bit",
        "20 pips your way, moving my stop to entry",
        "20 in profit, going nowhere yet, holding",
        "20 up bro, sitting tight on this one",
        "That is 20, stop coming to entry for me",
        "20 pips, decent start, see what it does now",
        "20 onside, holding, no rush here",
        "20 up bro, this one looks like it wants more",
        "20 in, tucking my stop up to entry",
        "20 pips your way, staying with it",
        "20 up, quietly happy with this so far",
        "20 in profit bro, letting it run a bit",
    ],
    60: [
        "60 up now bro, happy to sit in this a bit longer",
        "60 pips, going well, still holding",
        "60 in profit, letting this one work",
        "60 up bro, nice and steady this",
        "60 pips your way, staying in",
        "60 onside, still plenty in it I reckon",
        "60 up, trailing my stop behind it now",
        "60 pips bro, this one is moving well",
        "60 in, comfortable holding this",
        "60 up now, letting it push on",
        "60 pips, no reason to touch it yet",
        "60 onside bro, happy with how this is going",
        "60 in profit, still in, still watching",
        "60 up, pulling my stop up a bit",
        "60 pips bro, sitting in this one",
    ],
    100: [
        "100 up bro, halfway to target, letting it run",
        "100 pips in, going nicely this one",
        "100 onside, still holding, plenty left in it",
        "100 up bro, happy with this",
        "100 pips, halfway there, staying in",
        "100 in profit, letting it push on to target",
        "100 up, this one is doing exactly what I wanted",
        "100 pips bro, still riding it",
        "100 onside, stop is well in profit now",
        "100 up, halfway home, holding",
        "100 pips in bro, letting it keep going",
        "100 onside, no reason to get out yet",
        "100 up, moving well this, staying with it",
        "100 pips bro, target is in sight",
        "100 in, quietly buzzing with this one",
    ],
    200: [
        "200 pips bro, target hit, closing it here",
        "Target done, 200 pips, happy with that",
        "200 up, thats the target, out on this one",
        "200 pips bro, closed it, good trade",
        "Target reached, 200 pips, taking it",
        "200 in, thats me out, decent one that",
        "200 pips, done and dusted bro",
        "Target hit at 200, closing it out",
        "200 up, exactly where I wanted it, out",
        "Thats 200 bro, closed, on to the next",
        "200 pips, target done, pleased with that",
        "Closed at 200, cannot complain with that one",
        "200 in profit, taking it, good move that",
        "Target smashed bro, 200 pips, out",
        "200 pips and done, tidy trade",
    ],
    "SL": [
        "Stop got me on that one bro, get it back on the next",
        "Stopped out, no dramas, next one",
        "That one got me, moving on",
        "Stop hit bro, it happens, I will recover it",
        "Stopped out on this, back at it shortly",
        "Took the stop, no excuses, next setup",
        "That did not work bro, onto the next",
        "Stop got hit, I will get it back",
        "Stopped out, part of the game, moving on",
        "Lost that one bro, no worries",
        "Stop taken, back on the charts",
        "That one beat me, I will have the next",
        "Stopped out bro, not fussed, next one",
        "Took a stop there, all good, moving on",
        "Stop hit, on to the next one",
    ],
    "BE": [
        "Back to entry, out at nothing, no harm done",
        "Came back to my entry bro, out flat",
        "Back to breakeven, nothing lost, next one",
        "Out at entry on this, no damage",
        "Came all the way back, out at nothing bro",
        "Back to breakeven, glad my stop was up",
        "Out flat on this one, no complaints",
        "Back at entry, nothing gained nothing lost",
        "Came back to entry bro, that happens",
        "Out at breakeven, on to the next",
        "Back to my entry, closed at nothing",
        "Flat on that one, no worries bro",
        "Back to entry level, out clean",
        "Breakeven on this, next setup then",
        "Came back on me, out at entry, all good",
    ],
}

PROMO_MARKERS = ["JOIN", "FREE", "WHATSAPP", "WHATS APP", " DM ", "SUPPORT",
                 "T.ME/", "HTTP", "SUBSCRIBE", "PM NOW", "STRIPE", "VIP",
                 "BONUS", "INSTAGRAM", "FTMO"]


def _clean_line(text):
    """Strip dashes, emojis and any heading the model tacked on."""
    text = text.replace("\u2014", ",").replace("\u2013", ",").replace(" - ", ", ")
    text = re.sub(r"^\s*<b>.*?</b>\s*", "", text).strip()
    text = re.sub(
        "[\U0001F000-\U0001FAFF\U00002600-\U000027BF\U0001F1E6-\U0001F1FF"
        "\u2705\u274c\u26a0\ufe0f]", "", text).strip()
    text = re.sub(r"\s{2,}", " ", text)
    return text.strip()


def _ask_claude(prompt, max_tokens=90):
    """One call to the Anthropic API. Returns cleaned text or None."""
    if not ANTHROPIC_API_KEY:
        return None
    try:
        r = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={"x-api-key": ANTHROPIC_API_KEY,
                     "anthropic-version": "2023-06-01",
                     "content-type": "application/json"},
            json={"model": "claude-haiku-4-5-20251001",
                  "max_tokens": max_tokens,
                  "messages": [{"role": "user", "content": prompt}]},
            timeout=12,
        )
        if r.status_code == 200:
            parts = [b.get("text", "") for b in r.json().get("content", [])
                     if b.get("type") == "text"]
            out = _clean_line(" ".join(parts))
            return out or None
        logger.warning(f"Anthropic {r.status_code}: {r.text[:150]}")
    except Exception as e:
        logger.warning(f"Anthropic call failed: {e}")
    return None


def _not_a_repeat(text):
    """Keep the last 12 lines and avoid sending the same one twice in a row."""
    if text in last_sent_texts:
        return False
    last_sent_texts.append(text)
    if len(last_sent_texts) > 12:
        del last_sent_texts[0]
    return True


def alert_message(level):
    """Rotating heading plus a fresh personal line beneath it."""
    heading = random.choice(HEADINGS.get(level, ["<b>UPDATE</b>"]))
    brief = LEVEL_BRIEF.get(level, "")

    line = None
    for _ in range(2):
        candidate = _ask_claude(
            "You are a UK gold trader dropping a quick message in your own "
            "Telegram group. Write ONE line, max 18 words.\n\n"
            "VOICE: relaxed, casual, like texting a mate. British, not American. "
            "Say 'bro' sometimes, roughly one message in three, and only where it "
            "lands naturally. Never force it. 'bro' is the ONLY term of address "
            "allowed, never 'mate', 'guys', 'team', 'fam', 'lads' or 'everyone'. "
            "Avoid heavy slang like 'innit' or 'proper'. "
            "Use 'I' for yourself and 'you' when talking to them.\n\n"
            "HARD RULES, breaking any of these makes the message unusable:\n"
            "- Never claim a specific action you took with your position. No "
            "'closed half', 'took partials', 'locked in half', 'banked some'. "
            "You do not know what was actually done.\n"
            "- Never invent a number. The only figure allowed is the pip level "
            "in the context. No prices, no lot sizes, no fractions, no percentages.\n"
            "- Never reference time. No 'today', 'this morning', 'all week'.\n"
            "- Never mention joining, subscribing, links, VIP or any service.\n"
            "- No emojis. No dashes of any kind, use commas or full stops.\n"
            "- No heading, no ticks, output the single line only.\n\n"
            "STYLE: you are thinking out loud to your group, not instructing "
            "them. Talk about what YOU are doing and what you reckon, not what "
            "they should do. It should read like a quick text you fired off "
            "without thinking about it. Short is better than clever.\n\n"
            "Good examples of the register:\n"
            "  20 pips bro, holding this one, lets see where it goes\n"
            "  60 up now, happy to sit in this a bit longer\n"
            "  Stop got me on that one bro, get it back on the next\n\n"
            "Vary the opening so it is not the same as last time.\n\n"
            "Context: " + brief
        )
        if candidate and _not_a_repeat(candidate):
            line = candidate
            break

    if not line:
        pool = FALLBACK_LINE.get(level, ["Trade update"])
        for _ in range(6):
            candidate = random.choice(pool)
            if _not_a_repeat(candidate):
                line = candidate
                break
        line = line or random.choice(pool)

    return f"{heading}\n\n{line}"


ENTRY_OPENERS = {
    "BUY": [
        "Buying gold now",
        "Buying gold here bro",
        "Taking a buy on gold",
        "Buying this area on gold",
        "Gold buy for me here",
        "Buying gold at this level",
        "Buy on gold bro",
        "Buying gold from here",
        "Getting my buy on gold now",
        "Buying gold here",
        "Gold buy taken bro",
        "Buying into gold now",
    ],
    "SELL": [
        "Selling gold now",
        "Selling gold here bro",
        "Taking a sell on gold",
        "Selling this area on gold",
        "Gold sell for me here",
        "Selling gold at this level",
        "Sell on gold bro",
        "Selling gold from here",
        "Getting my sell on gold now",
        "Selling gold here",
        "Gold sell taken bro",
        "Selling into gold now",
    ],
}


def entry_message(name, direction, elow, ehigh, tp, sl, dec):
    """Casual opener, then the levels in a fixed clear block.

    The numbers are never written by the model. It only supplies the
    opening line, so the direction and levels cannot come out wrong.
    """
    opener = _ask_claude(
        "You are a UK gold trader about to post a trade to your Telegram "
        "group. Write ONLY the opening line, max 8 words, saying you are "
        f"{'buying' if direction == 'BUY' else 'selling'} {name}.\n\n"
        "Casual, British, first person. You may say 'bro' about one time in "
        "three. No emojis. No dashes. NO NUMBERS AT ALL, the levels come "
        "after. No prediction about how it will go. Output the line only.",
        max_tokens=40,
    )

    # The opener is the ONLY place the direction is stated, so it has to
    # contain a direction word. Anything vague gets thrown away.
    required = ("buy", "buying") if direction == "BUY" else ("sell", "selling")
    wrong = (("sell", "selling", "short") if direction == "BUY"
             else ("buy", "buying", "long"))

    def _opener_ok(text):
        if not text or len(text) > 70:
            return False
        if any(ch.isdigit() for ch in text):
            return False
        low = text.lower()
        if any(w in low for w in wrong):
            return False
        return any(w in low for w in required)

    if _opener_ok(opener) and not _not_a_repeat(opener):
        opener = None
    if not _opener_ok(opener):
        pool = ENTRY_OPENERS[direction]
        opener = random.choice(pool)
        for _ in range(6):
            if _not_a_repeat(opener):
                break
            opener = random.choice(pool)

    return (
        f"<b>{opener}</b>\n\n"
        f"Entry: {elow:.{dec}f} to {ehigh:.{dec}f}\n"
        f"\u2705 TP: {tp:.{dec}f}\n"
        f"\U0001F6AB SL: {sl:.{dec}f}"
    )


# ══════════════════════════════════════════════════════
# SIGNAL PARSING
# ══════════════════════════════════════════════════════
def parse_signal(text):
    if not text:
        return None
    u = text.upper()
    if "ENTRY" not in u:
        return None

    if "BUY" in u:
        direction = "BUY"
    elif "SELL" in u:
        direction = "SELL"
    else:
        return None

    if "BTC" in u or "BITCOIN" in u:
        pair = "BTCUSD"
    elif "XAU" in u or "GOLD" in u:
        pair = "XAUUSD"
    else:
        return None

    entry_low = None
    for line in text.splitlines():
        if "ENTRY" in line.upper():
            nums = [float(n) for n in re.findall(r"\d+(?:\.\d+)?", line.replace(",", ""))]
            if len(nums) >= 2:
                entry_low = min(nums[0], nums[1])
            elif len(nums) == 1:
                entry_low = nums[0]
            break

    if entry_low is None:
        return None

    return {"direction": direction, "pair": pair, "entry": entry_low}


def build_trade(sig):
    pair, direction, base = sig["pair"], sig["direction"], sig["entry"]

    # anchor = the worst fill in the entry band, so every pip figure we
    # post is one the slowest filled follower actually got.
    if direction == "BUY":
        elow, ehigh = base, base + ENTRY_WIDEN
        anchor = ehigh
        tp, sl = anchor + TP_POINTS, anchor - SL_POINTS
    else:
        elow, ehigh = base - ENTRY_WIDEN, base
        anchor = elow
        tp, sl = anchor - TP_POINTS, anchor + SL_POINTS

    name = "gold" if pair == "XAUUSD" else "bitcoin"
    dec = 2 if pair == "XAUUSD" else 1

    post = entry_message(name, direction, elow, ehigh, tp, sl, dec)

    trade = {
        "pair": pair,
        "direction": direction,
        "entry_price": base,
        "profit_anchor": anchor,
        "tp": tp,
        "sl": sl,
        "timestamp": time.time(),
        "status": "open",
        "entry_msg_id": None,
    }
    return post, trade


# ══════════════════════════════════════════════════════
# PRICES
# ══════════════════════════════════════════════════════
mt5_prices = {"XAUUSD": None, "BTCUSD": None, "ts": 0}
MT5_FRESH_SECONDS = 30


def get_oanda_price(pair):
    if not OANDA_API_KEY:
        return None
    try:
        instrument = "XAU_USD" if pair == "XAUUSD" else "BTC_USD"
        url = f"{OANDA_HOST}/v3/accounts/{OANDA_ACCOUNT}/pricing"
        r = requests.get(url,
                         params={"instruments": instrument},
                         headers={"Authorization": f"Bearer {OANDA_API_KEY}"},
                         timeout=5)
        if r.status_code == 200:
            p = r.json()["prices"][0]
            return (float(p["bids"][0]["price"]) + float(p["asks"][0]["price"])) / 2
        logger.warning(f"OANDA {r.status_code}: {r.text[:120]}")
    except Exception as e:
        logger.error(f"OANDA price error: {e}")
    return None


def get_goldapi_price(pair):
    """Free keyless spot feed. Backup only, gold and bitcoin."""
    try:
        if pair == "XAUUSD":
            r = requests.get("https://api.gold-api.com/price/XAU", timeout=6)
            if r.status_code == 200:
                p = float(r.json().get("price", 0))
                if p > 1000:
                    return p
        else:
            r = requests.get(
                "https://api.binance.com/api/v3/ticker/price",
                params={"symbol": "BTCUSDT"}, timeout=6)
            if r.status_code == 200:
                p = float(r.json()["price"])
                if p > 0:
                    return p
    except Exception as e:
        logger.warning(f"Backup price failed: {e}")
    return None


# ── Twelve Data budget guard ──────────────────────────
# Basic plan: 800 calls/day, 8/minute, and the Kevin bot shares this key.
# We only spend a call to CONFIRM a message that is about to be posted,
# never for routine polling. Reserve leaves headroom for the other bots.
TD_DAILY_CAP = int(os.environ.get("TD_DAILY_CAP", "250"))
TD_PER_MIN_CAP = 4
td_budget = {"day": None, "used_today": 0, "minute": None, "used_minute": 0,
             "last_error": None}
td_lock = threading.Lock()


def _td_allowed():
    """True if we can afford a Twelve Data call right now."""
    now = time.time()
    day = time.strftime("%Y-%m-%d", time.gmtime(now))
    minute = int(now // 60)
    with td_lock:
        if td_budget["day"] != day:
            td_budget["day"] = day
            td_budget["used_today"] = 0
        if td_budget["minute"] != minute:
            td_budget["minute"] = minute
            td_budget["used_minute"] = 0
        if td_budget["used_today"] >= TD_DAILY_CAP:
            return False
        if td_budget["used_minute"] >= TD_PER_MIN_CAP:
            return False
        td_budget["used_today"] += 1
        td_budget["used_minute"] += 1
        return True


def get_twelvedata_price(pair, force=False):
    """Twelve Data spot price. Budget guarded, confirmation use only."""
    if not TWELVE_DATA_KEY:
        with td_lock:
            td_budget["last_error"] = "TWELVE_DATA_KEY not set on this service"
        return None
    if not force and not _td_allowed():
        with td_lock:
            td_budget["last_error"] = "budget cap reached, skipped"
        return None
    try:
        symbol = "XAU/USD" if pair == "XAUUSD" else "BTC/USD"
        r = requests.get("https://api.twelvedata.com/price",
                         params={"symbol": symbol,
                                 "apikey": TWELVE_DATA_KEY.strip().strip('"').strip("'")},
                         timeout=8)
        body = r.json() if r.status_code == 200 else {}
        # Twelve Data returns 200 with an error object when the quota is gone
        if isinstance(body, dict) and body.get("status") == "error":
            with td_lock:
                td_budget["last_error"] = str(body.get("message"))[:200]
            logger.warning(f"TwelveData error: {body.get('message')}")
            return None
        if r.status_code == 200:
            p = float(body.get("price", 0))
            if p > 0:
                with td_lock:
                    td_budget["last_error"] = None
                return p
            with td_lock:
                td_budget["last_error"] = f"unexpected body: {str(body)[:150]}"
        else:
            with td_lock:
                td_budget["last_error"] = f"HTTP {r.status_code}: {r.text[:150]}"
            logger.warning(f"TwelveData {r.status_code}: {r.text[:120]}")
    except Exception as e:
        with td_lock:
            td_budget["last_error"] = f"request failed: {e}"
        logger.warning(f"TwelveData failed: {e}")
    return None


def confirm_price(pair, watch_price):
    """Called only when a message is about to go out. Spends one Twelve Data
    call to verify the level really was reached. Falls back to the watch
    price if Twelve Data is unavailable."""
    td = get_twelvedata_price(pair)
    if td is not None:
        return td, "twelvedata"
    return watch_price, "free feed"


def get_price(pair):
    """MT5 first (your real broker), then OANDA, Twelve Data, free feed."""
    if (mt5_prices.get(pair) is not None
            and (time.time() - mt5_prices["ts"]) <= MT5_FRESH_SECONDS):
        return mt5_prices[pair]
    # Routine polling uses only unmetered feeds. Twelve Data is reserved
    # for confirming a level right before a message is posted.
    return get_goldapi_price(pair) or get_oanda_price(pair)


def pips_in_profit(pair, direction, anchor, current):
    size = PIP_SIZE.get(pair, 0.10)
    diff = (current - anchor) if direction == "BUY" else (anchor - current)
    return max(0, round(diff / size))


# ══════════════════════════════════════════════════════
# TELEGRAM
# ══════════════════════════════════════════════════════
async def send_to_telegram(text, reply_to_id=None):
    global client
    try:
        if not client or not await client.is_user_authorized():
            logger.error("Client not authorized")
            return None

        entity = "me" if SEND_TO_SAVED else await client.get_entity(TARGET_GROUP_ID)
        reply = reply_to_id
        if reply is None and not SEND_TO_SAVED and TARGET_TOPIC_ID:
            reply = TARGET_TOPIC_ID

        msg = await client.send_message(entity, text, parse_mode="html", reply_to=reply)
        logger.info(f"Sent (reply_to={reply})")
        return msg.id
    except Exception as e:
        logger.error(f"Send error: {e}")
        return None


async def init_client():
    global client
    try:
        if not TERRY_SESSION_STRING:
            logger.error("Missing TERRY_SESSION_STRING")
            return False

        client = TelegramClient(StringSession(TERRY_SESSION_STRING), API_ID, API_HASH)
        await client.connect()

        if not await client.is_user_authorized():
            logger.error("Session not authorized")
            return False

        me = await client.get_me()
        logger.info(f"Logged in as {me.first_name} (@{me.username})")

        if SOURCE_CHANNEL_ID:
            @client.on(events.NewMessage(chats=SOURCE_CHANNEL_ID))
            async def on_channel_message(event):
                try:
                    await handle_source_message(event.message.message or "")
                except Exception as e:
                    logger.error(f"Channel handler error: {e}")

            logger.info(f"Listening to source channel {SOURCE_CHANNEL_ID}")

        return True
    except Exception as e:
        logger.error(f"Client init error: {e}")
    return False


threading.Thread(
    target=lambda: asyncio.run_coroutine_threadsafe(init_client(), loop),
    daemon=True,
).start()


# ══════════════════════════════════════════════════════
# SOURCE HANDLING
# ══════════════════════════════════════════════════════
async def open_trade_from_signal(sig):
    post, trade = build_trade(sig)
    tid = f"{sig['pair']}_{int(time.time())}"

    msg_id = await send_to_telegram(post)
    trade["entry_msg_id"] = msg_id

    with trade_lock:
        active_trades[tid] = trade
        reported_levels[tid] = set()

    logger.info(f"Opened {tid} {sig['direction']} {sig['pair']} @ {sig['entry']}")
    return tid


async def handle_source_message(text):
    try:
        last_channel_msgs.append({"ts": time.time(), "text": (text or "")[:400]})
        if len(last_channel_msgs) > 10:
            del last_channel_msgs[0]
    except Exception:
        pass

    u = (text or "").upper()

    if any(m in u for m in PROMO_MARKERS) and "ENTRY" not in u:
        logger.info("Skipped promo message")
        return

    sig = parse_signal(text)
    if sig:
        with trade_lock:
            already = any(t["pair"] == sig["pair"] and t["status"] == "open"
                          for t in active_trades.values())
        if already:
            logger.info(f"Ignored, {sig['pair']} trade already open")
            return
        await open_trade_from_signal(sig)
    else:
        logger.info("Ignored non actionable message")


def channel_poller():
    """Backup for the live listener, checks for new posts every 8s."""
    if not SOURCE_CHANNEL_ID:
        logger.info("No SOURCE_CHANNEL_ID set, poller idle")
        return

    logger.info("Channel poller started (8s)")
    last_id = {"id": 0}

    for _ in range(30):
        if client:
            break
        time.sleep(1)
    time.sleep(3)

    async def _prime():
        try:
            ent = await client.get_entity(SOURCE_CHANNEL_ID)
            async for msg in client.iter_messages(ent, limit=1):
                last_id["id"] = msg.id
                logger.info(f"Poller primed at {msg.id}")
        except Exception as e:
            logger.error(f"Prime error: {e}")

    try:
        asyncio.run_coroutine_threadsafe(_prime(), loop).result(timeout=20)
    except Exception as e:
        logger.error(f"Prime failed: {e}")

    while True:
        try:
            async def _check():
                fresh = []
                ent = await client.get_entity(SOURCE_CHANNEL_ID)
                async for msg in client.iter_messages(ent, limit=10):
                    if msg.id > last_id["id"]:
                        fresh.append(msg)
                for msg in reversed(fresh):
                    last_id["id"] = max(last_id["id"], msg.id)
                    await handle_source_message(msg.message or "")
                return len(fresh)

            n = asyncio.run_coroutine_threadsafe(_check(), loop).result(timeout=20)
            if n:
                logger.info(f"Poller handled {n} new message(s)")
        except Exception as e:
            logger.error(f"Poller error: {e}")
        time.sleep(8)


threading.Thread(target=channel_poller, daemon=True).start()


# ══════════════════════════════════════════════════════
# PRICE MONITOR
# ══════════════════════════════════════════════════════
def close_trade(tid, level, reply_id):
    """Send the closing message and clear the trade."""
    asyncio.run_coroutine_threadsafe(
        send_to_telegram(alert_message(level), reply_to_id=reply_id), loop)
    with trade_lock:
        active_trades.pop(tid, None)
        reported_levels.pop(tid, None)


def monitor_profits():
    logger.info("Profit monitor started (10s)")
    while True:
        try:
            now = time.time()

            with trade_lock:
                stale = [tid for tid, t in active_trades.items()
                         if now - t.get("timestamp", now) > TRADE_EXPIRY_SECONDS]
                for tid in stale:
                    active_trades.pop(tid, None)
                    reported_levels.pop(tid, None)
                    logger.info(f"Auto cleared {tid} after 3h")
                snapshot = dict(active_trades)

            for tid, t in snapshot.items():
                if t["status"] != "open":
                    continue

                price = get_price(t["pair"])
                if not price:
                    continue

                anchor = t["profit_anchor"]
                direction = t["direction"]
                reply_id = t.get("entry_msg_id")
                pips = pips_in_profit(t["pair"], direction, anchor, price)
                seen = reported_levels.get(tid, set())

                # Stop loss first, it ends the trade
                hit_sl = (price <= t["sl"]) if direction == "BUY" else (price >= t["sl"])
                if hit_sl:
                    cp, src = confirm_price(t["pair"], price)
                    still = (cp <= t["sl"]) if direction == "BUY" else (cp >= t["sl"])
                    if not still:
                        logger.info(f"SL not confirmed on {tid} "
                                    f"(watch {price}, {src} {cp}), holding")
                        continue
                    logger.info(f"SL hit {tid} at {cp} (confirmed by {src})")
                    close_trade(tid, "SL", reply_id)
                    continue

                # Back to entry after having been 20+ pips up
                if 20 in seen:
                    back = (price <= anchor) if direction == "BUY" else (price >= anchor)
                    if back:
                        cp, src = confirm_price(t["pair"], price)
                        still = ((cp <= anchor) if direction == "BUY"
                                 else (cp >= anchor))
                        if not still:
                            logger.info(f"BE not confirmed on {tid} "
                                        f"(watch {price}, {src} {cp}), holding")
                            continue
                        logger.info(f"Breakeven {tid} at {cp} (confirmed by {src})")
                        close_trade(tid, "BE", reply_id)
                        continue

                # Profit milestones
                for lvl in ALERT_LEVELS:
                    if pips < lvl or lvl in seen:
                        continue

                    # Spend one Twelve Data call to confirm before posting
                    cp, src = confirm_price(t["pair"], price)
                    confirmed_pips = pips_in_profit(t["pair"], direction, anchor, cp)
                    if confirmed_pips < lvl:
                        logger.info(f"{lvl} not confirmed on {tid} "
                                    f"(watch {pips}p, {src} {confirmed_pips}p), waiting")
                        break
                    logger.info(f"{lvl} confirmed by {src} at {confirmed_pips} pips")

                    fut = asyncio.run_coroutine_threadsafe(
                        send_to_telegram(alert_message(lvl), reply_to_id=reply_id), loop)
                    try:
                        if fut.result(timeout=20) is not None:
                            with trade_lock:
                                if tid in reported_levels:
                                    reported_levels[tid].add(lvl)
                            logger.info(f"{lvl} pips alert sent ({tid})")
                    except Exception as e:
                        logger.error(f"Alert send failed: {e}")
                        break

                    if lvl == 200:
                        with trade_lock:
                            active_trades.pop(tid, None)
                            reported_levels.pop(tid, None)
                        logger.info(f"TP hit, closed {tid}")
                        break

        except Exception as e:
            logger.error(f"Monitor error: {e}")

        time.sleep(10)


threading.Thread(target=monitor_profits, daemon=True).start()


# ══════════════════════════════════════════════════════
# ENDPOINTS
# ══════════════════════════════════════════════════════
@app.route("/", methods=["GET"])
def health():
    with trade_lock:
        active = len(active_trades)
    mode = "SAVED MESSAGES (testing)" if SEND_TO_SAVED else "LIVE GROUP"
    return (
        "Terry Gold Bot running\n"
        f"Mode: {mode}\n"
        f"Active trades: {active}\n"
        f"Client: {'connected' if client else 'disconnected'}\n"
        f"Price feeds: MT5 {'yes' if mt5_prices['ts'] else 'not reporting'}, "
        f"OANDA {'key set' if OANDA_API_KEY else 'no key'}, "
        f"TwelveData {'key set' if TWELVE_DATA_KEY else 'no key'}, "
        f"free feed always on\n"
        f"TD confirmations used today: {td_budget['used_today']}/{TD_DAILY_CAP}\n"
        f"Claude wording: {'on' if ANTHROPIC_API_KEY else 'off, using fallbacks'}\n"
        f"Source channel: {SOURCE_CHANNEL_ID or 'not set'}\n"
        f"Target group: {TARGET_GROUP_ID or 'NOT SET'}\n"
        f"LIVE_MODE variable: {'true' if LIVE_MODE else 'false'}\n"
        f"Signal input: {'channel listener' if SOURCE_CHANNEL_ID else 'webhook only (/webhook)'}\n"
        f"Alerts at: {ALERT_LEVELS} pips\n"
        f"TP {TP_POINTS} points ({int(TP_POINTS * 10)} pips), "
        f"SL {SL_POINTS} points ({int(SL_POINTS * 10)} pips)\n"
    ), 200


@app.route("/webhook", methods=["POST"])
def webhook():
    """TradingView entry. Body: {"pair":"XAUUSD","direction":"BUY","price":4021.5}"""
    try:
        data = request.get_json(force=True)
        pair = str(data.get("pair", "XAUUSD")).upper()
        if "BTC" in pair:
            pair = "BTCUSD"
        elif "XAU" in pair or "GOLD" in pair:
            pair = "XAUUSD"

        direction = str(data.get("direction", "BUY")).upper()
        if direction not in ("BUY", "SELL"):
            return jsonify({"error": "direction must be BUY or SELL"}), 400

        price = float(str(data.get("price", "0")).replace(",", ""))
        if price <= 0:
            return jsonify({"error": "price required"}), 400

        with trade_lock:
            if any(t["pair"] == pair and t["status"] == "open"
                   for t in active_trades.values()):
                return jsonify({"status": "ignored", "reason": "trade already open"}), 200

        sig = {"pair": pair, "direction": direction, "entry": price}
        fut = asyncio.run_coroutine_threadsafe(open_trade_from_signal(sig), loop)
        tid = fut.result(timeout=30)

        return jsonify({"status": "ok", "trade_id": tid}), 200

    except Exception as e:
        logger.error(f"Webhook error: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/mt5_price", methods=["POST"])
def mt5_price():
    try:
        data = request.get_json(force=True)
        if "XAUUSD" in data:
            mt5_prices["XAUUSD"] = float(data["XAUUSD"])
        if "BTCUSD" in data:
            mt5_prices["BTCUSD"] = float(data["BTCUSD"])
        mt5_prices["ts"] = time.time()
        return jsonify({"status": "ok"}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/price", methods=["GET"])
def price_check():
    """Compare feeds before you trust them. /price?pair=GOLD"""
    p = request.args.get("pair", "GOLD").upper()
    pair = "BTCUSD" if ("BTC" in p or "BITCOIN" in p) else "XAUUSD"

    oanda = get_oanda_price(pair)
    age = (time.time() - mt5_prices["ts"]) if mt5_prices["ts"] else None
    mt5_val = mt5_prices.get(pair)
    fresh = (mt5_val is not None and age is not None and age <= MT5_FRESH_SECONDS)

    lines = [f"Pair: {pair}"]
    lines.append(f"MT5 feed: {mt5_val if mt5_val is not None else 'none yet'}"
                 + (f"  ({age:.0f}s ago, {'fresh' if fresh else 'stale'})"
                    if age is not None else ""))
    lines.append(f"OANDA:        {oanda if oanda is not None else 'none'}")
    td = get_twelvedata_price(pair, force=True)
    lines.append(f"Twelve Data:  {td if td is not None else 'none'}")
    if td is None and td_budget["last_error"]:
        lines.append(f"  TD error: {td_budget['last_error']}")
    backup = get_goldapi_price(pair)
    lines.append(f"Free feed:    {backup if backup is not None else 'none'}")
    lines.append("")

    size = PIP_SIZE.get(pair, 0.1)
    ref = mt5_val if mt5_val is not None else (oanda or td or backup)
    if ref:
        for label, val in [("OANDA", oanda), ("Twelve Data", td), ("Free feed", backup)]:
            if val is not None and ref != val:
                lines.append(f"{label} vs "
                             f"{'MT5' if mt5_val is not None else 'reference'}: "
                             f"{abs(val - ref):.2f} ({abs(val - ref) / size:.0f} pips)")
        lines.append("")

    if fresh:
        using = "MT5 (your broker, most accurate)"
    elif oanda:
        using = "OANDA"
    elif td:
        using = "Twelve Data"
    elif backup:
        using = "free feed"
    else:
        using = "NOTHING"
    lines.append(f"Bot will use (routine watching): {using}")
    lines.append(f"Confirms with Twelve Data before posting: "
                 f"{'yes' if TWELVE_DATA_KEY else 'no key set'}")
    lines.append(f"TD budget: {td_budget['used_today']}/{TD_DAILY_CAP} today")

    if using == "NOTHING":
        lines.append("")
        lines.append("No feed at all. The bot CANNOT fire alerts. Run /diag_price")
    elif not fresh:
        lines.append("")
        lines.append("MT5 not reporting. Point your PriceFeed EA at this service")
        lines.append("for prices that match your own broker exactly.")
    return "\n".join(lines), 200


@app.route("/diag_price", methods=["GET"])
def diag_price():
    """Why is there no price? Tries both OANDA hosts and lists your accounts."""
    out = []
    if not OANDA_API_KEY:
        return "OANDA_API_KEY is not set on this service.", 200

    out.append(f"Key ends: ...{OANDA_API_KEY[-6:]}")
    out.append(f"Account configured: {OANDA_ACCOUNT}")
    out.append("")

    hosts = [
        ("practice", "https://api-fxpractice.oanda.com"),
        ("live", "https://api-fxtrade.oanda.com"),
    ]

    for label, host in hosts:
        out.append(f"--- {label} ({host}) ---")

        # 1. Does the token work on this host at all?
        try:
            r = requests.get(f"{host}/v3/accounts",
                             headers={"Authorization": f"Bearer {OANDA_API_KEY}"},
                             timeout=10)
            out.append(f"list accounts: HTTP {r.status_code}")
            if r.status_code == 200:
                accts = [a.get("id") for a in r.json().get("accounts", [])]
                out.append(f"accounts on this key: {accts if accts else 'none'}")
                for acct in accts:
                    try:
                        pr = requests.get(
                            f"{host}/v3/accounts/{acct}/pricing",
                            params={"instruments": "XAU_USD"},
                            headers={"Authorization": f"Bearer {OANDA_API_KEY}"},
                            timeout=10)
                        if pr.status_code == 200:
                            px = pr.json()["prices"][0]
                            bid = float(px["bids"][0]["price"])
                            ask = float(px["asks"][0]["price"])
                            out.append(f"  {acct} XAU_USD mid: "
                                       f"{(bid + ask) / 2:.2f}  "
                                       f"(bid {bid:.2f} / ask {ask:.2f})")
                        else:
                            out.append(f"  {acct} pricing: HTTP {pr.status_code} "
                                       f"{pr.text[:120]}")
                    except Exception as e:
                        out.append(f"  {acct} pricing failed: {e}")
            else:
                out.append(f"body: {r.text[:200]}")
        except Exception as e:
            out.append(f"request failed: {e}")
        out.append("")

    out.append("If a mid price shows above, copy that host's account id into the")
    out.append("OANDA_ACCOUNT variable, and tell me which host it was on.")
    return "\n".join(out), 200


@app.route("/status", methods=["GET"])
def status():
    with trade_lock:
        info = []
        for tid, t in active_trades.items():
            live = get_price(t["pair"])
            info.append({
                "trade_id": tid,
                "pair": t["pair"],
                "direction": t["direction"],
                "entry": t["entry_price"],
                "anchor": t["profit_anchor"],
                "tp": t["tp"],
                "sl": t["sl"],
                "current_price": live,
                "pips": pips_in_profit(t["pair"], t["direction"], t["profit_anchor"], live)
                if live else None,
                "alerts_sent": sorted(str(x) for x in reported_levels.get(tid, [])),
                "age_minutes": round((time.time() - t["timestamp"]) / 60, 1),
            })
    return jsonify({"mode": "SAVED" if SEND_TO_SAVED else "LIVE",
                    "active": len(info), "trades": info}), 200


@app.route("/switch_mode", methods=["GET"])
def switch_mode():
    global SEND_TO_SAVED
    if SEND_TO_SAVED and not TARGET_GROUP_ID:
        return ("Cannot go live: TARGET_GROUP_ID is not set.\n"
                "Add it in Railway variables first."), 400
    SEND_TO_SAVED = not SEND_TO_SAVED
    note = ""
    if not SEND_TO_SAVED and not LIVE_MODE:
        note = ("\n\nWARNING: this only lasts until the next restart. "
                "Set LIVE_MODE=true in Railway to make it stick.")
    return (f"Now sending to "
            f"{'SAVED MESSAGES' if SEND_TO_SAVED else 'THE LIVE GROUP'}{note}"), 200


@app.route("/reset", methods=["GET"])
def reset():
    with trade_lock:
        n = len(active_trades)
        active_trades.clear()
        reported_levels.clear()
    last_sent_texts.clear()
    return f"Cleared {n} trades", 200


@app.route("/test_signal", methods=["GET"])
def test_signal():
    """/test_signal?dir=BUY&pair=GOLD&entry=4044.50"""
    try:
        direction = request.args.get("dir", "BUY").upper()
        p = request.args.get("pair", "GOLD").upper()
        pair = "BTCUSD" if ("BTC" in p or "BITCOIN" in p) else "XAUUSD"

        entry = request.args.get("entry")
        if entry:
            entry = float(entry)
        else:
            entry = get_price(pair)
            if not entry:
                return "No price available, pass &entry=4044.50", 500

        sig = {"pair": pair, "direction": direction, "entry": entry}
        fut = asyncio.run_coroutine_threadsafe(open_trade_from_signal(sig), loop)
        tid = fut.result(timeout=30)

        dest = "Saved Messages" if SEND_TO_SAVED else "the live group"
        return (f"Test {direction} {pair} @ {entry} posted to {dest}.\n"
                f"Trade id: {tid}\nNow tracking live, check /status"), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/simulate", methods=["GET"])
def simulate():
    """Post a whole trade the way it will actually look, threaded.
    /simulate?outcome=win   entry, 20, 60, 100, 200
    /simulate?outcome=sl    entry, 20, stop loss
    /simulate?outcome=be    entry, 20, 60, back to entry
    Optional &dir=SELL &entry=4021.00 &gap=4  (seconds between messages)
    Does not create a tracked trade and does not touch the price feeds.
    """
    outcome = request.args.get("outcome", "win").lower()
    direction = request.args.get("dir", "BUY").upper()
    gap = max(1, min(int(request.args.get("gap", "4")), 30))

    entry = request.args.get("entry")
    if entry:
        base = float(entry)
    else:
        base = get_price("XAUUSD") or 4021.00

    sequences = {
        "win": [20, 60, 100, 200],
        "sl": [20, "SL"],
        "be": [20, 60, "BE"],
    }
    if outcome not in sequences:
        return "Use ?outcome=win or ?outcome=sl or ?outcome=be", 400

    post, trade = build_trade({"pair": "XAUUSD",
                               "direction": direction,
                               "entry": round(base, 2)})

    def _run():
        try:
            fut = asyncio.run_coroutine_threadsafe(send_to_telegram(post), loop)
            root_id = fut.result(timeout=25)
            if root_id is None:
                logger.error("Simulation entry failed to send")
                return
            for step in sequences[outcome]:
                time.sleep(gap)
                asyncio.run_coroutine_threadsafe(
                    send_to_telegram(alert_message(step), reply_to_id=root_id),
                    loop).result(timeout=25)
            logger.info(f"Simulation '{outcome}' finished")
        except Exception as e:
            logger.error(f"Simulation error: {e}")

    threading.Thread(target=_run, daemon=True).start()

    dest = "Saved Messages" if SEND_TO_SAVED else "THE LIVE GROUP"
    steps = " then ".join(str(x) for x in sequences[outcome])
    total = gap * len(sequences[outcome])
    return (f"Simulating a '{outcome}' trade into {dest}.\n\n"
            f"{direction} gold, signal price {base:.2f}\n"
            f"entry band {min(trade['profit_anchor'], base):.2f} to "
            f"{max(trade['profit_anchor'], base):.2f}\n"
            f"tp {trade['tp']:.2f}  sl {trade['sl']:.2f}\n\n"
            f"Entry posts now, then {steps}, {gap}s apart.\n"
            f"All replies thread under the entry. Done in about {total}s.\n\n"
            f"Try the others: /simulate?outcome=sl  /simulate?outcome=be"), 200


@app.route("/test/<level>", methods=["GET"])
def test_level(level):
    mapping = {"20": 20, "60": 60, "100": 100, "200": 200,
               "tp": 200, "sl": "SL", "be": "BE"}
    key = level.lower()
    if key not in mapping:
        return "Use /test/20 /test/60 /test/100 /test/tp /test/sl /test/be", 400

    fut = asyncio.run_coroutine_threadsafe(
        send_to_telegram(alert_message(mapping[key])), loop)
    ok = fut.result(timeout=20)
    dest = "Saved Messages" if SEND_TO_SAVED else "the live group"
    return (f"Test '{key}' sent to {dest}" if ok else "Send failed"), 200


@app.route("/preview", methods=["GET"])
def preview():
    """See the wording without sending anything to Telegram."""
    demo_post, demo_trade = build_trade(
        {"pair": "XAUUSD", "direction": "BUY", "entry": 4044.50})
    out = ["ENTRY (real maths, signal price 4044.50):", demo_post,
           f"  anchor {demo_trade['profit_anchor']}  "
           f"tp {demo_trade['tp']}  sl {demo_trade['sl']}", ""]
    for lvl in ALERT_LEVELS + ["SL", "BE"]:
        out.append(f"{lvl}:")
        out.append(alert_message(lvl).replace("<b>", "").replace("</b>", ""))
        out.append("")
    return "\n".join(out), 200


@app.route("/diag", methods=["GET"])
def diag():
    """Is Claude actually answering, or are we silently on fallbacks?"""
    lines = []
    if not ANTHROPIC_API_KEY:
        return "ANTHROPIC_API_KEY not set. Running on fallback wording only.", 200

    try:
        r = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={"x-api-key": ANTHROPIC_API_KEY,
                     "anthropic-version": "2023-06-01",
                     "content-type": "application/json"},
            json={"model": "claude-haiku-4-5-20251001",
                  "max_tokens": 40,
                  "messages": [{"role": "user",
                                "content": "Reply with exactly: OK"}]},
            timeout=15,
        )
        lines.append(f"HTTP status: {r.status_code}")
        if r.status_code == 200:
            body = r.json()
            txt = " ".join(b.get("text", "") for b in body.get("content", []))
            lines.append(f"Reply: {txt.strip()}")
            lines.append("")
            lines.append("WORKING. Wording will be fresh every time.")
        else:
            lines.append(f"Error body: {r.text[:400]}")
            lines.append("")
            lines.append("NOT WORKING. Bot is using the fallback lines.")
            lines.append("401 = bad or revoked key. 404 = wrong model name.")
            lines.append("400 = malformed request. 429 = rate limited or no credit.")
    except Exception as e:
        lines.append(f"Request failed entirely: {e}")
        lines.append("NOT WORKING. Bot is using the fallback lines.")

    return "\n".join(lines), 200


@app.route("/debug_channel", methods=["GET"])
def debug_channel():
    if not last_channel_msgs:
        return "Nothing received from the source channel yet.", 200
    return "LAST RECEIVED:\n\n" + "\n\n".join(
        f"[{int(time.time() - m['ts'])}s ago] {m['text'][:200]}"
        for m in last_channel_msgs[-10:]), 200


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    logger.info(f"Starting Terry Gold Bot on port {port}")
    app.run(host="0.0.0.0", port=port, debug=False)
