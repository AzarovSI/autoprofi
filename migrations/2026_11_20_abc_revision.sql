-- ABC → вкладка «Ревизия матрицы»: решение и заметка по товару (решение владельца 20.11.2026).
-- Рабочие пометки ревизии, отдельно от статусов площадок (catalog_marketplace.status) и без
-- автоматического переноса в них. Одна строка на товар (артикул в верхнем регистре, как в ABC),
-- без истории; пустые решение и заметка — строка удаляется.
CREATE TABLE IF NOT EXISTS abc_revision (
    seller_article     text        PRIMARY KEY,
    decision           text        CHECK (decision IN ('Усилить', 'Оставить', 'Только Ozon', 'Только WB', 'Вывести', 'Подумать')),
    note               text,
    updated_at         timestamptz NOT NULL DEFAULT now(),
    updated_by_user_id integer
);
