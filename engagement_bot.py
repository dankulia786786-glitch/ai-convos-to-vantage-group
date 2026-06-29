import os
import asyncio
import threading
import logging
import time
import json
import random
from datetime import datetime
from io import BytesIO
from PIL import Image, ImageDraw, ImageFont
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
VANTAGE_GROUP_ID = int(os.environ.get("VANTAGE_GROUP_ID", "0"))
VANTAGE_TOPIC_ID = int(os.environ.get("VANTAGE_TOPIC_ID", "0"))

CHART_IMG_KEY = os.environ.get("CHART_IMG_KEY", "")
TWELVE_DATA_KEY = os.environ.get("TWELVE_DATA_KEY", "")

# ═══════════════════════════════════════════════════════════
# GLOBAL STATE
# ═══════════════════════════════════════════════════════════

client = None
loop = asyncio.new_event_loop()

# Track open trades
active_trades = {}
trade_lock = threading.Lock()

# Track message cooldowns (prevent spam)
last_message_time = {}
message_cooldown_seconds = 1800  # 30 minutes

# Price levels already reported for each trade
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


# Initialize client on startup
future = asyncio.run_coroutine_threadsafe(init_client(), loop)
future.result(timeout=10)


# ═══════════════════════════════════════════════════════════
# PROFIT RANGES & EXPLANATIONS
# ═══════════════════════════════════════════════════════════

PROFIT_LEVELS = {
    20: {"£_range": (20, 35), "pips": 20},
    40: {"£_range": (50, 70), "pips": 40},
    60: {"£_range": (75, 80), "pips": 60},
    80: {"£_range": (80, 110), "pips": 80},
    100: {"£_range": (120, 160), "pips": 100, "label": "TP1 SMASHED ✅✅✅"},
}

EXPLANATIONS = {
    "BUY_ENTRY": [
        "Bullish momentum confirmed. Price above key support level.",
        "Buyers stepping in at demand zone. Upside targets identified.",
        "Break above resistance structure. Smart money long from here.",
    ],
    "SELL_ENTRY": [
        "Bearish structure intact. Rejection from supply zone confirmed.",
        "Sellers in control. Price below key resistance level.",
        "Liquidity sweep complete. Smart money short from here.",
    ],
}


# ═══════════════════════════════════════════════════════════
# CHART & IMAGE GENERATION
# ═══════════════════════════════════════════════════════════

def get_chart_image(pair):
    """Fetch trading view chart"""
    if not CHART_IMG_KEY:
        return None
    
    try:
        symbol = "OANDA:XAUUSD" if pair == "XAUUSD" else "COINBASE:BTCUSD"
        url = (
            f"https://api.chart-img.com/v1/tradingview/advanced-chart"
            f"?symbol={symbol}&interval=5m&theme=dark"
            f"&studies=MASimple@tv-basicstudies,RSI@tv-basicstudies"
            f"&key={CHART_IMG_KEY}"
        )
        
        r = requests.get(url, timeout=10)
        if r.status_code == 200 and "image" in r.headers.get("content-type", ""):
            return r.content
    except Exception as e:
        logger.error(f"Chart image error: {e}")
    
    return None


def find_font(bold=True, size=54):
    """Find available font"""
    candidates = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    ]
    
    for path in candidates:
        try:
            if os.path.exists(path):
                return ImageFont.truetype(path, size)
        except:
            pass
    
    return ImageFont.load_default()


def generate_profit_overlay(pair, pips, profit_gbp, chart_bytes=None):
    """Generate profit card image"""
    try:
        if chart_bytes:
            chart_img = Image.open(BytesIO(chart_bytes)).convert("RGB")
        else:
            chart_img = Image.new("RGB", (800, 400), (10, 10, 15))
        
        CW, CH = chart_img.size
        band_h = int(CH * 0.22)
        band = Image.new("RGB", (CW, band_h), (8, 8, 12))
        draw = ImageDraw.Draw(band)
        
        draw.rectangle([0, 0, CW, 5], fill=(212, 175, 55))
        draw.rectangle([0, band_h - 5, CW, band_h], fill=(212, 175, 55))
        
        font_profit = find_font(bold=True, size=int(band_h * 0.52))
        font_label = find_font(bold=True, size=int(band_h * 0.22))
        font_small = find_font(bold=True, size=int(band_h * 0.17))
        
        label = f"{pips} PIPS IN PROFIT 📈"
        profit_str = f"+£{profit_gbp:,.2f}"
        detail_str = f"0.11 Lots  |  +{pips} PIPS"
        
        bbox = draw.textbbox((0, 0), label, font=font_label)
        tw = bbox[2] - bbox[0]
        draw.text(((CW - tw) // 2, 8), label, font=font_label, fill=(255, 255, 255))
        
        bbox = draw.textbbox((0, 0), profit_str, font=font_profit)
        tw = bbox[2] - bbox[0]
        py = int(band_h * 0.28)
        draw.text(((CW - tw) // 2 + 3, py + 3), profit_str, font=font_profit, fill=(0, 60, 0))
        draw.text(((CW - tw) // 2, py), profit_str, font=font_profit, fill=(0, 230, 80))
        
        bbox = draw.textbbox((0, 0), detail_str, font=font_small)
        tw = bbox[2] - bbox[0]
        draw.text(((CW - tw) // 2, band_h - int(band_h * 0.22)), detail_str, font=font_small, fill=(212, 175, 55))
        
        combined = Image.new("RGB", (CW, CH + band_h))
        combined.paste(chart_img, (0, 0))
        combined.paste(band, (0, CH))
        
        buf = BytesIO()
        combined.save(buf, format="JPEG", quality=92)
        buf.seek(0)
        return buf.read()
    
    except Exception as e:
        logger.error(f"Profit overlay error: {e}")
        return chart_bytes


# ═══════════════════════════════════════════════════════════
# TELEGRAM MESSAGE SENDING
# ═══════════════════════════════════════════════════════════

async def send_to_vantage(text, image_bytes=None):
    """Send message to Vantage group"""
    global client
    
    try:
        if not client or not await client.is_user_authorized():
            logger.error("Client not authorized")
            return False
        
        entity = await client.get_entity(VANTAGE_GROUP_ID)
        
        if image_bytes:
            await client.send_file(
                entity,
                image_bytes,
                caption=text,
                parse_mode='html',
                reply_to=VANTAGE_TOPIC_ID
            )
        else:
            await client.send_message(
                entity,
                text,
                parse_mode='html',
                reply_to=VANTAGE_TOPIC_ID
            )
        
        logger.info(f"✅ Message sent to Vantage group")
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
            
            logger.info(f"Trade opened: {trade_id}")
            return jsonify({"status": "ok"})
        
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
        
        if close_type == "TP1":
            text = (
                "<b>TP1 SMASHED ✅✅✅</b>\n\n"
                "☑️ Close your positions now and secure your profits\n\n"
                "Or\n\n"
                "☑️ Move your SL to Break Even and let the trade run risk free"
            )
            
            # Send message
            future = asyncio.run_coroutine_threadsafe(send_to_vantage(text), loop)
            future.result(timeout=15)
        
        elif close_type == "TP2":
            text = (
                "<b>TP2 SMASHED ✅✅✅✅</b>\n\n"
                "☑️ Close remaining positions and secure your profits\n\n"
                "Or\n\n"
                "☑️ Let the remaining trade run risk free to TP3"
            )
            future = asyncio.run_coroutine_threadsafe(send_to_vantage(text), loop)
            future.result(timeout=15)
        
        elif close_type == "TP3":
            text = (
                "<b>TP3 SMASHED ✅✅✅✅✅</b>\n\n"
                "☑️ ALL TARGETS HIT!\n\n"
                "💰 Full profits secured.\n\n"
                "👏 Well done team!"
            )
            future = asyncio.run_coroutine_threadsafe(send_to_vantage(text), loop)
            future.result(timeout=15)
        
        elif close_type == "SL":
            text = "❌ SL Triggered Team ❌\nLooking for the next Set-Up. Lets win on the Next one!"
            future = asyncio.run_coroutine_threadsafe(send_to_vantage(text), loop)
            future.result(timeout=15)
        
        return jsonify({"status": "ok"})
    
    except Exception as e:
        logger.error(f"mt5_close error: {e}")
        return jsonify({"status": "error"}), 500


# ═══════════════════════════════════════════════════════════
# PROFIT MONITORING (Background Thread)
# ═══════════════════════════════════════════════════════════

def monitor_profits():
    """Monitor open trades and send profit level messages"""
    logger.info("Profit monitor started")
    
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
                
                # Get current price
                if pair == "XAUUSD":
                    current_price = get_gold_price()
                else:
                    current_price = get_btc_price()
                
                if not current_price:
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
                
                # Check profit levels
                for level_pips in sorted(PROFIT_LEVELS.keys()):
                    if pips >= level_pips and level_pips not in reported_levels.get(trade_id, set()):
                        # Check cooldown
                        if not check_cooldown(trade_id, level_pips):
                            continue
                        
                        # Get profit amount
                        £_range = PROFIT_LEVELS[level_pips]["£_range"]
                        profit_gbp = round(random.uniform(£_range[0], £_range[1]), 2)
                        
                        # Generate message
                        label = PROFIT_LEVELS[level_pips].get("label", f"{level_pips} PIPS IN PROFIT 📈")
                        explanation = random.choice(EXPLANATIONS["BUY_ENTRY" if direction == "BUY" else "SELL_ENTRY"])
                        
                        text = (
                            f"<b>{label}</b>\n\n"
                            f"+£{profit_gbp:,.2f}\n\n"
                            f"0.11 Lots | +{level_pips} PIPS\n\n"
                            f"💡 {explanation}"
                        )
                        
                        # Get chart
                        chart = get_chart_image(pair)
                        image_bytes = None
                        if chart:
                            image_bytes = generate_profit_overlay(pair, level_pips, profit_gbp, chart)
                        
                        # Send
                        future = asyncio.run_coroutine_threadsafe(
                            send_to_vantage(text, image_bytes),
                            loop
                        )
                        try:
                            future.result(timeout=15)
                            
                            with trade_lock:
                                if trade_id in reported_levels:
                                    reported_levels[trade_id].add(level_pips)
                        except:
                            pass
        
        except Exception as e:
            logger.error(f"Monitor error: {e}")
        
        time.sleep(10)


threading.Thread(target=monitor_profits, daemon=True).start()


# ═══════════════════════════════════════════════════════════
# PRICE FETCHING
# ═══════════════════════════════════════════════════════════

def get_gold_price():
    try:
        if TWELVE_DATA_KEY:
            r = requests.get(f"https://api.twelvedata.com/price?symbol=XAU/USD&apikey={TWELVE_DATA_KEY}", timeout=5)
            if r.status_code == 200:
                p = float(r.json().get("price", 0))
                if p > 3000:
                    return p
    except:
        pass
    
    try:
        r = requests.get("https://api.metals.live/v1/spot/gold", timeout=6)
        if r.status_code == 200:
            data = r.json()
            if isinstance(data, list) and data:
                p = float(data[0].get("gold", 0))
                if p > 3000:
                    return p
    except:
        pass
    
    return None


def get_btc_price():
    try:
        r = requests.get("https://api.binance.com/api/v3/ticker/price?symbol=BTCUSDT", timeout=5)
        if r.status_code == 200:
            p = float(r.json()["price"])
            if p > 0:
                return p
    except:
        pass
    
    return None


# ═══════════════════════════════════════════════════════════
# HEALTH CHECK
# ═══════════════════════════════════════════════════════════

@app.route("/", methods=["GET"])
def health():
    with trade_lock:
        active_count = len([t for t in active_trades.values() if t["status"] == "open"])
    
    return (
        f"✅ Trade Alert Bot Running!\n"
        f"Vantage Group: {VANTAGE_GROUP_ID}\n"
        f"Active Trades: {active_count}\n"
        f"Client: {'Connected' if client else 'Disconnected'}\n"
    )


@app.route("/reset", methods=["GET"])
def reset():
    with trade_lock:
        active_trades.clear()
        reported_levels.clear()
    
    return "All trades cleared! ✅"


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
