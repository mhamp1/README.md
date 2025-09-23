#!/usr/bin/env python3
"""
Unified jupiter_2x.py - Optimized 2025 Version (Enhanced V4)
Combines dashboard, metrics, visualizer, strategy agents, market fetch (Kraken-first), adapters,
DB, startup/init wiring and a persistent async run loop suitable to hand to an AI designing bot.
Adjust environment variables in .env and ensure required packages are installed.
**WARNING: THIS SCRIPT CAN EXECUTE REAL TRADES ON SOLANA MAINNET IF DRY_RUN=False, WHICH CAN RESULT IN FINANCIAL LOSS.**
- Set DRY_RUN = True in .env to log/simulate without submitting transactions (default: True for safety).
- Set ENCRYPTED_SOL_KEY = Fernet-encrypted base58 key (see instructions to generate).
- Set FERNET_SECRET = Fernet key for decryption.
- Set SWAP_AMOUNT_LAMPORTS = amount in lamports (e.g., 100000000 for 0.1 SOL).
- Set SWAP_AMOUNT_USDC = amount in USDC base units (e.g., 100000000 for 100 USDC).
- Trades if RSI < 30 (buy SOL with USDC) or >70 (sell SOL to USDC), with hybrid enhancements.
- Set ENABLE_ARB = True to enable arbitrage checks (default: False).
- Set ENABLE_LOOPING = True to enable yield looping (default: False).
- Set ENABLE_STOP_LOSS = True to enable post-tx stop-loss monitoring (default: False).
- Set ENABLE_ML = True to enable ML predictions for agent scores (default: False).
- Set ENABLE_SNIPING = True to enable Pump.fun sniping (default: False).
- Set ENABLE_FLASH = True to enable flash loans (default: False).
- Set ENABLE_MEV = True to enable MEV bundles (default: False).
- Set MULTI_WALLETS_JSON = '[{"key": "base58key1"}, {"key": "base58key2"}]' for multi-wallet scaling (default: None).
- Set ENABLE_COMPOUND = True to enable auto-compounding yields (default: False).
- Set REFERRAL_CODE = "your_referral" for farming referrals (default: None).
- Set RUG_THRESHOLD_DEV_HOLD = 0.2 for scam detection (dev hold >20% skips) (default: 0.2).
- Set DAILY_PNL_CAP = 0.1 for daily PNL cap (10% to prevent over-aggression) (default: 0.1).
- Set INITIAL_TRADE = True to force one initial buy trade on startup if USDC available (for testing; set to False after).
- Always test on devnet first by changing RPC_URL.
"""
import asyncio
import base58
import base64
import json
import os
import time
import logging
import aiohttp
import numpy as np
import random
import uuid
from concurrent.futures import ThreadPoolExecutor
from cryptography.fernet import Fernet
from dotenv import load_dotenv
from fastapi import FastAPI
from contextlib import asynccontextmanager, suppress
from solders.pubkey import Pubkey
from solders.keypair import Keypair
from solders.transaction import VersionedTransaction
from solders.message import MessageV0
from solana.rpc.async_api import AsyncClient
from solana.rpc.types import TokenAccountOpts, TxOpts
from solana.rpc.commitment import Confirmed
from anchorpy import Provider, Wallet, Program
from prometheus_fastapi_instrumentator import Instrumentator
from prometheus_client import Counter, Gauge, Histogram
from pathlib import Path
try:
    import pandas as pd
except ImportError:
    pd = None
try:
    from sklearn.linear_model import LinearRegression
except ImportError:
    LinearRegression = None
try:
    import websocket
except ImportError:
    websocket = None
try:
    import nest_asyncio
except ImportError:
    nest_asyncio = None
try:
    import uvicorn
except ImportError:
    uvicorn = None
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy import text
engine = create_async_engine('sqlite+aiosqlite:///trades.db')
AsyncSessionLocal = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
from spl.token.constants import TOKEN_PROGRAM_ID, ASSOCIATED_TOKEN_PROGRAM_ID
from spl.token.instructions import get_associated_token_address, create_associated_token_account
# Ensure log directory exists and safe logging setup
log_dir = "logs"
os.makedirs(log_dir, exist_ok=True)
try:
    handler = logging.FileHandler(os.path.join(log_dir, "bot.log"))
except FileNotFoundError:
    handler = logging.StreamHandler()
logger = logging.getLogger(__name__)
logger.addHandler(handler)
logger.setLevel(logging.INFO)
formatter = logging.Formatter("%(asctime)s | %(levelname)s | %(message)s")
handler.setFormatter(formatter)
# Load dotenv for secrets
load_dotenv()
if nest_asyncio:
    nest_asyncio.apply()
else:
    logger.warning("nest_asyncio not installed; some async features may fail.")
executor = ThreadPoolExecutor(max_workers=2)
# ConfigLoader for safe env parsing
class ConfigLoader:
    def __init__(self):
        self._env = os.environ
        self._config = {}
    def get_int(self, key: str, default: int) -> int:
        val = self._env.get(key, str(default))
        try:
            parsed = int(val)
        except (TypeError, ValueError):
            parsed = default
        self._config[key] = parsed
        return parsed
    def get_float(self, key: str, default: float) -> float:
        val = self._env.get(key, str(default))
        try:
            parsed = float(val)
        except (TypeError, ValueError):
            parsed = default
        self._config[key] = parsed
        return parsed
    def get_bool(self, key: str, default: bool) -> bool:
        val = self._env.get(key, str(default)).lower()
        parsed = val in ["true", "1", "yes"]
        self._config[key] = parsed
        return parsed
    def get_str(self, key: str, default: str) -> str:
        parsed = self._env.get(key, default)
        self._config[key] = parsed
        return parsed
    def override(self, key: str, value: str):
        self._env[key] = value
        self._config[key] = value
    def get_all(self):
        return self._config
config = ConfigLoader()
# Live config from config
DRY_RUN = config.get_bool("DRY_RUN", True)
ENCRYPTED_SOL_KEY = config.get_str("ENCRYPTED_SOL_KEY", "")
FERNET_SECRET = config.get_str("FERNET_SECRET", "")
SWAP_AMOUNT_LAMPORTS = config.get_int("SWAP_AMOUNT_LAMPORTS", 100000000)
SWAP_AMOUNT_USDC = config.get_int("SWAP_AMOUNT_USDC", 100000000)
ENABLE_ARB = config.get_bool("ENABLE_ARB", False)
ENABLE_LOOPING = config.get_bool("ENABLE_LOOPING", False)
ENABLE_STOP_LOSS = config.get_bool("ENABLE_STOP_LOSS", False)
ENABLE_ML = config.get_bool("ENABLE_ML", False)
ENABLE_SNIPING = config.get_bool("ENABLE_SNIPING", False)
ENABLE_FLASH = config.get_bool("ENABLE_FLASH", False)
ENABLE_MEV = config.get_bool("ENABLE_MEV", False)
MULTI_WALLETS_JSON = config.get_str("MULTI_WALLETS_JSON", None)
ENABLE_COMPOUND = config.get_bool("ENABLE_COMPOUND", False)
REFERRAL_CODE = config.get_str("REFERRAL_CODE", None)
RUG_THRESHOLD_DEV_HOLD = config.get_float("RUG_THRESHOLD_DEV_HOLD", 0.2)
DAILY_PNL_CAP = config.get_float("DAILY_PNL_CAP", 0.1)
WEBHOOK_URL = config.get_str("WEBHOOK_URL", None)
INITIAL_TRADE = config.get_bool("INITIAL_TRADE", False) # New: Force initial buy on startup if USDC available
SOL_MINT = "So11111111111111111111111111111111111111112"
USDC_MINT = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
RPC_URL = config.get_str("RPC_URL", "https://api.mainnet-beta.solana.com")
JUPITER_QUOTE_URL = "https://quote-api.jup.ag/v6/quote"
JUPITER_SWAP_URL = "https://quote-api.jup.ag/v6/swap"
PUMP_FUN_PROGRAM_ID = "6EF8rrecthR5Dkzon8Niu3k5kXwjoZ5wpsSUEsrm6fuh"
JITO_RPC = config.get_str("JITO_RPC", "https://mainnet.rpc.jito.wtf")
# Program IDs (from searches/docs)
MARGINFI_PROGRAM_ID = "MFv2hWf31Z9kbCa1snEPYctw99gKtYrQzPRzDENCc9Am"
KAMINO_PROGRAM_ID = "KLend2g3cP87fffoy8q1mQqGKjrxjC8qeVv1DB6usxs"
JITO_PROGRAM_ID = "Jito4APyf642JPZFDaL6gJGLuGdJXrvq7y9WJ3fWZUH"
ORCA_PROGRAM_ID = "whirLbMiicVdio4qvUfM5KAg6Ct8VwpYzGff3uctyCc"
PHOENIX_PROGRAM_ID = "PhoeniX1DtafNJmF1gmQcr7A27eW4VnffLtuEdPhY8"
RAYDIUM_PROGRAM_ID = "675kPX9MHTjS2zt1qfr1NYHuzeLXfQM9H24wFSUt1Mp8"
# Metrics (extended)
tx_success_total = Counter("tx_success_total", "Total successful transactions")
tx_failure_total = Counter("tx_failure_total", "Total failed transactions")
tx_latency_seconds = Histogram("tx_latency_seconds", "Transaction latency in seconds")
model_mse = Gauge("model_mse", "Mean squared error of ML predictions")
model_confidence = Gauge("model_confidence", "Confidence score of current model prediction")
agent_score_gauge = Gauge("agent_score", "Score of strategy agents", ["agent_id"])
strategy_action_distribution = Gauge("strategy_action_distribution", "Normalized action weights", ["action"])
rpc_error_total = Counter("rpc_error_total", "Total RPC errors")
retry_attempts_total = Counter("retry_attempts_total", "Total retry attempts")
circuit_breaker_triggered = Counter("circuit_breaker_triggered", "Circuit breaker activations")
mutation_events_total = Counter("mutation_events_total", "Total strategy mutations")
agent_evolution_depth = Gauge("agent_evolution_depth", "Depth of strategy evolution tree")
market_fallbacks_total = Counter("market_fallbacks_total", "Number of market provider fallbacks")
arb_opportunities_total = Counter("arb_opportunities_total", "Total arbitrage opportunities detected")
loop_executions_total = Counter("loop_executions_total", "Total yield looping executions")
lp_additions_total = Counter("lp_additions_total", "Total liquidity provision additions")
stop_loss_triggers_total = Counter("stop_loss_triggers_total", "Total stop-loss triggers")
run_loop_tick_duration_seconds = Histogram("run_loop_tick_duration_seconds", "Run loop tick duration in seconds")
snipe_executions_total = Counter("snipe_executions_total", "Total snipes executed")
flash_loans_total = Counter("flash_loans_total", "Total flash loans executed")
def record_strategy_distribution(strategy: dict):
    for action, weight in strategy.items():
        strategy_action_distribution.labels(action=action).set(weight)
def record_agent_scores(agent_pool):
    for agent in agent_pool:
        agent_score_gauge.labels(agent_id=agent.name).set(agent.score)
def record_model_metrics(prediction, actual):
    error = (prediction - actual) ** 2
    model_mse.set(error)
    model_confidence.set(1 / (1 + error))
def record_tx_latency(start_time):
    latency = time.time() - start_time
    tx_latency_seconds.observe(latency)
# visualizer.py integration
LOG_PATH = Path("logs/evolution.jsonl")
LOG_PATH.parent.mkdir(exist_ok=True)
def log_mutation(agent_id, parent_id, mutation, score, depth):
    event = {
        "timestamp": time.time(),
        "agent_id": agent_id,
        "parent_id": parent_id,
        "mutation": mutation,
        "score": score,
        "depth": depth,
        "uuid": str(uuid.uuid4())
    }
    with open(LOG_PATH, "a") as f:
        f.write(json.dumps(event) + "\n")
    logger.info(f"Logged mutation: {event}")
    mutation_events_total.inc()
    agent_evolution_depth.set(depth)
# Helper utilities
def normalize(d: dict) -> dict:
    total = sum(d.values())
    return {k: v / total for k, v in d.items()} if total > 0 else d
# Wallet decryption
def get_wallet():
    try:
        from cryptography.fernet import Fernet, InvalidToken
        import os
        fernet_key = os.getenv('FERNET_SECRET')
        encrypted_key = os.getenv('ENCRYPTED_SOL_KEY')
        if not fernet_key or not encrypted_key:
            logger.warning("No FERNET_SECRET or ENCRYPTED_SOL_KEY provided")
            return None
        logger.info(f"FERNET_SECRET loaded: {fernet_key}")
        logger.info(f"ENCRYPTED_SOL_KEY loaded: {encrypted_key}")
        logger.info(f"Key length: {len(encrypted_key)}, Secret length: {len(fernet_key)}")
        f = Fernet(fernet_key.encode())
        decrypted = f.decrypt(encrypted_key.encode()).decode()
        logger.info("Decryption successful")
        kp = Keypair.from_base58_string(decrypted)
        logger.info(f"Bot wallet address from decryption: {str(kp.pubkey())}")
        return Wallet(kp)
    except InvalidToken:
        logger.error("Decryption failed: Invalid token (bad key or corrupted encrypted data)")
        return None
    except Exception as e:
        logger.error(f"Decryption failed with error: {str(e)}")
        return None
# Multi-wallet support
multi_wallets = []
if MULTI_WALLETS_JSON:
    try:
        multi_wallets = json.loads(MULTI_WALLETS_JSON)
    except Exception as e:
        logger.exception(f"Failed to parse MULTI_WALLETS_JSON: {e}")
def get_multi_wallets():
    wallets = []
    for wal in multi_wallets:
        try:
            kp = Keypair.from_base58(wal["key"])
            wallets.append(Wallet(kp))
        except Exception as e:
            logger.exception(f"Failed to load multi-wallet key: {e}")
    return wallets
# StrategyAgent canonical definition (extended for volume)
class StrategyAgent:
    def __init__(self, name, strategy_fn, species="base", parent_id=None, depth=0):
        self.name = name
        self.strategy_fn = strategy_fn
        self.species = species
        self.score = 0.0
        self.memory = [] # (state, pnl)
        self.age = 0
        self.parent_id = parent_id
        self.depth = depth
    def evaluate(self, yields, rsi, volatility, macd, signal, volume_rising):
        try:
            return self.strategy_fn(yields, rsi, volatility, macd, signal, volume_rising)
        except Exception as e:
            logger.warning(f"Agent {self.name} strategy error: {e}")
            return {}
    def mutate(self, mutation_rate=0.1):
        mutation = random.choice(["swap_bias++", "lend_bias--", "risk_tolerance++"])
        def mutated_strategy(yields, rsi, volatility, macd,
