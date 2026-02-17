import os
import asyncio
from dotenv import load_dotenv
from telegram import Bot
from telegram.ext import Application, CommandHandler, ContextTypes
from telegram import Update
from supabase import create_client, Client
from wallet_monitor import get_wallet_transactions, format_transaction_alert

load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
TELEGRAM_CHAT_ID = os.getenv('TELEGRAM_CHAT_ID')
SUPABASE_URL = os.getenv('SUPABASE_URL')
SUPABASE_KEY = os.getenv('SUPABASE_KEY')

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
seen_signatures = set()

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    try:
        supabase.table('users').upsert({
            'telegram_chat_id': str(chat_id),
            'subscription_tier': 'free'
        }).execute()
        await update.message.reply_text(
            f"⚡ *Alert Forge*\n\n"
            f"✅ Connected + Registered\n"
            f"📋 Chat ID: `{chat_id}`\n"
            f"🎯 Tier: Free\n\n"
            f"*Commands:*\n"
            f"/watch <wallet> - Monitor a wallet\n"
            f"/unwatch <wallet> - Stop monitoring\n"
            f"/list - Show your watched wallets",
            parse_mode='Markdown'
        )
    except Exception as e:
        print(f"❌ Error: {e}")

async def watch_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    
    # Check wallet address provided
    if not context.args:
        await update.message.reply_text(
            "❌ Please provide a wallet address.\n\n"
            "Usage: `/watch <wallet_address>`",
            parse_mode='Markdown'
        )
        return
    
    wallet_address = context.args[0]
    
    # Basic Solana address validation
    if len(wallet_address) < 32 or len(wallet_address) > 44:
        await update.message.reply_text("❌ Invalid wallet address.")
        return
    
    try:
        # Get or create user
        user = supabase.table('users').select('*').eq('telegram_chat_id', chat_id).execute()
        
        if not user.data:
            supabase.table('users').insert({
                'telegram_chat_id': chat_id,
                'subscription_tier': 'free'
            }).execute()
            user = supabase.table('users').select('*').eq('telegram_chat_id', chat_id).execute()
        
        user_id = user.data[0]['id']
        
        # Check bot limit (free = 1 wallet)
        existing_bots = supabase.table('bots').select('*').eq('user_id', user_id).eq('is_active', True).execute()
        
        if len(existing_bots.data) >= 1 and user.data[0]['subscription_tier'] == 'free':
            await update.message.reply_text(
                "⚠️ *Free tier limit reached.*\n\n"
                "You can monitor 1 wallet on free tier.\n"
                "Upgrade to Pro for 3 wallets.\n\n"
                "Coming soon: /upgrade",
                parse_mode='Markdown'
            )
            return
        
        # Check if already watching
        existing = supabase.table('bots').select('*').eq('user_id', user_id).eq('config->>wallet_address', wallet_address).execute()
        
        if existing.data:
            await update.message.reply_text("⚠️ Already monitoring this wallet.")
            return
        
        # Add bot to Supabase
        supabase.table('bots').insert({
            'user_id': user_id,
            'bot_name': f"Wallet {wallet_address[:8]}...",
            'alert_type': 'wallet',
            'config': {'wallet_address': wallet_address},
            'telegram_chat_id': chat_id,
            'is_active': True
        }).execute()
        
        await update.message.reply_text(
            f"✅ *Watching wallet*\n\n"
            f"`{wallet_address[:8]}...{wallet_address[-8:]}`\n\n"
            f"You'll receive alerts for new transactions.",
            parse_mode='Markdown'
        )
        print(f"✅ User {chat_id} watching {wallet_address[:8]}...")
        
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {e}")
        print(f"❌ Watch error: {e}")

async def unwatch_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    
    if not context.args:
        await update.message.reply_text(
            "Usage: `/unwatch <wallet_address>`",
            parse_mode='Markdown'
        )
        return
    
    wallet_address = context.args[0]
    
    try:
        user = supabase.table('users').select('*').eq('telegram_chat_id', chat_id).execute()
        if not user.data:
            await update.message.reply_text("❌ No account found.")
            return
        
        user_id = user.data[0]['id']
        
        supabase.table('bots').update({'is_active': False}).eq('user_id', user_id).eq('config->>wallet_address', wallet_address).execute()
        
        await update.message.reply_text(
            f"🛑 *Stopped monitoring*\n\n"
            f"`{wallet_address[:8]}...{wallet_address[-8:]}`",
            parse_mode='Markdown'
        )
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {e}")

async def list_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    
    try:
        user = supabase.table('users').select('*').eq('telegram_chat_id', chat_id).execute()
        if not user.data:
            await update.message.reply_text("❌ No account found. Send /start first.")
            return
        
        user_id = user.data[0]['id']
        bots = supabase.table('bots').select('*').eq('user_id', user_id).eq('is_active', True).execute()
        
        if not bots.data:
            await update.message.reply_text(
                "👛 *No wallets monitored.*\n\n"
                "Add one with `/watch <wallet_address>`",
                parse_mode='Markdown'
            )
            return
        
        wallet_list = ""
        for bot in bots.data:
            wallet = bot['config']['wallet_address']
            wallet_list += f"• `{wallet[:8]}...{wallet[-8:]}`\n"
        
        await update.message.reply_text(
            f"👛 *Monitored Wallets*\n\n{wallet_list}",
            parse_mode='Markdown'
        )
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {e}")

async def check_wallets(bot: Bot):
    """Check all active wallets from Supabase"""
    try:
        active_bots = supabase.table('bots').select('*').eq('is_active', True).eq('alert_type', 'wallet').execute()
        
        for bot_config in active_bots.data:
            wallet = bot_config['config']['wallet_address']
            chat_id = bot_config['telegram_chat_id']
            
            txs = get_wallet_transactions(wallet, limit=5)
            
            for tx in txs:
                sig = tx.get('signature', '')
                
                if sig in seen_signatures:
                    continue
                
                seen_signatures.add(sig)
                alert = format_transaction_alert(tx, wallet)
                
                await bot.send_message(
                    chat_id=chat_id,
                    text=alert,
                    parse_mode='Markdown',
                    disable_web_page_preview=True
                )
                print(f"✅ Alert sent: {sig[:20]}...")
                
    except Exception as e:
        print(f"❌ Check error: {e}")

async def monitoring_loop(bot: Bot):
    print("👁 Monitoring started...")
    
    try:
        active_bots = supabase.table('bots').select('*').eq('is_active', True).execute()
        for bot_config in active_bots.data:
            wallet = bot_config['config']['wallet_address']
            existing = get_wallet_transactions(wallet, limit=20)
            for tx in existing:
                seen_signatures.add(tx.get('signature', ''))
        print(f"📋 Loaded {len(seen_signatures)} existing transactions")
    except Exception as e:
        print(f"❌ Startup load error: {e}")
    
    while True:
        await check_wallets(bot)
        await asyncio.sleep(60)

async def post_init(application):
    asyncio.create_task(monitoring_loop(application.bot))

def main():
    print("🚀 Alert Forge starting...")
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).post_init(post_init).build()
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("watch", watch_command))
    app.add_handler(CommandHandler("unwatch", unwatch_command))
    app.add_handler(CommandHandler("list", list_command))
    print("✅ Bot running.")
    app.run_polling()

if __name__ == "__main__":
    main()