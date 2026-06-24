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


def get_market_open_message():
    """Check if 30 mins before market open - USA, Asia, London, Europe"""
    uk_time = get_uk_time()
    hour = uk_time.hour
    minute = uk_time.minute
    
    messages = []
    
    # USA Market opens 14:30 UK time (30 mins before = 14:00)
    if hour == 14 and 0 <= minute < 30:
        messages.append("🚨 USA market opening in 30 mins! Gold and BTC usually move hard when US opens. Get ready team 📊")
    
    # Asia market opens 00:00 UK time (midnight, 30 mins before = 23:30 prev day)
    if hour == 23 and 30 <= minute < 60:
        messages.append("⏰ Asia market about to open in 30 mins. Usually volatile on that session. Watch Gold and BTC closely 🔥")
    
    # London market opens 08:00 UK time (30 mins before = 07:30)
    if hour == 7 and 30 <= minute < 60:
        messages.append("🇬🇧 London market opening in 30 mins! Early morning volatility incoming. Stay sharp team 💪")
    
    # Europe market opens 09:00 UK time (30 mins before = 08:30)
    if hour == 8 and 30 <= minute < 60:
        messages.append("🇪🇺 Europe market about to open in 30 mins. Watch for volatility spikes on BTC and Gold 📈")
    
    return messages[0] if messages else None


def generate_fallback_response(prices):
    """COMPLETE COMMUNITY BOT - Everything integrated, all LIVE"""
    
    gold_price = prices["gold"]
    btc_price = int(prices["btc"])
    uk_time = get_uk_time()
    
    # CHECK FOR MARKET OPEN ALERTS FIRST
    market_alert = get_market_open_message()
    if market_alert:
        return market_alert
    
    # COMMUNITY ENGAGEMENT QUESTIONS (group-focused, not individual)
    community_questions = [
        f"Gold at ${gold_price:.2f} - team, are you seeing consolidation or breakdown? 🤔",
        f"BTC at ${btc_price:,} - what's everyone's read on the 1hr? Bullish or waiting? 👀",
        f"Gold consolidating at ${gold_price:.2f}. Who's gonna take this trade if it breaks? Show of hands 🙋",
        f"Team check: BTC at ${btc_price:,}. Is this buy the dip or wait? 💭",
        f"Gold at ${gold_price:.2f} - everyone watching the same support level? 👀",
    ]
    
    # RISK/PSYCHOLOGY TIPS (group teaching, not individual)
    psychology_tips = [
        f"Gold at ${gold_price:.2f}. The traders who WIN don't chase. We're waiting together on this 🎯",
        f"BTC at ${btc_price:,}. Best trades feel boring, not exciting. If you're stressed, probably not the right one 😅",
        f"Gold consolidating - here's what separates us from losers: discipline. We have it. Most don't 💪",
        f"Never risk more than 1% per trade. That's how we survive bad days 📊",
        f"The hardest part isn't analysis. It's sitting on your hands when you don't have a setup. You're doing great 🙌",
    ]
    
    # PERSONALITY/HUMOR (memorable moments)
    humor_messages = [
        f"Gold just did the classic fake-out at ${gold_price:.2f}. Classic 😅",
        f"BTC said 'jk' and bounced at ${btc_price:,}. That's trading 🔄",
        f"Gold at ${gold_price:.2f} - when you're right but still stressed anyway. Yeah, that's it 🤷",
        f"BTC giving us the range-bound special at ${btc_price:,}. Consolidation szn 📦",
        f"Gold at ${gold_price:.2f}. The market said 'wait' and here we are waiting. I like it 🤝",
    ]
    
    # FOLLOW-UPS (reference previous calls, live progression)
    followups = [
        f"Remember when I said watch this Gold level? We're testing it RIGHT NOW at ${gold_price:.2f} 🎯",
        f"That BTC setup I mentioned is playing out. We're at ${btc_price:,} exactly where I said 📈",
        f"Gold at ${gold_price:.2f} - following the script. This is what happens with a plan 💯",
        f"Called the consolidation on Gold. Here we are at ${gold_price:.2f} doing exactly that. Trust the process 🔄",
    ]
    
    # MARKET NEWS AWARENESS (live info)
    news_messages = [
        f"Gold at ${gold_price:.2f}. Watch for Fed comments today - last time they spoke, metals spiked 📰",
        f"BTC at ${btc_price:,}. Trump just tweeted about the Fed. Market usually reacts. Stay alert 🚨",
        f"Jobs data coming tomorrow. Watch how Gold at ${gold_price:.2f} reacts. Teaching moment incoming 📊",
        f"Economic data alert at ${gold_price:.2f} Gold. These moments move the market 📈",
    ]
    
    # LIVE ANALYSIS (always current)
    live_analysis = [
        f"Gold at ${gold_price:.2f} right now. 1hr looks like it could hold here, 5min showing weakness 👀",
        f"BTC at ${btc_price:,}. Not convinced yet. 4hr consolidating, need the break 🎯",
        f"Gold ${gold_price:.2f} - the 1hr is fighting the 4hr. That's when we WAIT, don't guess 🤲",
        f"BTC holding at ${btc_price:,}. Volume is light right now. Not serious yet 📊",
        f"Gold at ${gold_price:.2f}. Consolidation is HEALTHY. Means the move could be bigger 💪",
    ]
    
    # MOTIVATION/CONFIDENCE (keeps group engaged)
    motivation = [
        f"You got this team 💪 Most people quit BEFORE the move happens. Not us 🚀",
        f"Gold at ${gold_price:.2f}, BTC at ${btc_price:,}. Patience = profits 📈",
        f"Trading is 90% waiting, 10% execution. We're in the grind right now 🏆",
        f"Gold at ${gold_price:.2f}. Trust the plan. Big moves come from patience 🎯",
        f"BTC at ${btc_price:,}. Respect to everyone holding and waiting. That's discipline 🙌",
    ]
    
    # SPECIFIC INDICATOR TEACHING - Real trader language, varied, human
    indicator_teaching = [
        # Moving Averages (varied ways to explain)
        f"Gold at ${gold_price:.2f}. Notice price just bounced off the EMA50 on 1hr? That's what support looks like 📊",
        f"BTC showing something interesting at ${btc_price:,} - the EMA200 just became resistance. When that flips, big deal 🔄",
        f"See this on Gold? EMA is pointing UP on the 4hr at ${gold_price:.2f}. That's the trend telling you something 📈",
        f"BTC at ${btc_price:,}. EMA50 crossed below EMA200 yesterday. That usually means sellers taking control 📉",
        
        # Volume Analysis (different examples)
        f"Gold at ${gold_price:.2f} and volume just SPIKED. When volume comes in, that move is REAL 💪",
        f"BTC volume dropping here at ${btc_price:,}. You know what that means? Consolidation before the breakout 🔐",
        f"Look at Gold volume bar - it's tiny at ${gold_price:.2f}. Low volume moves = ignore them, wait for real confirmation 🤐",
        f"BTC just pumped on HIGH volume at ${btc_price:,}. That's conviction. That's money moving 📊",
        
        # RSI (natural explanations)
        f"Gold RSI just hit 70 at ${gold_price:.2f}. Getting stretched. That's when you STOP chasing 🎯",
        f"BTC RSI is down at 35 on the 1hr at ${btc_price:,}. Oversold territory. Usually bounce from here 📈",
        f"Honest take: Gold RSI at ${gold_price:.2f} is at 50. Neutral. Could go either way. That's when I do nothing 🤷",
        f"See BTC RSI on 4hr? It's telling you buyers are getting tired. That matters 📉",
        
        # Divergences (real world examples)
        f"Gold made a new high at ${gold_price:.2f} but the RSI didn't. Classic divergence. Reversal coming 🔄",
        f"BTC showing divergence on the daily at ${btc_price:,}. Price up, RSI down. That's a RED FLAG 🚨",
        f"This divergence on Gold 4hr? That's literally FREE MONEY if you can spot it 💰",
        
        # Consolidation (conversational)
        f"Gold consolidating tight at ${gold_price:.2f}. Boring now but... this is where the big move starts 🚀",
        f"BTC been ranging at ${btc_price:,} for hours. That's GOOD. Preparation before the move 📦",
        f"You see that narrow range on Gold at ${gold_price:.2f}? Energy building. Someone's about to break this 💥",
        
        # Support/Resistance (real talk)
        f"Gold just tested that support at ${gold_price:.2f} and bounced. Buyers are HERE 💪",
        f"BTC resistance above at ${btc_price:,}. If it breaks THIS time, next level is way up 📈",
        f"This level on Gold at ${gold_price:.2f} has held 3 times now. That's real support 🔐",
        
        # Breakouts (teaching how to trade them)
        f"Gold at ${gold_price:.2f} about to break consolidation. When it does, WATCH the volume. That tells you if it's real 👀",
        f"BTC breakout attempt at ${btc_price:,}. Most people buy the break. Smart people wait for confirmation first 🎯",
        f"See how Gold broke resistance at ${gold_price:.2f} but came back down? Fake. Wait for it to HOLD 🔄",
        
        # Fake-outs (learning moments)
        f"Gold pumped at ${gold_price:.2f} on low volume. Classic trap. This teaches you something 📚",
        f"BTC broke out at ${btc_price:,} yesterday but couldn't hold. That's why volume matters so much 📊",
        f"Ngl that fake-out on Gold at ${gold_price:.2f} got a lot of people. Me too sometimes, that's trading 😅",
        
        # Time Frames (practical teaching)
        f"Gold on 5min looks bullish at ${gold_price:.2f} but 4hr is bearish. Never ignore the bigger picture 📊",
        f"Trading tip: BTC at ${btc_price:,}. Always check the 4hr FIRST. Then trade the 1hr. That's the way 🎯",
        f"See this on Gold? The 1hr and daily don't match at ${gold_price:.2f}. When they don't align, WAIT 🤐",
        
        # Trends (explaining them simply)
        f"Gold in uptrend on 4hr at ${gold_price:.2f}. In uptrends, you BUY the dips. It's that simple 📈",
        f"BTC in downtrend at ${btc_price:,}. Every pump is a SHORT opportunity. That's the rule 📉",
        f"No trend on Gold right now at ${gold_price:.2f}. Sideways = don't force it. Wait for direction 🤝",
        
        # Practical tips (mixed teaching)
        f"Gold at ${gold_price:.2f} - the best trades come when price AND volume agree. When they don't, skip it 💯",
        f"BTC showing something at ${btc_price:,}. When 2 indicators agree, THAT'S your signal. Not just one 🎯",
        f"Real lesson on Gold at ${gold_price:.2f}: support/resistance + volume + timeframe = WINNING combo 💪",
    ]
    all_messages = (
        community_questions + 
        psychology_tips + 
        humor_messages + 
        followups + 
        news_messages + 
        live_analysis + 
        motivation +
        indicator_teaching
    )
    
    return random.choice(all_messages)


async def send_message_to_entity(entity_target, message_text, chart_image=None, reply_to=None):
    """EXACT COPY FROM WORKING CODE - charlie-vantage-forwarder"""
    global client

    try:
        if not client or not await client.is_user_authorized():
            logger.error("Not logged in")
            return None

        # Use username instead of ID (works with fresh sessions!)
        entity = await client.get_entity("vantageofficialcommunity")

        kwargs = {
            "parse_mode": "md"
        }

        if reply_to:
            kwargs["reply_to"] = reply_to

        if chart_image:
            sent = await client.send_file(
                entity,
                chart_image,
                caption=message_text,
                force_document=False,
                **kwargs
            )
        else:
            sent = await client.send_message(
                entity,
                message_text,
                **kwargs
            )

        logger.info("Message sent")
        return sent

    except Exception as e:
        logger.error(f"Send error: {e}")
        return None


async def send_to_vantage(message_text, chart_image=None, reply_to=None):
    """EXACT COPY FROM WORKING CODE - charlie-vantage-forwarder"""
    if not ENABLE_GROUP_SEND:
        logger.warning("Group sending is locked")
        return None

    if not VANTAGE_GROUP_ID:
        logger.error("VANTAGE_GROUP_ID missing")
        return None

    if chart_image is None and reply_to is None:
        logger.error("Chart image missing. Refusing to send fresh group update.")
        return None

    return await send_message_to_entity(
        VANTAGE_GROUP_ID,
        message_text,
        chart_image=chart_image,
        reply_to=reply_to if reply_to else VANTAGE_TOPIC_ID if VANTAGE_TOPIC_ID and VANTAGE_TOPIC_ID > 0 else None
    )


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
            
            sent = await send_to_vantage(
                response,
                chart_image=None,
                reply_to=VANTAGE_TOPIC_ID
            )
            
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
            "btc": f"${prices['btc']:,.0f}"
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
        
        # Pass reply_to since no chart_image
        sent = await send_to_vantage(
            response,
            chart_image=None,
            reply_to=VANTAGE_TOPIC_ID
        )
        
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
