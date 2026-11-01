-- Время среза цены из отчёта площадки (решение владельца 01.11.2026): WB «Цены с СПП» — колонка «Время»,
-- Ozon «Цены и Соинвест» — колонка «Время загрузки». Время московское, как и дата. NULL — в отчёте времени нет
-- (старые отчёты, пустая ячейка). Показывается в «Цены → Индекс цен»: «Данные на: WB — 30.10.2026 08:24 …».
-- ALTER существующей таблицы: в рабочей БД TimeWeb применить ЭТОТ ФАЙЛ под gen_user до выкладки — он сам
-- отмечает себя в app_schema_migrations, иначе сайт при старте повторит ALTER под autoprofi_app и не стартует.
ALTER TABLE mp_price_daily ADD COLUMN IF NOT EXISTS price_time time;
INSERT INTO app_schema_migrations (name) VALUES ('2026_11_01_price_time') ON CONFLICT DO NOTHING;
