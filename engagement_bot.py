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
last_promo_time = 0  # Track last WhatsApp promo send


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
            if message.text and not message.text.startswith("🚨"):  # Skip bot's own messages
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


async def send_whatsapp_promo():
    """Send WhatsApp group promo to user's channel every 5 hours with clickable button"""
    global client
    
    try:
        if not client or not await client.is_user_authorized():
            logger.error("Not logged in for promo")
            return None
        
        # Send to USER'S CHANNEL (not Vantage group)
        entity = await client.get_entity(USER_CHANNEL_ID)
        
        message_text = """🚀 Join Our WhatsApp Exclusive Community! 🚀
600+ traders receiving DAILY SIGNALS + live analysis"""
        
        # Send with inline button
        from telethon.tl.types import KeyboardButtonUrl
        from telethon.tl.types import ReplyInlineMarkup
        from telethon.tl.types import InlineKeyboardButton
        
        buttons = [
            [InlineKeyboardButton(
                text="✅ JOIN WHATSAPP GROUP ✅",
                url="https://chat.whatsapp.com/IkmwitDmS5D3vWo8fN6Mhj"
            )]
        ]
        
        sent = await client.send_message(
            entity,
            message_text,
            buttons=buttons
        )
        
        logger.info(f"✅ WhatsApp promo sent to YOUR CHANNEL!")
        return sent
        
    except Exception as e:
        logger.error(f"Promo send error: {e}")
        return None
    """Randomly reply to 1 message every few posts (every 3rd-4th time)"""
    global client
    
    try:
        # Only reply 50% of the time (more active, still natural)
        if random.random() > 0.5:
            return None
        
        # Get recent messages
        messages = await get_last_messages_with_ids(limit=25)
        
        if not messages or len(messages) < 1:
            return None
        
        # Pick a random message from last 25 (not bot's own)
        target_message = random.choice(messages)
        
        # Don't reply to very short messages or just emojis
        if len(target_message["text"]) < 5:
            return None
        
        logger.info(f"Replying to {target_message['sender']}: {target_message['text'][:50]}")
        
        # Generate reply using Claude
        reply = generate_reply_to_message(
            target_message["text"],
            target_message["sender"],
            prices
        )
        
        if not reply:
            logger.warning("Failed to generate reply")
            return None
        
        # Send as reply to their message
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
    """NATURAL HUMAN TRADER - Direction + Analysis + News + Timeframes"""
    
    gold_price = prices["gold"]
    btc_price = int(prices["btc"])
    uk_time = get_uk_time()
    
    # CHECK FOR MARKET OPEN ALERTS FIRST
    market_alert = get_market_open_message()
    if market_alert:
        return market_alert
    
    # TRADES & SETUPS (natural direction, specific levels, timeframes)
    trade_setups = [
        f"Gold looking solid on the daily, but 1hr is overbought. Might wait for a pullback around ${gold_price - 5:.2f} 👀",
        f"4hr support holding nicely here at ${gold_price:.2f}. Could bounce, worth watching if buyers step in 📈",
        f"Not convinced yet. Daily is bullish but if Gold loses ${gold_price - 10:.2f}, that's a problem. Watching that 🔴",
        f"Gold just bounced off support. Buyers are definitely here. Short term target ${gold_price + 15:.2f} 🎯",
        f"Weekly is up, 4hr is holding, but 1hr showing weakness. Conflicting signals. I'd wait 🤐",
        f"Gold breaking above ${gold_price + 5:.2f} would change the whole game. That's key resistance 👀",
        f"Support at ${gold_price - 8:.2f} is solid. If it holds, could see rally to ${gold_price + 20:.2f} 📊",
        f"Daily trend is up but running out of steam. Could see pullback to ${gold_price - 12:.2f} before continuing 📉",
        f"BTC at ${btc_price:,}. 4hr looks bullish but need to confirm above key level. Watching closely 🔍",
        f"Gold consolidating between ${gold_price - 10:.2f} and ${gold_price + 10:.2f}. Breakout coming soon 💥",
        f"Daily shows strength but getting tired. 1hr dip could be BUY opportunity here 💰",
        f"Gold rejected at ${gold_price + 10:.2f}. If it fails again, could drop to ${gold_price - 15:.2f} 📉",
    ]
    
    # TIMEFRAME ANALYSIS (naturally mentioned)
    timeframe_analysis = [
        f"Daily support is strong but 1hr is getting stretched. Could see pullback here, not a bad spot to buy dip 🎯",
        f"Weekly is bullish Gold trend, 4hr holding up, 1hr showing some weakness. Bigger picture still good 📈",
        f"The 4hr looks good but daily has resistance around ${gold_price + 15:.2f}. That's where sellers might step in 🔴",
        f"1hr is oversold but 4hr daily are up. This dip at ${gold_price:.2f} could be a BUY 💪",
        f"Gold on weekly is in clear uptrend. Don't fight the trend. Dips are buys 📈",
        f"4hr and daily don't match right now. When they're conflicting, I wait for clarity 🤲",
        f"1hr bounced hard but need 4hr confirmation. If it holds, we're good to push higher 👀",
        f"Weekly showing fatigue. Even though 1hr is strong, be careful chasing here 🛑",
        f"Daily gold still bullish. But 1hr on the verge of reversing. Good entry on dips only 🎯",
        f"4hr in consolidation. When this breaks, it'll be on 1hr and daily confirmation. Watch for that 👁️",
    ]
    
    # NEWS & GEOPOLITICS (Trump, Fed, jobs, Iran, etc)
    news_messages = [
        f"Trump tweeted about the Fed again. Historically Gold pops on that. Could see spike today 📈",
        f"Jobs report tomorrow. Weak = Gold probably goes up, strong = pressure. Already pricing in something 📊",
        f"Fed meeting coming. Gold usually moves hard on Fed talk. Watch for any dovish signals 🚨",
        f"Iran tensions up again. Safe haven flows into Gold. Could see strength here 🔥",
        f"Inflation data showed cooling. That usually hurts Gold short term. Watch support 📉",
        f"Fed speaker today. These guys can move markets. Gold sensitive to hawkish/dovish talk 📻",
        f"Economic slowdown fears. That's Gold bullish. Could see relief rally coming 📈",
        f"Trump talking about trade wars again. Usually good for Gold as safe haven 🛡️",
        f"Dollar weakness = Gold strength. Watching if DXY keeps falling 💹",
        f"Geopolitical tensions ramping up. Gold should benefit. Classic safe haven play 🌍",
        f"Central bank buying Gold. Usual signal for more upside coming 💰",
        f"Recession chatter on Bloomberg. That's normally bullish for precious metals 📺",
    ]
    
    # PSYCHOLOGY/RISK (natural, group-focused)
    psychology_tips = [
        f"Don't chase here. Let Gold come to you at support. Patience wins 🎯",
        f"Most people panic sell at the worst times. That's when real money steps in. Stay calm 💯",
        f"Risk only 1% per trade. One bad loss can wipe you out. Protect yourself 📊",
        f"The winning traders wait. They don't force trades. We're waiting together 🤝",
        f"When it feels too easy, that's usually when it goes wrong. Stay humble 🙏",
        f"Best trades feel boring. If you're excited, probably chasing. That's a bad sign 😅",
        f"Take your wins. Don't let greed turn wins into losses. Lock it in 📌",
        f"Losing is part of the game. What matters is how you respond. Stay disciplined 💪",
        f"Everyone can spot the obvious trade. Real money waits for everyone else to panic 🔮",
        f"Your biggest enemy is yourself. Emotions will destroy you. Keep it cold 🧊",
        f"Stop losses exist for a reason. If you don't have one, you're gambling 🛑",
        f"The market will humble you. Stay sharp and adapt 🧠",
    ]
    
    # HUMOR/PERSONALITY (real, memorable)
    humor_messages = [
        f"Gold did the classic fake-out lol. Got a lot of people. Classic 😅",
        f"When you're right but still sweating anyway 😤",
        f"Gold said 'I'm going up' then immediately said 'jk' 🔄",
        f"That move looked so real but nope, trap. These are the moments that teach you 📚",
        f"Gold bounced so hard it scared the sellers lol 💨",
        f"Market giveth, market taketh. That's trading 🎲",
        f"If trading was easy everyone would be rich. Thank god it's not 😅",
        f"That candle hurt but we're still here grinding 💪",
        f"Gold playing mind games with us today 🎪",
        f"Support held better than my anxiety did 😂",
        f"This volatility is actually good for us 🎢",
        f"Gold testing my patience and my account lol 😅",
    ]
    
    # COMMUNITY ENGAGEMENT (natural questions)
    community_questions = [
        f"Gold at ${gold_price:.2f} - what's your read team? Bullish or waiting? 👀",
        f"Anyone else seeing that support holding? Or am I missing something? 🤔",
        f"That bounce looked real or fake? What's everyone thinking? 💭",
        f"Team thoughts on ${gold_price:.2f} - is this a dip to buy or a trap? 🎯",
        f"Who bought that dip? How's it looking for you guys? 📊",
        f"Gold testing resistance again. Think it breaks this time? 🤷",
        f"Honest question - are you shorting this bounce or going long? 💬",
        f"What's your target if Gold breaks above ${gold_price + 15:.2f}? 🚀",
        f"Anyone holding from the dip? Where's your stop loss? 🎯",
        f"Gold bouncing hard. Is this the real move or another trap? 🤔",
    ]
    
    # LIVE ANALYSIS (natural observations, direction)
    live_analysis = [
        f"Gold bouncing nicely from support. Buyers definitely stepping in 📈",
        f"Not looking convinced yet. Need to hold this level to stay bullish 🤐",
        f"Price action looks healthy. Could see continuation higher 💪",
        f"Volume is light. Not a serious move yet. Waiting for real conviction 📊",
        f"That rejection at resistance was sharp. Could pull back now 📉",
        f"Gold holding support is good sign. But daily resistance is coming 👀",
        f"Looks like consolidation. Big move coming when it breaks 💥",
        f"Price is stuck. Neither buyers nor sellers in control yet 🤝",
        f"That pump came on no volume. Classic trap move. Be careful 🚨",
        f"Gold looks tired here. Could see pullback forming 📉",
        f"Buyers stepping in nicely. Could form support here 🏠",
        f"Really sharp rejection. Sellers have control right now 🔴",
    ]
    
    # MOTIVATION/CONFIDENCE (keeps energy up)
    motivation = [
        f"Most people quit before the move happens. Not us 🚀",
        f"Patience = profits. You're doing great if you're still here 💯",
        f"This is the grind. 90% waiting, 10% execution. We're in it 🏆",
        f"Trust the process. Big moves come from patience 🎯",
        f"Respect to everyone holding and waiting. That's real discipline 🙌",
        f"You got this team. The winners are the ones who don't panic 💪",
        f"Keep your emotions out of it. Cold + calculated = winners 🧊",
        f"Every loss teaches you something. Every win builds confidence. Keep going 📈",
        f"The market rewards patience. Everyone else loses fast 🔮",
        f"Stay focused. We're building something here 🏗️",
        f"This dip is opportunity for smart money. Be that person 🧠",
        f"Volatility is a feature, not a bug. We're making money off it 💰",
    ]
    
    all_messages = (
        trade_setups +
        timeframe_analysis +
        news_messages +
        psychology_tips +
        humor_messages +
        community_questions +
        live_analysis +
        motivation
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
    """Main engagement loop with posting + intelligent replies + WhatsApp promo"""
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
            
            # Get REAL LIVE prices only
            prices = get_live_prices()
            
            # Only post if we have REAL prices (no fallbacks!)
            if prices["btc"] is None or prices["gold"] is None:
                logger.warning("❌ Missing real prices - skipping this post cycle")
                continue
            
            logger.info(f"📊 REAL Prices: BTC ${prices['btc']:,.0f}, Gold ${prices['gold']:.2f}")
            
            # MAIN POST
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
                logger.info(f"✨ Posted main message successfully!")
            else:
                logger.warning("Failed to send main message")
            
            # SMART REPLY (50% chance, every few posts)
            reply_sent = await maybe_reply_to_messages(prices)
            if reply_sent:
                logger.info(f"💬 Smart reply sent!")
            
            # WHATSAPP PROMO (every 5 hours = 18000 seconds)
            current_time = time.time()
            if current_time - last_promo_time >= 18000:  # 5 hours
                promo_sent = await send_whatsapp_promo()
                if promo_sent:
                    logger.info(f"📱 WhatsApp promo sent!")
                    last_promo_time = current_time
            
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
