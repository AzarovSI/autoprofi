-- Лист «Воронка» файла «Дашборд - рекламная статистика» (29.10.2026): карточка WB в целом по дням.
-- В отчёте «Реклама → Wildberries» — для CR карточки, доли рекламы в заказах и ДРР общего.
-- Проценты (cr_cart_pct, cr_order_pct, buyout_pct) — доли 0..1; в файле они процентными пунктами.
CREATE TABLE IF NOT EXISTS wb_ads_funnel_daily (
    date              date   NOT NULL,
    nm_id             bigint NOT NULL,
    seller_article    text,
    item_name         text,
    brand             text,
    subject           text,
    visits            numeric,
    carts             numeric,
    deferred          numeric,
    orders            numeric,
    orders_sum        numeric,
    buyouts           numeric,
    buyouts_sum       numeric,
    cancels           numeric,
    cancels_sum       numeric,
    buyout_pct        numeric,
    cr_cart_pct       numeric,
    cr_order_pct      numeric,
    avg_price         numeric,
    currency          text,
    source_updated_at timestamp,
    upload_id         bigint,
    PRIMARY KEY (date, nm_id)
);
CREATE INDEX IF NOT EXISTS idx_wb_ads_funnel_daily_art ON wb_ads_funnel_daily (seller_article, date);
