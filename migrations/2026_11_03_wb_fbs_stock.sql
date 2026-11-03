-- Остатки на собственных складах FBS по дням (решение владельца 03.11.2026): Google-таблица «Воронка ВБ - для
-- дашборда», лист «Воронка», колонки «г. Москва», «Краснодар», «г. Артём» правее «Остаток». Снимок на дату, как
-- stock_wb_qty: NULL — в файле пусто или колонки нет. В РНП WB — под строкой «Остаток на складе WB, шт».
-- ALTER существующей таблицы: в рабочей БД TimeWeb применить ЭТОТ ФАЙЛ под gen_user до выкладки — он сам
-- отмечает себя в app_schema_migrations, иначе сайт при старте повторит ALTER под autoprofi_app и не стартует.
ALTER TABLE wb_daily_sales
    ADD COLUMN IF NOT EXISTS stock_fbs_msk numeric,
    ADD COLUMN IF NOT EXISTS stock_fbs_krd numeric,
    ADD COLUMN IF NOT EXISTS stock_fbs_art numeric;
INSERT INTO app_schema_migrations (name) VALUES ('2026_11_03_wb_fbs_stock') ON CONFLICT DO NOTHING;
