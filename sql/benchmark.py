import logging
import time
from datetime import datetime, timedelta, timezone
import shutil
from pipeline.extract import CACHE_DIR



from pipeline.extract import (
    get_historical_prices_serial,
    get_historical_prices_concurrent,
)


def _clear_cache():
    if CACHE_DIR.exists():
        shutil.rmtree(CACHE_DIR)

log = logging.getLogger(__name__)

COIN_IDS = [
    "bitcoin",
    "ethereum",
    "tether",
    "binancecoin",
    "solana",
]

LOOKBACK_DAYS = 30

def benchmark():
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=LOOKBACK_DAYS)

    log.info("Benchmark period: %s to %s", start, end)
    log.info("Coins: %s", COIN_IDS)

    _clear_cache()
    log.info("Starting serial extraction (cold cache)...")
    serial_start = time.perf_counter()
    serial_result = get_historical_prices_serial(
        COIN_IDS,
        start,
        end,
    )
    serial_time = time.perf_counter() - serial_start

    log.info(
        "Serial extraction completed in %.2f seconds (%d rows)",
        serial_time,
        len(serial_result),
    )

    _clear_cache()
    # Concurrent
    log.info("Starting concurrent extraction (cold cache)...")
    concurrent_start = time.perf_counter()

    concurrent_result = get_historical_prices_concurrent(
        COIN_IDS,
        start,
        end,
        max_workers=5,
    )

    concurrent_time = time.perf_counter() - concurrent_start

    log.info(
        "Concurrent extraction completed in %.2f seconds (%d rows)",
        concurrent_time,
        len(concurrent_result),
    )

    # Comparison
    speedup = serial_time / concurrent_time if concurrent_time > 0 else 0
    time_saved = serial_time - concurrent_time

    log.info("========== Benchmark Results ==========")
    log.info("Serial time:       %.2f seconds", serial_time)
    log.info("Concurrent time:   %.2f seconds", concurrent_time)
    log.info("Time saved:        %.2f seconds", time_saved)
    log.info("Speedup:            %.2fx", speedup)
    log.info("Rows returned:     %d", len(concurrent_result))

    return {
        "serial_time": serial_time,
        "concurrent_time": concurrent_time,
        "time_saved": time_saved,
        "speedup": speedup,
        "rows": len(concurrent_result),
    }


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(message)s",
    )

    benchmark()