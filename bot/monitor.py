import os
import asyncio
import requests
from dotenv import load_dotenv
from supabase import create_client, Client
from telegram import Bot

load_dotenv()

# Environment variables
TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
SUPABASE_URL = os.getenv('SUPABASE_URL')
SUPABASE_KEY = os.getenv('SUPABASE_KEY')
HELIUS_API_KEY = os.getenv('HELIUS_API_KEY')
PAYMENT_WALLET = os.getenv('PAYMENT_WALLET')

# USDC mint address on Solana
USDC_MINT = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"

# Initialize
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
bot = Bot(token=TELEGRAM_BOT_TOKEN)

def get_wallet_transactions(wallet_address: str, limit: int = 5):
    """Fetch latest transactions for a wallet"""
    url = f"https://api.helius.xyz/v0/addresses/{wallet_address}/transactions"
    params = {"api-key": HELIUS_API_KEY, "limit": limit}
    
    try:
        response = requests.get(url, params=params)
        response.raise_for_status()
        return response.json()
    except Exception as e:
        print(f"❌ Helius API error for {wallet_address[:8]}: {e}")
        return []

def is_signature_seen(signature: str) -> bool:
    """Check if transaction signature already processed"""
    try:
        result = supabase.table('seen_signatures').select('signature').eq('signature', signature).execute()
        return len(result.data) > 0
    except:
        return False

def mark_signature_seen(signature: str, wallet_address: str):
    """Mark transaction signature as processed"""
    try:
        supabase.table('seen_signatures').upsert({
            'signature': signature,
            'wallet_address': wallet_address
        }, on_conflict='signature').execute()
    except Exception as e:
        print(f"⚠️ Mark seen error: {e}")

def format_transaction_alert(tx: dict, wallet_address: str) -> str:
    """Format transaction into Telegram message"""
    tx_type = tx.get('type', 'UNKNOWN')
    signature = tx.get('signature', '')[:20]
    fee = tx.get('fee', 0) / 1e9
    
    # Get SOL balance change
    account_changes = tx.get('accountData', [])
    sol_change = 0
    for account in account_changes:
        if account.get('account') == wallet_address:
            sol_change = account.get('nativeBalanceChange', 0) / 1e9
    
    direction = "📈 RECEIVED" if sol_change > 0 else "📉 SENT"
    amount = abs(sol_change)
    
    return (
        f"⚡ ALERT FORGE - Wallet Alert\n\n"
        f"👛 Wallet: `{wallet_address[:8]}...{wallet_address[-8:]}`\n"
        f"🔔 Type: {tx_type}\n"
        f"{direction}: {amount:.4f} SOL\n"
        f"⛽ Fee: {fee:.6f} SOL\n"
        f"🔗 Sig: `{signature}...`\n\n"
        f"[View on Solscan](https://solscan.io/tx/{tx.get('signature', '')})"
    )

async def check_wallet_monitoring():
    """Check all monitored wallets for new transactions"""
    try:
        # Get all active bots
        bots = supabase.table('bots').select('*').eq('is_active', True).execute()
        
        for bot_record in bots.data:
            wallet = bot_record['wallet_address']
            chat_id = bot_record.get('telegram_chat_id')
            
            # Get user_id to find chat_id if not in bot record
            if not chat_id:
                user = supabase.table('users').select('telegram_chat_id').eq('id', bot_record['user_id']).execute()
                if user.data:
                    chat_id = user.data[0]['telegram_chat_id']
            
            if not chat_id:
                print(f"⚠️ No chat_id for wallet {wallet[:8]}")
                continue
            
            # Get transactions
            txs = get_wallet_transactions(wallet, limit=5)
            
            for tx in txs:
                sig = tx.get('signature', '')
                
                if is_signature_seen(sig):
                    continue
                
                # New transaction - send alert
                alert = format_transaction_alert(tx, wallet)
                
                try:
                    await bot.send_message(
                        chat_id=chat_id,
                        text=alert,
                        parse_mode='Markdown',
                        disable_web_page_preview=True
                    )
                    print(f"✅ Alert sent for {wallet[:8]}: {sig[:20]}")
                    
                    # Mark as seen
                    mark_signature_seen(sig, wallet)
                    
                except Exception as e:
                    print(f"❌ Telegram send error: {e}")
                
    except Exception as e:
        print(f"❌ Wallet monitoring error: {e}")

async def check_payment_monitoring():
    """Check payment wallet for USDC payments"""
    try:
        txs = get_wallet_transactions(PAYMENT_WALLET, limit=10)
        
        for tx in txs:
            sig = tx.get('signature', '')
            
            if is_signature_seen(sig):
                continue
            
            # Check for USDC transfers
            token_transfers = tx.get('tokenTransfers', [])
            
            for transfer in token_transfers:
                if (transfer.get('mint') == USDC_MINT and 
                    transfer.get('toUserAccount') == PAYMENT_WALLET):
                    
                    amount = transfer.get('tokenAmount', 0)
                    from_wallet = transfer.get('fromUserAccount', '')
                    
                    print(f"💰 Payment detected: {amount} USDC from {from_wallet[:8]}")
                    
                    # Determine tier
                    if amount >= 30:
                        new_tier = 'premium'
                        tier_name = 'Premium'
                    elif amount >= 10:
                        new_tier = 'pro'
                        tier_name = 'Pro'
                    else:
                        print(f"⚠️ Payment too low: {amount} USDC")
                        mark_signature_seen(sig, PAYMENT_WALLET)
                        continue
                    
                    # Find user by their watched wallet
                    bots = supabase.table('bots').select('user_id').eq('wallet_address', from_wallet).execute()
                    
                    if bots.data:
                        user_id = bots.data[0]['user_id']
                        
                        # Get chat_id
                        user = supabase.table('users').select('telegram_chat_id').eq('id', user_id).execute()
                        
                        if user.data:
                            chat_id = user.data[0]['telegram_chat_id']
                            
                            # Upgrade tier
                            supabase.table('users').update({'subscription_tier': new_tier}).eq('id', user_id).execute()
                            
                            print(f"✅ Upgraded user {chat_id} to {tier_name}")
                            
                            # Send confirmation
                            try:
                                await bot.send_message(
                                    chat_id=chat_id,
                                    text=(
                                        f"🎉 PAYMENT CONFIRMED\n\n"
                                        f"✅ Upgraded to {tier_name}\n"
                                        f"💰 {amount} USDC received\n\n"
                                        f"You can now monitor more wallets!\n"
                                        f"Use /list to see your limits."
                                    )
                                )
                            except Exception as e:
                                print(f"❌ Telegram send error: {e}")
                    
                    # Mark payment as seen
                    mark_signature_seen(sig, PAYMENT_WALLET)
        
    except Exception as e:
        print(f"❌ Payment monitoring error: {e}")

async def monitoring_loop():
    """Main monitoring loop - runs every 60 seconds"""
    print("🚀 Alert Forge Monitor starting...")
    print(f"📡 Monitoring wallet transactions and payments")
    
    # ONE-TIME SEED: Load existing transactions to prevent spam
    print("📋 Seeding existing transactions...")
    try:
        bots = supabase.table('bots').select('wallet_address').eq('is_active', True).execute()
        seed_count = 0
        for bot_record in bots.data:
            wallet = bot_record['wallet_address']
            txs = get_wallet_transactions(wallet, limit=50)
            for tx in txs:
                mark_signature_seen(tx.get('signature', ''), wallet)
                seed_count += 1
        print(f"✅ Seeded {seed_count} existing transactions")
    except Exception as e:
        print(f"⚠️ Seed error: {e}")
    
    # NOW START MONITORING
    while True:
        try:
            await check_wallet_monitoring()
            await check_payment_monitoring()
            await asyncio.sleep(60)
        except Exception as e:
            print(f"❌ Loop error: {e}")
            await asyncio.sleep(60)

if __name__ == "__main__":
    asyncio.run(monitoring_loop())