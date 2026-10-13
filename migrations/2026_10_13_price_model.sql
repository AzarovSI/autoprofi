-- «Цены → Ценовая модель»: цена категории, которую вводит продакт-менеджер.
-- Из неё и СПП площадки сайт считает рекомендуемую загружаемую цену.
-- Одно текущее значение на артикул + площадку; пустое значение = строки нет.
CREATE TABLE IF NOT EXISTS price_model_category (
    seller_article     text        NOT NULL,
    marketplace        text        NOT NULL CHECK (marketplace IN ('Wildberries', 'Ozon')),
    category_price     integer     NOT NULL CHECK (category_price > 0),
    updated_at         timestamptz NOT NULL DEFAULT now(),
    updated_by_user_id integer,
    PRIMARY KEY (seller_article, marketplace)
);
