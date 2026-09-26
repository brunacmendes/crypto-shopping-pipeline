--Median and dispersion by bucket (per coin)

SELECT p.coin_id,
       d.vol_bucket,
       COUNT(*)                                                             AS n,
       ROUND(AVG(p.price)::numeric, 2)                                      AS avg_spend,
       ROUND((percentile_cont(0.5) WITHIN GROUP (ORDER BY p.price))::numeric, 2) AS median_spend,
       ROUND(STDDEV(p.price)::numeric, 2)                                   AS stddev_spend
FROM fact_purchases p
JOIN dim_crypto_daily d ON d.coin_id = p.coin_id AND d.date = p.purchase_date
WHERE p.coin_id = 'bitcoin'
GROUP BY p.coin_id, d.vol_bucket
ORDER BY CASE d.vol_bucket WHEN 'low' THEN 1 WHEN 'medium' THEN 2 ELSE 3 END;
 
 
--do people spend differently when crypto is up vs. down
-- (independent of volatility)?
SELECT CASE WHEN d.daily_return > 0 THEN 'up'
            WHEN d.daily_return < 0 THEN 'down'
            ELSE 'flat' END                 AS market_move,
       COUNT(*)                             AS n,
       ROUND(AVG(p.price)::numeric, 2)      AS avg_spend
FROM fact_purchases p
JOIN dim_crypto_daily d ON d.coin_id = p.coin_id AND d.date = p.purchase_date
WHERE p.coin_id = 'bitcoin'
  AND d.daily_return IS NOT NULL
GROUP BY market_move;
 
 
-- Category mix by bucket (using a window function)
--does what people buy change on more volatile days?
WITH spend AS (
    SELECT d.vol_bucket, p.category_code, SUM(p.price) AS total
    FROM fact_purchases p
    JOIN dim_crypto_daily d ON d.coin_id = p.coin_id AND d.date = p.purchase_date
    WHERE p.coin_id = 'bitcoin'
      AND p.category_code IS NOT NULL
    GROUP BY d.vol_bucket, p.category_code
)
SELECT vol_bucket, category_code,
       ROUND(total::numeric, 2) AS total_spend,
       ROUND((100 * total / SUM(total) OVER (PARTITION BY vol_bucket))::numeric, 1) AS pct_of_bucket
FROM spend
ORDER BY vol_bucket, pct_of_bucket DESC;
 
 
--is there a linear relationship between daily spend and the volatility (or the size of the move) of ONE specific coin? Run this onc
WITH daily AS (
    SELECT p.purchase_date AS date,
           SUM(p.price) AS total_spend,
           d.volatility_7d,
           d.daily_return
    FROM fact_purchases p
    JOIN dim_crypto_daily d ON d.coin_id = p.coin_id AND d.date = p.purchase_date
    WHERE p.coin_id = 'bitcoin'
    GROUP BY p.purchase_date, d.volatility_7d, d.daily_return
)
SELECT COUNT(*)                                               AS n_days,
       ROUND(corr(total_spend, volatility_7d)::numeric, 3)    AS corr_spend_vol,
       ROUND(corr(total_spend, ABS(daily_return))::numeric, 3) AS corr_spend_abs_move
FROM daily;

WITH daily_summary AS (
    SELECT
        p.date,
        p.coin_id,
        c.volatility_7d,
        c.daily_return,
        AVG(p.price_usd * p.quantity) AS avg_spend
    FROM fact_purchases p
    JOIN dim_crypto_daily c
        ON c.coin_id = p.coin_id
       AND c.date = p.date
    GROUP BY
        p.date,
        p.coin_id,
        c.volatility_7d,
        c.daily_return
)
SELECT
    coin_id,
    COUNT(*) AS n_days,
    CORR(avg_spend, volatility_7d) AS corr_spend_vol,
    CORR(avg_spend, ABS(daily_return)) AS corr_spend_abs_move
FROM daily_summary
WHERE volatility_7d IS NOT NULL
GROUP BY coin_id
ORDER BY coin_id;

--does yesterday's return explain today's spend?
WITH ret AS (
    SELECT date, daily_return,
           LAG(daily_return) OVER (ORDER BY date) AS prev_return
    FROM dim_crypto_daily
    WHERE coin_id = 'bitcoin'
), spend AS (
    SELECT purchase_date AS date, SUM(price) AS total_spend
    FROM fact_purchases
    WHERE coin_id = 'bitcoin'
    GROUP BY purchase_date
)
SELECT ROUND(corr(s.total_spend, r.daily_return)::numeric, 3) AS same_day,
       ROUND(corr(s.total_spend, r.prev_return)::numeric, 3)  AS next_day,
       COUNT(*) AS n_days
FROM spend s
JOIN ret r USING (date);
 
 --average spend by day and volatility bucket
SELECT
    p.purchase_date                      AS date,
    COALESCE(d.vol_bucket, 'unknown')    AS vol_bucket,
    COUNT(*)                             AS n_purchases,
    ROUND(AVG(p.price)::numeric, 2)      AS avg_spend
FROM fact_purchases p
LEFT JOIN dim_crypto_daily d
       ON d.coin_id = p.coin_id AND d.date = p.purchase_date
WHERE p.coin_id = 'bitcoin'
GROUP BY p.purchase_date, COALESCE(d.vol_bucket, 'unknown')
ORDER BY date;


SELECT
    REGR_SLOPE(avg_spend, volatility_7d) AS slope,
    REGR_INTERCEPT(avg_spend, volatility_7d) AS intercept,
    REGR_R2(avg_spend, volatility_7d) AS r_squared
FROM daily_summary
WHERE volatility_7d IS NOT NULL;
 