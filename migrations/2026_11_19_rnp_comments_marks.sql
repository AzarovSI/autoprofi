-- Отметки комментариев РНП (19.11.2026, решение владельца по макетам
-- «Макет РНП отметки комментариев — *.png»). Необязательные: пустой массив — обычный комментарий
-- (только красный уголок). ads — «Реклама», content — «Контент», important — «Важно»;
-- в ячейке — цветные точки в левом нижнем углу. Прежние комментарии остаются без отметок.
ALTER TABLE rnp_comments ADD COLUMN IF NOT EXISTS marks text[] NOT NULL DEFAULT '{}';

-- Повторный запуск (если файл применили вручную без записи в app_schema_migrations) не падает.
ALTER TABLE rnp_comments DROP CONSTRAINT IF EXISTS rnp_comments_marks_check;
ALTER TABLE rnp_comments
    ADD CONSTRAINT rnp_comments_marks_check CHECK (marks <@ ARRAY['ads', 'content', 'important']::text[]);
