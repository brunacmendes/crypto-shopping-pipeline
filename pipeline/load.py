import json
import logging
import os
from pathlib import Path
 
import pandas as pd

from sqlalchemy import create_engine, text
 
log = logging.getLogger(__name__)
SCHEMA_PATH = Path("sql/schema.sql")

FACT_PURCHASE_COLUMNS = [
    "event_time",
    "purchase_date",
    "event_type",
    "user_id",
    "user_session",
    "product_id",
    "category_id",
    "category_code",
    "brand",
    "price",
    "coin_id",
]

CRYPTO_COLUMNS = [
    "coin_id",
    "date",
    "price_usd",
    "volume_usd",
    "daily_return",
    "volatility_7d",
    "vol_bucket",
]

##connect 
def get_engine():
    url = os.environ.get(
        "DATABASE_URL",
        "postgresql+psycopg://postgres:postgres@localhost:5432/pipeline",
    )
    return create_engine(url)


def create_schema(engine) -> None:
    with engine.begin() as conn:
        conn.exec_driver_sql(SCHEMA_PATH.read_text())
    log.info("Schema created")

def _select_columns(df: pd.DataFrame, columns: list[str], table_name: str) -> pd.DataFrame:
    missing = [c for c in columns if c not in df.columns]
    if missing:
        raise ValueError(f"DataFrame for {table_name} is missing columns: {missing}")
    return df[columns].copy()

def load_tables(
    engine,
    purchases: pd.DataFrame,
    crypto: pd.DataFrame,
    rejects: pd.DataFrame | None = None,
) -> None:
    """
    records rejected rows separately.
 
    purchase is expected to be the joined DataFrame (output of
    transform.join_purchases_crypto), but only FACT_PURCHASE_COLUMNS are
    actually inserted.
 
    TRUNCATE before insert, so re-running the pipeline doesn't
    duplicate rows.
    """
    crypto_to_load = _select_columns(crypto, CRYPTO_COLUMNS, "dim_crypto_daily")
    purchases_to_load = _select_columns(purchases, FACT_PURCHASE_COLUMNS, "fact_purchases")
 
    with engine.begin() as conn:
        conn.execute(text("TRUNCATE fact_purchases, dim_crypto_daily RESTART IDENTITY"))
 
        # crypto first: fact_purchases logically depends on it
        crypto_to_load.to_sql("dim_crypto_daily", conn, if_exists="append", index=False)
        purchases_to_load.to_sql("fact_purchases", conn, if_exists="append", index=False)
 
    log.info(
        "Loaded %d purchases, %d crypto days into PostgreSQL",
        len(purchases_to_load), len(crypto_to_load),
    )
 
    if rejects is not None and not rejects.empty:
        _load_rejects(engine, rejects)
 
 
def _load_rejects(engine, rejects: pd.DataFrame) -> None:
    """Appends rejected rows to the audit table (not truncated — this is a log)."""
    if rejects.empty:
        return

    rows = []

    for _, r in rejects.iterrows():
        clean_row = r.where(pd.notna(r), None).to_dict()

    rows.append(
            {
                "reason": r["reason"],
                "raw_row": json.dumps(clean_row),
            }
        )

    with engine.begin() as conn:
        conn.execute(
            text("INSERT INTO rejected_rows (reason, raw_row) VALUES (:reason, :raw_row)"),
            rows,
        )
    log.info("Logged %d rejected rows to rejected_rows", len(rows))
 
 
def run_example_query(engine, sql: str = "SELECT * FROM v_spend_by_day_bucket ORDER BY date") -> pd.DataFrame:
    with engine.connect() as conn:
        return pd.read_sql(text(sql), conn)
 