-- Права, убранные 03.11.2026 (app/permissions.py, REMOVED): №102–103 — вкладки юнит-экономики
-- «Wildberries / OZON (старый вид)», №126 — тестовая вкладка ABC «Сводная v2».
-- Решение владельца 03.11.2026: строки ролей с ними удалить. №14–15 не трогаем (решение 26.10.2026).
DELETE FROM app_role_permissions
WHERE permission IN ('unit.tab.oz_old', 'unit.tab.wb_old', 'abc.tab.summary_v2');
