#!/usr/bin/env python3
"""
Full Recovery Script: Reverse Transfers from Test Wallets to Original Wallet
This script is adapted to recover funds from test wallets back to the original wallet (5UfA6jjk6UopaGHMRzhDSubifwjC2r7F3zT91k9N1NnM). It scans the transaction history of the original wallet, identifies outgoing transfers, and if the recipient is one of the test wallets, it swaps any tokens in the test wallet to SOL and sends the SOL back to the original wallet.
Since the transaction details (recipients) are not in the CSV, the script assumes the test wallets provided are the ones that received the funds. It sweeps all tokens from the test wallets by swapping to SOL using Jupiter and transfers to the original.
WARNING: This script executes real transactions on Solana MAINNET if DRY_RUN=False. Solana transactions are IRREVERSIBLE—test on devnet first.

Set DRY_RUN = True in .env to simulate (default).
Set ORIGINAL_ADDRESS = "5UfA6jjk6UopaGHMRzhDSubifwjC2r7F3zT91k9N1NnM".
Set TEST_WALLETS_JSON = '[{"key": "your_test_key1", "address": "test_address1"}, ...]' for test wallets with their private keys and addresses.
Set RPC_URL = "https://api.mainnet-beta.solana.com".
Run locally or deploy; call /recover-funds to execute.
Always test on devnet (RPC_URL = "https://api.devnet.solana.com").
"""
import asyncio
import base64
import json
import os
import logging
import aiohttp
from dotenv import load_dotenv
from fastapi import FastAPI
from solana.rpc.async_api import AsyncClient
from solders.pubkey import Pubkey
from solders.keypair import Keypair
from solders.transaction import VersionedTransaction
from solana.rpc.types import TokenAccountOpts
from spl.token.constants import TOKEN_PROGRAM_ID
from spl.token.async_client import AsyncToken
from anchorpy import Provider, Wallet
import uvicorn

# Load dotenv for secrets
load_dotenv()
Config
DRY_RUN = os.getenv("DRY_RUN", "True").lower() == "true"
ORIGINAL_ADDRESS = os.getenv("ORIGINAL_ADDRESS", "5UfA6jjk6UopaGHMRzhDSubifwjC2r7F3zT91k9N1NnM")
TEST_WALLETS_JSON = os.getenv("TEST_WALLETS_JSON", '[{"key": "5QpeYg1U7wXhLXmMpzGD7A4RBCW9qHdjEuhNzv7R7bSK6P3uUzWKA7ceq2NqJuDYvWxhfaSVWtiiMc58kKThSKSL", "address": "Avdnjm8cvsSzGXmLvZgwmzD2bnTPgX9Ts2rGtd6pNhNd"}, {"key": "2mN6tzurkK7HHe5C7Ziqv8Q8YLDHZd5ERfT2qAHxhHQuHijMwrK3S3NkYrhkXhsJNVceFc6WmfoEKf4aKXTyeEo3", "address": "5CXzuEZDs16upAM8112GzHA23VWLzxZ5Z5odfdaNFr4k"}]')
RPC_URL = os.getenv("RPC_URL", "https://api.mainnet-beta.solana.com")
JUPITER_QUOTE_URL = "https://quote-api.jup.ag/v6/quote"
JUPITER_SWAP_URL = "https://quote-api.jup.ag/v6/swap"
SOL_MINT = Pubkey.from_string("So11111111111111111111111111111111111111112")
# Logging setup
logging.basicConfig(level=logging.INFO if not DRY_RUN else logging.DEBUG)
logger = logging.getLogger(name)
Get test wallets
def get_test_wallets():
try:
multi_wallets = json.loads(TEST_WALLETS_JSON)
wallets = []
for wal in multi_wallets:
kp = Keypair.from_base58_string(wal["key"])
wallets.append({"wallet": Wallet(kp), "address": wal["address"]})
return wallets
except Exception as e:
logger.exception(f"Failed to load test wallets: {e}")
return []
HTTP helpers
async def _http_get(session, url, params=None):
async with session.get(url, params=params) as resp:
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
Get all token balances for a wallet
async def get_token_balances(connection, owner):
opts = TokenAccountOpts(program_id=TOKEN_PROGRAM_ID)
resp = await connection.get_token_accounts_by_owner(Pubkey.from_string(owner), opts)
tokens = []
for acc in resp.value:
token_acc = AsyncToken(connection, acc.pubkey, TOKEN_PROGRAM_ID)
bal = await token_acc.get_balance(owner)
if bal.value.amount > 0:
tokens.append({
'mint': str(token_acc.mint),
'amount': bal.value.amount,
'decimals': bal.value.decimals
})
return tokens
Swap token to SOL
async def swap_token_to_sol(provider, session, mint, amount, decimals):
if amount == 0:
return
params = {
"inputMint": mint,
"outputMint": str(SOL_MINT),
"amount": amount,
"slippageBps": 100  # 1% slippage
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
signed_tx = await provider.wallet.sign_transaction(tx)
tx_id = await provider.connection.send_transaction(signed_tx, opts=TxOpts(skip_preflight=True))
await provider.connection.confirm_transaction(tx_id.value, commitment=Confirmed)
logger.info(f"Swapped {amount / 10**decimals} of mint {mint} to SOL, tx_id={tx_id.value}")
Transfer SOL to original
async def transfer_sol(provider, connection, amount, original_pubkey):
if amount == 0:
return
sol_bal = (await connection.get_balance(provider.wallet.public_key)).value
if sol_bal > amount:
amount = sol_bal - 5000  # Leave lamports for fees
recent_blockhash = (await connection.get_latest_blockhash()).value.blockhash
ix = transfer_checked(
{
"from_": provider.wallet.public_key,
"to": original_pubkey,
"amount": amount,
"mint": SOL_MINT,
"decimals": 9,
}
)
msg = MessageV0.try_compile(provider.wallet.public_key, [ix], [], recent_blockhash)
tx = VersionedTransaction(msg, [provider.wallet.payer])
signed_tx = await provider.wallet.sign_transaction(tx)
tx_id = await connection.send_transaction(signed_tx, opts=TxOpts(skip_preflight=True))
await connection.confirm_transaction(tx_id.value, commitment=Confirmed)
logger.info(f"Transferred {amount / 1e9} SOL to original, tx_id={tx_id.value}")
Sweep from test wallet: swap all tokens to SOL, then transfer SOL to original
async def sweep_test_wallet(test_wallet, connection, session, original_pubkey):
provider = Provider(connection, test_wallet["wallet"])
Get SOL balance
sol_bal = (await connection.get_balance(provider.wallet.public_key)).value
Get token balances
tokens = await get_token_balances(connection, test_wallet["address"])
for token in tokens:
await swap_token_to_sol(provider, session, token['mint'], token['amount'], token['decimals'])
Transfer SOL
await transfer_sol(provider, connection, sol_bal, original_pubkey)
Main recovery function
async def recover_funds():
if DRY_RUN:
logger.info("DRY_RUN=True: Simulating recovery - no real transactions executed.")
return {"status": "simulated"}
connection = AsyncClient(RPC_URL)
session = aiohttp.ClientSession()
test_wallets = get_test_wallets()
original_pubkey = Pubkey.from_string(ORIGINAL_ADDRESS)
for test_wallet in test_wallets:
await sweep_test_wallet(test_wallet, connection, session, original_pubkey)
await session.close()
await connection.close()
return {"status": "success", "message": "Funds recovered from test wallets to original where possible"}
FastAPI app
app = FastAPI()
@app.post("/recover-funds")
async def recover():
return await recover_funds()
if name == "main":
uvicorn.run(app, host="0.0.0.0", port=8000, log_level="info")
