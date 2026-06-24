import os
import asyncio
import threading
import logging
import time
import requests
import random
from datetime import datetime
from zoneinfo import ZoneInfo
from flask import Flask, request, jsonify
from telethon import TelegramClient
from telethon.sessions import StringSession

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)

# Environment Variables
API_ID = int(os.environ.get("API_ID", "0"))
API_HASH = os.environ.get("API_HASH", "")
PHONE = os.environ.get("PHONE", "")
SESSION_STRING = os.environ.get("SESSION_STRING", "")

VANTAGE_GROUP_ID = os.environ.get("VANTAGE_GROUP_ID", "")
VANTAGE_TOPIC_ID = int(os.environ.get("VANTAGE_TOPIC_ID", "0"))

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
ALPHA_VANTAGE_KEY = os.environ.get("ALPHA_VANTAGE_KEY", "")

ENABLE_GROUP_SEND = os.environ.get("ENABLE_GROUP_SEND", "false").lower() == "true"

# Global state
client = None
loop = asyncio.new_event_loop()
engagement_running = False
last_posted_time = 0


def run_loop():
    asyncio.set_event_loop(loop)
    loop.run_forever()


threading.Thread(target=run_loop, daemon=True).start()


async def init_client():
    global client

    if not API_ID or not API_HASH:
        logger.error("API_ID or API_HASH missing")
        return False

    if SESSION_STRING:
        client = TelegramClient(StringSession(SESSION_STRING), API_ID, API_HASH)
        await client.connect()

        if await client.is_user_authorized():
            logger.info("Logged in via session string")
            return True

        logger.error("Session string invalid")
        return False

    client = TelegramClient(StringSession(), API_ID, API_HASH)
    await client.connect()
    logger.info("No session string. Login via /send_code")
    return False


future = asyncio.run_coroutine_threadsafe(init_client(), loop)
try:
    future.result(timeout=30)
except Exception as e:
    logger.error(f"Init error: {e}")


def get_uk_time():
    """Get current time in UK timezone"""
    return datetime.now(ZoneInfo("Europe/London"))


def is_market_hours():
    """Check if we're in active market hours (6 AM - 11 PM UK time)"""
    uk_time = get_uk_time()
    hour = uk_time.hour
    return 6 <= hour < 23


def get_next_post_delay():
    """Return random delay 5-15 minutes with variation"""
    return random.randint(300, 900)  # 5-15 minutes


def get_live_prices():
    """Fetch REAL LIVE prices from Gold-API - BTC & Gold only. NO FALLBACK!"""
    prices = {}
    
    # Get REAL Gold from Gold-API (XAU = Gold)
    try:
        response = requests.get(
            "https://api.gold-api.com/price/XAU/USD",
            timeout=5
        )
        data = response.json()
        if "price" in data:
            prices["gold"] = float(data["price"])
            logger.info(f"✅ Real Gold (XAU): ${prices['gold']}")
        else:
            logger.warning("Gold API: No price in response")
            prices["gold"] = None
    except Exception as e:
        logger.error(f"❌ Gold API failed: {e}")
        prices["gold"] = None
    
    # Get REAL BTC from Gold-API
    try:
        response = requests.get(
            "https://api.gold-api.com/price/BTC/USD",
            timeout=5
        )
        data = response.json()
        if "price" in data:
            prices["btc"] = float(data["price"])
            logger.info(f"✅ Real BTC: ${prices['btc']:,.0f}")
        else:
            logger.warning("BTC API: No price in response")
            prices["btc"] = None
    except Exception as e:
        logger.error(f"❌ BTC API failed: {e}")
        prices["btc"] = None
    
    return prices


async def get_last_messages(limit=5):
    """Get last N messages from group"""
    global client
    
    try:
        if not client or not await client.is_user_authorized():
            logger.error("Not logged in")
            return []
        
        entity = await client.get_entity(VANTAGE_GROUP_ID)
        messages = []
        
        async for message in client.iter_messages(entity, limit=limit):
            if message.text:
                sender = "Unknown"
                if message.sender:
                    try:
                        user = await client.get_entity(message.sender)
                        sender = user.first_name or "Unknown"
                    except:
                        sender = "Unknown"
                
                messages.append({
                    "sender": sender,
                    "text": message.text
                })
        
        messages.reverse()
        return messages
        
    except Exception as e:
        logger.error(f"Error getting messages: {e}")
        return []


def generate_contextual_response(messages_context, prices):
    """Generate contextual response using Claude with BTC & Gold only"""
    
    if not messages_context:
        return None
    
    context_text = "\n".join([f"{m['sender']}: {m['text']}" for m in messages_context[-5:]])
    
    gold_price = prices["gold"]
    btc_price = int(prices["btc"])
    
    prompt = f"""You are a 21-year-old trader in a Telegram group chat with 14,000 people. 

Recent chat:
{context_text}

REAL LIVE prices RIGHT NOW:
- Gold: ${gold_price}
- BTC: ${btc_price:,}

Generate ONE natural, conversational response (1-2 sentences MAX) that:
- Flows naturally into this discussion
- References what people just said
- Use the ACTUAL LIVE PRICES naturally in your response
- Sounds like a real trader
- NO emojis or excessive punctuation
- NO "ALERT" or "UPDATE" language

Examples:
- "Gold at ${gold_price} rn, buyers defending or consolidating?"
- "BTC ${btc_price:,}, feeling like we're building support here"
- "Anyone else seeing gold ${gold_price} as key?"

Generate ONLY the response text, nothing else."""

    try:
        response = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "Content-Type": "application/json",
                "x-api-key": ANTHROPIC_API_KEY,
                "anthropic-version": "2023-06-01"
            },
            json={
                "model": "claude-sonnet-4-6",
                "max_tokens": 100,
                "messages": [
                    {"role": "user", "content": prompt}
                ]
            },
            timeout=15
        )
        
        data = response.json()
        
        if "content" in data and len(data["content"]) > 0:
            message_text = data["content"][0]["text"].strip()
            sentences = message_text.split(".")
            if len(sentences) > 2:
                message_text = ".".join(sentences[:2]) + "."
            return message_text
        
    except Exception as e:
        logger.error(f"Claude API error: {e}")
    
    return None


def generate_fallback_response(prices):
    """Generate fallback with BTC & Gold prices only"""
    
    gold_price = prices["gold"]
    btc_price = int(prices["btc"])
    
    fallback_messages = [
        f"Gold at ${gold_price}, consolidating or reversing?",
        f"BTC ${btc_price:,}, buyers stepping in or bounce?",
        "Patience over everything. Sometimes best move is no move",
        "Risk management > big wins. Protect the account",
        f"Gold ${gold_price} is key level, watch if it holds",
        "Market testing patience but that's when trades happen",
        "Consolidation builds for next move. Stay ready",
        f"BTC at ${btc_price:,}, feeling support building here",
        f"Gold defending ${gold_price}, what's next?",
    ]
    
    return random.choice(fallback_messages)


async def send_to_vantage(message_text):
    """Send message to Vantage group"""
    global client, last_posted_time
    
    try:
        if not ENABLE_GROUP_SEND:
            logger.warning("Group send locked")
            return False
        
        if not client or not await client.is_user_authorized():
            logger.error("Not logged in")
            return False
        
        if not VANTAGE_GROUP_ID:
            logger.error("VANTAGE_GROUP_ID missing")
            return False
        
        entity = await client.get_entity(VANTAGE_GROUP_ID)
        
        await client.send_message(entity, message_text)
        last_posted_time = time.time()
        logger.info(f"✅ SENT: {message_text[:60]}...")
        return True
        
    except Exception as e:
        logger.error(f"Send error: {e}")
        logger.error(f"Full error: {str(e)}")
        return False


async def engagement_loop():
    """Main engagement loop"""
    global engagement_running, last_posted_time
    
    logger.info("🚀 Engagement loop started - REAL LIVE PRICES MODE")
    last_posted_time = time.time()
    
    while engagement_running:
        try:
            delay = get_next_post_delay()
            next_post_minutes = delay / 60
            
            uk_time = get_uk_time()
            logger.info(f"[{uk_time.strftime('%H:%M UTC')}] Next post in {next_post_minutes:.1f} mins")
            
            await asyncio.sleep(delay)
            
            if not engagement_running:
                break
            
            # Get REAL LIVE prices only
            prices = get_live_prices()
            
            # Only post if we have REAL prices (no fallbacks!)
            if prices["btc"] is None or prices["gold"] is None:
                logger.warning("❌ Missing real prices - skipping this post cycle")
                continue
            
            logger.info(f"📊 REAL Prices: BTC ${prices['btc']:,.0f}, Gold ${prices['gold']}")
            
            messages = await get_last_messages(5)
            
            if messages and len(messages) > 0:
                logger.info(f"Read {len(messages)} messages from chat")
                response = await asyncio.get_event_loop().run_in_executor(
                    None, 
                    lambda: generate_contextual_response(messages, prices)
                )
                
                if not response:
                    response = generate_fallback_response(prices)
            else:
                response = generate_fallback_response(prices)
            
            sent = await send_to_vantage(response)
            
            if sent:
                logger.info(f"✨ Posted successfully!")
            else:
                logger.warning("Failed to send message")
            
        except Exception as e:
            logger.error(f"Loop error: {e}")
            await asyncio.sleep(60)
    
    logger.info("Engagement loop stopped")


@app.route("/test_prices", methods=["GET"])
def test_prices():
    """Test BTC & Gold prices from Gold-API"""
    
    results = {
        "btc": {"status": "testing", "price": None},
        "gold": {"status": "testing", "price": None}
    }
    
    # Test GOLD (XAU) from Gold-API
    try:
        response = requests.get(
            "https://api.gold-api.com/price/XAU/USD",
            timeout=5
        )
        data = response.json()
        if "price" in data:
            results["gold"]["price"] = float(data["price"])
            results["gold"]["status"] = "✅ SUCCESS"
    except Exception as e:
        results["gold"]["status"] = f"❌ Error"
    
    # Test BTC from Gold-API
    try:
        response = requests.get(
            "https://api.gold-api.com/price/BTC/USD",
            timeout=5
        )
        data = response.json()
        if "price" in data:
            results["btc"]["price"] = float(data["price"])
            results["btc"]["status"] = "✅ SUCCESS"
    except Exception as e:
        results["btc"]["status"] = f"❌ Error"
    
    return jsonify({
        "test": "LIVE PRICES from Gold-API",
        "timestamp": get_uk_time().strftime("%Y-%m-%d %H:%M:%S %Z"),
        "results": results,
        "all_working": all(r["status"].startswith("✅") for r in results.values()),
        "source": "Gold-API (Free, No Auth, No Limits!)"
    })


@app.route("/", methods=["GET"])
def health():
    """Health check"""
    uk_time = get_uk_time()
    prices = get_live_prices()
    
    return jsonify({
        "status": "AI Trader Engagement Bot Running",
        "mode": "REAL LIVE PRICES",
        "logged_in": SESSION_STRING != "",
        "engagement_running": engagement_running,
        "group_send_enabled": ENABLE_GROUP_SEND,
        "vantage_group_id": VANTAGE_GROUP_ID,
        "uk_time": uk_time.strftime("%Y-%m-%d %H:%M:%S %Z"),
        "live_prices": {
            "gold": f"${prices['gold']}",
            "btc": f"${prices['btc']:,.0f}",
            "oil": f"${prices['oil']}"
        },
        "test_endpoint": "/test_prices",
        "post_frequency": "5-15 mins random",
        "tone": "21-year-old trader - casual, knowledgeable"
    })


@app.route("/start_engagement", methods=["GET"])
def start_engagement():
    """Start the engagement loop"""
    global engagement_running
    
    if engagement_running:
        return jsonify({"status": "Already running"})
    
    if not ENABLE_GROUP_SEND:
        return jsonify({"status": "blocked", "reason": "ENABLE_GROUP_SEND is false"}), 403
    
    engagement_running = True
    asyncio.run_coroutine_threadsafe(engagement_loop(), loop)
    
    return jsonify({"status": "Engagement started", "mode": "REAL LIVE PRICES"})


@app.route("/stop_engagement", methods=["GET"])
def stop_engagement():
    """Stop the engagement loop"""
    global engagement_running
    
    engagement_running = False
    return jsonify({"status": "Engagement stopped"})


@app.route("/test_post_now", methods=["GET"])
def test_post_now():
    """Manually trigger a post with REAL prices"""
    
    async def _test():
        if not ENABLE_GROUP_SEND:
            return {"error": "ENABLE_GROUP_SEND is false"}
        
        prices = get_live_prices()
        messages = await get_last_messages(5)
        
        if messages and len(messages) > 0:
            response = await asyncio.get_event_loop().run_in_executor(
                None, 
                lambda: generate_contextual_response(messages, prices)
            )
            
            if not response:
                response = generate_fallback_response(prices)
        else:
            response = generate_fallback_response(prices)
        
        sent = await send_to_vantage(response)
        
        return {
            "status": "success" if sent else "failed",
            "message_sent": response,
            "real_live_prices": {
                "gold": f"${prices['gold']}",
                "btc": f"${prices['btc']:,.0f}",
                "oil": f"${prices['oil']}"
            }
        }
    
    try:
        future = asyncio.run_coroutine_threadsafe(_test(), loop)
        result = future.result(timeout=30)
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/send_code", methods=["GET"])
def send_code():
    """Send login code to phone"""
    global client
    
    if not PHONE:
        return jsonify({"error": "PHONE not set"}), 400
    
    async def _send():
        await client.send_code_request(PHONE)
        return True
    
    try:
        future = asyncio.run_coroutine_threadsafe(_send(), loop)
        future.result(timeout=15)
        return jsonify({"status": "Code sent to " + PHONE})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/verify", methods=["GET"])
def verify():
    """Verify login code"""
    global client
    
    code = request.args.get("code", "")
    
    if not code:
        return jsonify({"error": "Provide ?code=XXXXX"}), 400
    
    async def _verify():
        await client.sign_in(PHONE, code)
        return client.session.save()
    
    try:
        future = asyncio.run_coroutine_threadsafe(_verify(), loop)
        session_string = future.result(timeout=15)
        return jsonify({"status": "Logged in!", "SESSION_STRING": session_string})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
