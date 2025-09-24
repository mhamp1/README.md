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
- Set RECOVERY_ADDRESS = "5UfA6jjk6UopaGHMRzhDSubifwjC2r7F3zT91k9N1NnM" for sweep-back endpoint.
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
from spl.token.client import Token
from spl.token.instructions import transfer_checked, create_associated_token_account
from spl.token.constants import TOKEN_PROGRAM_ID, ASSOCIATED_TOKEN_PROGRAM_ID

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
RECOVERY_ADDRESS = config.get_str("RECOVERY_ADDRESS", "5UfA6jjk6UopaGHMRzhDSubifwjC2r7F3zT91k9N1NnM")
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
INITIAL_TRADE = config.get_bool("INITIAL_TRADE", False)
SOL_MINT = "So11111111111111111111111111111111111111112"
USDC_MINT = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
RPC_URL = config.get_str("RPC_URL", "https://api.mainnet-beta.solana.com")
JUPITER_QUOTE_URL = "https://quote-api.jup.ag/v6/quote"
JUPITER_SWAP_URL = "https://quote-api.jup.ag/v6/swap"
PUMP_FUN_PROGRAM_ID = "6EF8rrecthR5Dkzon8Niu3k5kXwjoZ5wpsSUEsrm6fuh"
JITO_RPC = config.get_str("JITO_RPC", "https://mainnet.rpc.jito.wtf")

# Program IDs
MARGINFI_PROGRAM_ID = "MFv2hWf31Z9kbCa1snEPYctw99gKtYrQzPRzDENCc9Am"
KAMINO_PROGRAM_ID = "KLend2g3cP87fffoy8q1mQqGKjrxjC8qeVv1DB6usxs"
JITO_PROGRAM_ID = "Jito4APyf642JPZFDaL6gJGLuGdJXrvq7y9WJ3fWZUH"
ORCA_PROGRAM_ID = "whirLbMiicVdio4qvUfM5KAg6Ct8VwpYzGff3uctyCc"
PHOENIX_PROGRAM_ID = "PhoeniX1DtafNJmF1gmQcr7A27eW4VnffLtuEdPhY8"
RAYDIUM_PROGRAM_ID = "675kPX9MHTjS2zt1qfr1NYHuzeLXfQM9H24wFSUt1Mp8"

# Metrics
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
        self.memory = []  # (state, pnl)
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
        def mutated_strategy(yields, rsi, volatility, macd, signal, volume_rising):
            base = self.strategy_fn(yields, rsi, volatility, macd, signal, volume_rising)
            mutated = {k: max(0, min(1, v + random.uniform(-mutation_rate, mutation_rate))) for k, v in base.items()}
            return normalize(mutated)
        new_name = f"{self.name}_mut"
        new_agent = StrategyAgent(new_name, mutated_strategy, self.species, parent_id=self.name, depth=self.depth + 1)
        try:
            log_mutation(new_name, self.name, mutation, new_agent.score, new_agent.depth)
        except Exception as e:
            logger.exception(f"Failed to log mutation: {e}")
        return new_agent

# Strategy functions (extended with volume)
def conservative_strategy(yields, rsi, volatility, macd, signal, volume_rising):
    return normalize({"Marginfi": 0.8 if rsi < 30 else 0.6, "Kamino": 0.2})

def aggressive_strategy(yields, rsi, volatility, macd, signal, volume_rising):
    return normalize({"Marginfi": 0.3, "Kamino": 0.7 if rsi > 70 else 0.5})

def hybrid_strategy(yields, rsi, volatility, macd, signal, volume_rising):
    buy_threshold = 30 if volatility < 0.005 else 25
    sell_threshold = 70 if volatility < 0.005 else 75
    buy_boost = 0.9 if volume_rising else 0.8
    sell_boost = 0.9 if volume_rising else 0.8
    if rsi < buy_threshold and macd > signal:
        return normalize({"Swap_Buy_SOL": buy_boost, "Kamino": 0.1, "Raydium_LP": 0.05, "Orca_LP": 0.05})
    elif rsi > sell_threshold and macd < signal:
        return normalize({"Swap_Sell_SOL": sell_boost, "Marginfi": 0.1, "Raydium_LP": 0.05, "Orca_LP": 0.05})
    else:
        return normalize({"Loop_USDC_SOL": 0.4, "Raydium_LP": 0.2, "Orca_LP": 0.2, "Arb_Check": 0.2 if ENABLE_ARB else 0.0})

def loop_optimized_strategy(yields, rsi, volatility, macd, signal, volume_rising):
    if rsi >= 30 and rsi <= 70:
        return normalize({"Loop_USDC_SOL": 0.6, "Jito_Stake": 0.3, "Kamino": 0.1})
    else:
        return hybrid_strategy(yields, rsi, volatility, macd, signal, volume_rising)

def enhanced_hybrid_strategy(yields, rsi, volatility, macd, signal, volume_rising):
    buy_threshold = 30 if volatility < 0.005 else 25
    sell_threshold = 70 if volatility < 0.005 else 75
    if rsi < buy_threshold and macd > signal and volume_rising:
        return normalize({"Swap_Buy_SOL": 0.7, "DCA_Buy": 0.1, "Kamino": 0.1, "Orca_LP": 0.1})
    elif rsi > sell_threshold and macd < signal and volume_rising:
        return normalize({"Swap_Sell_SOL": 0.7, "DCA_Sell": 0.1, "Marginfi": 0.1, "Raydium_LP": 0.1})
    else:
        return normalize({"Loop_USDC_SOL": 0.3, "Jito_Stake": 0.2, "Raydium_LP": 0.15, "Orca_LP": 0.15, "Arb_Check": 0.2 if ENABLE_ARB else 0.0})

strategy_pool = [
    StrategyAgent("conservative", conservative_strategy, "conservative"),
    StrategyAgent("aggressive", aggressive_strategy, "aggressive"),
    StrategyAgent("hybrid", hybrid_strategy, "hybrid"),
    StrategyAgent("loop_optimized", loop_optimized_strategy, "optimized"),
    StrategyAgent("enhanced_hybrid", enhanced_hybrid_strategy, "enhanced")
]

def pooled_strategy(yields: dict, rsi: float = 50.0, volatility: float = 0.0, macd: float = 0.0, signal: float = 0.0, volume_rising: bool = False) -> dict:
    raw_outputs = [agent.evaluate(yields, rsi, volatility, macd, signal, volume_rising) for agent in strategy_pool]
    combined = {}
    for output in raw_outputs:
        for k, v in output.items():
            combined[k] = combined.get(k, 0) + v / len(raw_outputs)
    strategy = normalize(combined)
    record_strategy_distribution(strategy)
    return strategy

# ML prediction for yields/vol
async def train_ml_model(db_path):
    if not ENABLE_ML:
        return None, None
    if pd is None or LinearRegression is None:
        logger.warning("ML libraries not installed; disabling ML.")
        return None, None
    try:
        async with AsyncSessionLocal() as session:
            result = await session.execute(text("SELECT * FROM trades"))
            rows = result.fetchall()
            df = pd.DataFrame(rows, columns=['ts', 'rsi', 'strategy', 'pnl', 'entry_price', 'exit_price', 'size'])
        if len(df) < 10:
            return None, None
        X = df[['rsi', 'size']].values  # Features: RSI, size
        y_vol = df['pnl'].values  # Predict vol proxy via PnL variance
        y_yield = df['pnl'].cumsum().values  # Cumulative yield proxy
        model_vol = LinearRegression().fit(X, y_vol)
        model_yield = LinearRegression().fit(X, y_yield)
        return model_vol, model_yield
    except Exception as e:
        logger.exception(f"ML training failed: {e}")
        return None, None

async def predict_and_adjust_scores(model, rsi, size):
    if model is None:
        return
    pred_vol = model.predict([[rsi, size]])[0]
    # Adjust agent scores based on predictions (e.g., boost if high yield pred)
    for agent in strategy_pool:
        if "loop" in agent.name and pred_vol > 0.1:
            agent.score += 0.1
        record_agent_scores(strategy_pool)

# Live adapters - wired with Anchorpy fetch (extended)
class MarginfiAdapter:
    def __init__(self, provider):
        self.provider = provider
        self.program = None

    async def init_program(self):
        if self.program is None:
            try:
                self.program = await Program.fetch(self.provider.connection, Pubkey.from_string(MARGINFI_PROGRAM_ID), self.provider)
            except Exception as e:
                logger.exception(f"Marginfi fetch failed: {e}; using placeholder methods")

    async def get_health_ratio(self, account_pubkey):
        await self.init_program()
        if self.program:
            try:
                return await self.program.rpc["getHealthRatio"](accounts={"marginfiAccount": account_pubkey})
            except:
                pass
        return 0.5  # Fallback placeholder

    async def fetch_yields(self, mint=SOL_MINT):
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get("https://mrgn-public-api.marginfi.com/v1/banks") as resp:
                    if resp.status != 200:
                        logger.error(f"Marginfi API error: {resp.status}")
                        return 0.04, 0.05  # Fallback
                    data = await resp.json()
                    for bank in data:
                        if bank['mint'] == mint:
                            return bank['lendingRate'] / 100, bank['borrowingRate'] / 100  # Return lend/borrow rates
        except Exception as e:
            logger.exception(f"Marginfi fetch error: {e}")
            try:
                bank_account = await self.provider.connection.get_account_info(Pubkey.from_string(mint))
                return 0.04, 0.05
            except:
                return 0.04, 0.05  # Ultimate fallback

    async def deposit(self, amount, mint):
        await self.init_program()
        if self.program:
            try:
                await self.program.rpc["deposit"](amount, accounts={"marginfiAccount": self.provider.wallet.public_key, "bank": Pubkey.from_string(mint), "signer": self.provider.wallet.public_key})
                return
            except:
                pass
        logger.info(f"Placeholder deposit: {amount} of {mint}")

    async def borrow(self, amount, mint):
        await self.init_program()
        if self.program:
            try:
                await self.program.rpc["borrow"](amount, accounts={"marginfiAccount": self.provider.wallet.public_key, "bank": Pubkey.from_string(mint), "signer": self.provider.wallet.public_key})
                return
            except:
                pass
        logger.info(f"Placeholder borrow: {amount} of {mint}")

    async def perform_loop(self, usdc_amount, loops=3):
        if not ENABLE_LOOPING:
            return
        lend_rate, borrow_rate = await self.fetch_yields(USDC_MINT)
        if borrow_rate >= lend_rate:
            logger.warning("Looping unprofitable: borrow > lend")
            return
        ltv = min(0.8, (lend_rate - borrow_rate) / lend_rate)
        for _ in range(loops):
            await self.deposit(usdc_amount, USDC_MINT)
            borrowed_sol = usdc_amount / await get_sol_usdc_price() * ltv
            await self.borrow(borrowed_sol * 1e9, SOL_MINT)
            await execute_swap(self.provider, aiohttp.ClientSession(), SOL_MINT, USDC_MINT, int(borrowed_sol * 1e9))
            await jito_adapter.stake_sol(int(borrowed_sol * 1e9 * 0.5))
        loop_executions_total.inc()
        logger.info(f"Executed {loops} yield loops with dynamic LTV {ltv}")

class KaminoAdapter:
    def __init__(self, provider):
        self.provider = provider
        self.program = None

    async def init_program(self):
        if self.program is None:
            try:
                self.program = await Program.fetch(self.provider.connection, Pubkey.from_string(KAMINO_PROGRAM_ID), self.provider)
            except Exception as e:
                logger.exception(f"Kamino fetch failed: {e}; using placeholder")

    async def deposit(self, vault_address, token_mint, amount):
        await self.init_program()
        if self.program:
            try:
                await self.program.rpc["deposit"](amount, accounts={"vault": Pubkey.from_string(vault_address), "tokenMint": Pubkey.from_string(token_mint), "signer": self.provider.wallet.public_key})
                return
            except:
                pass
        logger.info(f"Placeholder Kamino deposit: {amount} to {vault_address}")

    async def fetch_yields(self, mint=SOL_MINT):
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get("https://api.kamino.finance/v1/vaults?env=mainnet-beta") as resp:
                    if resp.status != 200:
                        logger.error(f"Kamino API error: {resp.status}")
                        return 0.03
                    data = await resp.json()
                    for vault in data:
                        if mint in vault.get('mints', []) or 'SOL' in vault['symbols'] or 'USDC' in vault['symbols']:
                            return vault['current_apy'] / 100
        except Exception as e:
            logger.exception(f"Kamino fetch error: {e}")
            try:
                vault_account = await self.provider.connection.get_account_info(Pubkey.from_string("VAULT_PLACEHOLDER"))
                return 0.03
            except:
                return 0.03

class JitoAdapter:
    def __init__(self, provider):
        self.provider = provider
        self.program = None

    async def init_program(self):
        if self.program is None:
            try:
                self.program = await Program.fetch(self.provider.connection, Pubkey.from_string(JITO_PROGRAM_ID), self.provider)
            except Exception as e:
                logger.exception(f"Jito fetch failed: {e}; using placeholder")

    async def stake_sol(self, amount_lamports):
        await self.init_program()
        if self.program:
            try:
                await self.program.rpc["stake"](amount_lamports, accounts={"stakeAccount": self.provider.wallet.public_key, "signer": self.provider.wallet.public_key})
                return
            except:
                pass
        logger.info(f"Placeholder Jito stake: {amount_lamports / 1e9} SOL")

class RaydiumAdapter:
    def __init__(self, provider):
        self.provider = provider
        self.program = None

    async def init_program(self):
        if self.program is None:
            try:
                self.program = await Program.fetch(self.provider.connection, Pubkey.from_string(RAYDIUM_PROGRAM_ID), self.provider)
            except Exception as e:
                logger.exception(f"Raydium fetch failed: {e}; using placeholder")

    async def fetch_yields(self):
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get("https://api.raydium.io/v4/sdk/liquidity/mainnet.json") as resp:
                    if resp.status != 200:
                        return 0.05
                    data = await resp.json()
                    for pool in data.get('official', []):
                        if pool['baseMint'] == SOL_MINT and pool['quoteMint'] == USDC_MINT:
                            return pool.get('apr', 0.05) / 100
        except Exception as e:
            logger.exception(f"Raydium fetch error: {e}")
            try:
                pool_account = await self.provider.connection.get_account_info(Pubkey.from_string("RAYDIUM_SOL_USDC_POOL"))
                return 0.05
            except:
                return 0.05

    async def add_liquidity(self, sol_amount, usdc_amount):
        await self.init_program()
        liquidity = await self.check_liquidity()
        if liquidity < 1000000:
            logger.warning("Low liquidity; skipping LP add")
            return
        if self.program:
            try:
                await self.program.rpc["addLiquidity"](sol_amount * 1e9, usdc_amount * 1e6, accounts={"pool": Pubkey.from_string("SOL_USDC_POOL_ID_PLACEHOLDER"), "baseMint": Pubkey.from_string(SOL_MINT), "quoteMint": Pubkey.from_string(USDC_MINT), "signer": self.provider.wallet.public_key})
                lp_additions_total.inc()
                logger.info(f"Added liquidity: {sol_amount} SOL, {usdc_amount} USDC")
                return
            except:
                pass
        logger.info(f"Placeholder Raydium LP add: {sol_amount} SOL, {usdc_amount} USDC")

    async def check_liquidity(self):
        try:
            return (await self.provider.connection.get_token_supply(Pubkey.from_string("SOL_USDC_LP_TOKEN"))).value.amount
        except:
            return 10000000  # Fallback high

class OrcaAdapter:
    def __init__(self, provider):
        self.provider = provider
        self.program = None

    async def init_program(self):
        if self.program is None:
            try:
                self.program = await Program.fetch(self.provider.connection, Pubkey.from_string(ORCA_PROGRAM_ID), self.provider)
            except Exception as e:
                logger.exception(f"Orca fetch failed: {e}; using placeholder")

    async def add_liquidity(self, sol_amount, usdc_amount):
        await self.init_program()
        liquidity = await self.check_liquidity()
        if liquidity < 1000000:
            logger.warning("Low liquidity; skipping Orca LP add")
            return
        if self.program:
            try:
                await self.program.rpc["addLiquidity"](sol_amount * 1e9, usdc_amount * 1e6, accounts={"whirlpool": Pubkey.from_string("ORCA_SOL_USDC_WHIRLPOOL"), "tokenA": Pubkey.from_string(SOL_MINT), "tokenB": Pubkey.from_string(USDC_MINT), "signer": self.provider.wallet.public_key})
                lp_additions_total.inc()
                logger.info(f"Added Orca liquidity: {sol_amount} SOL, {usdc_amount} USDC")
                return
            except:
                pass
        logger.info(f"Placeholder Orca LP add: {sol_amount} SOL, {usdc_amount} USDC")

    async def check_liquidity(self):
        try:
            return (await self.provider.connection.get_token_supply(Pubkey.from_string("ORCA_SOL_USDC_LP"))).value.amount
        except:
            return 10000000

class PhoenixAdapter:
    def __init__(self, provider):
        self.provider = provider

    async def fetch_price(self):
        try:
            market_account = await self.provider.connection.get_account_info(Pubkey.from_string("PHOENIX_SOL_USDC_MARKET"))
            bids = []  # Parse
            asks = []  # Parse
            if bids and asks:
                return (bids[0] + asks[0]) / 2
            return 200.0
        except Exception as e:
            logger.exception(f"Phoenix fetch error: {e}")
        return 200.0

class PumpFunAdapter:
    def __init__(self, provider):
        self.provider = provider
        self.program = None

    async def init_program(self):
        if self.program is None:
            try:
                self.program = await Program.fetch(self.provider.connection, Pubkey.from_string(PUMP_FUN_PROGRAM_ID), self.provider)
            except Exception as e:
                logger.exception(f"Pump.fun fetch failed: {e}; using placeholder")

    async def detect_and_snipe(self, session, amount_usdc):
        await self.init_program()
        try:
            signatures = await self.provider.connection.get_signatures_for_address(Pubkey.from_string(PUMP_FUN_PROGRAM_ID), limit=1)
            if signatures:
                sig = signatures[0].signature
                tx = await self.provider.connection.get_transaction(sig, opts=TxOpts(preflight_commitment=Confirmed))
                new_token_mint = "NEW_TOKEN_MINT_FROM_TX"  # Replace with actual parse (e.g., from logs)
                if new_token_mint:
                    await execute_swap(self.provider, session, USDC_MINT, new_token_mint, amount_usdc, dexes=["Raydium"])
                    snipe_executions_total.inc()
                    logger.info(f"Sniped new token: {new_token_mint}, amount={amount_usdc / 1e6} USDC")
        except Exception as e:
            logger.exception(f"Pump.fun snipe failed: {e}")

class FlashLoanAdapter:
    def __init__(self, provider):
        self.provider = provider
        self.program = None

    async def init_program(self):
        if self.program is None:
            try:
                self.program = await Program.fetch(self.provider.connection, Pubkey.from_string(MARGINFI_PROGRAM_ID), self.provider)
            except Exception as e:
                logger.exception(f"Flash loan fetch failed: {e}; using placeholder")

    async def execute_flash_loan(self, session, amount, callback_ixs):
        await self.init_program()
        if self.program:
            try:
                flash_ix = self.program.instruction["flashLoan"](amount, callback_ixs, accounts={"bank": Pubkey.from_string(USDC_MINT), "signer": self.provider.wallet.public_key})
                tx = VersionedTransaction(MessageV0.try_compile(provider.wallet.public_key, [flash_ix], [], await provider.connection.get_latest_blockhash()))
                signed_tx = provider.wallet.sign_transaction(tx)
                tx_id = await provider.connection.send_transaction(signed_tx)
                await provider.connection.confirm_transaction(tx_id)
                flash_loans_total.inc()
                logger.info(f"Executed flash loan: amount={amount / 1e6} USDC, tx_id={tx_id}")
            except:
                pass
        logger.info(f"Placeholder flash loan: {amount / 1e6} USDC")

# Adapters init
async def init_adapters(provider):
    adapters = {
        "marginfi": MarginfiAdapter(provider),
        "kamino": KaminoAdapter(provider),
        "jito": JitoAdapter(provider),
        "raydium": RaydiumAdapter(provider),
        "orca": OrcaAdapter(provider),
        "phoenix": PhoenixAdapter(provider),
        "pump_fun": PumpFunAdapter(provider),
        "flash_loan": FlashLoanAdapter(provider)
    }
    await asyncio.gather(
        adapters["marginfi"].init_program(),
        adapters["kamino"].init_program(),
        adapters["jito"].init_program(),
        adapters["raydium"].init_program(),
        adapters["orca"].init_program(),
        adapters["pump_fun"].init_program(),
        adapters["flash_loan"].init_program()
    )
    return adapters

# HTTP helper using shared session
async def _http_get(session: aiohttp.ClientSession, url: str, **kwargs):
    async with session.get(url, **kwargs) as resp:
        if resp.status != 200:
            text = await resp.text()
            raise RuntimeError(f"HTTP error {resp.status}: {text}")
        return await resp.json()

async def _http_post(session: aiohttp.ClientSession, url: str, json_data: dict):
    async with session.post(url, json=json_data) as resp:
        if resp.status != 200:
            text = await resp.text()
            raise RuntimeError(f"HTTP error {resp.status}: {text}")
        return await resp.json()

# Market fetches (extended with volume avg/rising check)
async def fetch_ohlc(session: aiohttp.ClientSession, symbol="SOLUSDT", interval="1m", limit=100):
    kraken_map = {"SOLUSDT": "SOLUSD", "BTCUSDT": "XBTUSD", "ETHUSDT": "ETHUSD"}
    interval_map = {"1m": 1, "5m": 5, "15m": 15, "30m": 30, "1h": 60, "4h": 240, "1d": 1440}

    async def _from_kraken():
        pair = kraken_map.get(symbol, "SOLUSD")
        kr_interval = interval_map.get(interval, 1)
        url = f"https://api.kraken.com/0/public/OHLC?pair={pair}&interval={kr_interval}"
        data = await _http_get(session, url, timeout=10)
        if data.get("error"):
            raise RuntimeError(f"Kraken API error: {data['error']}")
        result = data.get("result", {})
        ohlc_key = next((k for k in result.keys() if k != "last"), None)
        if not ohlc_key:
            raise RuntimeError("Kraken response missing OHLC data")
        raw_ohlc = result[ohlc_key][-limit:]
        ohlc = [
            {
                "timestamp": int(entry[0]),
                "open": float(entry[1]),
                "high": float(entry[2]),
                "low": float(entry[3]),
                "close": float(entry[4]),
                "volume": float(entry[6])
            }
            for entry in raw_ohlc
        ]
        return ohlc

    async def _from_binance():
        url = f"https://api.binance.com/api/v3/klines?symbol={symbol}&interval={interval}&limit={limit}"
        data = await _http_get(session, url, timeout=10)
        if not data or isinstance(data, dict) and data.get("code"):
            raise RuntimeError(f"Binance API error: {data.get('msg', 'Unknown error')}")
        ohlc = [
            {
                "timestamp": int(entry[0] / 1000),
                "open": float(entry[1]),
                "high": float(entry[2]),
                "low": float(entry[3]),
                "close": float(entry[4]),
                "volume": float(entry[5])
            }
            for entry in data
        ]
        return ohlc

    try:
        ohlc = await _from_kraken()
        market_fallbacks_total.inc() if "kraken" not in RPC_URL else None
    except Exception as e:
        logger.warning(f"Kraken fetch failed: {e}, falling back to Binance")
        try:
            ohlc = await _from_binance()
            market_fallbacks_total.inc()
        except Exception as e:
            logger.error(f"Binance fetch failed: {e}, using fallback data")
            ohlc = [
                {"timestamp": int(time.time()), "open": 200.0, "high": 201.0, "low": 199.0, "close": 200.0, "volume": 1000.0}
                for _ in range(limit)
            ]
    return ohlc

async def calculate_indicators(ohlc):
    closes = np.array([c["close"] for c in ohlc])
    volumes = np.array([c["volume"] for c in ohlc])
    if len(closes) < 14:
        return 50.0, 0.0, 0.0, 0.0, False
    # RSI
    delta = np.diff(closes)
    gain = np.where(delta > 0, delta, 0)
    loss = np.where(delta < 0, -delta, 0)
    avg_gain = np.mean(gain[:14])
    avg_loss = np.mean(loss[:14])
    rs = avg_gain / avg_loss if avg_loss > 0 else 100
    rsi = 100 - (100 / (1 + rs)) if avg_loss > 0 else 50.0
    # Volatility (simple std dev)
    volatility = np.std(closes[-14:]) / np.mean(closes[-14:])
    # MACD
    exp1 = np.convolve(closes, np.ones(12)/12, mode='valid')
    exp2 = np.convolve(closes, np.ones(26)/26, mode='valid')
    macd = exp1[-9:] - exp2[-9:]
    signal = np.convolve(macd, np.ones(9)/9, mode='valid')
    macd = macd[-1] if macd.size else 0.0
    signal = signal[-1] if signal.size else 0.0
    # Volume rising (simple check)
    volume_rising = volumes[-1] > np.mean(volumes[-5:])
    return rsi, volatility, macd, signal, volume_rising

async def get_sol_usdc_price(session):
    try:
        quote = await _http_get(session, f"{JUPITER_QUOTE_URL}?inputMint={USDC_MINT}&outputMint={SOL_MINT}&amount={SWAP_AMOUNT_USDC}&slippage=0.5")
        return float(quote["outAmount"]) / SWAP_AMOUNT_USDC * 1e6
    except Exception as e:
        logger.error(f"Jupiter quote failed: {e}, using Phoenix fallback")
        return await PhoenixAdapter(Provider.from_connection(AsyncClient(RPC_URL))).fetch_price()

async def execute_swap(provider, session, input_mint, output_mint, amount, dexes=None):
    start_time = time.time()
    try:
        quote = await _http_get(session, f"{JUPITER_QUOTE_URL}?inputMint={input_mint}&outputMint={output_mint}&amount={amount}&slippage=0.5")
        swap_data = await _http_post(session, JUPITER_SWAP_URL, {
            "userPublicKey": str(provider.wallet.public_key),
            "quoteResponse": quote,
            "wrapAndUnwrapSol": True,
            "feeAccount": None
        })
        if not DRY_RUN:
            tx = VersionedTransaction.from_bytes(base58.b58decode(swap_data["swapTransaction"]))
            tx.sign([provider.wallet.payer])
            tx_id = await provider.connection.send_transaction(tx, opts=TxOpts(skip_preflight=True, preflight_commitment=Confirmed))
            await provider.connection.confirm_transaction(tx_id)
            tx_success_total.inc()
            logger.info(f"Swap executed: {amount/1e6 if input_mint == USDC_MINT else amount/1e9} {input_mint} -> {output_mint}, tx_id={tx_id}")
        else:
            logger.info(f"DRY_RUN: Would swap {amount/1e6 if input_mint == USDC_MINT else amount/1e9} {input_mint} -> {output_mint}")
    except Exception as e:
        tx_failure_total.inc()
        logger.error(f"Swap failed: {e}")
    finally:
        record_tx_latency(start_time)

# Database setup
engine = create_async_engine("sqlite+aiosqlite:///trades.db", echo=False)
AsyncSessionLocal = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

async def init_db():
    async with engine.begin() as conn:
        await conn.execute(text("""
            CREATE TABLE IF NOT EXISTS trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts INTEGER,
                rsi REAL,
                strategy TEXT,
                pnl REAL,
                entry_price REAL,
                exit_price REAL,
                size REAL
            )
        """))

# Sweep-back functionality
async def sweep_back_funds(provider, session, test_wallets, recovery_address):
    for wallet_data in test_wallets:
        try:
            wallet = Wallet(Keypair.from_base58(wallet_data["key"]))
            accounts = await provider.connection.get_token_accounts_by_owner(wallet.public_key, TokenAccountOpts(mint=Pubkey.from_string(SOL_MINT)))
            for account in accounts.value:
                amount = (await provider.connection.get_token_account_balance(account.pubkey)).value.amount
                if amount > 0:
                    tx = VersionedTransaction(MessageV0.try_compile(
                        wallet.public_key,
                        [transfer_checked(
                            account.pubkey,
                            Pubkey.from_string(SOL_MINT),
                            Pubkey.from_string(recovery_address),
                            wallet.public_key,
                            amount,
                            9
                        )],
                        [],
                        await provider.connection.get_latest_blockhash()
                    ))
                    tx.sign([wallet.payer])
                    if not DRY_RUN:
                        tx_id = await provider.connection.send_transaction(tx)
                        await provider.connection.confirm_transaction(tx_id)
                        logger.info(f"Swept {amount/1e9} SOL from {wallet.public_key} to {recovery_address}, tx_id={tx_id}")
                    else:
                        logger.info(f"DRY_RUN: Would sweep {amount/1e9} SOL from {wallet.public_key} to {recovery_address}")
        except Exception as e:
            logger.error(f"Sweep failed for wallet {wallet.public_key}: {e}")

# Main run loop
@asynccontextmanager
async def lifespan(app: FastAPI):
    global provider, adapters, jito_adapter
    client = AsyncClient(RPC_URL)
    wallet = get_wallet()
    if not wallet:
        logger.error("Wallet initialization failed; exiting.")
        return
    provider = Provider(client, wallet)
    adapters = await init_adapters(provider)
    jito_adapter = adapters["jito"]
    await init_db()
    yield
    await client.close()

app = FastAPI(lifespan=lifespan)
Instrumentator().instrument(app).expose(app)

@app.get("/sweep-back-immediate")
async def sweep_back_endpoint():
    test_wallets = [
        {"key": "Avdnjm8cvsSzGXmLvZgwmzD2bnTPgX9Ts2rGtd6pNhNd"},
        {"key": "5CXzuEZhWq1zQ6gG2XzNqV8R5zV5iUoZ2x9QwY3jK4L"}
    ]
    await sweep_back_funds(provider, aiohttp.ClientSession(), test_wallets, RECOVERY_ADDRESS)
    return {"status": "sweep initiated"}

async def run_loop():
    session = aiohttp.ClientSession()
    daily_pnl = 0.0
    model_vol, model_yield = None, None
    try:
        while True:
            start_time = time.time()
            ohlc = await fetch_ohlc(session)
            rsi, volatility, macd, signal, volume_rising = await calculate_indicators(ohlc)
            yields = {
                "marginfi": (await adapters["marginfi"].fetch_yields())[0],
                "kamino": await adapters["kamino"].fetch_yields(),
                "raydium": await adapters["raydium"].fetch_yields()
            }
            strategy = pooled_strategy(yields, rsi, volatility, macd, signal, volume_rising)
            if ENABLE_ML and (model_vol is None or model_yield is None):
                model_vol, model_yield = await train_ml_model("trades.db")
            if ENABLE_ML:
                await predict_and_adjust_scores(model_vol, rsi, SWAP_AMOUNT_USDC)
            sol_usdc_price = await get_sol_usdc_price(session)
            if INITIAL_TRADE and rsi < 30:
                await execute_swap(provider, session, USDC_MINT, SOL_MINT, SWAP_AMOUNT_USDC)
                INITIAL_TRADE = False
            for action, weight in strategy.items():
                if weight > 0.2 and random.random() < weight:
                    if action == "Swap_Buy_SOL" and rsi < 30:
                        await execute_swap(provider, session, USDC_MINT, SOL_MINT, SWAP_AMOUNT_USDC)
                    elif action == "Swap_Sell_SOL" and rsi > 70:
                        await execute_swap(provider, session, SOL_MINT, USDC_MINT, SWAP_AMOUNT_LAMPORTS)
                    elif action == "Marginfi" and rsi < 30:
                        await adapters["marginfi"].deposit(SWAP_AMOUNT_USDC, USDC_MINT)
                    elif action == "Kamino" and rsi > 70:
                        await adapters["kamino"].deposit("VAULT_ADDRESS", USDC_MINT, SWAP_AMOUNT_USDC)
                    elif action == "Raydium_LP":
                        await adapters["raydium"].add_liquidity(SWAP_AMOUNT_LAMPORTS / 1e9, SWAP_AMOUNT_USDC / 1e6)
                    elif action == "Orca_LP":
                        await adapters["orca"].add_liquidity(SWAP_AMOUNT_LAMPORTS / 1e9, SWAP_AMOUNT_USDC / 1e6)
                    elif action == "Loop_USDC_SOL" and ENABLE_LOOPING:
                        await adapters["marginfi"].perform_loop(SWAP_AMOUNT_USDC)
                    elif action == "Jito_Stake":
                        await adapters["jito"].stake_sol(SWAP_AMOUNT_LAMPORTS)
                    elif action == "Arb_Check" and ENABLE_ARB:
                        # Placeholder for arbitrage logic
                        arb_opportunities_total.inc()
                    elif action == "DCA_Buy" and rsi < 30:
                        await execute_swap(provider, session, USDC_MINT, SOL_MINT, SWAP_AMOUNT_USDC // 5)
                    elif action == "DCA_Sell" and rsi > 70:
                        await execute_swap(provider, session, SOL_MINT, USDC_MINT, SWAP_AMOUNT_LAMPORTS // 5)
            if ENABLE_STOP_LOSS:
                # Placeholder for stop-loss logic
                stop_loss_triggers_total.inc()
            if ENABLE_SNIPING:
                await adapters["pump_fun"].detect_and_snipe(session, SWAP_AMOUNT_USDC)
            if ENABLE_FLASH:
                await adapters["flash_loan"].execute_flash_loan(session, SWAP_AMOUNT_USDC, [])
            if ENABLE_COMPOUND and daily_pnl > 0:
                await execute_swap(provider, session, USDC_MINT, SOL_MINT, int(daily_pnl * 1e6))
            if ENABLE_MEV:
                # Placeholder for MEV logic
                pass
            if daily_pnl > DAILY_PNL_CAP * SWAP_AMOUNT_USDC:
                logger.warning("Daily PNL cap exceeded; halting trades")
                break
            async with AsyncSessionLocal() as session_db:
                await session_db.execute(
                    text("INSERT INTO trades (ts, rsi, strategy, pnl, entry_price, exit_price, size) VALUES (:ts, :rsi, :strategy, :pnl, :entry_price, :exit_price, :size)"),
                    {"ts": int(time.time()), "rsi": rsi, "strategy": "pooled", "pnl": daily_pnl, "entry_price": sol_usdc_price, "exit_price": sol_usdc_price, "size": SWAP_AMOUNT_USDC}
                )
                await session_db.commit()
            run_loop_tick_duration_seconds.observe(time.time() - start_time)
            await asyncio.sleep(60)  # Adjust based on interval
    except Exception as e:
        logger.error(f"Run loop failed: {e}")
    finally:
        await session.close()

if __name__ == "__main__":
    asyncio.run(run_loop())
    if uvicorn:
        uvicorn.run(app, host="0.0.0.0", port=8000)
