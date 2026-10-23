-- Комментарии к дневным ячейкам категорий третьего уровня в РНП (23.10.2026, решение владельца).
-- Комментарий относится либо к товару (seller_article), либо к категории L3 (category_key —
-- путь «L1|L2|L3», как ключ узла дерева в rnp_sales_router). Переименование категории в
-- справочнике отрывает её прежние комментарии: они остаются в таблице под старым путём.
ALTER TABLE rnp_comments ADD COLUMN IF NOT EXISTS category_key text;
ALTER TABLE rnp_comments ALTER COLUMN seller_article DROP NOT NULL;
ALTER TABLE rnp_comments
    ADD CONSTRAINT rnp_comments_target_check CHECK ((seller_article IS NULL) <> (category_key IS NULL));
