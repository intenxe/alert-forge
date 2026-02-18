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


async def is_seen(sig: str) -> bool:
    result = supabase.table('seen_signatures').select('signature').eq('signature', sig).execute()
    return len(result.data) > 0

async def mark_seen(sig: str, wallet: str):
    supabase.table('seen_signatures').insert({
        'signature': sig,
        'wallet_address': wallet
    }).execute()

async def cleanup_old_signatures():
    supabase.table('seen_signatures')\
        .delete()\
        .lt('created_at', "now() - interval '7 days'")\
        .execute()


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

    if not context.args:
        await update.message.reply_text(
            "❌ Please provide a wallet address.\n\nUsage: `/watch <wallet_address>`",
            parse_mode='Markdown'
        )
        return

    wallet_address = context.args[0]

    if len(wallet_address) < 32 or len(wallet_address) > 44:
        await update.message.reply_text("❌ Invalid wallet address.")
        return

    try:
        user = supabase.table('users').select('*').eq('telegram_chat_id', chat_id).execute()

        if not user.data:
            supabase.table('users').insert({
                'telegram_chat_id': chat_id,
                'subscription_tier': 'free'
            }).execute()
            user = supabase.table('users').select('*').eq('telegram_chat_id', chat_id).execute()

        user_id = user.data[0]['id']

        # Free tier limit check
        existing_bots = supabase.table('bots').select('*')\
            .eq('user_id', user_id)\
            .eq('is_active', True)\
            .execute()

        if len(existing_bots.data) >= 1 and user.data[0]['subscription_tier'] == 'free':
            await update.message.reply_text(
                "⚠️ *Free tier limit reached.*\n\n"
                "You can monitor 1 wallet on free tier.\n"
                "Upgrade to Pro for 3 wallets.\n\n"
                "Coming soon: /upgrade",
                parse_mode='Markdown'
            )
            return

        # Already watching check
        existing = supabase.table('bots').select('*')\
            .eq('user_id', user_id)\
            .eq('config->>wallet_address', wallet_address)\
            .eq('is_active', True)\
            .execute()

        if existing.data:
            await update.message.reply_text("⚠️ Already monitoring this wallet.")
            return

        # Seed existing txs so we don't fire alerts on old transactions
        existing_txs = get_wallet_transactions(wallet_address, limit=50)
        for tx in existing_txs:
            sig = tx.get('signature', '')
            if sig:
                await mark_seen(sig, wallet_address)
        print(f"📋 Seeded {len(existing_txs)} existing txs for {wallet_address[:8]}...")

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
        print(f"❌ Watch error: {type(e).__name__}: {e}")
        await update.message.reply_text(f"❌ Error: {e}")


async def unwatch_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)

    if not context.args:
        await update.message.reply_text("Usage: `/unwatch <wallet_address>`", parse_mode='Markdown')
        return

    wallet_address = context.args[0]

    try:
        user = supabase.table('users').select('*').eq('telegram_chat_id', chat_id).execute()
        if not user.data:
            await update.message.reply_text("❌ No account found.")
            return

        user_id = user.data[0]['id']

        existing = supabase.table('bots').select('*').eq('user_id', user_id).execute()
        print(f"🔍 All bots for user: {existing.data}")

        target = [b for b in existing.data if b['config'].get('wallet_address') == wallet_address]

        if not target:
            await update.message.reply_text("❌ Wallet not found in your monitored list.")
            return

        for row in target:
            supabase.table('bots').delete().eq('id', row['id']).execute()
            print(f"🗑 Deleted bot id: {row['id']}")

        await update.message.reply_text(
            f"🛑 *Stopped monitoring*\n\n`{wallet_address[:8]}...{wallet_address[-8:]}`",
            parse_mode='Markdown'
        )

    except Exception as e:
        print(f"❌ Unwatch error: {type(e).__name__}: {e}")
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
                "👛 *No wallets monitored.*\n\nAdd one with `/watch <wallet_address>`",
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
    try:
        active_bots = supabase.table('bots').select('*').eq('is_active', True).eq('alert_type', 'wallet').execute()

        for bot_config in active_bots.data:
            wallet = bot_config['config']['wallet_address']
            chat_id = bot_config['telegram_chat_id']

            txs = get_wallet_transactions(wallet, limit=5)

            for tx in txs:
                sig = tx.get('signature', '')
                if not sig:
                    continue

                if await is_seen(sig):
                    continue

                await mark_seen(sig, wallet)
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
    cycle = 0

    while True:
        await check_wallets(bot)
        cycle += 1

        # Cleanup old signatures every 24 hours (1440 cycles at 60s each)
        if cycle % 1440 == 0:
            await cleanup_old_signatures()
            print("🧹 Cleaned up old signatures")

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