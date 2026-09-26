CREATE TABLE IF NOT EXISTS dim_crypto_daily (
    coin_id        TEXT             NOT NULL,
    date           DATE             NOT NULL,
    price_usd      DOUBLE PRECISION NOT NULL CHECK (price_usd > 0),
    volume_usd     DOUBLE PRECISION,
    daily_return   DOUBLE PRECISION,
    volatility_7d  DOUBLE PRECISION,
    vol_bucket     TEXT CHECK (vol_bucket IN ('low', 'medium', 'high')),
    PRIMARY KEY (coin_id, date)
);
 

CREATE TABLE IF NOT EXISTS fact_purchases (
    purchase_id    BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    event_time     TIMESTAMPTZ      NOT NULL,   --already year-shifted
    purchase_date  DATE             NOT NULL,   --event_time column truncated to a day; the join key
    event_type     TEXT             NOT NULL,
    user_id        BIGINT           NOT NULL,
    user_session   TEXT,
    product_id     BIGINT,
    category_id    BIGINT,
    category_code  TEXT,
    brand          TEXT,
    price          DOUBLE PRECISION NOT NULL CHECK (price > 0),
    coin_id        TEXT             NOT NULL
);
 
CREATE INDEX IF NOT EXISTS idx_purchases_date_coin
    ON fact_purchases (purchase_date, coin_id);
 
CREATE TABLE IF NOT EXISTS rejected_rows (
    id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    reason      TEXT  NOT NULL,
    raw_row     JSONB NOT NULL,
    rejected_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
 
CREATE OR REPLACE VIEW v_spend_by_day_bucket AS
SELECT
    p.purchase_date                      AS date,
    COALESCE(d.vol_bucket, 'unknown')    AS vol_bucket,
    COUNT(*)                             AS n_purchases,
    ROUND(AVG(p.price)::numeric, 2)      AS avg_spend_usd,
    ROUND(SUM(p.price)::numeric, 2)      AS total_spend_usd
FROM fact_purchases p
LEFT JOIN dim_crypto_daily d
       ON d.coin_id = p.coin_id AND d.date = p.purchase_date
GROUP BY p.purchase_date, COALESCE(d.vol_bucket, 'unknown');