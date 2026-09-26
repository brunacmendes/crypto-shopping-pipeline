"""
Run with:
    python3 -m pipeline.run
"""
 
import logging
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
 
from pipeline.extract import (
    load_shopping_dataset,
    get_coin_ids,
    resolve_coin_id,
    get_historical_prices_concurrent,
)
from pipeline.transform import (
    clean_shopping_df,
    shift_year,
    add_crypto_metrics,
    join_purchases_crypto,
    validate_all,
)
##from pipeline.validate import validate_all
from pipeline.load import get_engine, create_schema, load_tables
 
log = logging.getLogger(__name__)
 
CSV_PATH = "2019-Oct.csv"  # the source dataset is from 2019, but we will shift its year to 2025
#COIN_NAME = "bitcoin"
COIN_NAMES = [
    "bitcoin",
    "ethereum",
    "tether",
    "binancecoin",
    "solana",
]
 
SOURCE_YEAR = 2019
TARGET_YEAR = 2025
LOOKBACK_DAYS = 31
 
 
# Logging setup
 
def setup_logging() -> None:
    Path("logs").mkdir(exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[
            logging.FileHandler("logs/run.log"),
            logging.StreamHandler(),
        ],
    )
 
 
@contextmanager
def timed(step_name: str):
    """Logs how long a pipeline step took, and whether it failed."""
    start = time.perf_counter()
    log.info("Starting step: %s", step_name)
    try:
        yield
    except Exception:
        log.exception("Step failed: %s", step_name)
        raise
    else:
        elapsed = time.perf_counter() - start
        log.info("Finished step: %s (%.2fs)", step_name, elapsed)
 
 
# Pipeline
 
def main() -> None:
    setup_logging()
    log.info("=== Pipeline run started ===")
    run_start = time.perf_counter()
 
    # ---- Extract ----
    with timed("extract"):
        shopping_raw = load_shopping_dataset(CSV_PATH)
 
        coins = get_coin_ids()
        #coin_id = resolve_coin_id(COIN_NAME, coins=coins)
        coin_ids = [
        resolve_coin_id(name, coins=coins)
        for name in COIN_NAMES
        ]
 
        ##end = datetime.now(timezone.utc)
        end = datetime(2025, 11, 1, tzinfo=timezone.utc)
        start = end - timedelta(days=LOOKBACK_DAYS)
        ##crypto_raw = get_historical_prices(coin_id, start, end)
        crypto_raw = get_historical_prices_concurrent(coin_ids,start,end,max_workers=5,)
 
    # ---- Transform ----
    with timed("transform"):
        shopping = clean_shopping_df(shopping_raw)
        shopping = shift_year(shopping, from_year=SOURCE_YEAR, to_year=TARGET_YEAR)
        crypto = add_crypto_metrics(crypto_raw)
        joined = join_purchases_crypto(shopping, crypto,)
 
    # ---- Validate ----
    with timed("validate"):
        good, rejects = validate_all(joined)
        if not rejects.empty:
            log.warning("%d rows failed validation and were rejected", len(rejects))
 
    # ---- Load ----
    with timed("load"):
        engine = get_engine()
        create_schema(engine)
        load_tables(engine, purchases=good, crypto=crypto, rejects=rejects)
 
    total = time.perf_counter() - run_start
    log.info(
        "=== Pipeline run finished in %.2fs | purchases=%d rejected=%d crypto_days=%d ===",
        total, len(good), len(rejects), len(crypto),
    )
 
 
if __name__ == "__main__":
    main()
 