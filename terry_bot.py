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
    20: ("price has moved 20 pips your way. Suggest they can shift their stop "
         "to entry now so the trade costs them nothing if it turns."),
    60: ("price is now 60 pips onside and still moving. Say it is going well "
         "and they can trail their stop up behind it if they want."),
    100: ("price is 100 pips onside, halfway to the target. Say it is running "
          "well and their stop can sit safely in profit now."),
    200: ("price reached the full 200 pip target. Say the target is done and "
          "you are pleased with the move."),
    "SL": ("the stop was hit and the trade is closed. Stay calm and matter of "
           "fact, say you are looking at the next setup."),
    "BE": ("price drifted back to the entry level after being onside. Say "
           "anyone who moved their stop to entry is out at nothing, and you "
           "are watching for the next one."),
}

FALLBACK_LINE = {
    20: [
        "20 pips onside already, shift your stop to entry and it costs you nothing",
        "Nice start bro, get your stop to entry and ride it for free",
        "20 up. Move your stop to entry and you cannot lose on it now",
        "Off to a decent start, stop to entry and let it breathe",
        "That is 20 for you bro, worth getting the stop up to entry",
        "Moving my stop to entry here, nothing to lose from this point",
        "20 pips in. Stop to entry and enjoy the rest of it",
        "Good early move, I would get that stop up to entry",
        "20 onside bro. Risk free from here if you move the stop",
        "Straight into profit, stop goes to entry for me",
        "20 up already, tidy start to this one",
        "Stop is at entry for me now, 20 pips onside",
        "Decent start bro, no reason to risk anything on it now",
        "20 pips your way. Get the stop to entry and relax",
        "Moved my stop up, 20 in the bag so far",
    ],
    60: [
        "60 pips onside now, trail that stop up behind it",
        "Running well bro, worth pulling your stop up as it goes",
        "60 up. Stop can sit in profit from here comfortably",
        "Nice run this one, trailing my stop behind the move",
        "60 pips your way bro, no reason to give any of it back",
        "Good move so far, get that stop trailing up",
        "60 onside. I am trailing mine, you do what suits you",
        "Momentum is with us, 60 up and still going",
        "60 pips in bro. Trail the stop and let it work",
        "Comfortable 60 onside now, protect it as it moves",
        "That is 60 for you, still plenty of room to target",
        "Trailing my stop up here, 60 onside and climbing",
        "60 up bro, this one is behaving itself nicely",
        "Solid 60 pips, stop is well clear of trouble now",
        "60 onside and holding, happy with this",
    ],
    100: [
        "100 pips onside, halfway to target and running clean",
        "100 up bro, stop can sit well in profit now",
        "Halfway there, 100 onside and still pushing",
        "100 pips your way, this is behaving exactly as I wanted",
        "That is 100 bro. Halfway to target with room left",
        "Trailing my stop again here, 100 onside",
        "100 up and moving, target is well within reach",
        "Halfway to the target now, 100 pips clear",
        "100 pips onside bro, keep that stop moving with it",
        "Clean 100 up, letting this one run to target",
        "100 onside. Stop is deep in profit, nothing to worry about",
        "Halfway home bro, 100 pips and holding strong",
        "100 up. This is the sort of run you wait for",
        "Nicely onside now at 100, target next",
        "100 pips in profit, still plenty in this move",
    ],
    200: [
        "Target done, full 200 pips, very happy with that one",
        "200 pips bro, target reached and closed out",
        "That is the full 200, exactly where I wanted it",
        "Target hit at 200 pips, clean run from start to finish",
        "200 done bro. Textbook move that one",
        "Full target reached, 200 pips, on to the next",
        "200 pips banked, that is the trade done",
        "Target smashed at 200, pleased with how that ran",
        "That is 200 bro, closing this one out here",
        "Full 200 pips, exactly to target, happy days",
        "Target reached. 200 pips and this one is finished",
        "200 onside and target done, cannot ask for more",
        "Closed at target bro, full 200 pips on it",
        "That is the 200 done, clean trade all the way",
        "Target hit, 200 pips secured, that is that one wrapped",
    ],
    "SL": [
        "Stopped out on this one, on to the next setup",
        "Not this time bro, already looking at the next one",
        "Took the stop, happens, next one is out there",
        "That one did not work, watching for the next entry",
        "Stopped out bro, no drama, plenty more coming",
        "Did not go my way this time, back to the charts",
        "Stop hit. Part of it, on to the next",
        "Wrong side of that one bro, next setup is coming",
        "Took the loss, moving on, I will find the next",
        "Stopped out. Nothing to dwell on, next one",
        "That one got me bro, already hunting the next",
        "Loss on this one, it happens, staying patient",
        "Stop taken, market had other ideas, next up",
        "Not our one bro, watching for a fresh setup",
        "Stopped. On to the next, no point overthinking it",
    ],
    "BE": [
        "Back to entry. Anyone who moved their stop is out at nothing",
        "Came back to entry bro, no harm done if your stop was there",
        "Drifted back to entry, that is why the stop goes up early",
        "Back at entry level. Out at nothing, watching for the next",
        "Returned to entry bro, no loss if you moved your stop",
        "Back to where it started, nothing lost, next one coming",
        "Entry level again. That stop move earlier paid off",
        "Came all the way back bro, out flat, on to the next",
        "Back to entry, no damage, looking for a fresh setup",
        "Right back to entry. Out at breakeven, no complaints",
        "Drifted back bro, glad the stop was at entry",
        "Entry hit again, flat on it, watching the charts",
        "Back to breakeven, nothing gained nothing lost",
        "Came back to entry bro, that is the game sometimes",
        "Out at entry. Next setup is what matters now",
    ],
}

ENTRY_FALLBACK = {
    "BUY": [
        "Buying gold here {elow} to {ehigh}. TP {tp}, SL {sl}",
        "In long on gold bro, {elow} to {ehigh}. Target {tp}, stop {sl}",
        "Getting long gold, entry {elow} to {ehigh}, TP {tp}, SL {sl}",
        "Taking a buy on gold {elow} to {ehigh}. TP {tp}, SL {sl}",
        "Long gold from {elow} to {ehigh} bro. Target {tp}, stop at {sl}",
        "Just gone long gold, {elow} to {ehigh}, TP {tp} and SL {sl}",
        "Buying this area {elow} to {ehigh}. TP {tp}, SL {sl}",
        "In on gold long bro, {elow} to {ehigh}, target {tp}, stop {sl}",
    ],
    "SELL": [
        "Selling gold here {elow} to {ehigh}. TP {tp}, SL {sl}",
        "In short on gold bro, {elow} to {ehigh}. Target {tp}, stop {sl}",
        "Getting short gold, entry {elow} to {ehigh}, TP {tp}, SL {sl}",
        "Taking a sell on gold {elow} to {ehigh}. TP {tp}, SL {sl}",
        "Short gold from {elow} to {ehigh} bro. Target {tp}, stop at {sl}",
        "Just gone short gold, {elow} to {ehigh}, TP {tp} and SL {sl}",
        "Selling this area {elow} to {ehigh}. TP {tp}, SL {sl}",
        "In on gold short bro, {elow} to {ehigh}, target {tp}, stop {sl}",
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
            "lands naturally. Never force it. Never say 'guys', 'team', 'fam' or "
            "'everyone'. Use 'I' for yourself and 'you' when talking to them.\n\n"
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
            "Stick to what the price has done and what they could do about it. "
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


def entry_message(name, direction, elow, ehigh, tp, sl, dec):
    facts = (f"{'buying' if direction == 'BUY' else 'selling'} {name}, "
             f"entry {elow:.{dec}f} to {ehigh:.{dec}f}, "
             f"take profit {tp:.{dec}f}, stop loss {sl:.{dec}f}")

    out = _ask_claude(
        "You are a UK gold trader posting your own trade into your Telegram "
        "group. Write ONE short message like a human texting a mate, not a "
        "formatted signal card.\n\n"
        "Include these numbers exactly, and no other numbers at all: "
        + facts + ".\n\n"
        "VOICE: casual, British, first person 'I'. You may say 'bro' but only "
        "about one time in three and only if it reads naturally. Never 'guys', "
        "'team' or 'fam'. No emojis. No dashes of any kind, use commas. "
        "Never mention joining, subscribing, links, VIP or any service. "
        "Vary the opening so it is not identical each time. "
        "Output only the message."
    )
    if out and _not_a_repeat(out):
        return out

    pool = ENTRY_FALLBACK[direction]
    tpl = random.choice(pool)
    for _ in range(6):
        if _not_a_repeat(tpl):
            break
        tpl = random.choice(pool)
    line = tpl.format(elow=f"{elow:.{dec}f}", ehigh=f"{ehigh:.{dec}f}",
                      tp=f"{tp:.{dec}f}", sl=f"{sl:.{dec}f}")
    if name != "gold":
        line = line.replace("gold", name)
    return line


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
