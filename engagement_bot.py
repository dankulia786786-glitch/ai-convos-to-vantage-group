#!/usr/bin/env python3
"""
Get SESSION_STRING for your Telegram account
Run this script to login and get your session string for the bot
"""

import asyncio
from telethon import TelegramClient
from telethon.sessions import StringSession

# Your API credentials
API_ID = 30752070
API_HASH = "45d346751438ce944b988fb54bed5ae1"

async def main():
    print("=" * 60)
    print("TELEGRAM SESSION STRING GENERATOR")
    print("=" * 60)
    print()
    
    # Create client with empty session
    client = TelegramClient(StringSession(), API_ID, API_HASH)
    
    try:
        # Connect
        print("📱 Connecting to Telegram...")
        await client.connect()
        
        # Start (login)
        print("🔐 Starting login process...")
        await client.start(phone="+447520676563")
        
        # Get session string
        session_string = client.session.save()
        
        print()
        print("=" * 60)
        print("✅ SUCCESS! HERE'S YOUR SESSION STRING:")
        print("=" * 60)
        print()
        print(session_string)
        print()
        print("=" * 60)
        print()
        print("📋 COPY THE ENTIRE STRING ABOVE!")
        print("It starts with '1' and is very long.")
        print()
        print("Then paste it into Railway variables as SESSION_STRING")
        print()
        
    except Exception as e:
        print(f"❌ Error: {e}")
    finally:
        await client.disconnect()

if __name__ == "__main__":
    asyncio.run(main())
