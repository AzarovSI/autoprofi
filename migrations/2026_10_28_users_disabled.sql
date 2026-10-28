-- Отключение пользователей вместо удаления (28.10.2026, решение владельца). Удаление из app_users
-- каскадом стирало комментарии РНП автора (rnp_comments.author_id ON DELETE CASCADE).
-- disabled_at — когда отключён; NULL — активен. Отключённый не входит на сайт, его записи остаются.
ALTER TABLE app_users ADD COLUMN IF NOT EXISTS disabled_at timestamptz;
