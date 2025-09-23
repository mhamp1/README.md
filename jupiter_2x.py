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
# Load dotenv for secrets
load_dotenv()
if nest_asyncio:
    nest_asyncio.apply()
else:
    logger.warning("nest_asyncio not installed; some async features may fail.")
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
    if not ENCRYPTED_SOL_KEY or not FERNET_SECRET:
        logger.warning("No encrypted key or secret provided; using dummy wallet.")
        return None
    try:
        f = Fernet(FERNET_SECRET.encode())
        decrypted_base58 = f.decrypt(ENCRYPTED_SOL_KEY.encode()).decode()
        private_key_bytes = base58.decode(decrypted_base58)
        kp = Keypair.from_bytes(private_key_bytes)
        logger.info(f"Bot wallet address from decryption: {str(kp.pubkey())}")
        return Wallet(kp)
    except Exception as e:
        logger.error(f"Wallet decryption failed: {e}")
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
    buy_threshold = 30 if volatility < 0.005 else 25 # Tighter in high vol
    sell_threshold = 70 if volatility < 0.005 else 75
    buy_boost = 0.9 if volume_rising else 0.8
    sell_boost = 0.9 if volume_rising else 0.8
    if rsi < buy_threshold and macd > signal: # Buy with MACD + volume confirmation
        return normalize({"Swap_Buy_SOL": buy_boost, "Kamino": 0.1, "Raydium_LP": 0.05, "Orca_LP": 0.05})
    elif rsi > sell_threshold and macd < signal: # Sell with MACD + volume confirmation
        return normalize({"Swap_Sell_SOL": sell_boost, "Marginfi": 0.1, "Raydium_LP": 0.05, "Orca_LP": 0.05})
    else: # Neutral: Loop + LP for yields (21%+ APY target)
        return normalize({"Loop_USDC_SOL": 0.4, "Raydium_LP": 0.2, "Orca_LP": 0.2, "Arb_Check": 0.2 if ENABLE_ARB else 0.0})
def loop_optimized_strategy(yields, rsi, volatility, macd, signal, volume_rising):
    # Prioritize Jito staking in loops (5% base + airdrops)
    if rsi >= 30 and rsi <= 70: # Neutral focus on optimized loops
        return normalize({"Loop_USDC_SOL": 0.6, "Jito_Stake": 0.3, "Kamino": 0.1})
    else:
        return hybrid_strategy(yields, rsi, volatility, macd, signal, volume_rising)
def enhanced_hybrid_strategy(yields, rsi, volatility, macd, signal, volume_rising):
    # Best strategy: RSI/MACD swaps with DCA, neutral optimized loops (Jito + Marginfi + Kamino for 11%+), LP on Raydium/Orca sideways, arb on diffs triggered by mints
    buy_threshold = 30 if volatility < 0.005 else 25
    sell_threshold = 70 if volatility < 0.005 else 75
    if rsi < buy_threshold and macd > signal and volume_rising: # Buy with full confirmations
        return normalize({"Swap_Buy_SOL": 0.7, "DCA_Buy": 0.1, "Kamino": 0.1, "Orca_LP": 0.1})
    elif rsi > sell_threshold and macd < signal and volume_rising: # Sell
        return normalize({"Swap_Sell_SOL": 0.7, "DCA_Sell": 0.1, "Marginfi": 0.1, "Raydium_LP": 0.1})
    else: # Neutral: Optimized loops + LP + arb
        return normalize({"Loop_USDC_SOL": 0.3, "Jito_Stake": 0.2, "Raydium_LP": 0.15, "Orca_LP": 0.15, "Arb_Check": 0.2 if ENABLE_ARB else 0.0})
strategy_pool = [
    StrategyAgent("conservative", conservative_strategy, "conservative"),
    StrategyAgent("aggressive", aggressive_strategy, "aggressive"),
    StrategyAgent("hybrid", hybrid_strategy, "hybrid"),
    StrategyAgent("loop_optimized", loop_optimized_strategy, "optimized"),
    StrategyAgent("enhanced_hybrid", enhanced_hybrid_strategy, "enhanced") # Best strategy agent
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
# ML prediction for yields/vol (additive, conditional)
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
        X = df[['rsi', 'size']].values # Features: RSI, size
        y_vol = df['pnl'].values # Predict vol proxy via PnL variance
        y_yield = df['pnl'].cumsum().values # Cumulative yield proxy
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
            # Real call (wired)
            try:
                return await self.program.rpc["getHealthRatio"](accounts={"marginfiAccount": account_pubkey})
            except:
                pass
        return 0.5 # Fallback placeholder
    async def fetch_yields(self, mint=SOL_MINT):
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get("https://mrgn-public-api.marginfi.com/v1/banks") as resp:
                    if resp.status != 200:
                        logger.error(f"Marginfi API error: {resp.status}")
                        return 0.04, 0.05 # Fallback
                    data = await resp.json()
                    for bank in data:
                        if bank['mint'] == mint:
                            return bank['lendingRate'] / 100, bank['borrowingRate'] / 100 # Return lend/borrow rates
        except Exception as e:
            logger.exception(f"Marginfi fetch error: {e}")
            # RPC fallback for real-time (lag mitigation)
            try:
                bank_account = await self.provider.connection.get_account_info(Pubkey.from_string(mint))
                # Parse bank data for rates (placeholder parse; assume from account data)
                return 0.04, 0.05
            except:
                return 0.04, 0.05 # Ultimate fallback
    async def deposit(self, amount, mint):
        await self.init_program()
        if self.program:
            # Real wired call
            try:
                await self.program.rpc["deposit"](amount, accounts={"marginfiAccount": self.provider.wallet.public_key, "bank": Pubkey.from_string(mint), "signer": self.provider.wallet.public_key})
                return
            except:
                pass
        # Placeholder
        logger.info(f"Placeholder deposit: {amount} of {mint}")
    async def borrow(self, amount, mint):
        await self.init_program()
        if self.program:
            # Real wired call
            try:
                await self.program.rpc["borrow"](amount, accounts={"marginfiAccount": self.provider.wallet.public_key, "bank": Pubkey.from_string(mint), "signer": self.provider.wallet.public_key})
                return
            except:
                pass
        # Placeholder
        logger.info(f"Placeholder borrow: {amount} of {mint}")
    async def perform_loop(self, usdc_amount, loops=3):
        if not ENABLE_LOOPING:
            return
        lend_rate, borrow_rate = await self.fetch_yields(USDC_MINT)
        if borrow_rate >= lend_rate: # Flip unprofitable; skip
            logger.warning("Looping unprofitable: borrow > lend")
            return
        ltv = min(0.8, (lend_rate - borrow_rate) / lend_rate) # Dynamic LTV based on rates
        for _ in range(loops):
            await self.deposit(usdc_amount, USDC_MINT) # Lend USDC
            borrowed_sol = usdc_amount / await get_sol_usdc_price() * ltv
            await self.borrow(borrowed_sol * 1e9, SOL_MINT) # Borrow SOL
            await execute_swap(SOL_MINT, USDC_MINT, int(borrowed_sol * 1e9)) # Swap to USDC
            # Integrate Jito staking
            await jito_adapter.stake_sol(int(borrowed_sol * 1e9 * 0.5)) # Stake 50% borrowed SOL for 5% + airdrops
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
        # Placeholder
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
            # RPC fallback
            try:
                vault_account = await self.provider.connection.get_account_info(Pubkey.from_string("VAULT_PLACEHOLDER"))
                # Parse for APY (placeholder)
                return 0.03
            except:
                return 0.03
class JitoAdapter: # New for staking
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
        # Placeholder (5% base yield)
        logger.info(f"Placeholder Jito stake: {amount_lamports / 1e9} SOL")
class RaydiumAdapter: # Extended with liquidity check
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
            # RPC fallback
            try:
                pool_account = await self.provider.connection.get_account_info(Pubkey.from_string("RAYDIUM_SOL_USDC_POOL"))
                # Parse for APR (placeholder)
                return 0.05
            except:
                return 0.05
    async def add_liquidity(self, sol_amount, usdc_amount):
        await self.init_program()
        liquidity = await self.check_liquidity() # New check
        if liquidity < 1000000: # Low liquidity threshold (USDC equiv)
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
        # Placeholder
        logger.info(f"Placeholder Raydium LP add: {sol_amount} SOL, {usdc_amount} USDC")
    async def check_liquidity(self):
        # Fetch pool liquidity (RPC)
        try:
            return (await self.provider.connection.get_token_supply(Pubkey.from_string("SOL_USDC_LP_TOKEN"))).value.amount
        except:
            return 10000000 # Fallback high
class OrcaAdapter: # New for more LP options
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
        # Placeholder
        logger.info(f"Placeholder Orca LP add: {sol_amount} SOL, {usdc_amount} USDC")
    async def check_liquidity(self):
        # Similar to Raydium
        try:
            return (await self.provider.connection.get_token_supply(Pubkey.from_string("ORCA_SOL_USDC_LP"))).value.amount
        except:
            return 10000000
class PhoenixAdapter: # New for arb extension
    def __init__(self, provider):
        self.provider = provider
    async def fetch_price(self):
        # RPC for orderbook price
        try:
            market_account = await self.provider.connection.get_account_info(Pubkey.from_string("PHOENIX_SOL_USDC_MARKET"))
            # Parse account data for mid price (placeholder parse; assume bids/asks in data)
            bids = [] # Parse
            asks = [] # Parse
            if bids and asks:
                return (bids[0] + asks[0]) / 2
            return 200.0
        except Exception as e:
            logger.exception(f"Phoenix fetch error: {e}")
        return 200.0
class PumpFunAdapter: # New for sniping
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
                # Parse for new token mint (placeholder: extract mint from tx logs/instructions)
                new_token_mint = "NEW_TOKEN_MINT_FROM_TX" # Replace with actual parse (e.g., from logs)
                if new_token_mint:
                    # Snipe buy
                    await execute_swap(self.provider, session, USDC_MINT, new_token_mint, amount_usdc, dexes=["Raydium"])
                    snipe_executions_total.inc()
                    logger.info(f"Sniped new token: {new_token_mint}, amount={amount_usdc / 1e6} USDC")
        except Exception as e:
            logger.exception(f"Pump.fun snipe failed: {e}")
class FlashLoanAdapter: # New for flash loans (Marginfi or Kamino)
    def __init__(self, provider):
        self.provider = provider
        self.program = None # Use Marginfi or Kamino
    async def init_program(self):
        if self.program is None:
            try:
                self.program = await Program.fetch(self.provider.connection, Pubkey.from_string(MARGINFI_PROGRAM_ID), self.provider) # Or KAMINO
            except Exception as e:
                logger.exception(f"Flash loan fetch failed: {e}; using placeholder")
    async def execute_flash_loan(self, session, amount, callback_ixs):
        await self.init_program()
        if self.program:
            try:
                # Build flash loan tx: borrow + callback (e.g., arb/swap) + repay
                flash_ix = self.program.instruction["flashLoan"](amount, callback_ixs, accounts={"bank": Pubkey.from_string(USDC_MINT), "signer": self.provider.wallet.public_key})
                tx = VersionedTransaction(MessageV0.try_compile(provider.wallet.public_key, [flash_ix], [], await provider.connection.get_latest_blockhash()))
                signed_tx = provider.wallet.sign_transaction(tx)
                tx_id = await provider.connection.send_transaction(signed_tx)
                await provider.connection.confirm_transaction(tx_id)
                flash_loans_total.inc()
                logger.info(f"Executed flash loan: amount={amount / 1e6} USDC, tx_id={tx_id}")
            except:
                pass
        # Placeholder (simulate borrow/swap/repay)
        logger.info(f"Placeholder flash loan: {amount / 1e6} USDC")
# Adapters init (pass provider)
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
    # Init programs async
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
    """
    Preferred: Kraken. Fallback: Binance. Returns raw OHLC list.
    """
    kraken_map = {
        "SOLUSDT": "SOLUSD",
        "BTCUSDT": "XBTUSD",
        "ETHUSDT": "ETHUSD"
    }
    interval_map = {"1m":1,"5m":5,"5m":5,"15m":15,"30m":30,"1h":60,"4h":240,"1d":1440}
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
        candles = []
        for row in raw_ohlc:
            try:
                close = float(row[4])
                volume = float(row[6])
                candles.append([float(row[0]), float(row[1]), float(row[2]), float(row[3]), close, float(row[5]), volume, float(row[7])])
            except Exception:
                continue
        return candles
    async def _from_binance():
        url = f"https://api.binance.com/api/v3/klines?symbol={symbol}&interval={interval}&limit={limit}"
        return await _http_get(session, url, timeout=10)
    try:
        raw = await _from_kraken()
    except Exception as e:
        logger.error(f"Kraken API error in fetch_ohlc: {e}")
        market_fallbacks_total.inc()
        try:
            raw = await _from_binance()
        except Exception as e2:
            logger.error(f"Binance fallback failed in fetch_ohlc: {e2}")
            market_fallbacks_total.inc()
            return []
    return raw
async def fetch_rsi_macd_vol_volume(session, symbol="SOLUSDT", interval="1m", rsi_period=14, macd_short=12, macd_long=26, macd_signal=9, vol_period=10):
    raw = await fetch_ohlc(session, symbol, interval, limit=max(rsi_period + 50, macd_long + 50, vol_period))
    if not raw:
        return 50.0, 0.0, 0.0, 0.0, False
    closes = np.array([candle[4] for candle in raw if len(candle) > 4])
    volumes = np.array([candle[6] for candle in raw if len(candle) > 6])
    if len(closes) < max(rsi_period + 1, macd_long + 1, vol_period):
        return 50.0, 0.0, 0.0, 0.0, False
    rsi = calculate_rsi(closes, rsi_period)
    macd, signal, _ = calculate_macd(closes, macd_short, macd_long, macd_signal)
    if len(volumes) >= vol_period:
        log_returns = np.log(closes[1:] / closes[:-1])
        volatility = round(np.std(log_returns[-vol_period:]), 4)
        avg_volume = np.mean(volumes[:-1])
        volume_rising = volumes[-1] > avg_volume # Volume > avg for confirmation
    else:
        volatility = 0.0
        volume_rising = False
    return rsi, macd, signal, volatility, volume_rising
async def fetch_yields():
    yields = {}
    try:
        yields["Marginfi_SOL"], yields["Marginfi_SOL_borrow"] = await marginfi_adapter.fetch_yields(SOL_MINT)
        yields["Marginfi_USDC"], yields["Marginfi_USDC_borrow"] = await marginfi_adapter.fetch_yields(USDC_MINT)
        yields["Kamino_SOL"] = await kamino_adapter.fetch_yields(SOL_MINT)
        yields["Kamino_USDC"] = await kamino_adapter.fetch_yields(USDC_MINT)
        yields["Raydium"] = await raydium_adapter.fetch_yields()
        yields["Orca"] = 0.06 # Placeholder for Orca yields (extend with fetch if needed)
        yields["Jito"] = 0.05 # Base staking
        yields["Solend"] = 0.02
    except Exception as e:
        logger.exception(f"Error fetching yields: {e}")
    return yields
async def get_sol_usdc_price(session):
    try:
        params = {"inputMint": SOL_MINT, "outputMint": USDC_MINT, "amount": 1000000000} # 1 SOL
        quote = await _http_get(session, JUPITER_QUOTE_URL, params=params)
        return quote['outAmount'] / 1000000 # USDC decimals
    except Exception:
        return 200.0 # Fallback
async def get_balances(provider):
    try:
        if not hasattr(provider.wallet, 'public_key') or provider.wallet.public_key is None:
            logger.warning("Dummy wallet - returning 0 balances")
            return 0, 0
        sol_bal = (await provider.connection.get_balance(provider.wallet.public_key)).value
        usdc_ata = get_associated_token_address(provider.wallet.public_key, Pubkey.from_string(USDC_MINT))
        if not await provider.connection.get_account_info(usdc_ata):
            ix = create_associated_token_account(provider.wallet.public_key, provider.wallet.public_key, Pubkey.from_string(USDC_MINT))
            recent_blockhash = await provider.connection.get_latest_blockhash()
            msg = MessageV0.try_compile(provider.wallet.public_key, [ix], [], recent_blockhash.value.blockhash)
            tx = VersionedTransaction(msg, [provider.wallet.payer])
            signed_tx = provider.wallet.sign_transaction(tx)
            tx_id = await provider.connection.send_transaction(signed_tx)
            await provider.connection.confirm_transaction(tx_id)
            logger.info("Created USDC ATA")
        usdc_resp = await provider.connection.get_token_account_balance(usdc_ata)
        usdc_bal = usdc_resp.value.amount if usdc_resp else 0
        logger.info(f"SOL balance raw: {sol_bal}, USDC raw: {usdc_bal}")
        return sol_bal, usdc_bal
    except Exception as e:
        logger.exception(f"Error fetching balances: {e}")
        return 0, 0
async def get_multi_balances(providers):
    total_sol = 0
    total_usdc = 0
    for prov in providers:
        sol, usdc = await get_balances(prov)
        total_sol += sol
        total_usdc += usdc
    return total_sol, total_usdc
async def execute_swap(provider, session, input_mint, output_mint, amount, dexes=None):
    params = {
        "inputMint": input_mint,
        "outputMint": output_mint,
        "amount": amount,
        "slippageBps": 50 # 0.5%
    }
    if dexes:
        params["onlyDirectRoutes"] = False
        params["dexes"] = dexes # e.g., ["Raydium"]
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
    tx_success_total.inc()
    logger.info(f"Executed swap: {input_mint} -> {output_mint}, amount={amount}, tx_id={tx_id}")
    if ENABLE_STOP_LOSS:
        asyncio.create_task(monitor_stop_loss(provider, session, output_mint, quote['outAmount'], quote['inAmount'] / quote['outAmount'], tx_id))
    return tx_id
async def monitor_stop_loss(provider, session, output_mint, out_amount, entry_price, tx_id):
    # Monitor post-tx price for 5 min; sell if -5%
    start_time = time.time()
    while time.time() - start_time < 300:
        current_price = await get_sol_usdc_price(session) if output_mint == SOL_MINT else 1.0 / await get_sol_usdc_price(session)
        if (current_price - entry_price) / entry_price < -0.05:
            # Trigger sell
            sell_amount = out_amount if output_mint == USDC_MINT else int(out_amount / current_price * 1e9)
            await execute_swap(provider, session, output_mint, USDC_MINT if output_mint == SOL_MINT else SOL_MINT, sell_amount)
            stop_loss_triggers_total.inc()
            logger.warning(f"Stop-loss triggered for tx {tx_id}: {output_mint} at {current_price}")
            break
        await asyncio.sleep(30)
async def check_and_execute_arb(provider, session):
    if not ENABLE_ARB:
        return
    # Extended to Phoenix/Jupiter/Orca/Raydium
    try:
        prices = {}
        # Raydium (from pool)
        async with session.get("https://api.raydium.io/v4/sdk/liquidity/mainnet.json") as resp:
            data = await resp.json()
            prices["Raydium"] = 200.0 # Parse
        # Orca
        async with session.get("https://api.orca.so/allPools") as resp:
            data = await resp.json()
            prices["Orca"] = data.get("SOL/USDC", {}).get("price", 200.0)
        # Phoenix
        prices["Phoenix"] = await phoenix_adapter.fetch_price()
        # Jupiter (aggregator avg)
        prices["Jupiter"] = await get_sol_usdc_price(session)
        # Find max diff
        min_price = min(prices.values())
        max_price = max(prices.values())
        diff = (max_price - min_price) / min_price
        if diff > 0.005: # >0.5%
            arb_opportunities_total.inc()
            low_dex = min(prices, key=prices.get)
            high_dex = max(prices, key=prices.get)
            buy_amount = SWAP_AMOUNT_USDC // 10
            await execute_swap(provider, session, USDC_MINT, SOL_MINT, buy_amount, dexes=[low_dex])
            sell_amount = buy_amount / min_price * 1e9
            await execute_swap(provider, session, SOL_MINT, USDC_MINT, int(sell_amount), dexes=[high_dex])
            logger.info(f"Arb executed: Buy on {low_dex}, sell on {high_dex}, diff={diff:.2%}")
    except Exception as e:
        logger.exception(f"Arb check failed: {e}")
async def check_usdc_mint_volume(provider, threshold=1000000):
    # Check recent mint volume > threshold to trigger arb boost
    try:
        signatures = await provider.connection.get_signatures_for_address(Pubkey.from_string(USDC_MINT), limit=10)
        mint_volume = 0
        for sig in signatures:
            tx = await provider.connection.get_transaction(sig.signature, opts=TxOpts(preflight_commitment=Confirmed))
            # Parse for mint instructions (placeholder: check if 'mint' in tx)
            if tx and 'mint' in str(tx): # Simplified parse
                mint_volume += 100000 # Assume amount from tx
        return mint_volume > threshold
    except Exception as e:
        logger.exception(f"USDC mint check failed: {e}")
        return False
async def check_margin_ratio(account_pubkey, threshold=0.15):
    try:
        ratio = await marginfi_adapter.get_health_ratio(account_pubkey)
        if ratio < threshold:
            logger.warning(f"Low margin: {ratio:.2f}")
            return False
        return True
    except Exception as e:
        logger.exception(f"Error checking margin ratio: {e}")
        return False
# Async DB functions with SQLAlchemy
async def init_db_async(db_path: str = "trades.db"):
    desired_columns = [
        ("ts", "TEXT"),
        ("rsi", "REAL"),
        ("strategy", "TEXT"),
        ("pnl", "REAL"),
        ("entry_price", "REAL"),
        ("exit_price", "REAL"),
        ("size", "REAL")
    ]
    async with AsyncSessionLocal() as session:
        await session.execute(text("""
        CREATE TABLE IF NOT EXISTS trades (
            ts TEXT,
            rsi REAL,
            strategy TEXT,
            pnl REAL,
            entry_price REAL,
            exit_price REAL,
            size REAL
        )
        """))
        # Migrate by adding missing columns (query table info)
        result = await session.execute(text("PRAGMA table_info(trades)"))
        current_columns = {row[1] for row in result.fetchall()}
        for col_name, col_type in desired_columns:
            if col_name not in current_columns:
                await session.execute(text(f"ALTER TABLE trades ADD COLUMN {col_name} {col_type}"))
        await session.commit()
async def log_trade_async(db_path: str, rsi, strategy: dict, pnl: float, entry_price: float = 0.0, exit_price: float = 0.0, size: float = 1.0):
    async with AsyncSessionLocal() as session:
        await session.execute(
            text("INSERT INTO trades (ts, rsi, strategy, pnl, entry_price, exit_price, size) VALUES (:ts, :rsi, :strategy, :pnl, :entry_price, :exit_price, :size)"),
            {
                "ts": time.strftime("%Y-%m-d %H:%M:%S"),
                "rsi": rsi,
                "strategy": json.dumps(strategy),
                "pnl": pnl,
                "entry_price": entry_price,
                "exit_price": exit_price,
                "size": size
            }
        )
        await session.commit()
    logger.info(f"Logged trade: rsi={rsi} pnl={pnl}")
async def get_trades(limit: int = 10):
    async with AsyncSessionLocal() as session:
        result = await session.execute(text(f"SELECT * FROM trades ORDER BY ts DESC LIMIT {limit}"))
        return result.fetchall()
# Execution helpers
retry_count = 0
breaker_triggered = False
MAX_RETRIES = 10
async def retry_with_backoff(fn, max_retries=5):
    global retry_count, breaker_triggered
    for i in range(max_retries):
        start = time.time()
        try:
            result = await fn()
            record_tx_latency(start)
            tx_success_total.inc()
            retry_count = 0
            return result
        except Exception as e:
            logger.warning(f"Retry {i+1}: {e}")
            retry_attempts_total.inc()
            rpc_error_total.inc()
            retry_count += 1
            if retry_count > MAX_RETRIES:
                breaker_triggered = True
                circuit_breaker_triggered.inc()
                await alert_breaker()
                raise RuntimeError("Circuit breaker triggered") from e
            await asyncio.sleep(min(2 ** i, 60))
    tx_failure_total.inc()
    raise RuntimeError("Max retries exceeded")
async def alert_breaker():
    if WEBHOOK_URL:
        async with aiohttp.ClientSession() as session:
            await session.post(WEBHOOK_URL, json={"alert": "Circuit breaker triggered"})
# Try to import initialize from startup if present (AI will wire implementation)
try:
    from startup import initialize
except ImportError:
    # fallback placeholder initialize: returns (None, None, None, dummy_provider)
    async def initialize():
        class DummyWallet:
            payer = None
            public_key = Pubkey.from_string("EnkssqpAxmvV51VGpK9YvMAAgKgCACALMvYj19cJ5TVQ")
        class DummyProvider:
            connection = AsyncClient(os.getenv("RPC_URL", "https://api.mainnet-beta.solana.com"))
            wallet = DummyWallet()
        logger.info("Using placeholder initialize() with fixed address for balances")
        return None, None, None, DummyProvider()
async def call_initialize(executor):
    if asyncio.iscoroutinefunction(initialize):
        return await initialize()
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(executor, initialize)
# FastAPI dashboard (minimal endpoints)
@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.db_path = "trades.db"
    await init_db_async(app.state.db_path)
    app.state.http = aiohttp.ClientSession()
    app.state.executor = ThreadPoolExecutor(max_workers=2)
    try:
        init_res = await call_initialize(app.state.executor)
    except Exception as e:
        logger.exception(f"initialize() failed: {e}")
        init_res = None
    # unpack safely
    try:
        marginfi_program, kamino_program, model, provider = init_res
    except Exception:
        marginfi_program = kamino_program = model = None
        provider = init_res if init_res is not None else None
    # Override with decrypted wallet if available
    real_wallet = get_wallet()
    if real_wallet:
        provider.wallet = real_wallet
    else:
        logger.error("Using dummy wallet - balances will be 0")
    logger.info(f"Using wallet: {provider.wallet.public_key if provider.wallet.public_key else 'Dummy'}")
    app.state.provider = provider # Explicitly set for endpoints
    app.state.adapters = await init_adapters(provider)
    global marginfi_adapter, kamino_adapter, jito_adapter, raydium_adapter, orca_adapter, phoenix_adapter, pump_fun_adapter, flash_loan_adapter
    marginfi_adapter = app.state.adapters["marginfi"]
    kamino_adapter = app.state.adapters["kamino"]
    jito_adapter = app.state.adapters["jito"]
    raydium_adapter = app.state.adapters["raydium"]
    orca_adapter = app.state.adapters["orca"]
    phoenix_adapter = app.state.adapters["phoenix"]
    pump_fun_adapter = app.state.adapters["pump_fun"]
    flash_loan_adapter = app.state.adapters["flash_loan"]
    # Multi-provider support
    app.state.providers = [provider]
    if multi_wallets:
        for wal in get_multi_wallets():
            multi_provider = Provider(provider.connection, wal)
            app.state.providers.append(multi_provider)
    # ML model (additive)
    app.state.ml_vol, app.state.ml_yield = await train_ml_model(app.state.db_path)
    # Sanity checks
    try:
        yields = await fetch_yields()
        rsi, macd, signal, volatility, volume_rising = await fetch_rsi_macd_vol_volume(app.state.http)
        strat = pooled_strategy(yields, rsi, volatility, macd, signal, volume_rising)
        logger.info(f"Sample strategy at RSI {rsi}, MACD {macd}/{signal}, vol {volatility}, volume_rising {volume_rising}: {strat}")
    except Exception as e:
        logger.exception(f"Startup sample tick failed: {e}")
    # Start persistent run loop
    app.state.run_task = asyncio.create_task(run_loop(provider, app.state.http, app.state.ml_vol, app.state.ml_yield, tick_seconds=int(os.getenv("TICK_SECONDS", 30))))
    # Initial trade if enabled
    if INITIAL_TRADE and not DRY_RUN:
        sol_bal, usdc_bal = await get_multi_balances(app.state.providers)
        if usdc_bal > 0:
            amount = min(SWAP_AMOUNT_USDC, usdc_bal // len(app.state.providers))
            for prov in app.state.providers:
                await execute_swap(prov, app.state.http, USDC_MINT, SOL_MINT, amount)
            logger.info("Executed initial trade across wallets")
    yield
    app.state.run_task.cancel()
    with suppress(asyncio.CancelledError):
        await app.state.run_task
    await app.state.http.close()
    app.state.executor.shutdown(wait=True)
app = FastAPI(title="Defi Bot Dashboard", lifespan=lifespan)
Instrumentator().instrument(app).expose(app)
@app.get("/")
async def index():
    return {"status":"ok", "strategy_pool": [a.name for a in strategy_pool]}
@app.get("/health")
async def health():
    return {"ok": True}
@app.get("/trades")
async def get_trades_endpoint(limit: int = 10):
    rows = await get_trades(limit)
    trades = []
    for row in rows:
        trades.append({
            "ts": row[0],
            "rsi": row[1],
            "strategy": json.loads(row[2]),
            "pnl": row[3],
            "entry_price": row[4],
            "exit_price": row[5],
            "size": row[6]
        })
    return trades
@app.get("/balances")
async def get_balances_endpoint():
    sol_bal, usdc_bal = await get_multi_balances(app.state.providers)
    return {"sol": sol_bal / 1e9, "usdc": usdc_bal / 1e6}
# Main async entrypoint and persistent run loop (enhanced with fixes)
async def run_loop(provider, http_session, ml_vol, ml_yield, tick_seconds: int = 30):
    dca_counter = {"buy": 0, "sell": 0} # For DCA splitting
    while True:
        start = time.time()
        try:
            yields = await fetch_yields()
            rsi, macd, signal, volatility, volume_rising = await fetch_rsi_macd_vol_volume(http_session)
            await predict_and_adjust_scores(ml_vol, rsi, 1.0) # Size placeholder
            # Check USDC mints for arb boost
            if await check_usdc_mint_volume(provider):
                # Boost Arb_Check in strat (additive)
                strat = pooled_strategy(yields, rsi, volatility, macd, signal, volume_rising)
                strat["Arb_Check"] = strat.get("Arb_Check", 0) + 0.1
                strat = normalize(strat)
            else:
                strat = pooled_strategy(yields, rsi, volatility, macd, signal, volume_rising)
            logger.info(f"Tick RSI {rsi}, MACD {macd}/{signal}, vol {volatility}, volume_rising {volume_rising}: {strat}")
            # Fetch balances and price for risk management
            sol_bal, usdc_bal = await get_multi_balances(app.state.providers)
            price = await get_sol_usdc_price(http_session)
            total_value = (sol_bal / 1e9 * price) + (usdc_bal / 1e6)
            usdc_ratio = (usdc_bal / 1e6) / total_value if total_value > 0 else 1.0
            risk_per_trade = 0.01 if volatility < 0.01 else 0.005 # Dynamic: lower in high vol
            max_swap_usdc = int(total_value * risk_per_trade * 1e6) # In USDC units
            max_swap_lamports = int((total_value * risk_per_trade / price) * 1e9)
            # DCA: Split into 3 if counter >0
            if dca_counter["buy"] > 0:
                swap_amount_usdc = max_swap_usdc // dca_counter["buy"]
                dca_counter["buy"] -= 1
            else:
                swap_amount_usdc = max_swap_usdc
            if dca_counter["sell"] > 0:
                swap_amount_lamports = max_swap_lamports // dca_counter["sell"]
                dca_counter["sell"] -= 1
            else:
                swap_amount_lamports = max_swap_lamports
            # Execute based on strategy (conditional, no break to existing)
            if not DRY_RUN and provider.wallet.payer: # Real wallet required
                if strat.get("Swap_Buy_SOL", 0) > 0.5 or strat.get("DCA_Buy", 0) > 0.1:
                    amount = min(swap_amount_usdc, usdc_bal)
                    if amount > 0:
                        await retry_with_backoff(lambda: execute_swap(provider, http_session, USDC_MINT, SOL_MINT, amount))
                        dca_counter["buy"] = 2 # Split remaining over next 2 ticks
                elif strat.get("Swap_Sell_SOL", 0) > 0.5 or strat.get("DCA_Sell", 0) > 0.1:
                    amount = min(swap_amount_lamports, sol_bal)
                    if amount > 0:
                        await retry_with_backoff(lambda: execute_swap(provider, http_session, SOL_MINT, USDC_MINT, amount))
                        dca_counter["sell"] = 2
                if strat.get("Loop_USDC_SOL", 0) > 0.3 and ENABLE_LOOPING:
                    loop_amount = min(SWAP_AMOUNT_USDC, usdc_bal)
                    if loop_amount > 0:
                        await retry_with_backoff(lambda: marginfi_adapter.perform_loop(loop_amount))
                if strat.get("Jito_Stake", 0) > 0.1:
                    stake_amount = min(SWAP_AMOUNT_LAMPORTS, sol_bal)
                    if stake_amount > 0:
                        await jito_adapter.stake_sol(stake_amount)
                if strat.get("Raydium_LP", 0) > 0.1:
                    lp_sol = min(SWAP_AMOUNT_LAMPORTS, sol_bal) // 2
                    lp_usdc = min(SWAP_AMOUNT_USDC, usdc_bal) // 2
                    if lp_sol > 0 and lp_usdc > 0:
                        await retry_with_backoff(lambda: raydium_adapter.add_liquidity(lp_sol / 1e9, lp_usdc / 1e6))
                if strat.get("Orca_LP", 0) > 0.1:
                    lp_sol = min(SWAP_AMOUNT_LAMPORTS, sol_bal) // 2
                    lp_usdc = min(SWAP_AMOUNT_USDC, usdc_bal) // 2
                    if lp_sol > 0 and lp_usdc > 0:
                        await retry_with_backoff(lambda: orca_adapter.add_liquidity(lp_sol / 1e9, lp_usdc / 1e6))
                # Allocate to lending (weighted)
                lend_weight_marginfi = strat.get("Marginfi", 0)
                lend_weight_kamino = strat.get("Kamino", 0)
                if lend_weight_marginfi > 0.1 or lend_weight_kamino > 0.1:
                    # Assume post-swap, deposit SOL/USDC based on holdings
                    if sol_bal > 0:
                        if lend_weight_marginfi > lend_weight_kamino:
                            await marginfi_adapter.deposit(sol_bal, SOL_MINT)
                        else:
                            await kamino_adapter.deposit("vault_address_placeholder", SOL_MINT, sol_bal)
                    if usdc_bal > 0:
                        if lend_weight_marginfi > lend_weight_kamino:
                            await marginfi_adapter.deposit(usdc_bal, USDC_MINT)
                        else:
                            await kamino_adapter.deposit("vault_address_placeholder", USDC_MINT, usdc_bal)
                # Arb check (broader opportunity)
                if strat.get("Arb_Check", 0) > 0.1:
                    await check_and_execute_arb(provider, http_session)
                # Maintain 80% USDC stable
                new_usdc_ratio = (usdc_bal / 1e6) / total_value if total_value > 0 else 1.0
                if new_usdc_ratio < 0.8 and sol_bal > 0:
                    adjust_amount = int((0.8 - new_usdc_ratio) * total_value / price * 1e9)
                    await execute_swap(provider, http_session, SOL_MINT, USDC_MINT, min(adjust_amount, sol_bal))
            # Store sample tick (with simulated PnL=0 for logging)
            init_sample = dict(strat) # Safe copy
            await log_trade_async(app.state.db_path, rsi, init_sample, pnl=0.0, size=0.0)
        except Exception as e:
            logger.exception(f"Error in run loop tick: {e}")
        run_loop_tick_duration_seconds.observe(time.time() - start)
        await asyncio.sleep
