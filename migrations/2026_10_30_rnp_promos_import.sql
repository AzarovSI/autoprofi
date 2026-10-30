-- Акции РНП: права по площадкам (№122–124), загрузка из Excel (№125), журнал менеджерам
-- (решения владельца 30.10.2026, app/permissions.py). Получают роли, которые уже открывают акции
-- (rnp.promos.add), кроме «Продакта»; площадки — по площадкам роли (app_roles.marketplaces):
-- РОП и Маркетинг — все три, Менеджер WB — Wildberries, Менеджер WB+YA — Wildberries и Yandex и т. д.
-- Администратору строки не нужны.
INSERT INTO app_role_permissions (role_id, permission)
SELECT r.id, 'rnp.promos.mp.' || m.sfx
FROM app_roles r
JOIN app_role_permissions a ON a.role_id = r.id AND a.permission = 'rnp.promos.add'
JOIN (VALUES ('Wildberries', 'wb'), ('Ozon', 'ozon'), ('Yandex', 'ya')) AS m (mp, sfx) ON m.mp = ANY (r.marketplaces)
WHERE NOT r.is_admin AND r.name <> 'Продакт'
ON CONFLICT DO NOTHING;

INSERT INTO app_role_permissions (role_id, permission)
SELECT r.id, p.permission
FROM app_roles r
JOIN app_role_permissions a ON a.role_id = r.id AND a.permission = 'rnp.promos.add'
CROSS JOIN (VALUES ('rnp.promos.import'), ('rnp.promos.journal')) AS p (permission)
WHERE NOT r.is_admin AND r.name <> 'Продакт'
ON CONFLICT DO NOTHING;

-- «Продакт» акции только смотрит: открывать и закрывать акции — не его функция.
DELETE FROM app_role_permissions
WHERE role_id IN (SELECT id FROM app_roles WHERE name = 'Продакт')
  AND permission IN ('rnp.promos.add', 'rnp.promos.edit_own', 'rnp.promos.edit_any');
