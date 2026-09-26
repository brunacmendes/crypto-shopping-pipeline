# Install dependencies
from os import path
import os
import json
import kagglehub
from kagglehub import KaggleDatasetAdapter
import pandas as pd
from pathlib import Path
import logging
import requests
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed

import time

#setup logging
log = logging.getLogger(__name__)


#run before this code in the terminal
#export COINGECKO_API_KEY=CG-hpTw92gXRHBGaQT1EAoy2ykR
#export KAGGLE_API_TOKEN=KGAT_779255e679a8ca144bfbae2120da5203

#API info for coingcko
BASE_URL = "https://api.coingecko.com/api/v3"
API_KEY = os.environ.get("COINGECKO_API_KEY")  
CACHE_DIR = Path(".cache/coingecko")
CACHE_TTL_SECONDS = 60 * 60 * 24  #1 day


MAX_RETRIES = 5
BASE_BACKOFF = 1.0 #in seconds



# ----------SHOPPING DATASET CSV ----------

def load_shopping_dataset(path: str) -> pd.DataFrame:
    log.info("Loading shopping CSV file %s", path)
    # Load the latest version of the dataset from Kaggle
    df = kagglehub.dataset_load(
        KaggleDatasetAdapter.PANDAS,
        "mkechinov/ecommerce-behavior-data-from-multi-category-store",path)
    #path = kagglehub.dataset_download("zubairdhuddi/shopping-dataset")
    #csv_files = list(Path(path).rglob("*.csv"))
    
    log.info("Loaded %d rows, %d columns", *df.shape)
    return df

# ----------COINGECKO API AUTHENTICATION ----------

def _session() -> requests.Session:
    s = requests.Session()
    if API_KEY:
        s.headers.update({"x-cg-demo-api-key": API_KEY})
    return s


# ---------- RETRY LOGIC----------

def _request_with_retry(session: requests.Session, url: str, params: dict) -> dict:
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = session.get(url, params=params, timeout=10)

            if resp.status_code == 429:
                wait = float(resp.headers.get("Retry-After", BASE_BACKOFF * 2 ** attempt))
                log.warning("Rate limited (429). Waiting %.1fs (attempt %d/%d)", wait, attempt, MAX_RETRIES)
                time.sleep(wait)
                continue

            resp.raise_for_status()
            return resp.json()

        except (requests.Timeout, requests.ConnectionError) as e:
            wait = BASE_BACKOFF * 2 ** (attempt - 1)
            log.warning("Request failed (%s). Retrying in %.1fs (attempt %d/%d)", e, wait, attempt, MAX_RETRIES)
            time.sleep(wait)

        except requests.HTTPError as e:
            log.error("HTTP error calling %s: %s", url, e)
            raise

    raise RuntimeError(f"Failed to fetch {url} after {MAX_RETRIES} attempts")

# ---------- CACHE ----------

def _cache_path(key: str) -> Path: #make sure path does not have invalid chars
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    safe_key = key.replace("/", "_").replace("?", "_")
    return CACHE_DIR / f"{safe_key}.json"

def _read_cache(key: str) -> dict | None:
    path = _cache_path(key)
    if not path.exists():
        return None
    age = time.time() - path.stat().st_mtime
    if age > CACHE_TTL_SECONDS:
        return None
    return json.loads(path.read_text())

def _write_cache(key: str, data: dict) -> None:
    _cache_path(key).write_text(json.dumps(data))

#validations

def _validate_coins_list(data) -> None:
    if not isinstance(data, list) or not data:
        raise ValueError(f"Malformed coins list response: {data!r}")
    sample = data[0]
    if not {"id", "symbol", "name"}.issubset(sample.keys()):
        raise ValueError(f"Unexpected coin entry shape: {sample!r}")
 

def _validate_market_chart(data: dict) -> None:
    if "prices" not in data or not isinstance(data["prices"], list):
        raise ValueError(f"Malformed market_chart response: {data!r}")
    if data["prices"] and (
        not isinstance(data["prices"][0], list) or len(data["prices"][0]) != 2
    ):
        raise ValueError(f"Unexpected 'prices' entry shape: {data['prices'][0]!r}")

## CREATE DF FROM API ENDPOINTS

def get_coin_ids(session: requests.Session | None = None) -> pd.DataFrame:
    """Returns the full CoinGecko coin list as a DataFrame: id, symbol, name."""
    session = session or _session()
    cache_key = "coins_list"

    data = _read_cache(cache_key)
    if data is None:
        data = _request_with_retry(
            session, f"{BASE_URL}/coins/list", {"include_platform": "false"}
        )
        _validate_coins_list(data)
        _write_cache(cache_key, data)
    else:
        log.info("Using cached coins list (%d coins)", len(data))

    df = pd.DataFrame(data)  # columns: id, symbol, name
    log.info("Fetched %d coin ids from CoinGecko", len(df))
    return df

def resolve_coin_id(
    name_or_symbol: str,
    coins: pd.DataFrame | None = None,
    session: requests.Session | None = None,
) -> str:
    coins = coins if coins is not None else get_coin_ids(session)
 
    query = name_or_symbol.lower().strip()

     # Exact CoinGecko ID match
    matches = coins[coins["id"].str.lower() == query]
    if len(matches) == 1:
        return matches.iloc[0]["id"]
    #matches = coins[
     #   (coins["id"] == query)
      #  | (coins["symbol"].str.lower() == query)
      #  | (coins["name"].str.lower() == query)
    #]
       # Exact name match
    matches = coins[coins["name"].str.lower() == query]
    if len(matches) == 1:
        return matches.iloc[0]["id"]
    
  # Exact symbol match
    matches = coins[coins["symbol"].str.lower() == query]
    if len(matches) == 1:
        return matches.iloc[0]["id"]
    
    if matches.empty:
        raise ValueError(f"Coin '{name_or_symbol}' not found in CoinGecko coins list")
    
    if len(matches) > 1:
        log.warning(
            "Multiple matches for '%s', using first: %s",
            name_or_symbol, matches.iloc[0]["id"],
        )
    return matches.iloc[0]["id"]

def get_live_price(coin_id: str, session: requests.Session | None = None) -> dict:
    """Current price snapshot. Not cached — this is meant to always be fresh."""
    session = session or _session()
    data = _request_with_retry(
        session,
        f"{BASE_URL}/simple/price",
        {"ids": coin_id, "vs_currencies": "usd"},
    )
    return data[coin_id]

def get_historical_prices(
    coin_id: str,
    start: datetime,
    end: datetime,
    session: requests.Session | None = None,
) -> pd.DataFrame:
    session = session or _session()
    cache_key = f"range_{coin_id}_{start.date()}_{end.date()}"
 
    data = _read_cache(cache_key)
    if data is None:
        params = {
            "vs_currency": "usd",
            "from": int(start.replace(tzinfo=timezone.utc).timestamp()),
            "to": int(end.replace(tzinfo=timezone.utc).timestamp()),
            "interval": "daily",
        }
        data = _request_with_retry(
            session, f"{BASE_URL}/coins/{coin_id}/market_chart/range", params
        )
        _validate_market_chart(data)
        _write_cache(cache_key, data)
    else:
        log.info("Using cached response for %s", cache_key)
 
    prices = pd.DataFrame(data["prices"], columns=["timestamp_ms", "price_usd"])
    prices["date"] = pd.to_datetime(prices["timestamp_ms"], unit="ms", utc=True).dt.date
    prices["price_usd"] = prices["price_usd"].round(2)
    prices["coin_id"] = coin_id
 
    if "total_volumes" in data:
        volumes = pd.DataFrame(data["total_volumes"], columns=["timestamp_ms", "volume_usd"])
        prices = prices.merge(volumes, on="timestamp_ms", how="left")
    else:
        prices["volume_usd"] = None
 
    # near "today", the API can return more than one point per day; keep one
    prices = prices.drop_duplicates(subset="date", keep="last")
    return prices[["coin_id", "date", "price_usd", "volume_usd"]]

def get_historical_prices_serial(
    coin_ids: list[str],
    start: datetime,
    end: datetime,
) -> pd.DataFrame:
    """Fetch historical prices sequentially for multiple coins."""

    results = []

    log.info(
        "Starting serial historical extraction for %d coins",
        len(coin_ids),
    )

    for coin_id in coin_ids:
        log.info("Fetching historical data: coin_id=%s", coin_id)

        df = get_historical_prices(
            coin_id,
            start,
            end,
        )

        results.append(df)

    if not results:
        return pd.DataFrame()

    return pd.concat(results, ignore_index=True)


def get_historical_prices_concurrent(
    coin_ids: list[str],
    start: datetime,
    end: datetime,
    max_workers: int = 5,
) -> pd.DataFrame:
    """Fetch historical prices concurrently for multiple coins."""

    results = {}

    log.info(
        "Starting concurrent historical extraction for %d coins "
        "with %d workers",
        len(coin_ids),
        max_workers,
    )

    with ThreadPoolExecutor(max_workers=max_workers) as executor:

        futures = {
            executor.submit(
                get_historical_prices,
                coin_id,
                start,
                end,
            ): coin_id
            for coin_id in coin_ids
        }

        for future in as_completed(futures):

            coin_id = futures[future]

            try:
                df = future.result()

                results[coin_id] = df

                log.info(
                    "Completed historical extraction: "
                    "coin_id=%s rows=%d",
                    coin_id,
                    len(df),
                )

            except Exception:
                log.exception(
                    "Historical extraction failed: coin_id=%s",
                    coin_id,
                )
                raise

    if not results:
        return pd.DataFrame()

    return pd.concat(
        results.values(),
        ignore_index=True,
    )