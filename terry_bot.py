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
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")

# Test mode: True = Saved Messages, False = live group. Flip with /switch_mode
SEND_TO_SAVED = True

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
        "✅ <b>20 PIPS DOWN</b>",
    ],
    60: [
        "✅ <b>60 PIPS IN PROFIT</b>",
        "✅ <b>60 PIPS GREEN NOW</b>",
        "✅ <b>60 PIPS UP NOW</b>",
        "✅ <b>60 PIPS BANKED SO FAR</b>",
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
    20: "the trade is 20 pips in profit already. Mention they can move stop to entry now and go risk free.",
    60: "the trade is 60 pips in profit and running well. Mention securing some or trailing the stop.",
    100: "the trade is 100 pips in profit, halfway to target. Mention locking some in or letting it run.",
    200: "take profit just hit, the full 200 pips. Say you have closed it and you are happy with it.",
    "SL": "the stop loss was hit. Stay calm and confident, say you will catch the next one.",
    "BE": "price came back to entry after being in profit. Say well done to anyone who secured earlier, you are looking for the next entry.",
}

FALLBACK_LINE = {
    20: [
        "Moving my stop to entry now, this one is risk free from here",
        "Stop is going to entry, nothing to lose on it now",
        "20 up already, I am shifting my stop to entry",
        "Off to a good start, my stop is at entry now",
    ],
    60: [
        "Running nicely, I am trailing my stop up behind it",
        "60 up now, secure some here if you want to",
        "Good move so far, I am trailing my stop higher",
        "Happy with this one, trailing the stop as it goes",
    ],
    100: [
        "100 up, halfway to my target, stop is well in profit now",
        "Halfway there, I am letting this one keep running",
        "100 in the bag so far, trailing my stop behind it",
        "Nice run this, secure some or ride it with me",
    ],
    200: [
        "Target hit, 200 pips, I have closed this one out",
        "That is the full 200, closed and banked, happy with that",
        "TP done at 200 pips, closing it here, good one",
        "Full target reached, 200 pips secured, on to the next",
    ],
    "SL": [
        "Stopped out on this one, no drama, on to the next setup",
        "That one did not go my way, already looking at the next",
        "Took the stop, it happens, I will be back with the next entry",
        "Not this time, I am watching for the next one now",
    ],
    "BE": [
        "Back at entry, if you secured earlier then well done, looking for the next one",
        "Came back to my entry, nothing lost, hunting the next setup",
        "Breakeven on this, hope you banked some on the way up",
        "Back to entry, out at nothing, I am watching for a new one",
    ],
}

ENTRY_FALLBACK = {
    "BUY": [
        "Buying {name} here, {elow} to {ehigh}. TP {tp}, SL {sl}",
        "Getting long {name} now, entry {elow} to {ehigh}, target {tp}, stop {sl}",
        "Taking a buy on {name}, {elow} to {ehigh}. TP {tp}, SL {sl}",
        "In on {name} long from {elow} to {ehigh}, TP {tp} and stop at {sl}",
    ],
    "SELL": [
        "Selling {name} here, {elow} to {ehigh}. TP {tp}, SL {sl}",
        "Getting short {name} now, entry {elow} to {ehigh}, target {tp}, stop {sl}",
        "Taking a sell on {name}, {elow} to {ehigh}. TP {tp}, SL {sl}",
        "In on {name} short from {elow} to {ehigh}, TP {tp} and stop at {sl}",
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
            json={"model": "claude-sonnet-4-6",
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
            "You are a real gold trader posting a short personal update to your "
            "own Telegram group. Write ONE natural line, max 16 words. "
            "Sound like a relaxed human texting, not a signals service. "
            "No emojis. No dashes of any kind, use commas or full stops. "
            "First person 'I' only, never 'we', 'team', 'guys' or 'everyone'. "
            "Never mention joining anything, prices, links or other services. "
            "No heading, just the single line. Vary it so it is not the same as last time. "
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


def entry_message(name, direction, elow, ehigh, tp, sl, dec):
    facts = (f"{'buying' if direction == 'BUY' else 'selling'} {name}, "
             f"entry {elow:.{dec}f} to {ehigh:.{dec}f}, "
             f"take profit {tp:.{dec}f}, stop loss {sl:.{dec}f}")

    out = _ask_claude(
        "You are a real trader posting your own trade to your Telegram group. "
        "Write ONE short natural message like a human texting, not a formatted card. "
        "Include all these numbers exactly and nothing else numeric: " + facts + ". "
        "First person 'I'. No emojis. No dashes, use commas. "
        "Vary the opening so it is not identical each time. "
        "Never mention joining, subscribing, links or any other service. "
        "Output only the message."
    )
    if out and _not_a_repeat(out):
        return out

    tpl = random.choice(ENTRY_FALLBACK[direction])
    return tpl.format(name=name, elow=f"{elow:.{dec}f}", ehigh=f"{ehigh:.{dec}f}",
                      tp=f"{tp:.{dec}f}", sl=f"{sl:.{dec}f}")


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
        url = f"https://api-fxpractice.oanda.com/v3/accounts/{OANDA_ACCOUNT}/pricing"
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


def get_price(pair):
    """MT5 feed when it is fresh, OANDA otherwise."""
    if (mt5_prices.get(pair) is not None
            and (time.time() - mt5_prices["ts"]) <= MT5_FRESH_SECONDS):
        return mt5_prices[pair]
    return get_oanda_price(pair)


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
                    logger.info(f"SL hit {tid} at {price}")
                    close_trade(tid, "SL", reply_id)
                    continue

                # Back to entry after having been 20+ pips up
                if 20 in seen:
                    back = (price <= anchor) if direction == "BUY" else (price >= anchor)
                    if back:
                        logger.info(f"Breakeven {tid} at {price}")
                        close_trade(tid, "BE", reply_id)
                        continue

                # Profit milestones
                for lvl in ALERT_LEVELS:
                    if pips < lvl or lvl in seen:
                        continue

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
        f"OANDA: {'set' if OANDA_API_KEY else 'no key'}\n"
        f"Claude wording: {'on' if ANTHROPIC_API_KEY else 'off, using fallbacks'}\n"
        f"Source channel: {SOURCE_CHANNEL_ID or 'not set, webhook only'}\n"
        f"Target group: {TARGET_GROUP_ID or 'not set'}\n"
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
    lines.append(f"OANDA: {oanda if oanda is not None else 'none'}")
    if mt5_val is not None and oanda is not None:
        lines.append(f"Difference: {abs(mt5_val - oanda):.2f} "
                     f"({abs(mt5_val - oanda) / PIP_SIZE.get(pair, 0.1):.0f} pips)")
    lines.append(f"Bot will use: {'MT5' if fresh else 'OANDA'}")
    return "\n".join(lines), 200


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
    SEND_TO_SAVED = not SEND_TO_SAVED
    return f"Now sending to {'SAVED MESSAGES' if SEND_TO_SAVED else 'LIVE GROUP'}", 200


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
    out = ["ENTRY:", entry_message("gold", "BUY", 4044.50, 4046.50,
                                   4064.50, 4038.50, 2), ""]
    for lvl in ALERT_LEVELS + ["SL", "BE"]:
        out.append(f"{lvl}:")
        out.append(alert_message(lvl).replace("<b>", "").replace("</b>", ""))
        out.append("")
    return "\n".join(out), 200


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
