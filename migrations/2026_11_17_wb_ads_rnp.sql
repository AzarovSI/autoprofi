-- 17.11.2026: «Дашборд - рекламная статистика» WB загружается в «РНП → Загрузка данных → Wildberries»
-- (вместо отчёта 1С) и заполняет и РНП WB, и раздел «Реклама». Карточка загрузки в «Реклама →
-- Загрузка данных» убрана. Решение владельца 17.11.2026: роли «Маркетинг» — право карточки РНП №23;
-- право №120 («Загрузить статистику рекламы WB») больше ничего не открывает — строки ролей с ним удалить.
-- №23 получает и каждая роль, у которой было №120 (например, скопированная с «Маркетинга»).
INSERT INTO app_role_permissions (role_id, permission)
SELECT id, 'rnp.upload.wb_ads' FROM app_roles WHERE name = 'Маркетинг'
UNION
SELECT role_id, 'rnp.upload.wb_ads' FROM app_role_permissions WHERE permission = 'ads.upload.wb'
ON CONFLICT DO NOTHING;

DELETE FROM app_role_permissions WHERE permission = 'ads.upload.wb';
