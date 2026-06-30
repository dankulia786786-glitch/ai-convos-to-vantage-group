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
import requests

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)

# ── ENV ──────────────────────────────────────────────
API_ID = int(os.environ.get("API_ID", "0"))
API_HASH = os.environ.get("API_HASH", "")
VANTAGE_SESSION_STRING = os.environ.get("VANTAGE_SESSION_STRING", "")
VANTAGE_PHONE = os.environ.get("VANTAGE_PHONE", "")

SEND_TO_SAVED = True  # test mode -> Saved Messages; /switch_mode flips to group

VANTAGE_GROUP_ID = int(os.environ.get("VANTAGE_GROUP_ID", "0"))
VANTAGE_TOPIC_ID = int(os.environ.get("VANTAGE_TOPIC_ID", "0"))
SOURCE_CHANNEL_ID = int(os.environ.get("SOURCE_CHANNEL_ID", "-1001673250065"))
OANDA_API_KEY = os.environ.get("OANDA_API_KEY", "")

# ── RECALC RULES (points from source entry) ──────────
ENTRY_WIDEN = 4.0
TP1_POINTS = 10.0
TP2_POINTS = 20.0
SL_POINTS = 10.0
PIP_SIZE = {"XAUUSD": 0.10, "BTCUSD": 1.0}  # price move = 1 pip

# ── STATE ────────────────────────────────────────────
client = None
loop = asyncio.new_event_loop()
active_trades = {}
trade_lock = threading.Lock()
reported_levels = {}


def run_loop():
    asyncio.set_event_loop(loop)
    loop.run_forever()


threading.Thread(target=run_loop, daemon=True).start()

# ── RUNNING ALERT TEMPLATES (pips) ───────────────────
MESSAGE_TEMPLATES = {
    20: [
        "<b>\u2705\u2705\u2705 20 PIPS IN PROFIT</b>\n\nSecure it now or move SL to entry and let it run risk-free",
        "<b>\u2705\u2705\u2705 20 PIPS SECURED</b>\n\nClose part here or shift your SL to entry to lock it in",
        "<b>\u2705\u2705\u2705 20 PIPS UP</b>\n\nMove SL to break even now and let the rest ride safely",
    ],
    40: [
        "<b>\u2705\u2705\u2705 40 PIPS IN PROFIT</b>\n\nLock it in \u2014 move SL to entry or close part to secure gains",
        "<b>\u2705\u2705\u2705 40 PIPS SECURED</b>\n\nProtect your profit, shift SL to break even and let it run",
        "<b>\u2705\u2705\u2705 40 PIPS RUNNING NICELY</b>\n\nSecure some profit or trail your SL to entry",
    ],
    80: [
        "<b>\u2705\u2705\u2705 80 PIPS IN PROFIT</b>\n\nBig move \u2014 secure profits or move SL to entry and let it run",
        "<b>\u2705\u2705\u2705 80 PIPS SECURED</b>\n\nClose all to bank it or trail SL up to protect this run",
        "<b>\u2705\u2705\u2705 80 PIPS FLYING</b>\n\nSecure your profit or move SL to entry and stay in for more",
    ],
    100: [
        "<b>\u2705\u2705\u2705 TP1 SMASHED 100+ PIPS</b>\n\nSecure profits or move SL to entry and let it run to TP2!",
        "<b>\u2705\u2705\u2705 TP1 HIT 100 PIPS IN PROFIT</b>\n\nLock it in, move SL into profit and ride toward TP2!",
        "<b>\u2705\u2705\u2705 TP1 SMASHED 100 PIPS</b>\n\nSecure your gains now or let it run risk-free to the next target!",
    ],
    150: [
        "<b>\u2705\u2705\u2705 150 PIPS RUNNING</b>\n\nMonster move \u2014 secure profits or trail your SL and let it push on!",
        "<b>\u2705\u2705\u2705 150 PIPS IN PROFIT</b>\n\nBank some now or move SL deep into profit and ride toward TP2!",
        "<b>\u2705\u2705\u2705 150 PIPS AND CLIMBING</b>\n\nProtect this run \u2014 close part or trail SL up to lock it in!",
    ],
    200: [
        "<b>\u2705\u2705\u2705 TP2 SMASHED 200 PIPS</b>\n\nHuge result \u2014 I'm securing profit here. What a run!",
        "<b>\u2705\u2705\u2705 TP2 HIT 200 PIPS IN PROFIT</b>\n\nBanking it now \u2014 locked in a big win!",
        "<b>\u2705\u2705\u2705 TP2 DONE 200 PIPS SECURED</b>\n\nClosing it out and enjoying the profit!",
    ],
    "SL": [
        "\u274c <b>STOP LOSS HIT</b>\n\nStopped out this time \u2014 no worries, I'll catch the next one. \ud83d\udcaa",
        "\u274c <b>STOP LOSS HIT</b>\n\nThat one didn't work \u2014 I'm already hunting the next setup. \ud83c\udfaf",
        "\u274c <b>STOP LOSS HIT</b>\n\nNo worries, I'll try again on the next entry. \ud83d\ude80",
    ],
    "BE": [
        "\u26a0\ufe0f <b>BREAKEVEN HIT</b>\n\nFor those who secured profit earlier \u2014 congratulations! I'm now looking for new entries.",
        "\u26a0\ufe0f <b>BACK TO BREAKEVEN</b>\n\nIf you locked in profit, well done! I'm watching for the next setup now.",
        "\u26a0\ufe0f <b>BREAKEVEN</b>\n\nHope you banked some on the way up \u2014 nicely done. I'll be looking for the next entry.",
    ],
}

PROMO_MARKERS = ["JOIN", "FREE", "WHATSAPP", "WHATS APP", " DM ", "SUPPORT",
                 "T.ME/", "HTTP", "SUBSCRIBE", "PM NOW"]


# ── AI FRESH MESSAGE GENERATOR (fixed heading + fresh line beneath) ──
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")

# Fixed top headings — these NEVER change
FIXED_HEADING = {
    20: "\u2705\u2705\u2705 <b>20 PIPS IN PROFIT</b>",
    40: "\u2705\u2705\u2705 <b>40 PIPS IN PROFIT</b>",
    80: "\u2705\u2705\u2705 <b>80 PIPS IN PROFIT</b>",
    100: "\u2705\u2705\u2705 <b>TP1 SMASHED 100+ PIPS</b>",
    150: "\u2705\u2705\u2705 <b>150 PIPS IN PROFIT</b>",
    200: "\u2705\u2705\u2705 <b>TP2 SMASHED 200 PIPS</b>",
    "SL": "\u274c <b>STOP LOSS HIT</b>",
    "BE": "\u26a0\ufe0f <b>BREAKEVEN HIT</b>",
}

LEVEL_BRIEF = {
    20: "the trade is 20 pips in profit. Say they can secure it now or move stop loss to entry to go risk-free.",
    40: "the trade is 40 pips in profit. Say to lock in profit or trail the stop loss.",
    80: "the trade is 80 pips in profit, a big move. Say to secure profits or trail the stop loss.",
    100: "TP1 just hit, 100 pips. Say to secure or move stop into profit and let it run to TP2.",
    150: "the trade is 150 pips and running. Say to bank some or trail the stop loss.",
    200: "TP2 just hit, 200 pips, huge win. Say to secure the profit.",
    "SL": "the stop loss was hit. Stay positive and confident, say you'll catch the next setup.",
    "BE": "price came back to breakeven after being in profit. Congratulate anyone who secured profit earlier and say you're looking for new entries.",
}

# Fallback bottom lines (no dashes) if the API fails
FALLBACK_LINE = {
    20: ["Secure it now or move your SL to entry and ride it risk free \U0001f4b0",
         "Lock some in or shift your SL to entry, your call \U0001f680",
         "Bank a bit here or go risk free by moving SL to entry \U0001f4c8"],
    40: ["Protect it now or trail your SL up, looking strong \U0001f525",
         "Secure some profit or let it run, SL to entry \U0001f4aa",
         "Lock it in or trail the stop, momentum is with us \U0001f4c8"],
    80: ["Massive move, secure profits or trail that SL up \U0001f525",
         "Bank some here or ride it with SL trailing \U0001f680",
         "Big one, lock profit in or let it keep flying \U0001f4b0"],
    100: ["TP1 done! Secure it or move SL to profit and chase TP2 \U0001f3af",
          "Smashed TP1! Lock in or let it run to the next target \U0001f525",
          "TP1 in the bag! Protect it or ride toward TP2 \U0001f4b0"],
    150: ["150 up and flying! Bank some or trail the stop \U0001f525",
          "Monster run! Secure profit or let it keep going \U0001f680",
          "Still climbing! Lock some in or trail your SL up \U0001f4c8"],
    200: ["TP2 smashed! Huge result, banking this one \U0001f4b0",
          "200 pips done! Locking in a beauty \U0001f525",
          "TP2 hit! Securing this massive win \U0001f3af"],
    "SL": ["Stopped out this time, no stress, I'll catch the next one \U0001f4aa",
           "That one didn't go my way, already hunting the next setup \U0001f3af",
           "Took the loss, I'll be right back with the next entry \U0001f680"],
    "BE": ["Back to breakeven. If you secured profit earlier, well done! I'm looking for new entries \U0001f4b0",
           "Breakeven now. Hope you banked some on the way up! Hunting the next setup \U0001f680",
           "Came back to entry. Congrats if you locked profit in! On to the next one \U0001f525"],
}


def _clean_line(text):
    # strip any em/en dashes and stray heading the model may add
    text = text.replace("\u2014", ",").replace("\u2013", ",").replace(" - ", ", ")
    # remove a leading bold heading if the model added one
    text = re.sub(r"^\s*<b>.*?</b>\s*", "", text).strip()
    # keep it to the first 1-2 sentences
    return text.strip()


def ai_message(level):
    """Fixed heading on top + a fresh, dash-free line beneath (AI, with fallback)."""
    heading = FIXED_HEADING.get(level, "")
    brief = LEVEL_BRIEF.get(level, "")
    line = None
    if ANTHROPIC_API_KEY and brief:
        try:
            r = requests.post(
                "https://api.anthropic.com/v1/messages",
                headers={"x-api-key": ANTHROPIC_API_KEY,
                         "anthropic-version": "2023-06-01",
                         "content-type": "application/json"},
                json={
                    "model": "claude-sonnet-4-6",
                    "max_tokens": 80,
                    "messages": [{
                        "role": "user",
                        "content": (
                            "You're a hype, punchy gold/forex trader. Write ONE short line (max ~18 words) to your followers. "
                            "High energy, 1-2 emojis, first person 'I' only, NEVER 'we'/'team'/'us'/'group'. "
                            "ABSOLUTELY NO dashes of any kind (no - no \u2013 no \u2014); use commas or full stops instead. "
                            "Do NOT include any heading or ticks, just the single line. Vary it so it never repeats. "
                            "Context: " + brief
                        ),
                    }],
                },
                timeout=12,
            )
            if r.status_code == 200:
                parts = [b.get("text", "") for b in r.json().get("content", []) if b.get("type") == "text"]
                out = _clean_line(" ".join(parts))
                if out:
                    line = out
            else:
                logger.warning(f"Anthropic msg {r.status_code}: {r.text[:150]}")
        except Exception as e:
            logger.warning(f"Anthropic msg failed: {e}")

    if not line:
        line = random.choice(FALLBACK_LINE.get(level, ["Trade update \U0001f4c8"]))

    return f"{heading}\n\n{line}"


# ── CLIENT + CHANNEL LISTENER ────────────────────────
async def init_client():
    global client
    try:
        if not (VANTAGE_SESSION_STRING and VANTAGE_PHONE):
            logger.error("Missing VANTAGE_SESSION_STRING / VANTAGE_PHONE")
            return False
        client = TelegramClient(StringSession(VANTAGE_SESSION_STRING), API_ID, API_HASH)
        await client.connect()
        if not await client.is_user_authorized():
            logger.error("Session not authorized")
            return False
        me = await client.get_me()
        logger.info(f"Logged in as {me.first_name} (@{me.username})")

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


# ── PARSE SOURCE SIGNAL ──────────────────────────────
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

    entry_low = entry_high = None
    for line in text.splitlines():
        if "ENTRY" in line.upper():
            nums = [float(n) for n in re.findall(r"\d+(?:\.\d+)?", line.replace(",", ""))]
            if len(nums) >= 2:
                entry_low, entry_high = min(nums[0], nums[1]), max(nums[0], nums[1])
            elif len(nums) == 1:
                entry_low = entry_high = nums[0]
            break
    if entry_low is None:
        return None

    reasoning = ""
    for line in text.splitlines():
        if "\U0001f4a1" in line:
            reasoning = line.replace("\U0001f4a1", "").strip()
            break

    return {"direction": direction, "pair": pair,
            "entry_low": entry_low, "entry_high": entry_high, "reasoning": reasoning}


def is_sl_update(text):
    u = text.upper()
    if any(m in u for m in PROMO_MARKERS) and "ENTRY" not in u:
        return False
    return ("STOP LOSS" in u or "SL HIT" in u or "STOPPED OUT" in u)


# ── BUILD VANTAGE POST (A1 layout, recalculated) ─────


def reword_reasoning(reasoning, direction):
    if not reasoning:
        return ("Momentum is lining up on the higher timeframe, so I'm taking the "
                + ("long here." if direction == "BUY" else "short here."))

    # Try to reword with the Anthropic API: keep the facts/numbers, change the wording
    if ANTHROPIC_API_KEY:
        try:
            r = requests.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": ANTHROPIC_API_KEY,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json={
                    "model": "claude-sonnet-4-6",
                    "max_tokens": 120,
                    "messages": [{
                        "role": "user",
                        "content": (
                            "Reword this trading note in a natural first-person voice (use 'I', never 'we'/'team'). "
                            "Keep every number and indicator exactly the same. One or two short sentences, no emojis, "
                            "no preamble \u2014 just the reworded note:\n\n" + reasoning
                        ),
                    }],
                },
                timeout=12,
            )
            if r.status_code == 200:
                data = r.json()
                parts = [b.get("text", "") for b in data.get("content", []) if b.get("type") == "text"]
                out = " ".join(parts).strip()
                out = out.replace("\u2014", ",").replace("\u2013", ",").replace(" - ", ", ")
                if out:
                    return out
            else:
                logger.warning(f"Anthropic reword {r.status_code}: {r.text[:150]}")
        except Exception as e:
            logger.warning(f"Anthropic reword failed: {e}")

    # Fallback: light touch so it isn't identical to Kevin's
    return reasoning


def build_entry_post(sig):
    pair, direction, base = sig["pair"], sig["direction"], sig["entry_low"]
    if direction == "BUY":
        e1, e2 = base, base + ENTRY_WIDEN
        tp1, tp2, sl = base + TP1_POINTS, base + TP2_POINTS, base - SL_POINTS
    else:
        e1, e2 = base, base - ENTRY_WIDEN
        tp1, tp2, sl = base - TP1_POINTS, base - TP2_POINTS, base + SL_POINTS
    elow, ehigh = min(e1, e2), max(e1, e2)
    name = "GOLD" if pair == "XAUUSD" else "BITCOIN"
    arrow = "\U0001f7e2" if direction == "BUY" else "\U0001f534"
    dec = 2 if pair == "XAUUSD" else 1
    reasoning = reword_reasoning(sig["reasoning"], direction)
    post = (
        f"{arrow} <b>{name} \u2014 {direction} SETUP</b>\n"
        f"\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\n"
        f"Entry: {elow:.{dec}f} \u2013 {ehigh:.{dec}f}\n\n"
        f"\U0001f3af TP1   {tp1:.{dec}f}\n"
        f"\U0001f3af TP2   {tp2:.{dec}f}\n"
        f"\U0001f6d1 SL    {sl:.{dec}f}\n"
        f"\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\n"
        f"\U0001f4a1 {reasoning}"
    )
    trade = {"pair": pair, "direction": direction, "entry_price": base,
             "profit_anchor": (ehigh if direction == "BUY" else elow),
             "tp1": tp1, "tp2": tp2, "sl": sl, "timestamp": time.time(), "status": "open"}
    return post, trade


# ── HANDLE SOURCE MESSAGE ────────────────────────────
async def handle_source_message(text):
    u = text.upper()
    if any(m in u for m in PROMO_MARKERS) and "ENTRY" not in u:
        logger.info("Skipped promo/non-trade message")
        return
    sig = parse_signal(text)
    if sig:
        post, trade = build_entry_post(sig)
        tid = f"{sig['pair']}_{int(time.time())}"
        with trade_lock:
            active_trades[tid] = trade
            reported_levels[tid] = set()
        await send_to_telegram(post)
        logger.info(f"Posted recalculated entry {tid}")
        return
    if is_sl_update(text):
        await send_to_telegram(ai_message("SL"))
        logger.info("Forwarded SL update")
    else:
        logger.info("Ignored non-actionable message")


# ── OANDA + SEND ─────────────────────────────────────
def get_oanda_price(pair):
    if not OANDA_API_KEY:
        return None
    try:
        instrument = "XAU_USD" if pair == "XAUUSD" else "BTC_USD"
        url = "https://api-fxpractice.oanda.com/v3/accounts/001-011-8842842-001/pricing"
        headers = {"Authorization": f"Bearer {OANDA_API_KEY}"}
        r = requests.get(url, params={"instruments": instrument}, headers=headers, timeout=5)
        if r.status_code == 200:
            p = r.json()["prices"][0]
            return (float(p["bids"][0]["price"]) + float(p["asks"][0]["price"])) / 2
    except Exception as e:
        logger.error(f"OANDA price error: {e}")
    return None


# Live prices streamed from MT5 EA (preferred when fresh)
mt5_prices = {"XAUUSD": None, "BTCUSD": None, "ts": 0}
MT5_FRESH_SECONDS = 30  # if no update within this window, fall back to OANDA


def get_price(pair):
    """Prefer MT5 feed if it's fresh; otherwise fall back to OANDA."""
    if mt5_prices.get(pair) is not None and (time.time() - mt5_prices["ts"]) <= MT5_FRESH_SECONDS:
        return mt5_prices[pair]
    return get_oanda_price(pair)


async def send_to_telegram(text):
    global client
    try:
        if not client or not await client.is_user_authorized():
            logger.error("Client not authorized")
            return False
        entity = "me" if SEND_TO_SAVED else await client.get_entity(VANTAGE_GROUP_ID)
        reply = VANTAGE_TOPIC_ID if (not SEND_TO_SAVED and VANTAGE_TOPIC_ID) else None
        await client.send_message(entity, text, parse_mode="html", reply_to=reply)
        logger.info("Message sent")
        return True
    except Exception as e:
        logger.error(f"Send error: {e}")
        return False


def pips_in_profit(pair, direction, entry, current):
    size = PIP_SIZE.get(pair, 0.10)
    diff = (current - entry) if direction == "BUY" else (entry - current)
    return max(0, round(diff / size))


# ── PRICE MONITOR ────────────────────────────────────
def monitor_profits():
    logger.info("Profit monitor started (OANDA 10s)")
    levels = [20, 40, 80, 100, 150, 200]
    while True:
        try:
            now = time.time()
            with trade_lock:
                for tid in [t for t, v in active_trades.items()
                            if now - v.get("timestamp", now) > 10800]:
                    active_trades.pop(tid, None)
                    reported_levels.pop(tid, None)
                    logger.info(f"Auto-reset {tid} (3h)")
                trades_copy = dict(active_trades)

            for tid, t in trades_copy.items():
                if t["status"] != "open":
                    continue
                price = get_price(t["pair"])
                if not price:
                    continue

                anchor = t.get("profit_anchor", t["entry_price"])
                pips = pips_in_profit(t["pair"], t["direction"], anchor, price)

                # SL check first — closes trade, nothing else fires
                hit_sl = (price <= t["sl"]) if t["direction"] == "BUY" else (price >= t["sl"])
                if hit_sl:
                    with trade_lock:
                        if tid in active_trades:
                            active_trades.pop(tid, None)
                            reported_levels.pop(tid, None)
                            asyncio.run_coroutine_threadsafe(
                                send_to_telegram(ai_message("SL")), loop)
                            logger.info(f"SL hit {tid}")
                    continue

                # Breakeven check — only if trade had gone >= 20 pips, then came back to entry
                been_up = 20 in reported_levels.get(tid, set())
                back_to_be = (price <= anchor) if t["direction"] == "BUY" else (price >= anchor)
                if been_up and back_to_be and not t.get("be_sent"):
                    with trade_lock:
                        if tid in active_trades:
                            active_trades[tid]["be_sent"] = True
                            active_trades.pop(tid, None)
                            reported_levels.pop(tid, None)
                            asyncio.run_coroutine_threadsafe(
                                send_to_telegram(ai_message("BE")), loop)
                            logger.info(f"Breakeven hit {tid}")
                    continue

                # Profit levels
                for lvl in levels:
                    if pips >= lvl and lvl not in reported_levels.get(tid, set()):
                        txt = ai_message(lvl)
                        fut = asyncio.run_coroutine_threadsafe(send_to_telegram(txt), loop)
                        try:
                            if fut.result(timeout=15):
                                with trade_lock:
                                    if tid in reported_levels:
                                        reported_levels[tid].add(lvl)
                                logger.info(f"{lvl} pips alert ({tid})")
                        except Exception as e:
                            logger.error(f"Alert send failed: {e}")
        except Exception as e:
            logger.error(f"Monitor error: {e}")
        time.sleep(10)


threading.Thread(target=monitor_profits, daemon=True).start()


# ── ENDPOINTS ────────────────────────────────────────
@app.route("/", methods=["GET"])
def health():
    with trade_lock:
        active = len([t for t in active_trades.values() if t["status"] == "open"])
    mode = "SAVED MESSAGES (Testing)" if SEND_TO_SAVED else "VANTAGE GROUP (Live)"
    return (f"Trade Alert Bot v3 Running!\nMode: {mode}\nActive Trades: {active}\n"
            f"Client: {'Connected' if client else 'Disconnected'}\n"
            f"OANDA: {'Connected' if OANDA_API_KEY else 'No API key'}\n"
            f"Source channel: {SOURCE_CHANNEL_ID}\n"), 200


@app.route("/switch_mode", methods=["GET"])
def switch_mode():
    global SEND_TO_SAVED
    SEND_TO_SAVED = not SEND_TO_SAVED
    return f"Switched to {'SAVED MESSAGES' if SEND_TO_SAVED else 'VANTAGE GROUP'}!", 200


@app.route("/reset", methods=["GET"])
def reset():
    with trade_lock:
        n = len(active_trades)
        active_trades.clear()
        reported_levels.clear()
    return f"Cleared {n} trades!", 200


@app.route("/status", methods=["GET"])
def status():
    with trade_lock:
        info = [{"trade_id": tid, "pair": t["pair"], "direction": t["direction"],
                 "entry": t["entry_price"], "tp1": t["tp1"], "tp2": t["tp2"], "sl": t["sl"],
                 "reported": list(reported_levels.get(tid, []))}
                for tid, t in active_trades.items()]
    return jsonify({"mode": "SAVED" if SEND_TO_SAVED else "VANTAGE",
                    "active": len(info), "trades": info}), 200


@app.route("/test_signal", methods=["GET"])
def test_signal():
    try:
        d = request.args.get("dir", "BUY").upper()
        p = request.args.get("pair", "GOLD").upper()
        e = request.args.get("entry", "4044.54")
        is_gold = ("GOLD" in p or "XAU" in p)
        pairname = "XAU/USD | GOLD" if is_gold else "BTC/USD | BITCOIN"
        reason = ("Gold is forming a base near the 1H EMA50 at 4065.75 with RSI at 39.4 building. Watch for a break higher."
                  if is_gold else
                  "Bitcoin holding support with momentum building on the 1H. Bias up.")
        arrow = "\U0001f7e2" if d == "BUY" else "\U0001f534"
        sample = f"{arrow} {d} {pairname}\nENTRY : {e}\n\U0001f4a1 {reason}"
        sig = parse_signal(sample)
        if not sig:
            return "Could not parse sample", 500
        post, trade = build_entry_post(sig)
        tid = f"{sig['pair']}_{int(time.time())}"
        with trade_lock:
            active_trades[tid] = trade
            reported_levels[tid] = set()
        fut = asyncio.run_coroutine_threadsafe(send_to_telegram(post), loop)
        ok = fut.result(timeout=15)
        dest = "Saved Messages" if SEND_TO_SAVED else "VANTAGE GROUP"
        return (f"Sample {d} {p} entry posted to {dest}! Now tracking live. Check Telegram."
                if ok else "Send failed"), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/mt5_price", methods=["POST"])
def mt5_price():
    """Receive live prices streamed from the MT5 EA."""
    try:
        data = request.get_json(force=True)
        if "XAUUSD" in data:
            mt5_prices["XAUUSD"] = float(data["XAUUSD"])
        if "BTCUSD" in data:
            mt5_prices["BTCUSD"] = float(data["BTCUSD"])
        mt5_prices["ts"] = time.time()
        return jsonify({"status": "ok"}), 200
    except Exception as e:
        logger.error(f"mt5_price error: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/price", methods=["GET"])
def price():
    """Show MT5 (if fresh) and OANDA prices side by side. /price?pair=GOLD"""
    p = request.args.get("pair", "GOLD").upper()
    pair = "BTCUSD" if ("BTC" in p or "BITCOIN" in p) else "XAUUSD"
    oanda = get_oanda_price(pair)
    age = time.time() - mt5_prices["ts"] if mt5_prices["ts"] else None
    mt5_val = mt5_prices.get(pair)
    fresh = (mt5_val is not None and age is not None and age <= MT5_FRESH_SECONDS)
    lines = [f"Pair: {pair}"]
    lines.append(f"MT5 feed: {mt5_val if mt5_val is not None else 'none yet'}"
                 + (f"  ({age:.0f}s ago, {'FRESH' if fresh else 'STALE'})" if age is not None else ""))
    lines.append(f"OANDA: {oanda if oanda is not None else 'none'}")
    lines.append(f"Bot will use: {'MT5' if fresh else 'OANDA'}")
    return "\n".join(lines), 200


@app.route("/test/<level>", methods=["GET"])
def test_level(level):
    mp = {"20": 20, "40": 40, "80": 80, "tp1": 100, "150": 150, "tp2": 200, "sl": "SL", "be": "BE"}
    level = level.lower()
    if level not in mp:
        return "Use /test/20 /test/40 /test/80 /test/tp1 /test/150 /test/tp2 /test/sl", 400
    txt = ai_message(mp[level])
    fut = asyncio.run_coroutine_threadsafe(send_to_telegram(txt), loop)
    ok = fut.result(timeout=15)
    dest = "Saved Messages" if SEND_TO_SAVED else "VANTAGE GROUP"
    return (f"Test '{level}' sent to {dest}!" if ok else "Failed"), 200


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    logger.info(f"Starting Trade Alert Bot v3 on port {port}")
    app.run(host="0.0.0.0", port=port, debug=False)
