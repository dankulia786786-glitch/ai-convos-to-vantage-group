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

# For testing: send to Saved Messages (user ID = user's own ID)
SEND_TO_SAVED = True  # Set to False to send to Vantage group

VANTAGE_GROUP_ID = int(os.environ.get("VANTAGE_GROUP_ID", "0"))
VANTAGE_TOPIC_ID = int(os.environ.get("VANTAGE_TOPIC_ID", "0"))

CHART_IMG_KEY = os.environ.get("CHART_IMG_KEY", "")
OANDA_API_KEY = os.environ.get("OANDA_API_KEY", "")

# ═══════════════════════════════════════════════════════════
# GLOBAL STATE
# ═══════════════════════════════════════════════════════════

client = None
loop = asyncio.new_event_loop()

# Track open trades
active_trades = {}
trade_lock = threading.Lock()

# Track message cooldowns (30 minutes)
last_message_time = {}
message_cooldown_seconds = 1800

# Price levels already reported
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
        logger.error(f"Client init error: {e}")
    
    return False


future = asyncio.run_coroutine_threadsafe(init_client(), loop)
try:
    future.result(timeout=10)
except:
    pass


# ═══════════════════════════════════════════════════════════
# MESSAGE TEMPLATES (VARIED & HUMAN-LIKE)
# ═══════════════════════════════════════════════════════════

MESSAGE_TEMPLATES = {
    20: [
        "<b>✅✅✅ 20 PIPS IN PROFIT</b>\n\nYou can close now or Move SL to BE",
        "<b>✅✅✅ 20+ PIPS LOCKED IN</b>\n\nTake it or shift SL to break even",
        "<b>✅✅✅ 20 PIPS PROFIT SECURED</b>\n\nClose or let run risk-free",
        "<b>✅✅✅ 20 PIPS DOWN</b>\n\nClose now or move stop loss to entry",
    ],
    40: [
        "<b>✅✅✅ 40 PIPS SMASHED</b>\n\nClose remaining or push to TP2",
        "<b>✅✅✅ 40+ PIPS PROFIT</b>\n\nSecure now or let it run higher",
        "<b>✅✅✅ 40 PIPS LOCKED</b>\n\nClose positions or move SL to entry",
        "<b>✅✅✅ 40 PIPS IN PROFIT</b>\n\nTake gains or ride the momentum",
    ],
    60: [
        "<b>✅✅✅ 60 PIPS IN PROFIT</b>\n\nClose or let it chase TP3",
        "<b>✅✅✅ 60 PIPS SMASHED</b>\n\nSecure profits or stay in",
        "<b>✅✅✅ 60+ PIPS DOWN</b>\n\nClose now or move SL to break even",
        "<b>✅✅✅ 60 PIPS PROFIT</b>\n\nTake it or stay in the game",
    ],
    80: [
        "<b>✅✅✅ 80 PIPS IN PROFIT</b>\n\nClose or let it run to TP3",
        "<b>✅✅✅ 80 PIPS SMASHED</b>\n\nSecure gains or push higher",
        "<b>✅✅✅ 80+ PIPS DOWN</b>\n\nClose remaining or ride momentum",
        "<b>✅✅✅ 80 PIPS PROFIT LOCKED</b>\n\nTake it or stay the course",
    ],
    100: [
        "<b>✅✅✅ TP1 SMASHED 100+ PIPS</b>\n\nMore to come!",
        "<b>✅✅✅ 100 PIPS IN PROFIT</b>\n\nTP1 HIT! Better gains ahead!",
        "<b>✅✅✅ TP1 SMASHED 100 PIPS</b>\n\nStay tuned for TP2!",
        "<b>✅✅✅ 100+ PIPS DOWN</b>\n\nTP1 LOCKED! Momentum building!",
    ],
    "TP2": [
        "<b>✅✅✅ TP2 SMASHED</b>\n\nClose or let final trade run to TP3!",
        "<b>✅✅✅ TP2 HIT</b>\n\nMore profits secured! Targets closing!",
        "<b>✅✅✅ TP2 LOCKED</b>\n\nStay in or take the win!",
    ],
    "TP3": [
        "<b>✅✅✅ TP3 SMASHED</b>\n\nALL TARGETS HIT! 💰 Full profits locked!",
        "<b>✅✅✅ ALL TARGETS HIT</b>\n\nFull win secured! 💰 Well done team!",
        "<b>✅✅✅ TP3 LOCKED</b>\n\nComplete victory! 💰 All targets down!",
    ],
    "SL": [
        "❌ <b>SL TRIGGERED</b>\n\nLooking for the next setup! Let's win on the next one! 💪",
        "❌ <b>STOP LOSS HIT</b>\n\nWe move on! Next opportunity incoming! 🎯",
        "❌ <b>SL CLOSED</b>\n\nBetter luck on the next trade! Stay ready! 🚀",
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
        logger.error(f"OANDA price error: {e}")
    
    return None


# ═══════════════════════════════════════════════════════════
# TELEGRAM MESSAGE SENDING
# ═══════════════════════════════════════════════════════════

async def send_to_telegram(text):
    """Send message to Saved Messages or Vantage group"""
    global client
    
    try:
        if not client or not await client.is_user_authorized():
            logger.error("Client not authorized")
            return False
        
        if SEND_TO_SAVED:
            # Send to Saved Messages (own user ID)
            entity = "me"
        else:
            # Send to Vantage group
            entity = await client.get_entity(VANTAGE_GROUP_ID)
        
        await client.send_message(
            entity,
            text,
            parse_mode='html',
            reply_to=VANTAGE_TOPIC_ID if not SEND_TO_SAVED else None
        )
        
        logger.info(f"✅ Message sent")
        return True
    
    except Exception as e:
        logger.error(f"Send error: {e}")
        return False


def check_cooldown(trade_id, level):
    """Check if cooldown period has passed"""
    key = f"{trade_id}_{level}"
    now = time.time()
    
    if key in last_message_time:
        elapsed = now - last_message_time[key]
        if elapsed < message_cooldown_seconds:
            return False
    
    last_message_time[key] = now
    return True


# ═══════════════════════════════════════════════════════════
# WEBHOOK ENDPOINTS
# ═══════════════════════════════════════════════════════════

@app.route("/webhook", methods=["POST"])
def webhook():
    """Receive entry signals from TradingView"""
    try:
        data = request.get_json(force=True)
        event = data.get("event")
        pair = data.get("pair", "XAUUSD")
        direction = data.get("direction", "BUY").upper()
        price = float(str(data.get("price", "0")).replace(",", ""))
        
        logger.info(f"Webhook: {event} | {pair} | {direction} | {price}")
        
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
            return jsonify({"status": "ok", "trade_id": trade_id})
        
        return jsonify({"status": "ok"})
    
    except Exception as e:
        logger.error(f"Webhook error: {e}")
        return jsonify({"status": "error"}), 500


@app.route("/mt5_close", methods=["POST"])
def mt5_close():
    """Receive close signals from MT5"""
    try:
        data = request.get_json(force=True)
        pair = data.get("pair", "XAUUSD")
        close_type = data.get("close_type", "")
        price = float(data.get("price", 0))
        profit = float(data.get("profit", 0))
        
        logger.info(f"MT5 close: {pair} {close_type} price={price} profit={profit}")
        
        if close_type in ("TP1", "TP2", "TP3"):
            text = random.choice(MESSAGE_TEMPLATES.get(close_type, MESSAGE_TEMPLATES[100]))
        elif close_type == "SL":
            text = random.choice(MESSAGE_TEMPLATES["SL"])
        else:
            return jsonify({"status": "ignored"})
        
        # Send message
        future = asyncio.run_coroutine_threadsafe(send_to_telegram(text), loop)
        try:
            future.result(timeout=15)
        except:
            pass
        
        return jsonify({"status": "ok"})
    
    except Exception as e:
        logger.error(f"mt5_close error: {e}")
        return jsonify({"status": "error"}), 500


# ═══════════════════════════════════════════════════════════
# PROFIT MONITORING (Background Thread)
# ═══════════════════════════════════════════════════════════

def monitor_profits():
    """Monitor open trades and send profit level messages"""
    logger.info("✅ Profit monitor started - monitoring via OANDA")
    
    while True:
        try:
            with trade_lock:
                trades_copy = dict(active_trades)
            
            for trade_id, trade in trades_copy.items():
                if trade["status"] != "open":
                    continue
                
                pair = trade["pair"]
                direction = trade["direction"]
                entry_price = trade["entry_price"]
                
                # Get current price from OANDA
                current_price = get_oanda_price(pair)
                
                if not current_price:
                    logger.warning(f"Could not get price for {pair}")
                    continue
                
                # Calculate pips
                if pair == "XAUUSD":
                    if direction == "BUY":
                        pips = round((current_price - entry_price) * 100)
                    else:
                        pips = round((entry_price - current_price) * 100)
                else:
                    if direction == "BUY":
                        pips = int(current_price - entry_price)
                    else:
                        pips = int(entry_price - current_price)
                
                logger.info(f"Trade {trade_id}: Entry={entry_price}, Current={current_price}, Pips={pips}")
                
                # Check profit levels
                for level_pips in sorted(MESSAGE_TEMPLATES.keys()):
                    if isinstance(level_pips, str):
                        continue
                    
                    if pips >= level_pips and level_pips not in reported_levels.get(trade_id, set()):
                        # Check cooldown
                        if not check_cooldown(trade_id, level_pips):
                            logger.info(f"Cooldown active for {trade_id} level {level_pips}")
                            continue
                        
                        # Get random profit amount for display
                        profit_ranges = {
                            20: (20, 35),
                            40: (50, 70),
                            60: (75, 80),
                            80: (80, 110),
                            100: (120, 160),
                        }
                        
                        profit_range = profit_ranges.get(level_pips, (100, 150))
                        profit_gbp = round(random.uniform(profit_range[0], profit_range[1]), 2)
                        
                        # Get random message template
                        text = random.choice(MESSAGE_TEMPLATES[level_pips])
                        
                        # Send text message only
                        logger.info(f"📤 Sending {level_pips} pips message for {trade_id}")
                        future = asyncio.run_coroutine_threadsafe(
                            send_to_telegram(text),
                            loop
                        )
                        try:
                            future.result(timeout=15)
                            
                            with trade_lock:
                                if trade_id in reported_levels:
                                    reported_levels[trade_id].add(level_pips)
                            
                            logger.info(f"✅ {level_pips} pips message sent!")
                        except Exception as e:
                            logger.error(f"Send failed: {e}")
        
        except Exception as e:
            logger.error(f"Monitor error: {e}")
        
        time.sleep(10)


threading.Thread(target=monitor_profits, daemon=True).start()


# ═══════════════════════════════════════════════════════════
# HEALTH CHECK
# ═══════════════════════════════════════════════════════════

@app.route("/", methods=["GET"])
def health():
    with trade_lock:
        active_count = len([t for t in active_trades.values() if t["status"] == "open"])
    
    mode = "🟢 SAVED MESSAGES (Testing)" if SEND_TO_SAVED else "🔵 VANTAGE GROUP (Live)"
    
    return (
        f"✅ Trade Alert Bot v2 Running!\n"
        f"Mode: {mode}\n"
        f"Active Trades: {active_count}\n"
        f"Client: {'Connected ✅' if client else 'Disconnected ❌'}\n"
        f"OANDA: {'Connected ✅' if OANDA_API_KEY else 'No API key ❌'}\n"
    )


@app.route("/reset", methods=["GET"])
def reset():
    with trade_lock:
        active_trades.clear()
        reported_levels.clear()
    
    return "All trades cleared! ✅"


@app.route("/switch_mode", methods=["GET"])
def switch_mode():
    global SEND_TO_SAVED
    SEND_TO_SAVED = not SEND_TO_SAVED
    mode = "SAVED MESSAGES" if SEND_TO_SAVED else "VANTAGE GROUP"
    return f"Switched to {mode}! ✅"


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
