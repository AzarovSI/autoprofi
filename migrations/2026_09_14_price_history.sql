-- 2026-09-14: история цен (базовая + посуточная mp_price_daily) + журнал загрузок.
-- Применено к прод-БД одной транзакцией.
BEGIN;

CREATE TABLE catalog_base_prices_hist (
  seller_article       text        NOT NULL,
  marketplace          text        NOT NULL,
  base_price           integer,
  valid_from           timestamptz NOT NULL,
  valid_to             timestamptz,
  updated_by_user_id   integer,
  PRIMARY KEY (seller_article, marketplace, valid_from)
);
CREATE INDEX ix_cbp_hist_lookup
    ON catalog_base_prices_hist (seller_article, marketplace, valid_from DESC);

INSERT INTO catalog_base_prices_hist
       (seller_article, marketplace, base_price, valid_from, valid_to, updated_by_user_id)
SELECT seller_article, marketplace, base_price, updated_at, NULL, updated_by_user_id
  FROM catalog_base_prices;

CREATE TABLE mp_price_daily (
  seller_article       text          NOT NULL,
  marketplace          text          NOT NULL,
  date                 date          NOT NULL,
  upload_price         numeric(12,2),
  upload_source        text,
  upload_loaded_at     timestamptz,
  upload_loaded_by     integer,
  spp_pct              numeric(6,4),
  spp_source_date      date,
  spp_is_estimated     boolean       NOT NULL DEFAULT false,
  buyer_price          numeric(12,2),
  updated_at           timestamptz   NOT NULL DEFAULT now(),
  PRIMARY KEY (seller_article, marketplace, date)
);
CREATE INDEX ix_mpd_lookup
    ON mp_price_daily (seller_article, marketplace, date DESC);

CREATE TABLE mp_price_upload_log (
  id                   bigserial   PRIMARY KEY,
  loaded_at            timestamptz NOT NULL DEFAULT now(),
  loaded_by_user_id    integer,
  marketplace          text        NOT NULL,
  report_date          date        NOT NULL,
  file_name            text,
  rows_in_file         integer,
  rows_upserted        integer,
  rows_skipped_unknown integer,
  skipped_sample       text,
  status               text        NOT NULL,
  message              text
);
CREATE INDEX ix_mpul_recent  ON mp_price_upload_log (loaded_at DESC);
CREATE INDEX ix_mpul_mp_date ON mp_price_upload_log (marketplace, report_date DESC);

COMMIT;
