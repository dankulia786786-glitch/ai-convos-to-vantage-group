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
TWELVE_DATA_KEY = os.environ.get("TWELVE_DATA_KEY", "")

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
    """Fetch current gold, BTC, and oil prices"""
    try:
        if not TWELVE_DATA_KEY:
            return {
                "gold": round(4200 + random.uniform(-50, 50), 2),
                "btc": round(42000 + random.uniform(-1000, 1000), 0),
                "oil": round(75 + random.uniform(-5, 5), 2)
            }

        prices = {}
        
        try:
            response = requests.get(
                "https://api.twelvedata.com/price",
                params={"symbol": "XAU/USD", "apikey": TWELVE_DATA_KEY},
                timeout=10
            )
            data = response.json()
            if "price" in data:
                prices["gold"] = float(data["price"])
        except:
            prices["gold"] = round(4200 + random.uniform(-50, 50), 2)

        try:
            response = requests.get(
                "https://api.twelvedata.com/price",
                params={"symbol": "BTC/USD", "apikey": TWELVE_DATA_KEY},
                timeout=10
            )
            data = response.json()
            if "price" in data:
                prices["btc"] = float(data["price"])
        except:
            prices["btc"] = round(42000 + random.uniform(-1000, 1000), 0)

        try:
            response = requests.get(
                "https://api.twelvedata.com/price",
                params={"symbol": "WTI/USD", "apikey": TWELVE_DATA_KEY},
                timeout=10
            )
            data = response.json()
            if "price" in data:
                prices["oil"] = float(data["price"])
        except:
            prices["oil"] = round(75 + random.uniform(-5, 5), 2)

        return prices

    except Exception as e:
        logger.error(f"Error fetching prices: {e}")
        return {
            "gold": round(4200 + random.uniform(-50, 50), 2),
            "btc": round(42000 + random.uniform(-1000, 1000), 0),
            "oil": round(75 + random.uniform(-5, 5), 2)
        }


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
        
        # Reverse to get chronological order
        messages.reverse()
        return messages
        
    except Exception as e:
        logger.error(f"Error getting messages: {e}")
        return []


def generate_contextual_response(messages_context, prices):
    """Generate contextual response using Claude"""
    
    if not messages_context:
        return None
    
    # Format context
    context_text = "\n".join([f"{m['sender']}: {m['text']}" for m in messages_context[-5:]])
    
    gold_price = prices["gold"]
    btc_price = int(prices["btc"])
    oil_price = prices["oil"]
    
    prompt = f"""You are a 21-year-old trader in a Telegram group chat with 14,000 people. 

Recent chat:
{context_text}

Current prices: Gold ${gold_price}, BTC ${btc_price:,}, Oil ${oil_price}

Generate ONE natural, conversational response (1-2 sentences MAX) that:
- Flows naturally into this discussion (don't just post random stuff)
- References what people just said (agree, challenge, or add to it)
- Sometimes includes the live price naturally (don't just announce it)
- Sounds like a real trader, casual and knowledgeable
- NO emojis or excessive punctuation
- NO "ALERT" or "UPDATE" language
- Just a normal take from someone in the group

Examples of GOOD responses:
- "Yeah gold consolidating like that, buyers still defending that level"
- "BTC at 42k, feels like something's about to break if volume comes in"
- "Trump tweeting probably moving this more than technicals rn"
- "Anyone else seeing the same resistance or is it just me?"

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
            # Limit to 2 sentences
            sentences = message_text.split(".")
            if len(sentences) > 2:
                message_text = ".".join(sentences[:2]) + "."
            return message_text
        
    except Exception as e:
        logger.error(f"Claude API error: {e}")
    
    return None


def generate_fallback_response(prices):
    """Generate fallback response if reading chat fails"""
    
    gold_price = prices["gold"]
    btc_price = int(prices["btc"])
    oil_price = prices["oil"]
    
    fallback_messages = [
        f"Gold stuck around ${gold_price}, consolidating or reversing?",
        f"BTC at ${btc_price:,}, buyers stepping in or is this just a bounce?",
        f"Oil at ${oil_price}, sellers still in control here",
        "Patience over everything. Sometimes the best move is no move",
        "Risk management > big wins. Protect the account always",
        "What's everyone's take on this setup? Bullish or bearish?",
        f"Gold ${gold_price} is key level, watch if it holds",
        "Market's testing patience today but that's when real trades happen",
        "Consolidation builds up for the next move. Stay ready",
        "Anyone else seeing the same thing I'm seeing rn?",
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
        
        # Post directly to group
        await client.send_message(entity, message_text)
        last_posted_time = time.time()
        logger.info(f"✅ Sent: {message_text[:60]}...")
        return True
        
    except Exception as e:
        logger.error(f"Send error: {e}")
        logger.error(f"Full error: {str(e)}")
        return False


async def engagement_loop():
    """Main engagement loop"""
    global engagement_running, last_posted_time
    
    logger.info("🚀 Engagement loop started - Smart conversational mode")
    last_posted_time = time.time()
    
    while engagement_running:
        try:
            # Get random delay (5-15 mins)
            delay = get_next_post_delay()
            next_post_minutes = delay / 60
            
            uk_time = get_uk_time()
            logger.info(f"[{uk_time.strftime('%H:%M UTC')}] Next post in {next_post_minutes:.1f} mins")
            
            await asyncio.sleep(delay)
            
            if not engagement_running:
                break
            
            # Get live prices
            prices = get_live_prices()
            logger.info(f"Prices: Gold ${prices['gold']}, BTC ${prices['btc']:,}, Oil ${prices['oil']}")
            
            # Try to read chat and generate contextual response
            messages = await get_last_messages(5)
            
            if messages and len(messages) > 0:
                logger.info(f"Read {len(messages)} messages from chat")
                response = await asyncio.get_event_loop().run_in_executor(
                    None, 
                    lambda: generate_contextual_response(messages, prices)
                )
                
                if not response:
                    logger.warning("Claude returned None, using fallback")
                    response = generate_fallback_response(prices)
            else:
                logger.warning("No messages to read, using fallback")
                response = generate_fallback_response(prices)
            
            # Send the response
            sent = await send_to_vantage(response)
            
            if sent:
                logger.info(f"✨ Posted successfully!")
            else:
                logger.warning("Failed to send message")
            
        except Exception as e:
            logger.error(f"Loop error: {e}")
            await asyncio.sleep(60)
    
    logger.info("Engagement loop stopped")


@app.route("/", methods=["GET"])
def health():
    """Health check"""
    uk_time = get_uk_time()
    market_hours = is_market_hours()
    
    return jsonify({
        "status": "AI Trader Engagement Bot Running",
        "mode": "SMART CONVERSATIONAL",
        "logged_in": SESSION_STRING != "",
        "engagement_running": engagement_running,
        "group_send_enabled": ENABLE_GROUP_SEND,
        "vantage_group_id": VANTAGE_GROUP_ID,
        "vantage_topic_id": VANTAGE_TOPIC_ID,
        "uk_time": uk_time.strftime("%Y-%m-%d %H:%M:%S %Z"),
        "market_hours": market_hours,
        "post_frequency": "5-15 mins random (smart timing)",
        "last_posted": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(last_posted_time)) if last_posted_time > 0 else "Never",
        "how_it_works": "Reads last 5 messages from chat, uses Claude to generate contextual response that flows naturally into conversation",
        "response_length": "1-2 sentences conversational",
        "tone": "21-year-old trader - casual, knowledgeable, natural"
    })


@app.route("/start_engagement", methods=["GET"])
def start_engagement():
    """Start the engagement loop"""
    global engagement_running
    
    if engagement_running:
        return jsonify({"status": "Already running"})
    
    if not ENABLE_GROUP_SEND:
        return jsonify({
            "status": "blocked",
            "reason": "ENABLE_GROUP_SEND is false"
        }), 403
    
    engagement_running = True
    asyncio.run_coroutine_threadsafe(engagement_loop(), loop)
    
    return jsonify({"status": "Engagement started", "mode": "SMART CONVERSATIONAL"})


@app.route("/stop_engagement", methods=["GET"])
def stop_engagement():
    """Stop the engagement loop"""
    global engagement_running
    
    engagement_running = False
    return jsonify({"status": "Engagement stopped"})


@app.route("/test_post_now", methods=["GET"])
def test_post_now():
    """Manually trigger a post RIGHT NOW for testing"""
    
    async def _test():
        if not ENABLE_GROUP_SEND:
            return {"error": "ENABLE_GROUP_SEND is false"}
        
        # Get live prices
        prices = get_live_prices()
        
        # Try to read chat
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
        
        # Send it
        sent = await send_to_vantage(response)
        
        return {
            "status": "success" if sent else "failed",
            "message_sent": response,
            "prices": prices
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
        return jsonify({
            "status": "Code sent to " + PHONE,
            "next": "Call /verify?code=XXXXX with the code you received"
        })
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
        return jsonify({
            "status": "Logged in!",
            "SESSION_STRING": session_string,
            "next": "Add SESSION_STRING to Railway Variables"
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
