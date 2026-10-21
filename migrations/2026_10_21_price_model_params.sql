-- «Цены → Ценовая модель»: вводимые параметры с историей — цена категории, плановый ДРР и плановая маржа
-- (решение владельца 21.10.2026). Заменяет price_category_hist (2026_10_21_price_category): её строки переносятся.
--
-- Одна запись на артикул + площадку + параметр + день (дата по Москве): повторная правка в тот же день
-- перезаписывает значение дня. value NULL — значение удалено с этой даты.
-- Единицы: category_price — целые рубли; drr_pct, margin_pct — доли (0.125 = 12,5%).
-- Отчёт показывает срез на конец периода: последнюю запись с датой ≤ конца периода.
CREATE TABLE IF NOT EXISTS price_model_hist (
    seller_article     text          NOT NULL,
    marketplace        text          NOT NULL CHECK (marketplace IN ('Wildberries', 'Ozon', 'Yandex')),
    param              text          NOT NULL CHECK (param IN ('category_price', 'drr_pct', 'margin_pct')),
    date               date          NOT NULL,
    value              numeric(12,4),
    updated_at         timestamptz   NOT NULL DEFAULT now(),
    updated_by_user_id integer,
    PRIMARY KEY (seller_article, marketplace, param, date)
);

DO $$
BEGIN
    IF to_regclass('price_category_hist') IS NOT NULL THEN
        INSERT INTO price_model_hist (seller_article, marketplace, param, date, value, updated_at, updated_by_user_id)
        SELECT seller_article, marketplace, 'category_price', date, category_price, updated_at, updated_by_user_id
          FROM price_category_hist
        ON CONFLICT DO NOTHING;
        DROP TABLE price_category_hist;
    END IF;
END $$;
