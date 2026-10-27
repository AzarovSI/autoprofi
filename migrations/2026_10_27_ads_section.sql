-- Раздел «Реклама» (27.10.2026, пока в разработке): права №116–119 (app/permissions.py).
-- Решение владельца: раздел видят только «Администратор» и «Маркетинг».
-- Администратору строки не нужны.
INSERT INTO app_role_permissions (role_id, permission)
SELECT r.id, p.permission
FROM app_roles r
CROSS JOIN (VALUES ('ads.open'), ('ads.tab.wb'), ('ads.tab.ozon'), ('ads.uploads.open')) AS p (permission)
WHERE r.name = 'Маркетинг'
ON CONFLICT DO NOTHING;
