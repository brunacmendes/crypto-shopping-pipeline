import logging
 
import numpy as np
import pandas as pd
 
log = logging.getLogger(__name__)

#clean shopping df
def clean_shopping_df(df: pd.DataFrame) -> pd.DataFrame:

    df = df.copy()
    before = len(df)

    #rename columns 
    df.columns=(
    df.columns
    .str.strip()
    .str.lower()
    .str.replace(" ","_",regex=False)
    .str.replace(r"[^\w]","",regex=True))

    #convert event_time to datetime to match cryto data
    df["event_time"] = pd.to_datetime(df["event_time"], errors="coerce", utc=True)

    #price to numeric 
    df["price"] = pd.to_numeric(df["price"], errors="coerce")
    df = df[df["price"] > 0]


    log.info("Purchase df null columns:\n%s", df.isna().sum().to_string())
 
    # Drop rows with missing data
    df = df.dropna(subset=["event_time", "user_id", "price"])

    log.info("Event types in dataset:\n%s", df['event_type'].value_counts().to_string())


    #filter event_type to only purchases
    df = df[df["event_type"] == "purchase"]
    log.info(
        "clean_shopping: kept %d/%d rows with event_type == 'purchase'",
        len(df),before,
    )
 
     # Obvious outliers: non-positive price
    df = df[df["price"] > 0]
 
    # Cap extreme prices at the 99th percentile rather than dropping them —
    # keeps the row, avoids one outlier skewing averages.
    if len(df) > 0:
        cap = df["price"].quantile(0.99)
        n_capped = (df["price"] > cap).sum()
        df["price"] = df["price"].clip(upper=cap)
    else:
        n_capped = 0
 
    log.info(
        "clean_shopping: %d -> %d rows (dropped %d, capped %d)",
        before, len(df), before - len(df), n_capped,
    )

    return df.reset_index(drop=True)

def shift_year(
    df: pd.DataFrame,
    from_year: int,
    to_year: int,
    date_col: str = "event_time",
) -> pd.DataFrame:
    """Replaces from_year with to_year in date_col, keeping month/day/time
    unchanged, and create column purchase_date (a plain date, UTC) from the
    result.
 
    the dataset's real year (2019) is replaced with a recent one (2025) purely so the
    dates fall inside CoinGecko's ~365-day free-tier history window.
    Month and day-of-month are preserved and only the year itself is fictional. 
    Any resulting correlation with crypto prices
    should be read with that in mind.
 
    Handles the Feb 29 edge case.
    """
    df = df.copy()
    dt = pd.to_datetime(df[date_col], utc=True)
 
    def _replace_year(ts: pd.Timestamp) -> pd.Timestamp:
        try:
            return ts.replace(year=to_year)
        except ValueError:
            # source was Feb 29 and to_year isn't a leap year
            return ts.replace(year=to_year, day=28)
 
    n_from_year = (dt.dt.year == from_year).sum()
    if n_from_year < len(dt):
        log.warning(
            "shift_year: %d/%d rows are not in from_year=%d; they will still "
            "be shifted using their own year's month/day",
            len(dt) - n_from_year, len(dt), from_year,
        )
 
    df[date_col] = dt.apply(_replace_year)
    df["purchase_date"] = df[date_col].dt.date
 
    log.info(
        "shift_year: %s shifted from %d -> %d (%d rows, range %s to %s)",
        date_col, from_year, to_year, len(df),
        df["purchase_date"].min(), df["purchase_date"].max(),
    )
    return df


def _bucket_volatility(vol: pd.Series) -> pd.Series:
    """Buckets a volatility series into low/medium/high, robust to coins
    (e.g. stablecoins) whose volatility is constant or near-constant across
    the window. Falls back to fewer buckets (or a single 'low' bucket) when
    there aren't enough distinct volatility values to form three groups.
    """
    codes, bins = pd.qcut(vol, q=3, labels=False, duplicates="drop", retbins=True)
    n_bins = len(bins) - 1
 
    if n_bins == 0:
        return pd.Series("low", index=vol.index)
 
    label_map = {
        1: {0: "low"},
        2: {0: "low", 1: "high"},
        3: {0: "low", 1: "medium", 2: "high"},
    }[n_bins]
    return codes.map(label_map)

##create a crypto metric 
"""Adds daily_return, volatility_7d, and vol_bucket to a crypto price
    Metrics are calculated independently for each coin_id."""
def add_crypto_metrics(df: pd.DataFrame) -> pd.DataFrame:

    df = df.sort_values(["coin_id", "date"]).copy()

     # Calculate daily return independently for each coin
    df["daily_return"] = (
        df.groupby("coin_id")["price_usd"]
        .pct_change()
    )
    df["volatility_7d"] = (
        df.groupby("coin_id")["daily_return"]
        .rolling(window=7, min_periods=3)
        .std()
        .reset_index(level=0, drop=True)
    )

    df["vol_bucket"] = pd.NA

    for coin_id, group in df.groupby("coin_id"):
        valid = group["volatility_7d"].notna()
        if valid.sum() < 3:
            continue
        vol_values = group.loc[valid, "volatility_7d"]
        n_distinct = vol_values.nunique()
        if n_distinct < 3:
            log.warning(
                "add_crypto_metrics: %s has only %d distinct volatility "
                "value(s) in the valid window — using fewer buckets "
                "instead of the usual low/medium/high split",
                coin_id, n_distinct,
            )
        df.loc[group.index[valid], "vol_bucket"] = _bucket_volatility(vol_values)

    log.info(
        "add_crypto_metrics: %d rows, %d coins, %d bucketed rows",
        len(df), df["coin_id"].nunique(), df["vol_bucket"].notna().sum(),
    )
    
    return df.reset_index(drop=True)

##join purchases and crypto dataframes
    """
    Left join between purchases and crypto metrics.

    Each purchase is matched with the available crypto metrics
    for its purchase date. Since crypto contains multiple coins,
    one purchase can produce one row per coin.

    Join key:
        purchase_date <-> date
    """
def join_purchases_crypto(
    purchases: pd.DataFrame,
    crypto: pd.DataFrame,
    ##coin_id: str,
) -> pd.DataFrame:
    
    purchases = purchases.copy()
    purchases["purchase_date"] = pd.to_datetime(purchases["purchase_date"]).dt.date
 
    crypto_slim = crypto[["coin_id", "date", "vol_bucket", "daily_return", "volatility_7d"]]
 
    joined = purchases.merge(
        crypto_slim,
        left_on=["purchase_date"],
        right_on=["date"],
        how="left",
    ).drop(columns=["date"])
 
    n_unmatched = joined["vol_bucket"].isna().sum()
    log.info(
        "join_purchases_crypto: %d purchases joined, %d unmatched (%.1f%%),%d coins",
        len(joined), n_unmatched, 100 * n_unmatched / max(len(joined), 1),crypto["coin_id"].nunique(),
    )
    return joined



REQUIRED_COLUMNS = [
    "event_time", "purchase_date", "event_type",
    "user_id", "price", "coin_id",
]

def validate_all(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    #Returns (good, rejects)

    missing_cols = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if missing_cols:
        raise ValueError(f"Schema check failed — missing columns: {missing_cols}")
 
    df = df.copy()
    reasons = pd.Series([None] * len(df), index=df.index, dtype="object")
 
    # Null checks on required keys
    null_mask = df[REQUIRED_COLUMNS].isna().any(axis=1)
    reasons[null_mask] = "null_in_required_column"
 
    # Range checks
    bad_price = df["price"] <= 0
    reasons[bad_price & reasons.isna()] = "non_positive_price"
 
    dup_mask = df.duplicated(
        subset=["user_id", "user_session", "event_time", "product_id","coin_id"], keep="first"
    )
    reasons[dup_mask & reasons.isna()] = "duplicate_row"
 
    is_bad = reasons.notna()
    good = df[~is_bad].drop(columns=[], errors="ignore").reset_index(drop=True)
    rejects = df[is_bad].copy()
    rejects["reason"] = reasons[is_bad]
    rejects = rejects.reset_index(drop=True)
 
    log.info(
        "validate_all: %d good, %d rejected (%s)",
        len(good), len(rejects),
        rejects["reason"].value_counts().to_dict() if not rejects.empty else {},
    )
    return good, rejects


#print("Dataset shape:")
#print(df.shape)

#print("\nFirst 5 rows:")
#print(df.head())

#print("\nDataset info:")
#print(df.info())

#print("\nSummary statistics for numeric columns:")
#print(df.describe())

#print("\nSummary statistics for text columns")
#print(df.describe(include="str"))


