import os
import asyncio
from dotenv import load_dotenv
from telegram import Bot
from telegram.ext import Application, CommandHandler

load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
TELEGRAM_CHAT_ID = os.getenv('TELEGRAM_CHAT_ID')

async def start_command(update, context):
    chat_id = update.effective_chat.id
    await update.message.reply_text(
        f"⚡ Alert Forge connected.\n\n"
        f"Your Chat ID: `{chat_id}`\n\n"
        f"Save this ID to receive alerts.",
        parse_mode='Markdown'
    )

async def send_test_alert():
    """Send a test alert to confirm everything works"""
    bot = Bot(token=TELEGRAM_BOT_TOKEN)
    await bot.send_message(
        chat_id=TELEGRAM_CHAT_ID,
        text=(
            "⚡ *ALERT FORGE*\n\n"
            "🔔 *Test Alert Fired*\n\n"
            "✅ Bot connected\n"
            "✅ Supabase ready\n"
            "✅ Monitoring active\n\n"
            "Your alerts will look like this."
        ),
        parse_mode='Markdown'
    )
    print("✅ Test alert sent to Telegram")

def main():
    print("🚀 Alert Forge Bot starting...")
    
    # Send test alert on startup
    asyncio.get_event_loop().run_until_complete(send_test_alert())
    
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start_command))
    print("✅ Bot running.")
    app.run_polling()

if __name__ == "__main__":
    main()