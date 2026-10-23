-- Комментарии РНП — отдельно по площадке (23.10.2026). Раньше ячейка была (артикул, день),
-- и комментарий, оставленный в РНП одной площадки, показывался в РНП всех площадок.
ALTER TABLE rnp_comments ADD COLUMN IF NOT EXISTS marketplace text;

-- Комментарии, оставленные до миграции, площадку не записали. Все 8 комментариев в рабочей БД
-- на 23.10.2026 (21–22.10, Рябец И. и Азарова Е.) оставлены в РНП WB — решение владельца 23.10.2026.
UPDATE rnp_comments SET marketplace = 'Wildberries' WHERE marketplace IS NULL;

ALTER TABLE rnp_comments
    ALTER COLUMN marketplace SET NOT NULL,
    ADD CONSTRAINT rnp_comments_marketplace_check CHECK (marketplace IN ('Wildberries', 'Ozon', 'Yandex'));

DROP INDEX IF EXISTS idx_rnp_comments_date_art;
CREATE INDEX IF NOT EXISTS idx_rnp_comments_mp_date_art ON rnp_comments (marketplace, date, seller_article);
