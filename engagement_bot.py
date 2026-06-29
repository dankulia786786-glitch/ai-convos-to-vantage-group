import os
import asyncio
import threading
import logging
import time
import json
import random
from datetime import datetime
from flask import Flask, request, jsonify
from telethon import TelegramClient
from telethon.sessions import StringSession
import requests

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)

# ═══════════════════════════════════════════════════════════
# ENVIRONMENT VARIABLES
# ═══════════════════════════════════════════════════════════

API_ID = int(os.environ.get("API_ID", "0"))
API_HASH = os.environ.get("API_HASH", "")
VANTAGE_SESSION_STRING = os.environ.get("VANTAGE_SESSION_STRING", "")
VANTAGE_PHONE = os.environ.get("VANTAGE_PHONE", "")

SEND_TO_SAVED = True  # Test mode: Saved Messages. False = Vantage group

VANTAGE_GROUP_ID = int(os.environ.get("VANTAGE_GROUP_ID", "0"))
VANTAGE_TOPIC_ID = int(os.environ.get("VANTAGE_TOPIC_ID", "0"))

OANDA_API_KEY = os.environ.get("OANDA_API_KEY", "")

# ═══════════════════════════════════════════════════════════
# GLOBAL STATE
# ═══════════════════════════════════════════════════════════

client = None
loop = asyncio.new_event_loop()

active_trades = {}
trade_lock = threading.Lock()

last_message_time = {}
message_cooldown_seconds = 1800

reported_levels = {}


def run_loop():
    asyncio.set_event_loop(loop)
    loop.run_forever()


threading.Thread(target=run_loop, daemon=True).start()


# ═══════════════════════════════════════════════════════════
# TELETHON CLIENT INITIALIZATION
# ═══════════════════════════════════════════════════════════

async def init_client():
    global client
    try:
        if VANTAGE_SESSION_STRING and VANTAGE_PHONE:
            client = TelegramClient(
                StringSession(VANTAGE_SESSION_STRING),
                API_ID,
                API_HASH
            )
            await client.connect()
            
            if await client.is_user_authorized():
                me = await client.get_me()
                logger.info(f"✅ Logged in as {me.first_name} ({me.username})")
                return True
    except Exception as e:
        logger.error(f"❌ Client init error: {e}")
    
    return False


future = asyncio.run_coroutine_threadsafe(init_client(), loop)
try:
    future.result(timeout=10)
except Exception as e:
    logger.error(f"Client init timeout: {e}")


# ═══════════════════════════════════════════════════════════
# MESSAGE TEMPLATES (VARIED & HUMAN-LIKE)
# ═══════════════════════════════════════════════════════════

MESSAGE_TEMPLATES = {
    20: [
        "<b>✅✅✅ 20 PIPS IN PROFIT</b>\n\nSecure it now or move SL to entry and let it run risk-free",
        "<b>✅✅✅ 20 PIPS SECURED</b>\n\nClose part here or shift your SL to entry to lock it in",
        "<b>✅✅✅ 20 PIPS UP</b>\n\nMove SL to break even now and let the rest ride safely",
        "<b>✅✅✅ 20 PIPS IN THE BAG</b>\n\nTake some off the table or protect it by moving SL to entry",
    ],
    40: [
        "<b>✅✅✅ 40 PIPS IN PROFIT</b>\n\nLock it in — move SL to entry or close part to secure gains",
        "<b>✅✅✅ 40 PIPS SECURED</b>\n\nProtect your profit now, shift SL to break even and let it run",
        "<b>✅✅✅ 40 PIPS UP</b>\n\nClose all here or move SL to entry to ride risk-free",
        "<b>✅✅✅ 40 PIPS RUNNING NICELY</b>\n\nSecure some profit or trail your SL to entry",
    ],
    80: [
        "<b>✅✅✅ 80 PIPS IN PROFIT</b>\n\nBig move — secure profits now or move SL to entry and let it run",
        "<b>✅✅✅ 80 PIPS SECURED</b>\n\nClose all to bank it or trail SL up to protect this run",
        "<b>✅✅✅ 80 PIPS UP</b>\n\nProtect it — move SL well into profit or take gains here",
        "<b>✅✅✅ 80 PIPS FLYING</b>\n\nSecure your profit or move SL to entry and stay in for more",
    ],
    100: [
        "<b>✅✅✅ TP1 SMASHED 100+ PIPS</b>\n\nSecure profits or move SL to entry and let it run to TP2!",
        "<b>✅✅✅ TP1 SMASHED 100 PIPS</b>\n\nClose all to bank it or trail SL up — more to come!",
        "<b>✅✅✅ TP1 HIT 100 PIPS IN PROFIT</b>\n\nLock it in, move SL into profit and ride toward TP2!",
        "<b>✅✅✅ TP1 SMASHED 100 PIPS</b>\n\nSecure your gains now or let it run risk-free to the next target!",
    ],
    150: [
        "<b>✅✅✅ 150 PIPS RUNNING</b>\n\nMonster move — secure profits or trail your SL and let it push on!",
        "<b>✅✅✅ 150 PIPS IN PROFIT</b>\n\nBank some now or move SL deep into profit and ride toward TP2!",
        "<b>✅✅✅ 150 PIPS AND CLIMBING</b>\n\nProtect this run — close part or trail SL up to lock it in!",
        "<b>✅✅✅ 150 PIPS SECURED</b>\n\nTake profit here or let it run risk-free, SL well in profit!",
    ],
    "TP2": [
        "<b>✅✅✅ TP2 SMASHED 200 PIPS</b>\n\nHuge result — secure your profits or trail SL and let it run on!",
        "<b>✅✅✅ TP2 HIT 200 PIPS IN PROFIT</b>\n\nBank it now or move SL deep in profit for the final push!",
        "<b>✅✅✅ TP2 SMASHED 200 PIPS</b>\n\nLock in this win — close all or trail your SL to protect it!",
        "<b>✅✅✅ TP2 DONE 200 PIPS SECURED</b>\n\nTake the profit or let the runner ride risk-free!",
    ],
    "TP3": [
        "<b>✅✅✅ TP3 SMASHED</b>\n\nALL TARGETS HIT! 💰 Secure the full profit, incredible run team!",
        "<b>✅✅✅ ALL TARGETS HIT</b>\n\nFull win secured! 💰 Close it all and bank this beauty!",
        "<b>✅✅✅ TP3 LOCKED</b>\n\nComplete victory! 💰 Secure every pip — well done team!",
    ],
    "SL": [
        "❌ <b>SL TRIGGERED</b>\n\nLooking for the next setup! We win on the next one! 💪",
        "❌ <b>STOP LOSS HIT</b>\n\nWe move on! Next opportunity incoming! 🎯",
        "❌ <b>SL CLOSED</b>\n\nOn to the next trade — stay ready! 🚀",
    ],
}


# ═══════════════════════════════════════════════════════════
# OANDA PRICE FETCHING
# ═══════════════════════════════════════════════════════════

def get_oanda_price(pair):
    """Get live price from OANDA API"""
    if not OANDA_API_KEY:
        return None
    
    try:
        instrument = "XAU_USD" if pair == "XAUUSD" else "BTC_USD"
        
        url = f"https://api-fxpractice.oanda.com/v3/accounts/001-011-8842842-001/pricing"
        params = {"instruments": instrument}
        headers = {
            "Authorization": f"Bearer {OANDA_API_KEY}",
            "Content-Type": "application/json"
        }
        
        r = requests.get(url, params=params, headers=headers, timeout=5)
        
        if r.status_code == 200:
            data = r.json()
            if "prices" in data and len(data["prices"]) > 0:
                bid = float(data["prices"][0]["bids"][0]["price"])
                ask = float(data["prices"][0]["asks"][0]["price"])
                mid = (bid + ask) / 2
                return mid
    except Exception as e:
        logger.error(f"❌ OANDA price error: {e}")
    
    return None


# ═══════════════════════════════════════════════════════════
# TELEGRAM MESSAGE SENDING (TEXT ONLY)
# ═══════════════════════════════════════════════════════════

async def send_to_telegram(text):
    """Send text message to Saved Messages or Vantage group"""
    global client
    
    try:
        if not client or not await client.is_user_authorized():
            logger.error("❌ Client not authorized")
            return False
        
        if SEND_TO_SAVED:
            entity = "me"
        else:
            entity = await client.get_entity(VANTAGE_GROUP_ID)
        
        await client.send_message(
            entity,
            text,
            parse_mode='html',
            reply_to=VANTAGE_TOPIC_ID if not SEND_TO_SAVED and VANTAGE_TOPIC_ID else None
        )
        
        logger.info(f"✅ Message sent")
        return True
    
    except Exception as e:
        logger.error(f"❌ Send error: {e}")
        return False


CHART_IMG_KEY = os.environ.get("CHART_IMG_KEY", "")


def get_chart_image(pair):
    """Fetch a TradingView chart screenshot from Chart-Img API. Returns bytes or None."""
    if not CHART_IMG_KEY:
        logger.warning("⚠️ CHART_IMG_KEY not set - skipping chart")
        return None
    try:
        symbol = "OANDA:XAUUSD" if pair == "XAUUSD" else "BINANCE:BTCUSDT"
        url = "https://api.chart-img.com/v2/tradingview/advanced-chart"
        headers = {"x-api-key": CHART_IMG_KEY, "content-type": "application/json"}
        payload = {
            "symbol": symbol,
            "interval": "15m",
            "theme": "dark",
            "width": 800,
            "height": 600,
        }
        r = requests.post(url, headers=headers, json=payload, timeout=15)
        if r.status_code == 200:
            logger.info(f"✅ Chart fetched for {pair}")
            return r.content
        logger.error(f"❌ Chart API {r.status_code}: {r.text[:200]}")
    except Exception as e:
        logger.error(f"❌ Chart fetch error: {e}")
    return None


async def send_chart_with_caption(image_bytes, caption):
    """Send a chart image with caption. Falls back to text if no image."""
    global client
    try:
        if not client or not await client.is_user_authorized():
            logger.error("❌ Client not authorized")
            return False

        entity = "me" if SEND_TO_SAVED else await client.get_entity(VANTAGE_GROUP_ID)
        reply = VANTAGE_TOPIC_ID if (not SEND_TO_SAVED and VANTAGE_TOPIC_ID) else None

        if image_bytes:
            from io import BytesIO
            bio = BytesIO(image_bytes)
            bio.name = "chart.png"
            await client.send_file(entity, bio, caption=caption,
                                   parse_mode='html', reply_to=reply)
            logger.info("✅ Chart + caption sent")
        else:
            await client.send_message(entity, caption, parse_mode='html', reply_to=reply)
            logger.info("✅ Caption sent (no chart)")
        return True
    except Exception as e:
        logger.error(f"❌ Chart send error: {e}")
        return False


def check_cooldown(trade_id, level):
    """Check if cooldown period has passed (30 minutes)"""
    key = f"{trade_id}_{level}"
    now = time.time()
    
    if key in last_message_time:
        elapsed = now - last_message_time[key]
        if elapsed < message_cooldown_seconds:
            return False
    
    last_message_time[key] = now
    return True


def calculate_pips(pair, direction, entry_price, current_price):
    """Calculate pips based on pair and direction"""
    try:
        if pair == "XAUUSD":
            pips = round((current_price - entry_price) / 0.01)
        elif pair == "BTCUSD":
            pips = int(current_price - entry_price)
        else:
            pips = round((current_price - entry_price) * 10000)
        
        if direction == "SELL":
            pips = -pips
        
        return max(0, pips)
    except Exception as e:
        logger.error(f"❌ Pips calculation error: {e}")
        return 0


# ═══════════════════════════════════════════════════════════
# WEBHOOK ENDPOINTS
# ═══════════════════════════════════════════════════════════

@app.route("/webhook", methods=["POST"])
def webhook():
    """Receive entry signals from TradingView"""
    try:
        data = request.get_json(force=True)
        event = data.get("event")
        pair = data.get("pair", "XAUUSD").upper()
        direction = data.get("direction", "BUY").upper()
        price = float(str(data.get("price", "0")).replace(",", ""))
        
        logger.info(f"📥 Webhook: {event} | {pair} | {direction} | {price}")
        
        if event == "entry":
            trade_id = f"{pair}_{int(time.time())}"
            
            with trade_lock:
                active_trades[trade_id] = {
                    "pair": pair,
                    "direction": direction,
                    "entry_price": price,
                    "timestamp": time.time(),
                    "status": "open"
                }
                reported_levels[trade_id] = set()
            
            logger.info(f"✅ Trade opened: {trade_id} at {price}")

            # Send the chart ONLY at entry, with entry details underneath
            arrow = "🟢" if direction == "BUY" else "🔴"
            entry_caption = (
                f"<b>{arrow} {direction} {pair}</b>\n\n"
                f"Entry: {price}\n"
                f"Trade is now live — alerts will follow as it runs into profit."
            )
            chart = get_chart_image(pair)
            fut = asyncio.run_coroutine_threadsafe(
                send_chart_with_caption(chart, entry_caption), loop
            )
            try:
                fut.result(timeout=25)
            except Exception as e:
                logger.error(f"❌ Entry chart send failed: {e}")

            return jsonify({"status": "ok", "trade_id": trade_id}), 200
        
        return jsonify({"status": "ok"}), 200
    
    except Exception as e:
        logger.error(f"❌ Webhook error: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/mt5_close", methods=["POST"])
def mt5_close():
    """Receive close signals from MT5"""
    try:
        data = request.get_json(force=True)
        pair = data.get("pair", "XAUUSD").upper()
        close_type = data.get("close_type", "").upper()
        price = float(data.get("price", 0))
        profit = float(data.get("profit", 0))
        
        logger.info(f"📥 MT5 close: {pair} {close_type} price={price} profit={profit}")
        
        if close_type in ("TP1", "TP2", "TP3"):
            template_key = close_type if close_type != "TP1" else 100
            text = random.choice(MESSAGE_TEMPLATES.get(template_key, MESSAGE_TEMPLATES[100]))
        elif close_type == "SL":
            text = random.choice(MESSAGE_TEMPLATES["SL"])
        else:
            logger.warning(f"Unknown close type: {close_type}")
            return jsonify({"status": "ignored"}), 200
        
        future = asyncio.run_coroutine_threadsafe(
            send_chart_with_caption(get_chart_image(pair), text), loop
        )
        try:
            result = future.result(timeout=25)
            if result:
                logger.info(f"✅ {close_type} message sent")
        except Exception as e:
            logger.error(f"❌ Send timeout/error: {e}")
        
        return jsonify({"status": "ok"}), 200
    
    except Exception as e:
        logger.error(f"❌ mt5_close error: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500


# ═══════════════════════════════════════════════════════════
# PROFIT MONITORING (Background Thread)
# ═══════════════════════════════════════════════════════════

def monitor_profits():
    """Monitor open trades and send profit level messages"""
    logger.info("✅ Profit monitor started - monitoring via OANDA (10s interval)")
    
    while True:
        try:
            # Auto-reset trades older than 3 hours so stale/unfired trades clear themselves
            now_ts = time.time()
            with trade_lock:
                expired = [tid for tid, t in active_trades.items()
                           if now_ts - t.get("timestamp", now_ts) > 10800]
                for tid in expired:
                    active_trades.pop(tid, None)
                    reported_levels.pop(tid, None)
                if expired:
                    logger.info(f"♻️ Auto-reset {len(expired)} trade(s) older than 3 hours")

            with trade_lock:
                trades_copy = dict(active_trades)
            
            for trade_id, trade in trades_copy.items():
                if trade["status"] != "open":
                    continue
                
                pair = trade["pair"]
                direction = trade["direction"]
                entry_price = trade["entry_price"]
                
                current_price = get_oanda_price(pair)
                
                if not current_price:
                    logger.debug(f"⚠️ Could not get price for {pair}")
                    continue
                
                pips = calculate_pips(pair, direction, entry_price, current_price)
                
                logger.debug(f"Trade {trade_id}: Entry={entry_price}, Current={current_price}, Pips={pips}")
                
                levels_to_check = sorted([lvl for lvl in MESSAGE_TEMPLATES.keys() if isinstance(lvl, int)])
                
                for level_pips in levels_to_check:
                    if pips >= level_pips and level_pips not in reported_levels.get(trade_id, set()):

                        text = random.choice(MESSAGE_TEMPLATES[level_pips])
                        
                        logger.info(f"📤 Sending {level_pips} pips alert for {trade_id}")
                        chart = get_chart_image(pair)
                        future = asyncio.run_coroutine_threadsafe(
                            send_chart_with_caption(chart, text),
                            loop
                        )
                        try:
                            result = future.result(timeout=25)
                            
                            if result:
                                with trade_lock:
                                    if trade_id in reported_levels:
                                        reported_levels[trade_id].add(level_pips)
                                
                                logger.info(f"✅ {level_pips} pips alert sent!")
                        except Exception as e:
                            logger.error(f"❌ Send failed: {e}")
        
        except Exception as e:
            logger.error(f"❌ Monitor error: {e}")
        
        time.sleep(10)


threading.Thread(target=monitor_profits, daemon=True).start()


# ═══════════════════════════════════════════════════════════
# TELEGRAM AUTHENTICATION (Generate Session String)
# ═══════════════════════════════════════════════════════════

import concurrent.futures

temp_clients = {}

def _run_in_new_loop(coro_func):
    """Run an async function in a brand-new event loop inside its own thread."""
    result_holder = {}

    def worker():
        new_loop = asyncio.new_event_loop()
        asyncio.set_event_loop(new_loop)
        try:
            result_holder['value'] = new_loop.run_until_complete(coro_func())
        except Exception as e:
            result_holder['error'] = e
        finally:
            new_loop.close()

    t = threading.Thread(target=worker)
    t.start()
    t.join(timeout=30)

    if 'error' in result_holder:
        raise result_holder['error']
    return result_holder.get('value')


@app.route("/send_code", methods=["GET"])
def send_code():
    """Send verification code to phone"""
    try:
        phone = VANTAGE_PHONE
        if not phone:
            return jsonify({"status": "error", "message": "VANTAGE_PHONE not set in Railway"}), 400

        async def send():
            tc = TelegramClient(StringSession(), API_ID, API_HASH)
            await tc.connect()
            sent = await tc.send_code_request(phone)
            # Save the session + phone_code_hash so verify can reuse them
            temp_clients['session'] = tc.session.save()
            temp_clients['phone_code_hash'] = sent.phone_code_hash
            await tc.disconnect()
            return phone

        result_phone = _run_in_new_loop(send)

        return jsonify({
            "status": "success",
            "message": f"✅ Code sent to {result_phone}",
            "next_step": "Visit: /verify?code=YOUR_CODE"
        }), 200

    except Exception as e:
        logger.error(f"Send code error: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/verify", methods=["GET"])
def verify():
    """Verify code and return session string"""
    try:
        code = request.args.get("code", "")

        if not code:
            return jsonify({"status": "error", "message": "No code provided"}), 400

        if 'session' not in temp_clients:
            return jsonify({"status": "error", "message": "Call /send_code first"}), 400

        async def verify_code():
            tc = TelegramClient(StringSession(temp_clients['session']), API_ID, API_HASH)
            await tc.connect()
            await tc.sign_in(
                VANTAGE_PHONE,
                code,
                phone_code_hash=temp_clients.get('phone_code_hash')
            )
            session = tc.session.save()
            await tc.disconnect()
            return session

        session_string = _run_in_new_loop(verify_code)

        return jsonify({
            "status": "success",
            "session_string": session_string,
            "message": "✅ Copy session_string and update VANTAGE_SESSION_STRING in Railway"
        }), 200

    except Exception as e:
        logger.error(f"Verify error: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500


# ═══════════════════════════════════════════════════════════
# HEALTH CHECK & UTILITIES
# ═══════════════════════════════════════════════════════════

@app.route("/", methods=["GET"])
def health():
    """Health check endpoint"""
    with trade_lock:
        active_count = len([t for t in active_trades.values() if t["status"] == "open"])
    
    mode = "🟢 SAVED MESSAGES (Testing)" if SEND_TO_SAVED else "🔵 VANTAGE GROUP (Live)"
    client_status = "✅ Connected" if client else "❌ Disconnected"
    oanda_status = "✅ Connected" if OANDA_API_KEY else "❌ No API key"
    
    return (
        f"✅ Trade Alert Bot v2 Running!\n"
        f"Mode: {mode}\n"
        f"Active Trades: {active_count}\n"
        f"Client: {client_status}\n"
        f"OANDA: {oanda_status}\n"
    ), 200


@app.route("/reset", methods=["GET"])
def reset():
    """Clear all active trades"""
    with trade_lock:
        count = len(active_trades)
        active_trades.clear()
        reported_levels.clear()
        last_message_time.clear()
    
    return f"✅ Cleared {count} trades!", 200


@app.route("/switch_mode", methods=["GET"])
def switch_mode():
    """Toggle between SAVED MESSAGES and VANTAGE GROUP"""
    global SEND_TO_SAVED
    SEND_TO_SAVED = not SEND_TO_SAVED
    mode = "🟢 SAVED MESSAGES (Testing)" if SEND_TO_SAVED else "🔵 VANTAGE GROUP (Live)"
    return f"✅ Switched to {mode}!", 200


@app.route("/test_entry", methods=["GET"])
def test_entry():
    """Browser test: /test_entry?pair=XAUUSD&direction=BUY&price=4021
    Sends the entry chart + caption to the current destination."""
    try:
        pair = request.args.get("pair", "XAUUSD").upper()
        direction = request.args.get("direction", "BUY").upper()
        price = request.args.get("price", "0")

        arrow = "🟢" if direction == "BUY" else "🔴"
        caption = (
            f"<b>{arrow} {direction} {pair}</b>\n\n"
            f"Entry: {price}\n"
            f"Trade is now live — alerts will follow as it runs into profit."
        )
        chart = get_chart_image(pair)
        fut = asyncio.run_coroutine_threadsafe(
            send_chart_with_caption(chart, caption), loop
        )
        ok = fut.result(timeout=25)

        dest = "Saved Messages" if SEND_TO_SAVED else "VANTAGE GROUP"
        if ok:
            note = "" if chart else " (no chart — check CHART_IMG_KEY)"
            return f"✅ Entry chart sent to {dest}!{note} Check Telegram.", 200
        return "❌ Failed to send. Client may be disconnected.", 500
    except Exception as e:
        logger.error(f"Test entry error: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/test/<level>", methods=["GET"])
def test_message(level):
    """Browser-friendly test: visit /test/20, /test/40, /test/60, /test/80,
    /test/tp1, /test/tp2, /test/tp3, or /test/sl to send a sample message."""
    try:
        level = level.lower()

        mapping = {
            "20": 20,
            "40": 40,
            "80": 80,
            "tp1": 100,
            "150": 150,
            "tp2": "TP2",
            "tp3": "TP3",
            "sl": "SL",
        }

        if level not in mapping:
            return jsonify({
                "status": "error",
                "message": "Use one of: /test/20 /test/40 /test/80 /test/tp1 /test/150 /test/tp2 /test/tp3 /test/sl"
            }), 400

        key = mapping[level]
        text = random.choice(MESSAGE_TEMPLATES[key])

        pair = request.args.get("pair", "XAUUSD").upper()
        chart = get_chart_image(pair)
        future = asyncio.run_coroutine_threadsafe(
            send_chart_with_caption(chart, text), loop
        )
        result = future.result(timeout=25)

        dest = "Saved Messages" if SEND_TO_SAVED else "VANTAGE GROUP"
        if result:
            note = "" if chart else " (no chart — check CHART_IMG_KEY)"
            return f"✅ Test '{level}' sent to {dest}!{note} Check Telegram.", 200
        else:
            return f"❌ Failed to send. Client may be disconnected.", 500

    except Exception as e:
        logger.error(f"Test message error: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/status", methods=["GET"])
def status():
    """Get detailed trade status"""
    with trade_lock:
        trades_info = []
        for trade_id, trade in active_trades.items():
            trades_info.append({
                "trade_id": trade_id,
                "pair": trade["pair"],
                "direction": trade["direction"],
                "entry": trade["entry_price"],
                "reported_levels": list(reported_levels.get(trade_id, [])),
            })
    
    return jsonify({
        "mode": "SAVED_MESSAGES" if SEND_TO_SAVED else "VANTAGE_GROUP",
        "active_trades": len(trades_info),
        "trades": trades_info
    }), 200


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    logger.info(f"🚀 Starting Trade Alert Bot v2 on port {port}")
    app.run(host="0.0.0.0", port=port, debug=False)
