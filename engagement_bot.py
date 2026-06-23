import os
import asyncio
import threading
import logging
import time
import json
import random
import requests
from datetime import datetime, timedelta
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

VANTAGE_GROUP_ID = int(os.environ.get("VANTAGE_GROUP_ID", "0"))
VANTAGE_TOPIC_ID = int(os.environ.get("VANTAGE_TOPIC_ID", "0"))

TWELVE_DATA_KEY = os.environ.get("TWELVE_DATA_KEY", "")
ENABLE_GROUP_SEND = os.environ.get("ENABLE_GROUP_SEND", "false").lower() == "true"

# Global state
client = None
loop = asyncio.new_event_loop()
engagement_running = False
last_message_time = 0
last_posted_time = 0

MIN_MESSAGE_GAP_BEFORE_POST = 60  # Wait 60 seconds after last message


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
    """Return delay in seconds until next post based on time of day"""
    if is_market_hours():
        # 6 AM - 11 PM: post every 10-15 minutes
        return random.randint(600, 900)
    else:
        # 11 PM - 6 AM: post every 45 minutes
        return random.randint(2400, 2700)


def get_live_prices():
    """Fetch current gold, BTC, and oil prices"""
    try:
        if not TWELVE_DATA_KEY:
            return {
                "gold": 4240.00,
                "btc": 42500.00,
                "oil": 78.50
            }

        prices = {}
        
        # Get Gold
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
            prices["gold"] = 4240.00

        # Get BTC
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
            prices["btc"] = 42500.00

        # Get Oil
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
            prices["oil"] = 78.50

        return prices

    except Exception as e:
        logger.error(f"Error fetching prices: {e}")
        return {
            "gold": 4240.00,
            "btc": 42500.00,
            "oil": 78.50
        }


def format_price(price, asset):
    """Format price based on asset type"""
    if asset == "btc":
        return f"${price:,.0f}"
    elif asset == "gold":
        return f"${price:.2f}"
    elif asset == "oil":
        return f"${price:.2f}"
    return f"{price:.2f}"


def generate_trader_response(messages_context):
    """Generate natural trader response using Claude API"""
    
    # Prepare conversation context
    context_text = "\n".join([f"{m['sender']}: {m['text']}" for m in messages_context[-10:]])
    
    prices = get_live_prices()
    gold_price = format_price(prices["gold"], "gold")
    btc_price = format_price(prices["btc"], "btc")
    oil_price = format_price(prices["oil"], "oil")
    
    uk_time = get_uk_time().strftime("%H:%M UTC")
    
    # Response type options
    response_types = [
        "market_observation",
        "trading_wisdom",
        "risk_management",
        "technical_observation",
        "engagement_question",
        "news_related"
    ]
    
    response_type = random.choice(response_types)
    
    prompts = {
        "market_observation": f"""You're a 21-year-old trader chatting in a 14k member Telegram group. 
        
Recent chat: {context_text}

Current prices: Gold {gold_price}, BTC {btc_price}, Oil {oil_price}

Generate ONE casual but knowledgeable observation about the current market (focus on gold, BTC, or oil).
Sound like a real trader - natural, not professional. Include the live price naturally.
Keep it short (1-2 sentences max).
""",
        
        "trading_wisdom": f"""You're a young trader sharing quick wisdom in a group chat.
        
Generate ONE short trading wisdom or psychology tip (about risk, patience, emotions, entry/exit, etc).
Make it sound natural and casual, like advice from a friend who trades.
Keep it 1-2 sentences.
""",
        
        "risk_management": f"""You're giving casual risk management advice in a chat.
        
Generate ONE tip about position sizing, stop loss, or capital management.
Sound like a 21-year-old who actually cares about not blowing accounts.
Keep it relatable and casual. 1-2 sentences.
""",
        
        "technical_observation": f"""You're making a technical observation about gold, BTC, or oil.

Recent chat: {context_text}
Current prices: Gold {gold_price}, BTC {btc_price}, Oil {oil_price}

Make ONE observation about support/resistance, trend, momentum, or technical setup.
Sound natural, not robotic. Include the price in a casual way.
1-2 sentences.
""",
        
        "engagement_question": f"""You're engaging the group with a question about trading.

Recent chat: {context_text}

Ask ONE question that's relevant to what people are discussing.
Make it thought-provoking but casual. Something that makes people want to reply.
Keep it natural and short (1-2 sentences).
""",
        
        "news_related": f"""You're mentioning market news or economic events.

Recent chat: {context_text}

Make an observation about market news, economic data, or current events (like Fed decisions, inflation, geopolitics, etc).
Sound casual and like you actually follow the news.
1-2 sentences max.
"""
    }
    
    prompt = prompts.get(response_type, prompts["market_observation"])
    
    try:
        response = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "Content-Type": "application/json",
                "x-api-key": os.environ.get("ANTHROPIC_API_KEY", ""),
                "anthropic-version": "2023-06-01"
            },
            json={
                "model": "claude-sonnet-4-6",
                "max_tokens": 150,
                "messages": [
                    {"role": "user", "content": prompt}
                ]
            },
            timeout=15
        )
        
        data = response.json()
        
        if "content" in data and len(data["content"]) > 0:
            message_text = data["content"][0]["text"].strip()
            return message_text
        
    except Exception as e:
        logger.error(f"Claude API error: {e}")
    
    # Fallback messages if API fails
    fallback_messages = [
        f"Gold sitting around {gold_price} rn, consolidating before the next move 🤔",
        f"BTC looking interesting here at {btc_price}, anyone else watching this closely?",
        f"Oil at {oil_price}, sellers or buyers in control? Hard to say without more confirmation",
        "Sometimes the best trade is no trade. Patience > entering everything",
        "If you're not managing risk, you're not trading - you're gambling 🎲",
        f"Gold buyers defending {gold_price}, but is this real or just a bounce?",
        "The chart will tell you what's next, but only if you listen",
        f"BTC momentum looking different today at {btc_price}, anyone else see it?",
    ]
    
    return random.choice(fallback_messages)


async def get_last_messages(limit=10):
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
                    "text": message.text,
                    "timestamp": message.date
                })
        
        # Reverse to get chronological order
        messages.reverse()
        return messages
        
    except Exception as e:
        logger.error(f"Error getting messages: {e}")
        return []


async def send_engagement_message(message_text):
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
        
        kwargs = {}
        if VANTAGE_TOPIC_ID and VANTAGE_TOPIC_ID > 0:
            kwargs["reply_to"] = VANTAGE_TOPIC_ID
        
        await client.send_message(entity, message_text, **kwargs)
        last_posted_time = time.time()
        logger.info("Engagement message sent")
        return True
        
    except Exception as e:
        logger.error(f"Send error: {e}")
        return False


async def engagement_loop():
    """Main engagement loop"""
    global engagement_running, last_message_time, last_posted_time
    
    logger.info("Engagement loop started")
    last_posted_time = time.time()
    
    while engagement_running:
        try:
            # Calculate delay based on market hours
            delay = get_next_post_delay()
            
            uk_time = get_uk_time()
            market_status = "active" if is_market_hours() else "quiet"
            next_post_minutes = delay / 60
            
            logger.info(f"[{uk_time.strftime('%H:%M UTC')}] {market_status} hours. Next post in {next_post_minutes:.0f} mins")
            
            await asyncio.sleep(delay)
            
            if not engagement_running:
                break
            
            # Check if there's been recent chat activity
            messages = await get_last_messages(15)
            
            if not messages:
                logger.info("No messages in chat, skipping")
                continue
            
            # Check if messages are recent (within last 20 mins)
            latest_message_time = messages[-1]["timestamp"]
            minutes_since_last = (datetime.now(ZoneInfo("UTC")) - latest_message_time.replace(tzinfo=ZoneInfo("UTC"))).total_seconds() / 60
            
            if minutes_since_last > 20:
                logger.info(f"No recent activity ({minutes_since_last:.0f} mins ago), skipping")
                continue
            
            # Generate and send response
            logger.info(f"Generating response from {len(messages)} messages")
            response = generate_trader_response(messages)
            
            sent = await send_engagement_message(response)
            
            if sent:
                logger.info(f"Sent: {response[:80]}...")
            
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
        "logged_in": SESSION_STRING != "",
        "engagement_running": engagement_running,
        "group_send_enabled": ENABLE_GROUP_SEND,
        "vantage_group_id": VANTAGE_GROUP_ID,
        "vantage_topic_id": VANTAGE_TOPIC_ID,
        "uk_time": uk_time.strftime("%Y-%m-%d %H:%M:%S %Z"),
        "market_hours": market_hours,
        "post_frequency": "10-15 mins (active)" if market_hours else "45 mins (quiet)",
        "last_posted": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(last_posted_time)) if last_posted_time > 0 else "Never",
        "traders_focus": "Gold, BTC, Oil, Market News",
        "personality": "21-year-old trader - casual, knowledgeable, natural",
        "safe_test_mode": "/test_saved_messages",
        "safe_preview": "/preview_response"
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
    
    return jsonify({"status": "Engagement started"})


@app.route("/stop_engagement", methods=["GET"])
def stop_engagement():
    """Stop the engagement loop"""
    global engagement_running
    
    engagement_running = False
    return jsonify({"status": "Engagement stopped"})


@app.route("/preview_response", methods=["GET"])
def preview_response():
    """Preview a response without posting"""
    future = asyncio.run_coroutine_threadsafe(get_last_messages(10), loop)
    
    try:
        messages = future.result(timeout=20)
        
        if not messages:
            return jsonify({"error": "No messages in chat"}), 400
        
        response = generate_trader_response(messages)
        
        return jsonify({
            "ok": True,
            "response": response,
            "message_count": len(messages),
            "last_message": f"{messages[-1]['sender']}: {messages[-1]['text'][:80]}..."
        })
        
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/test_saved_messages", methods=["GET"])
def test_saved_messages():
    """Send test response to Saved Messages"""
    future = asyncio.run_coroutine_threadsafe(get_last_messages(10), loop)
    
    try:
        messages = future.result(timeout=20)
        
        if not messages:
            return jsonify({"error": "No messages in chat"}), 400
        
        response = generate_trader_response(messages)
        
        # Send to Saved Messages
        async def send_test():
            try:
                if not client or not await client.is_user_authorized():
                    return False
                await client.send_message("me", f"TEST RESPONSE:\n\n{response}")
                return True
            except:
                return False
        
        future2 = asyncio.run_coroutine_threadsafe(send_test(), loop)
        sent = future2.result(timeout=15)
        
        return jsonify({
            "ok": sent,
            "response": response,
            "sent_to": "Saved Messages" if sent else "Failed"
        })
        
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
