-- MPPROFIT WB: выплата на р/с с рекламой, разнесённой по товарам, и её состав.
-- Колонки отчёта: «Выплата на р/с (с рекл.)», «Баланс», «Промо бонусы».
-- Для Ozon/Яндекса и для периодов, загруженных до миграции, — NULL.
ALTER TABLE fact_weekly
    ADD COLUMN IF NOT EXISTS wb_payout_with_ads numeric,
    ADD COLUMN IF NOT EXISTS wb_ads_balance     numeric,
    ADD COLUMN IF NOT EXISTS wb_ads_bonus       numeric;

ALTER TABLE fact_monthly
    ADD COLUMN IF NOT EXISTS wb_payout_with_ads numeric,
    ADD COLUMN IF NOT EXISTS wb_ads_balance     numeric,
    ADD COLUMN IF NOT EXISTS wb_ads_bonus       numeric;
