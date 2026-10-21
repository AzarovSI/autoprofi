-- «Цены → Ценовая модель»: справочник средних цен категории с историей (решение владельца 21.10.2026).
-- Прежний лист «Цена категории + Загружаемая расчётная» (2026_10_13_price_model) отменён,
-- его тестовые значения не переносятся.
DROP TABLE IF EXISTS price_model_category;

-- Одна запись на артикул + площадку + день (дата по Москве): повторная правка в тот же день
-- перезаписывает значение дня. category_price NULL — цена удалена с этой даты.
-- Отчёт показывает срез на конец периода: последнюю запись с датой ≤ конца периода.
CREATE TABLE IF NOT EXISTS price_category_hist (
    seller_article     text        NOT NULL,
    marketplace        text        NOT NULL CHECK (marketplace IN ('Wildberries', 'Ozon', 'Yandex')),
    date               date        NOT NULL,
    category_price     integer     CHECK (category_price > 0),
    updated_at         timestamptz NOT NULL DEFAULT now(),
    updated_by_user_id integer,
    PRIMARY KEY (seller_article, marketplace, date)
);
