-- Миграция: раздел «Продажи, шт.» (план/факт). Идемпотентна.
-- 1) Таблица плана продаж в штуках (по артикулу/маркетплейсу/месяцу).
CREATE TABLE IF NOT EXISTS sales_plan (
    seller_article text NOT NULL,
    marketplace    text NOT NULL,   -- 'Ozon' / 'Wildberries' (как в fact_monthly)
    year           int  NOT NULL,
    month          int  NOT NULL,   -- 1..12
    plan_qty       numeric,         -- план в штуках (обычно целое)
    updated_at     timestamptz DEFAULT now(),
    PRIMARY KEY (seller_article, marketplace, year, month)
);

CREATE INDEX IF NOT EXISTS idx_sales_plan_mp_year ON sales_plan (marketplace, year);

-- 2) «Факт по дату» в журнале загрузок: до какого числа месяца собран факт
--    в загруженном МЕСЯЧНОМ отчёте. NULL ⇒ месяц считается ПОЛНЫМ.
ALTER TABLE report_uploads ADD COLUMN IF NOT EXISTS fact_through_date date;
