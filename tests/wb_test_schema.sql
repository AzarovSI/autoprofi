-- Только изолированная локальная БД wb_price_test. Не production.
CREATE TABLE IF NOT EXISTS app_users (
  id integer PRIMARY KEY, email text, display_name text, role text
);
CREATE TABLE IF NOT EXISTS catalog_marketplace (
  seller_article text, marketplace text, status text
);
CREATE TABLE IF NOT EXISTS catalog_items (
  seller_article text PRIMARY KEY, sample_name text,
  category_l1 text, category_l2 text, category_l3 text
);
CREATE TABLE IF NOT EXISTS catalog_base_prices (
  seller_article text, marketplace text, base_price integer,
  updated_at timestamptz, updated_by_user_id integer
);
CREATE TABLE IF NOT EXISTS catalog_base_prices_hist (
  seller_article text, marketplace text, base_price integer,
  valid_from timestamptz, valid_to timestamptz, updated_by_user_id integer,
  PRIMARY KEY (seller_article, marketplace, valid_from)
);
CREATE TABLE IF NOT EXISTS item_cost_hist (
  seller_article text, cost_calc numeric, start_date date
);
CREATE TABLE IF NOT EXISTS fact_weekly (
  seller_article text, marketplace text, period_start date, period_end date,
  revenue numeric DEFAULT 0, sales_qty numeric DEFAULT 0,
  orders_qty numeric DEFAULT 0, returns_qty numeric DEFAULT 0,
  profit numeric DEFAULT 0, holds_total numeric DEFAULT 0
);
CREATE TABLE IF NOT EXISTS mp_price_daily (
  seller_article text NOT NULL, marketplace text NOT NULL, date date NOT NULL,
  upload_price numeric(12,2), upload_source text,
  upload_loaded_at timestamptz, upload_loaded_by integer,
  spp_pct numeric(6,4), spp_source_date date,
  spp_is_estimated boolean NOT NULL DEFAULT false,
  buyer_price numeric(12,2), updated_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (seller_article, marketplace, date)
);
CREATE TABLE IF NOT EXISTS mp_price_upload_log (
  id bigserial PRIMARY KEY, loaded_at timestamptz NOT NULL DEFAULT now(),
  loaded_by_user_id integer, marketplace text NOT NULL,
  report_date date NOT NULL, file_name text, rows_in_file integer,
  rows_upserted integer, rows_skipped_unknown integer,
  skipped_sample text, status text NOT NULL, message text
);
CREATE TABLE IF NOT EXISTS wb_daily_sales (
  seller_article text, date date, spp_pct numeric
);
CREATE TABLE IF NOT EXISTS ozon_daily_sales (
  seller_article text, date date, spp_pct numeric
);
