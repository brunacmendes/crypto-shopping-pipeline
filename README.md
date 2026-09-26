# Crypto-Shopping Pipeline
 
An ETL pipeline that combines e-commerce purchase data with daily cryptocurrency
market data, to explore whether crypto market trends correlate with purchasing
patterns.
 
The pipeline joins e-commerce purchase events with daily cryptocurrency market
data (price, daily return, and volatility) from CoinGecko, and loads the
result into PostgreSQL for analysis.
 
## Table of contents
 
- [Dataset](#dataset)
- [Architecture](#architecture)
- [Join logic and assumptions](#join-logic-and-assumptions)
- [Resilience](#resilience)
- [Performance: concurrency](#performance-concurrency)
- [Tests and logging](#tests-and-logging)
- [How to run](#how-to-run)
- [What I'd do differently](#what-id-do-differently)

## Dataset
 
Finding the right shopping dataset took the most time in this project.
The dataset needed real historical purchase **dates** to be joined against
crypto market data — most shopping datasets on Kaggle turned out to be
static customer profiles (buying patterns, demographics, seasonal
preferences) with no timestamp to join on.
 
**Dataset used:** [eCommerce behavior data from multi-category store](https://www.kaggle.com/datasets/mkechinov/ecommerce-behavior-data-from-multi-category-store)
 
> Contains behavior data for 7 months (October 2019–April 2020) from a
> large multi-category online store. Each row is an event; every event
> relates a product to a user (a many-to-many relationship).
 
The raw `event_time` column is a string like `2019-11-15 02:51:51 UTC`
(with a literal `UTC` suffix, not a numeric timestamp). `pandas.to_datetime`
parses this format directly, so `clean_shopping_df` converts it to a proper
UTC `datetime` with no extra handling needed.
 
**Assumption:** the dataset's real year (2019) is shifted to 2025
(`shift_year()`, month/day/time preserved) purely so its dates fall inside
CoinGecko's ~365-day free-tier history window. Any resulting correlation
with crypto prices should be read with that in mind — this shift exists to
make the join possible, not to imply the events actually happened in 2025.
 
## Architecture
 
```
CSV (Kaggle)  ─┐
                ├─▶ extract ─▶ transform ─▶ validate ─▶ load ─▶ PostgreSQL
CoinGecko API ─┘
```
 
### Extract
 
- **Shopping data:** loaded from the source CSV via `kagglehub`.
- **Crypto data:** fetched from the CoinGecko API — the full coin list
  (resolve names/symbols, ids), daily historical prices per
  coin (`/coins/{id}/market_chart/range`), and the current live price.
- **Retry logic:** every API call retries on timeouts
  and connection errors, and on HTTP 429 respects the `Retry-After` header
  when present.
- **Disk cache:** API responses are cached for 24 hours.This avoids re-downloading the ~17k-row coin list
  and repeated historical-price ranges during development.

### Transform
 
**Cleaning** (`clean_shopping_df`):
- normalizes column names (strip, lowercase, spaces → underscores, strip
  special characters);
- converts `event_time` to UTC datetime and `price` to numeric;
- drops rows missing required values;
- keeps only `purchase` events (`view`/`cart` events are removed);
- drops non-positive prices;
- caps extreme prices at the 99th percentile instead of dropping them, to
  avoid one outlier skewing averages while keeping the row.
**Crypto metrics** (`add_crypto_metrics`), calculated independently per
`coin_id`:
- `daily_return` — day-over-day percentage change;
- `volatility_7d` — rolling 7-day standard deviation of daily returns;
- `vol_bucket` — `low` / `medium` / `high`, by tercile of `volatility_7d`.

### Load
 
The final dataset is stored in **PostgreSQL** rather than SQLite, its native types made more sense for this project given
my day-to-day experience with PL/SQL.
 
The schema (`sql/schema.sql`) is a small star schema:
- `dim_crypto_daily` — one row per `(coin_id, date)`, with price and the
  derived metrics above;
- `fact_purchases` — one row per purchase *per matched coin* (see
  [Join logic](#join-logic-and-assumptions));
- `rejected_rows` — an audit log of rows that failed validation;
- `v_spend_by_day_bucket` — a view supporting the assignment's example
  query ("average spend by day, by volatility bucket").
`sql/example_queries.sql` has further analytical queries (median/stddev
by bucket, spend on up vs. down days, category mix by bucket, day-of-week
control).
 
## Join logic and assumptions
 
Each purchase has an `event_time` timestamp; the crypto dataset has daily
observations keyed by `date`. The purchase timestamp is truncated to a UTC
calendar date and stored as `purchase_date`.
 
```
purchase_date  ↕  date
```
 
The pipeline was originally built for a single coin, joining on
`(purchase_date, coin_id)`. After extending extraction to multiple
coins — bitcoin, ethereum, tether, binancecoin, solana, the model
changed, so the join key is `purchase_date` alone, via a **left join**:
 
```python
purchases.merge(crypto, left_on="purchase_date", right_on="date", how="left")
```
 
**This means a single purchase can produce multiple output rows** — one
per coin available on that date (5 coins → 5 rows per purchase). Any
aggregate over `price` must filter or group by `coin_id`, or it will
double- (5×-) count the same purchase.
 
**Assumptions:**
1. Crypto prices are represented daily
2. A purchase is associated with the crypto metrics for its UTC calendar
   day.
3. Each coin is identified by its `coin_id`.
4. The crypto dataset has at most one relevant record per `(coin_id, date)`.
5. A purchase legitimately produces one row per coin, by design — this is
   a one-purchase-to-many-coins comparison.

## Resilience
 
- **Retries:** all CoinGecko calls retry up to 5 times with exponential
  backoff on timeouts and connection errors.
- **Rate limiting (HTTP 429):** handled explicitly — the client waits for
  the duration in `Retry-After` when the API provides it, or an
  exponential fallback otherwise, then retries.
- **Caching** since historical price ranges
  are cached for 24h.

## Performance: concurrency
 
The initial extraction fetched each coin's historical prices
**sequentially** and the pipeline waited for one HTTP request to finish
before starting the next. Since these requests are independent and the
cost is network I/O rather than CPU work, this is a natural fit for
concurrency (threads, not processes, no CPU).
 
I implemented a second extraction path using `ThreadPoolExecutor` and
benchmarked it against the serial version:
 
- **Serial:** one coin's request at a time.
- **Concurrent:** 5 coins' requests in parallel, 5 worker threads.
Both use the same coins and date range; both cache directories are
cleared before each run. Timed with `time.perf_counter()`.
 
| Approach   | Execution time | Rows returned |
|------------|-----------------|----------------|
| Serial     | _XX.XX s_       | _XXXX_         |
| Concurrent | _XX.XX s_       | _XXXX_         |
 
 
In production, I'd make the number of concurrent workers configurable
based on the API's actual rate limits, and I'd run the benchmark several
times and report the average rather than a single run.
 
## Tests and logging
 
Unit tests cover the main transformation steps — data cleaning, the year
shift, crypto metrics, and the join.
 
Logging runs throughout the pipeline: each step (extract, transform,
validate, load) logs its start, duration, and row counts, and errors are
logged with context. This makes it possible to tell where and why a run
failed without re-running it.
 
## How to run
 
**Requirements:** Python 3.10+, PostgreSQL 16+ (tested against 18),
a CoinGecko API key, a Kaggle API token.
 
```bash
git clone <repo-url>
```
```bash
cd crypto-shopping-pipeline
 ```
 ```bash
python3 -m venv .venv
 ```
  ```bash
source .venv/bin/activate
 ```
   ```bash
python3 -m pip install -r requirements.txt
 ```
  ```bash
export KAGGLE_API_TOKEN=your_kaggle_token_here
 ```
   ```bash
export COINGECKO_API_KEY=your_coingecko_key_here
 ```
    ```bash
export DATABASE_URL=postgresql+psycopg://postgres:postgres@localhost:5432/pipeline
  ```
  ```bash
python3 -m pipeline.run
```
 
Query the results:
```bash
psql "$DATABASE_URL" -f sql/example_queries.sql
```
 
Run the tests:
```bash
python3 -m pytest
```
 
Run the concurrency benchmark:
```bash
python3 benchmark.py
```
 
## What I'd do differently
 
With more time, I would:
- Add response-shape validation on every CoinGecko endpoint.
- Make the number of concurrent workers and the cache TTL configurable
  via environment variables instead of constants in the code.
- Run the benchmark multiple times and report mean/variance instead of a
  single measurement.
-
 