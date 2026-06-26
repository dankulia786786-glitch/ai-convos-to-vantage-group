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

USER_CHANNEL_ID = -1004447151625  # Your FREE GOLD & BTC SIGNALS channel

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
ALPHA_VANTAGE_KEY = os.environ.get("ALPHA_VANTAGE_KEY", "")

ENABLE_GROUP_SEND = os.environ.get("ENABLE_GROUP_SEND", "false").lower() == "true"

# Global state
client = None
loop = asyncio.new_event_loop()
engagement_running = False
last_posted_time = 0
last_promo_time = 0


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
    return random.randint(300, 900)


def get_live_prices():
    """Fetch REAL LIVE prices - BTC & Gold. Try Gold-API first, then Yahoo fallback"""
    prices = {}
    
    # Get REAL Gold from Gold-API (primary)
    try:
        response = requests.get(
            "https://api.gold-api.com/price/XAU/USD",
            timeout=5
        )
        data = response.json()
        if "price" in data:
            prices["gold"] = float(data["price"])
            logger.info(f"✅ Gold from Gold-API: ${prices['gold']}")
    except Exception as e:
        logger.warning(f"Gold-API failed: {e}, trying Yahoo...")
        prices["gold"] = None
    
    # Fallback: Get Gold from Yahoo Finance
    if prices.get("gold") is None:
        try:
            response = requests.get(
                "https://query1.finance.yahoo.com/v8/finance/chart/GC=F",
                params={"interval": "1h", "range": "30d"},
                headers={"User-Agent": "Mozilla/5.0"},
                timeout=10
            )
            data = response.json()
            closes = data.get("chart", {}).get("result", [{}])[0].get("indicators", {}).get("quote", [{}])[0].get("close", [])
            closes = [float(x) for x in closes if x is not None]
            if closes:
                prices["gold"] = closes[-1]
                logger.info(f"✅ Gold from Yahoo: ${prices['gold']}")
        except Exception as e:
            logger.error(f"Yahoo Gold failed: {e}")
            prices["gold"] = None
    
    # Get REAL BTC from Gold-API (primary)
    try:
        response = requests.get(
            "https://api.gold-api.com/price/BTC/USD",
            timeout=5
        )
        data = response.json()
        if "price" in data:
            prices["btc"] = float(data["price"])
            logger.info(f"✅ BTC from Gold-API: ${prices['btc']:,.0f}")
    except Exception as e:
        logger.warning(f"Gold-API BTC failed: {e}, trying Yahoo...")
        prices["btc"] = None
    
    # Fallback: Get BTC from Yahoo Finance
    if prices.get("btc") is None:
        try:
            response = requests.get(
                "https://query1.finance.yahoo.com/v8/finance/chart/BTC-USD",
                params={"interval": "1h", "range": "30d"},
                headers={"User-Agent": "Mozilla/5.0"},
                timeout=10
            )
            data = response.json()
            closes = data.get("chart", {}).get("result", [{}])[0].get("indicators", {}).get("quote", [{}])[0].get("close", [])
            closes = [float(x) for x in closes if x is not None]
            if closes:
                prices["btc"] = closes[-1]
                logger.info(f"✅ BTC from Yahoo: ${prices['btc']:,.0f}")
        except Exception as e:
            logger.error(f"Yahoo BTC failed: {e}")
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
                    "text": message.text,
                    "message_id": message.id
                })
        
        messages.reverse()
        return messages
        
    except Exception as e:
        logger.error(f"Error getting messages: {e}")
        return []


async def get_last_messages_with_ids(limit=30):
    """Get last N messages with message IDs for replies"""
    global client
    
    try:
        if not client or not await client.is_user_authorized():
            logger.error("Not logged in")
            return []
        
        entity = await client.get_entity(VANTAGE_GROUP_ID)
        messages = []
        
        async for message in client.iter_messages(entity, limit=limit):
            if message.text and not message.text.startswith("🚨"):
                sender = "Unknown"
                sender_id = None
                if message.sender:
                    try:
                        user = await client.get_entity(message.sender)
                        sender = user.first_name or "Unknown"
                        sender_id = user.id
                    except:
                        sender = "Unknown"
                
                messages.append({
                    "sender": sender,
                    "sender_id": sender_id,
                    "text": message.text,
                    "message_id": message.id
                })
        
        messages.reverse()
        return messages
        
    except Exception as e:
        logger.error(f"Error getting messages with IDs: {e}")
        return []


def generate_reply_to_message(user_message, sender_name, prices):
    """Generate contextual reply to user message using Claude"""
    
    gold_price = prices["gold"]
    btc_price = int(prices["btc"])
    
    prompt = f"""You are Charlie, a 21-year-old trader in a Telegram group. Someone just said something and you're replying to help them.

Their message: "{user_message}"
Their name: {sender_name}

REAL LIVE PRICES RIGHT NOW:
- Gold: ${gold_price:.2f}
- BTC: ${btc_price:,}

Generate ONE helpful, human reply (1-2 sentences MAX) that:
- Directly addresses what they said
- Is humorous or casual, but factual
- Gives them direction or help based on current prices
- Sounds like a real trader responding
- NO "ALERT" language
- Use emojis naturally (maybe)

Examples of good replies:
- "Yeah that was a classic fake pump. Low volume always tells the story 📊"
- "Gold looking tired here ngl. Could see pullback to $3980 before next move 👀"
- "That's the right mindset. Patience > chasing 💯"
- "Structure looks good but wait for confirmation. Don't FOMO in lol 😅"

Generate ONLY the reply text, nothing else."""

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
            reply_text = data["content"][0]["text"].strip()
            return reply_text
        
    except Exception as e:
        logger.error(f"Claude reply generation error: {e}")
    
    return None


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


def get_market_open_message():
    """Check if 30 mins before market open"""
    uk_time = get_uk_time()
    hour = uk_time.hour
    minute = uk_time.minute
    
    messages = []
    
    if hour == 14 and 0 <= minute < 30:
        messages.append("🚨 USA market opening in 30 mins! Gold and BTC usually move hard when US opens. Get ready team 📊")
    
    if hour == 23 and 30 <= minute < 60:
        messages.append("⏰ Asia market about to open in 30 mins. Usually volatile on that session. Watch Gold and BTC closely 🔥")
    
    if hour == 7 and 30 <= minute < 60:
        messages.append("🇬🇧 London market opening in 30 mins! Early morning volatility incoming. Stay sharp team 💪")
    
    if hour == 8 and 30 <= minute < 60:
        messages.append("🇪🇺 Europe market about to open in 30 mins. Watch for volatility spikes on BTC and Gold 📈")
    
    return messages[0] if messages else None


def generate_fallback_response(prices):
    """Generate fallback response with all message types"""
    
    gold_price = prices["gold"]
    btc_price = int(prices["btc"])
    
    trade_setups = [
        f"Gold looking solid on the daily, but 1hr is overbought. Might wait for pullback around ${gold_price - 5:.2f} 👀",
        f"4hr support holding nicely here at ${gold_price:.2f}. Could bounce, worth watching if buyers step in 📈",
        f"Not convinced yet. Daily is bullish but if Gold loses ${gold_price - 10:.2f}, that's a problem. Watching that 🔴",
    ]
    
    timeframe_analysis = [
        f"Daily support is strong but 1hr is getting stretched. Could see pullback here, not a bad spot to buy dip 🎯",
        f"Weekly is bullish Gold trend, 4hr holding up, 1hr showing some weakness. Bigger picture still good 📈",
    ]
    
    news_messages = [
        f"Trump tweeted about the Fed again. Historically Gold pops on that. Could see spike today 📈",
        f"Jobs report tomorrow. Weak = Gold probably goes up, strong = pressure. Already pricing in something 📊",
    ]
    
    psychology_tips = [
        f"Don't chase here. Let Gold come to you at support. Patience wins 🎯",
        f"Most people panic sell at the worst times. That's when real money steps in. Stay calm 💯",
    ]
    
    humor_messages = [
        f"Gold did the classic fake-out lol. Got a lot of people. Classic 😅",
        f"When you're right but still sweating anyway 😤",
    ]
    
    community_questions = [
        f"Gold at ${gold_price:.2f} - what's your read team? Bullish or waiting? 👀",
        f"Anyone else seeing that support holding? Or am I missing something? 🤔",
    ]
    
    live_analysis = [
        f"Gold bouncing nicely from support. Buyers definitely stepping in 📈",
        f"Not looking convinced yet. Need to hold this level to stay bullish 🤐",
    ]
    
    motivation = [
        f"Most people quit before the move happens. Not us 🚀",
        f"Patience = profits. You're doing great if you're still here 💯",
    ]
    
    all_messages = (
        trade_setups + timeframe_analysis + news_messages + psychology_tips +
        humor_messages + community_questions + live_analysis + motivation
    )
    
    return random.choice(all_messages)


async def maybe_reply_to_messages(prices):
    """Randomly reply to messages"""
    global client
    
    try:
        if random.random() > 0.5:
            return None
        
        messages = await get_last_messages_with_ids(limit=25)
        
        if not messages or len(messages) < 1:
            return None
        
        target_message = random.choice(messages)
        
        if len(target_message["text"]) < 5:
            return None
        
        logger.info(f"Replying to {target_message['sender']}: {target_message['text'][:50]}")
        
        reply = generate_reply_to_message(target_message["text"], target_message["sender"], prices)
        
        if not reply:
            return None
        
        entity = await client.get_entity(VANTAGE_GROUP_ID)
        
        sent = await client.send_message(
            entity,
            reply,
            reply_to=target_message["message_id"],
            parse_mode="md"
        )
        
        logger.info(f"✨ Replied to {target_message['sender']} successfully!")
        return sent
        
    except Exception as e:
        logger.error(f"Reply error: {e}")
        return None


async def send_whatsapp_promo():
    """Send WhatsApp promo to user's channel every 5 hours"""
    global client
    
    try:
        if not client or not await client.is_user_authorized():
            logger.error("Not logged in for promo")
            return None
        
        entity = await client.get_entity(USER_CHANNEL_ID)
        
        message_text = """🚀 Join Our WhatsApp Exclusive Community! 🚀

600+ traders receiving DAILY SIGNALS + live analysis"""
        
        from telethon.types import InlineKeyboardMarkup, InlineKeyboardButton
        
        buttons = InlineKeyboardMarkup([
            [InlineKeyboardButton(text="✅ JOIN WHATSAPP GROUP ✅", url="https://chat.whatsapp.com/IkmwitDmS5D3vWo8fN6Mhj")]
        ])
        
        sent = await client.send_message(entity, message_text, buttons=buttons)
        
        logger.info(f"📱 WhatsApp promo sent to YOUR CHANNEL!")
        return sent
        
    except Exception as e:
        logger.error(f"Promo send error: {e}")
        return None


async def send_to_vantage(message_text, reply_to=None):
    """Send message to Vantage group"""
    global client

    if not ENABLE_GROUP_SEND:
        logger.warning("Group sending is locked")
        return None

    if not VANTAGE_GROUP_ID:
        logger.error("VANTAGE_GROUP_ID missing")
        return None

    try:
        entity = await client.get_entity("vantageofficialcommunity")
        
        kwargs = {"parse_mode": "md"}
        if reply_to:
            kwargs["reply_to"] = reply_to

        sent = await client.send_message(entity, message_text, **kwargs)
        logger.info("Message sent")
        return sent

    except Exception as e:
        logger.error(f"Send error: {e}")
        return None


async def engagement_loop():
    """Main engagement loop"""
    global engagement_running, last_posted_time, last_promo_time
    
    logger.info("🚀 Engagement loop started - REAL LIVE PRICES MODE + SMART REPLIES + WHATSAPP PROMO")
    last_posted_time = time.time()
    last_promo_time = time.time()
    
    while engagement_running:
        try:
            delay = get_next_post_delay()
            next_post_minutes = delay / 60
            
            uk_time = get_uk_time()
            logger.info(f"[{uk_time.strftime('%H:%M UTC')}] Next post in {next_post_minutes:.1f} mins")
            
            await asyncio.sleep(delay)
            
            if not engagement_running:
                break
            
            prices = get_live_prices()
            
            if prices["btc"] is None or prices["gold"] is None:
                logger.warning("❌ Missing real prices - skipping this post cycle")
                continue
            
            logger.info(f"📊 REAL Prices: BTC ${prices['btc']:,.0f}, Gold ${prices['gold']:.2f}")
            
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
            
            sent = await send_to_vantage(response, reply_to=VANTAGE_TOPIC_ID)
            
            if sent:
                logger.info(f"✨ Posted main message successfully!")
            
            reply_sent = await maybe_reply_to_messages(prices)
            if reply_sent:
                logger.info(f"💬 Smart reply sent!")
            
            current_time = time.time()
            if current_time - last_promo_time >= 10800:
                promo_sent = await send_whatsapp_promo()
                if promo_sent:
                    logger.info(f"📱 WhatsApp promo sent!")
                    last_promo_time = current_time
            
        except Exception as e:
            logger.error(f"Loop error: {e}")
            await asyncio.sleep(60)
    
    logger.info("Engagement loop stopped")


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
            "btc": f"${prices['btc']:,.0f}"
        }
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


@app.route("/test_promo_now", methods=["GET"])
def test_promo_now():
    """Manually trigger WhatsApp promo to your channel"""
    
    async def _test():
        promo_sent = await send_whatsapp_promo()
        
        return {
            "status": "success" if promo_sent else "failed",
            "message": "🚀 Join Our WhatsApp Exclusive Community! 🚀\n600+ traders receiving DAILY SIGNALS + live analysis",
            "button": "✅ JOIN WHATSAPP GROUP ✅",
            "channel": "@gold_btc_signalss",
            "whatsapp_link": "https://chat.whatsapp.com/IkmwitDmS5D3vWo8fN6Mhj"
        }
    
    try:
        future = asyncio.run_coroutine_threadsafe(_test(), loop)
        result = future.result(timeout=30)
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/test_post_now", methods=["GET"])
def test_post_now():
    """Manually trigger a post"""
    
    async def _test():
        prices = get_live_prices()
        response = generate_fallback_response(prices)
        
        sent = await send_to_vantage(response, reply_to=VANTAGE_TOPIC_ID)
        
        return {
            "status": "success" if sent else "failed",
            "message_sent": response,
            "real_live_prices": {
                "gold": f"${prices['gold']:.2f}",
                "btc": f"${prices['btc']:,.0f}"
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
    
    async def _send():
        global client
        try:
            if not client:
                client = TelegramClient(StringSession(), API_ID, API_HASH)
            
            await client.connect()
            
            result = await client.send_code_request(PHONE)
            
            return {
                "status": "success",
                "message": f"Code sent to {PHONE}",
                "phone_code_hash": result.phone_code_hash
            }
        except Exception as e:
            return {"status": "error", "message": str(e)}
    
    try:
        future = asyncio.run_coroutine_threadsafe(_send(), loop)
        result = future.result(timeout=30)
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/verify", methods=["GET"])
def verify():
    """Verify code and get SESSION_STRING"""
    
    code = request.args.get("code")
    
    if not code:
        return jsonify({"error": "code parameter required"}), 400
    
    async def _verify():
        global client
        try:
            if not client:
                return {"status": "error", "message": "Client not initialized"}
            
            await client.sign_in(PHONE, code)
            
            session_string = client.session.save()
            
            return {
                "status": "success",
                "message": "Login successful!",
                "session_string": session_string,
                "instructions": "Copy the session_string above and update Railway variables with this value"
            }
        except Exception as e:
            return {"status": "error", "message": str(e)}
    
    try:
        future = asyncio.run_coroutine_threadsafe(_verify(), loop)
        result = future.result(timeout=30)
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
