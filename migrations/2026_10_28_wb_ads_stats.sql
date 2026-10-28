-- Статистика рекламы Wildberries для раздела «Реклама» (28.10.2026): файл владельца
-- «Дашборд - рекламная статистика», загрузчик app/wb_ads_stats_loader.py.
-- Проценты (ctr_pct, cr_pct) — доли 0..1; в файле они процентными пунктами.

-- Лист «Статистика кампаний»: день × кампания × артикул WB. in_campaign — рекламируемый товар
-- («Да»); false — «Нет, связанный заказ»: заказ другого товара через эту кампанию, без затрат.
-- campaign_status — статус на момент выгрузки (одинаковый во всех днях файла).
CREATE TABLE IF NOT EXISTS wb_ads_campaign_daily (
    date              date    NOT NULL,
    campaign_id       bigint  NOT NULL,
    nm_id             bigint  NOT NULL,
    campaign_name     text,
    placement         text,
    campaign_status   text,
    payment_type      text,
    bid_type          text,
    seller_article    text,
    item_name         text,
    in_campaign       boolean NOT NULL,
    in_campaign_text  text,
    views             numeric,
    clicks            numeric,
    ctr_pct           numeric,
    cpc               numeric,
    spend             numeric,
    atbs              numeric,
    orders            numeric,
    ordered_qty       numeric,
    orders_sum        numeric,
    cr_pct            numeric,
    cancels_qty       numeric,
    currency          text,
    source_updated_at timestamp,
    upload_id         bigint,
    PRIMARY KEY (date, campaign_id, nm_id)
);
CREATE INDEX IF NOT EXISTS idx_wb_ads_campaign_daily_art ON wb_ads_campaign_daily (seller_article, date);

-- Лист «Поисковые кластеры»: день × кампания × поисковый запрос (только поисковые кампании).
-- У кампаний с оплатой за клики показов, CTR и CPM в файле нет — NULL.
CREATE TABLE IF NOT EXISTS wb_ads_cluster_daily (
    date              date    NOT NULL,
    campaign_id       bigint  NOT NULL,
    nm_id             bigint  NOT NULL,
    cluster           text    NOT NULL,
    campaign_name     text,
    placement         text,
    campaign_status   text,
    payment_type      text,
    seller_article    text,
    item_name         text,
    views             numeric,
    clicks            numeric,
    ctr_pct           numeric,
    cpc               numeric,
    cpm               numeric,
    spend             numeric,
    atbs              numeric,
    orders            numeric,
    ordered_qty       numeric,
    avg_position      numeric,
    source_updated_at timestamp,
    upload_id         bigint,
    PRIMARY KEY (date, campaign_id, nm_id, cluster)
);
CREATE INDEX IF NOT EXISTS idx_wb_ads_cluster_daily_art ON wb_ads_cluster_daily (seller_article, date);

-- Лист «_Справочник товаров»: артикул WB → артикул продавца (последняя загрузка).
CREATE TABLE IF NOT EXISTS wb_ads_items (
    nm_id          bigint PRIMARY KEY,
    seller_article text   NOT NULL,
    item_name      text,
    upload_id      bigint,
    updated_at     timestamptz NOT NULL DEFAULT now()
);

-- Права №120–121 (загрузка и журнал) — роли «Маркетинг», как и весь раздел (решение владельца
-- 27.10.2026). Администратору строки не нужны.
INSERT INTO app_role_permissions (role_id, permission)
SELECT r.id, p.permission
FROM app_roles r
CROSS JOIN (VALUES ('ads.upload.wb'), ('ads.uploads.log')) AS p (permission)
WHERE r.name = 'Маркетинг'
ON CONFLICT DO NOTHING;
