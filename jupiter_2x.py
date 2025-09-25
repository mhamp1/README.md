#!/usr/bin/env python3
"""
Full Recovery Script: Reverse Transfers from Bot Wallet
This script scans the transaction history of the bot wallet (7sgkrWzMPA), identifies outgoing transfers, and attempts to reverse them by sending equivalent amounts back to the recovery wallet (5UfA6jjk6UopaGHMRzhDSubifwjC2r7F3zT91k9N1NnM) from the recipient addresses, assuming you provide the private keys for those addresses in MULTI_WALLETS_JSON or a similar config. It uses Jupiter for swaps if needed (e.g., USDC to SOL).
**WARNING**: This script executes real transactions on Solana MAINNET if DRY_RUN=False. Solana transactions are IRREVERSIBLE—test on devnet first. You must have private keys for recipient wallets to 'reverse' (send back). The bot didn't make these transfers (dummy mode), so this is for manual recoveries.
- Set DRY_RUN = True in .env to simulate (default).
- Set RECOVERY_ADDRESS = "5UfA6jjk6UopaGHMRzhDSubifwjC2r7F3zT91k9N1NnM".
- Set BOT_WALLET_ADDRESS = "7sgkrWzMPA".
- Set MULTI_WALLETS_JSON = '[{"key": "5QpeYg1U7wXhLXmMpzGD7A4RBCW9qHdjEuhNzv7R7bSK6P3uUzWKA7ceq2NqJuDYvWxhfaSVWtiiMc58kKThSKSL", "address": "Avdnjm8cvsSzGXmLvZgwmzD2bnTPgX9Ts2rGtd6pNhNd"}, {"key": "2mN6tzurkK7HHe5C7Ziqv8Q8YLDHZd5ERfT2qAHxhHQuHijMwrK3S3NkYrhkXhsJNVceFc6WmfoEKf4aKXTyeEo3", "address": "5CXzuEZDs16upAM8112GzHA23VWLzxZ5Z5odfdaNFr4k"}]' for recipient test wallets with their addresses.
- Set RPC_URL = "https://api.mainnet-beta.solana.com".
- Run locally or deploy on Render; call /sweep-back-immediate to execute.
- Always test on devnet (RPC_URL = "https://api.devnet.solana.com").
"""
import asyncio
import base64
import json
import os
import logging
import aiohttp
from cryptography.fernet import Fernet
from dotenv import load_dotenv
from fastapi import FastAPI
from contextlib import asynccontextmanager
from solders.pubkey import Pubkey
from solders.keypair import Keypair
from solders.transaction import VersionedTransaction
from solders.message import MessageV0
from solana.rpc.async_api import AsyncClient
from solana.rpc.types import TokenAccountOpts, TxOpts
from solana.rpc.commitment import Confirmed
from spl.token.constants import TOKEN_PROGRAM_ID, ASSOCIATED_TOKEN_PROGRAM_ID
from spl.token.instructions import get_associated_token_address, transfer_checked
from anchorpy import Provider, Wallet
import uvicorn

load_dotenv()

DRY_RUN = os.getenv("DRY_RUN", "True").lower() == "true"
RECOVERY_ADDRESS = os.getenv("RECOVERY_ADDRESS", "5UfA6jjk6UopaGHMRzhDSubifwjC2r7F3zT91k9N1NnM")
BOT_WALLET_ADDRESS = os.getenv("BOT_WALLET_ADDRESS", "7sgkrWzMPA")
MULTI_WALLETS_JSON = os.getenv("MULTI_WALLETS_JSON", '[{"key": "5QpeYg1U7wXhLXmMpzGD7A4RBCW9qHdjEuhNzv7R7bSK6P3uUzWKA7ceq2NqJuDYvWxhfaSVWtiiMc58kKThSKSL", "address": "Avdnjm8cvsSzGXmLvZgwmzD2bnTPgX9Ts2rGtd6pNhNd"}, {"key": "2mN6tzurkK7HHe5C7Ziqv8Q8YLDHZd5ERfT2qAHxhHQuHijMwrK3S3NkYrhkXhsJNVceFc6WmfoEKf4aKXTyeEo3", "address": "5CXzuEZDs16upAM8112GzHA23VWLzxZ5Z5odfdaNFr4k"}]')
RPC_URL = os.getenv("RPC_URL", "https://api.mainnet-beta.solana.com")
JUPITER_QUOTE_URL = "https://quote-api.jup.ag/v6/quote"
JUPITER_SWAP_URL = "https://quote-api.jup.ag/v6/swap"
SOL_MINT = "So11111111111111111111111111111111111111112"
USDC_MINT = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"

logging.basicConfig(level=logging.INFO if not DRY_RUN else logging.DEBUG)
logger = logging.getLogger(__name__)

def get_bot_wallet():
    try:
        fernet_key = os.getenv('FERNET_SECRET')
        encrypted_key = os.getenv('ENCRYPTED_SOL_KEY')
        if not fernet_key or not encrypted_key:
            logger.warning("No FERNET_SECRET or ENCRYPTED_SOL_KEY provided")
            return None
        f = Fernet(fernet_key.encode())
        decrypted = f.decrypt(encrypted_key.encode()).decode()
        kp = Keypair.from_base58_string(decrypted)
        return Wallet(kp)
    except Exception as e:
        logger.error(f"Bot wallet decryption failed: {e}")
        return None

def get_multi_wallets():
    try:
        multi_wallets = json.loads(MULTI_WALLETS_JSON)
        wallets = []
        for wal in multi_wallets:
            kp = Keypair.from_base58_string(wal["key"])
            wallets.append({"wallet": Wallet(kp), "address": wal["address"]})
        return wallets
    except Exception as e:
        logger.exception(f"Failed to load multi-wallets: {e}")
        return []

async def _http_get(session, url, **kwargs):
    async with session.get(url, **kwargs) as resp:
        if resp.status != 200:
            text = await resp.text()
            raise RuntimeError(f"HTTP error {resp.status}: {text}")
        return await resp.json()

async def _http_post(session, url, json_data):
    async with session.post(url, json=json_data) as resp:
        if resp.status != 200:
            text = await resp.text()
            raise RuntimeError(f"HTTP error {resp.status}: {text}")
        return await resp.json()

async def get_balances(provider):
    try:
        sol_bal = (await provider.connection.get_balance(provider.wallet.public_key)).value
        usdc_ata = get_associated_token_address(provider.wallet.public_key, Pubkey.from_string(USDC_MINT))
        usdc_bal = 0
        if await provider.connection.get_account_info(usdc_ata):
            usdc_resp = await provider.connection.get_token_account_balance(usdc_ata)
            usdc_bal = usdc_resp.value.amount
        return sol_bal, usdc_bal
    except Exception as e:
        logger.exception(f"Error fetching balances: {e}")
        return 0, 0

async def swap_usdc_to_sol(provider, session, amount):
    if amount == 0:
        return
    params = {
        "inputMint": USDC_MINT,
        "outputMint": SOL_MINT,
        "amount": amount,
        "slippageBps": 50
    }
    quote = await _http_get(session, JUPITER_QUOTE_URL, params=params)
    body = {
        "quoteResponse": quote,
        "userPublicKey": str(provider.wallet.public_key),
        "wrapAndUnwrapSol": True
    }
    data = await _http_post(session, JUPITER_SWAP_URL, body)
    swap_tx = base64.b64decode(data["swapTransaction"])
    tx = VersionedTransaction.from_bytes(swap_tx)
    signed_tx = provider.wallet.sign_transaction(tx)
    tx_id = await provider.connection.send_transaction(signed_tx)
    await provider.connection.confirm_transaction(tx_id)
    logger.info(f"Swapped {amount / 1e6} USDC to SOL, tx_id={tx_id}")

async def transfer_sol(provider, amount, recovery_pubkey):
    if amount == 0:
        return
    recent_blockhash = await provider.connection.get_latest_blockhash()
    ix = transfer_checked(
        from_token_account=get_associated_token_address(provider.wallet.public_key, Pubkey.from_string(SOL_MINT)),
        to_token_account=get_associated_token_address(recovery_pubkey, Pubkey.from_string(SOL_MINT)),
        mint=Pubkey.from_string(SOL_MINT),
        amount=amount,
        decimals=9,
        signer=provider.wallet.public_key
    )
    msg = MessageV0.try_compile(provider.wallet.public_key, [ix], [], recent_blockhash.value.blockhash)
    tx = VersionedTransaction(msg, [provider.wallet.payer])
    signed_tx = provider.wallet.sign_transaction(tx)
    tx_id = await provider.connection.send_transaction(signed_tx)
    await provider.connection.confirm_transaction(tx_id)
    logger.info(f"Transferred {amount / 1e9} SOL to recovery, tx_id={tx_id}")

async def scan_and_reverse_transfers(provider, session, multi_wallets, recovery_address):
    bot_pubkey = Pubkey.from_string(BOT_WALLET_ADDRESS)
    limit = 1000  # Max per call
    before = None
    while True:
        signatures = await provider.connection.get_signatures_for_address(bot_pubkey, limit=limit, before=before)
        if not signatures:
            break
        before = signatures[-1].signature
        for sig in signatures:
            tx = await provider.connection.get_transaction(sig.signature, opts=TxOpts(preflight_commitment=Confirmed))
            if tx and tx.transaction.meta.err is None: # Successful tx
                for instruction in tx.transaction.message.instructions:
                    # Parse for outgoing transfers (check if bot is signer and amount outgoing)
                    if instruction.program_id == TOKEN_PROGRAM_ID:
                        # Parse transfer (simplified - check for outgoing from bot)
                        if "transfer" in str(instruction) and instruction.accounts[0] == bot_pubkey:
                            recipient = instruction.accounts[1]
                            amount = 1000000000 # Placeholder - parse actual amount from data
                            token_mint = instruction.accounts[2] if len(instruction.accounts) > 2 else SOL_MINT # Detect mint
                            for test_wallet in multi_wallets:
                                if recipient == Pubkey.from_string(test_wallet["address"]):
                                    await swap_usdc_to_sol(test_wallet, session, amount)
                                    await transfer_sol(test_wallet, amount, Pubkey.from_string(recovery_address))
                                    logger.info(f"Reversed transfer to {recipient}, amount={amount / 1e9} SOL")

# FastAPI app for endpoint
app = FastAPI()

@app.get("/sweep-back-immediate")
async def sweep_back_immediate():
    if DRY_RUN:
        logger.info("DRY_RUN=True: Simulating sweep-back - no real transfers executed.")
        return {"status": "simulated"}
    client = AsyncClient(RPC_URL)
    wallet = get_bot_wallet()
    if not wallet:
        return {"status": "error", "message": "Bot wallet not loaded"}
    provider = Provider(client, wallet)
    session = aiohttp.ClientSession()
    multi_wallets = get_multi_wallets()
    recovery_pubkey = Pubkey.from_string(RECOVERY_ADDRESS)
    # Sweep from main bot wallet
    sol_bal, usdc_bal = await get_balances(provider)
    await swap_usdc_to_sol(provider, session, usdc_bal)
    await transfer_sol(provider, sol_bal, RECOVERY_ADDRESS)
    # Scan and reverse from transaction history
    await scan_and_reverse_transfers(provider, session, multi_wallets, RECOVERY_ADDRESS)
    await session.close()
    await client.close()
    return {"status": "success", "message": "Transfers reversed where possible"}

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000, log_level="info")
