# База данных

Обновлено: 07.10.2026. Объёмы — точный `COUNT(*)` из снимка только для чтения от 07.10.2026 14:18 МСК
(выгрузка Perplexity, `docs/archive/perplexity_export_20261007/07_database.md`). Ключи и грануляция
сверены с кодом загрузчиков и миграциями. Полный DDL базовых таблиц в репозитории отсутствует.
Списки колонок — `recovery/schema_contract.json` (без ключей и индексов).

## Подключение и роль

| Параметр | Значение |
|---|---|
| СУБД | PostgreSQL 17.10, Yandex Cloud Managed PostgreSQL |
| Хост | В конфигурации подключения (`DB_DSN` из окружения или `recovery/config.json`), в docs не пишется |
| Порт / SSL | 6432 (PgBouncer), `sslmode=require`, `sslrootcert` не задан |
| БД | `avtoprofi` |
| Пользователь | `app`: не superuser, без CREATEROLE/CREATEDB/REPLICATION/BYPASSRLS, LOGIN, `connlimit 50`; владелец всех таблиц `public` |
| Расширения | Только `plpgsql` |
| Размер | ~160 МиБ (167 761 587 байт) на 07.10.2026 |
| Резервные копии | Владелец сообщил, что они есть. График, срок хранения и PITR: НЕ ЗНАЮ |

Рабочая БД — только чтение (см. `CLAUDE.md`). На старте приложения выполняется
`daily_pi.migrate()`, поэтому `app.main` с рабочей БД для проверок не запускать.

## Таблицы

«Строк» — на 07.10.2026. Ключ — PK/UNIQUE по коду (`ON CONFLICT`, миграции) или фактическая
грануляция записи загрузчиком.

### Пользователи

| Таблица | Назначение | Ключ / грануляция | Строк |
|---|---|---|---:|
| `app_users` | Учётные записи: e-mail, bcrypt-хеш, имя, `role_id` (роль из `app_roles`, с 12.10.2026), старая `role` `admin`/`user` (ведётся для отката), `last_seen_at` | `id`; вход по e-mail | 8 (12.10, TimeWeb) |
| `app_roles` | Роли доступа: название, `is_admin`, площадки `marketplaces` (миграция `2026_10_12_access_roles`, рабочая БД — 12.10.2026) | `id`, UNIQUE `name`; одна роль с `is_admin` | 8 (12.10) |
| `app_role_permissions` | Разрешённые функции ролей (коды — `app/permissions.py`). Там же `app_users.role_id` | PK `(role_id, permission)` | 374 (12.10) |

### Каталог

| Таблица | Назначение | Ключ / грануляция | Строк |
|---|---|---|---:|
| `catalog_items` | Общие поля товара: наименование, категории L1–L3. Есть старые колонки `status`/`manager` (их ещё читает загрузчик MPPROFIT) | PK `seller_article` (канонический) | 1 032 |
| `catalog_marketplace` | Поля по МП: статус, менеджер, `product_url` | `(seller_article, marketplace)` | 1 878 |
| `category_tree` | Старое дерево категорий, в коде не используется | `id` | 0 |

### Факт MPPROFIT

| Таблица | Назначение | Ключ / грануляция | Строк |
|---|---|---|---:|
| `fact_weekly` | Недельная юнит-экономика (РНП юнит-экономика, метрики, тренды) | Товар (артикул + штрихкод WB / SKU Ozon) × ISO-неделя × год × МП, плюс строки «Общие удержания»; перезапись по `(year, week, marketplace)` | 12 091 |
| `fact_monthly` | Месячная юнит-экономика («Продажи, шт.», ABC) | То же по `(year, month, marketplace)` | 18 287 |

### Дневные РНП

| Таблица | Назначение | Ключ / грануляция | Строк |
|---|---|---|---:|
| `ozon_daily_sales` | РНП Ozon: заказы, Pi кабинета, СПП, CR, реклама, рейтинг, отзывы (`delivery_time_hours`), остатки, цены конкурентов | UNIQUE `(date, seller_article)` | 64 214 |
| `wb_daily_sales` | РНП WB: воронка, отзывы, СПП, реклама, Pi кабинета, цены конкурентов | UNIQUE `(date, seller_article)` | 27 830 |
| `ya_daily_sales` | РНП Яндекс: воронка, CTR, остаток FBY (`stock_ya_qty`) | День × артикул (уникальность в коде не используется) | 25 209 |
| `rnp_promos` | Акции товаров в РНП: площадка, артикул, название, вход, плановый и фактический выход, кто открыл / завершил / отменил (id без внешнего ключа — история переживает удаление пользователя); отменённые не удаляются | `id`; индекс `(marketplace, seller_article, date_from)` | — |
| `rnp_promo_events` | История акций: create / edit / close / cancel, `changes` jsonb `{поле: [было, стало]}`, кто и когда | `id`; FK `promo_id`; индекс `(promo_id, at)` | — |
| `rnp_comments` | Комментарии к ячейкам РНП — свои у каждой площадки (`marketplace`: `Wildberries`/`Ozon`/`Yandex`, с миграции `2026_10_23_rnp_comments_marketplace`) | `id`; индекс `(marketplace, date, seller_article)` | 8 |
| `anomaly_rules` | Общие для команды пороги аномалий РНП | PK `rule_key` | 17 |
| `abc_rules` | Пороги рекомендаций ABC (⚙): рубли в месяц по площадке, общие маржа и кратности, кто и когда менял (миграция `2026_10_19_abc_rules`, рабочая БД — 19.10.2026) | PK `(param, marketplace)`, marketplace `ozon`/`vb`/`ya`/`all` | 11 |

### Остатки и склады

| Таблица | Назначение | Ключ / грануляция | Строк |
|---|---|---|---:|
| `stock_daily` | Общий склад АВТОПРОФИ МСК (1С) | Дата × артикул (по docstring — уникальность `(date, upper(seller_article))`); день перезаписывается целиком | 43 345 |
| `wb_stock_daily` | Остатки WB по складам | `(date, seller_article, warehouse_id)` | 117 868 |
| `wb_stock_meta` | WB по товару за день: в пути, объём, всего на складах | `(date, seller_article)` | 21 924 |
| `wb_warehouses` | Справочник складов WB: объединение (`canonical_id`), статус, федеральный округ | `id`, UNIQUE `name` | 31 |
| `wb_wh_incidents` | Периоды инцидентов складов WB | `id`; `wh_id`, `start_date`, `end_date` (NULL = открыт) | 20 |
| `ozon_stock_daily` | Остатки Ozon по складам (сумма H–P) | `(date, seller_article, warehouse_id)` | 255 875 |
| `ozon_stock_meta` | Ozon по товару за день: доступно, в пути, возвраты, к вывозу | `(date, seller_article)` | 11 450 |
| `ozon_warehouses` | Справочник складов Ozon: кластер, округ, объединение, статус | `id`, UNIQUE `name` | 368 |
| `ozon_wh_incidents` | Периоды инцидентов складов Ozon | как `wb_wh_incidents` | 5 |
| `item_cost` | Актуальная себестоимость | PK `seller_article` | 731 |
| `item_cost_hist` | История себестоимости по дате начала действия | `(seller_article, start_date)` | 731 |

### Цены и Pi

| Таблица | Назначение | Ключ / грануляция | Строк |
|---|---|---|---:|
| `catalog_base_prices` | Текущая базовая цена (целое, ₽) | `(seller_article, marketplace)` | 960 |
| `catalog_base_prices_hist` | История базовых цен: интервалы `valid_from`–`valid_to` | PK `(seller_article, marketplace, valid_from)` | 1 038 |
| `mp_price_daily` | Дневные цены МП: загружаемая цена, СПП/соинвест, цена покупателя, `upload_source` | PK `(seller_article, marketplace, date)` | 15 077 |
| `price_index_daily` | Вычисленные дневные Pi: `wb_base_pi`, `ozon_base_pi`, `buyer_pi` | PK `(seller_article, date)` | 9 923 |
| `price_index_revision` | Счётчик пересчётов Pi (одна строка) | `singleton` | 1 |
| `price_model_hist` | «Ценовая модель»: вводимые параметры с историей — `param` = `category_price` (₽), `drr_pct`, `margin_pct` (доли); одна запись на день по Москве (последняя правка дня), NULL = значение удалено с этой даты; кто и когда менял (миграция `2026_10_21_price_model_params`, заменила `price_category_hist`) | PK `(seller_article, marketplace, param, date)` | 0 (21.10) |
| `price_model_rules` | «Ценовая модель», ⚙: порог сигнала корректировки загружаемой цены по площадке (доля, по умолчанию 0.05), кто и когда менял (миграция `2026_10_21_price_model_rules`) | PK `marketplace` (Wildberries / Ozon) | 2 (21.10) |
| ~~`price_category_hist`~~ | (удалена 21.10 миграцией `2026_10_21_price_model_params`, строки переносятся в `price_model_hist`) «Ценовая модель»: справочник средних цен категории с историей — одна запись на день по Москве (последняя правка дня), NULL = цена удалена с этой даты; кто и когда менял (миграция `2026_10_21_price_category`, заменила `price_model_category`) | PK `(seller_article, marketplace, date)`, Wildberries / Ozon / Yandex | 0 (21.10) |

### Планы

| Таблица | Назначение | Ключ / грануляция | Строк |
|---|---|---|---:|
| `sales_plan` | План продаж, шт.: `plan_qty`, признак ручной правки `is_manual`, `prev_qty` | PK `(seller_article, marketplace, year, month)` | 5 512 |

### Журналы

| Таблица | Назначение | Ключ / грануляция | Строк |
|---|---|---|---:|
| `report_uploads` | Журнал загрузок отчётов: `period_kind`, период, файл, статус, `fact_through_date`, `uploaded_by_user_id`. Старые колонки: `s3_key`, `uploaded_by` | `id` | 1 233 |
| `mp_price_upload_log` | Журнал прямых ценовых импортов: строка на каждую дату файла | `id` | 171 |

### Служебные

| Таблица | Назначение | Ключ / грануляция | Строк |
|---|---|---|---:|
| `app_schema_migrations` | Учёт версионных миграций | PK `name` | 1 |

Всего 31 таблица на 07.10.2026; с 12.10.2026 — 33 (`app_roles`, `app_role_permissions`). Самые большие по объёму с индексами: `ozon_daily_sales` (~24 МиБ),
`ozon_stock_daily` (~20 МиБ), `wb_stock_daily` (~16 МиБ), `fact_monthly` (~15 МиБ).

## Триггеры daily Pi

Источник: `migrations/2026_09_21_daily_pi.sql`, применяется через `app/daily_pi.py:migrate()`.

| Объект | Что делает |
|---|---|
| Таблицы-источники | `mp_price_daily`, `catalog_base_prices_hist`, `catalog_items` |
| `daily_pi_lock` (BEFORE, на оператор) | Берёт `pg_advisory_xact_lock(21092026, 1)`. Параллельные изменения цен (например, одновременные импорты WB и Ozon) выполняются по очереди |
| `daily_pi_insert/update/delete` (AFTER, на оператор, с transition tables) | Собирает затронутые артикулы и вызывает `refresh_daily_pi(articles, dates)`. Для `mp_price_daily` пересчитываются только затронутые даты, для базовых цен и каталога — все даты этих артикулов. Пустой набор строк (например, `ON CONFLICT DO NOTHING`) ничего не пересчитывает |
| `refresh_daily_pi` | Удаляет и заново вставляет строки `price_index_daily`, но только для дат, которые есть в `mp_price_daily` (Ozon/WB), без выдуманных дней. После пересчёта увеличивает `price_index_revision` |

Формулы в `refresh_daily_pi`:
- **Базовый Pi WB / Ozon** = цена дня / базовая цена, действовавшая на конец дня по Москве.
  Для товаров, у которых в `category_l1` есть «АВТОПРОФИ», в числитель идёт цена покупателя,
  для остальных — загружаемая цена. Если базовой цены нет или она 0, результат NULL.
- **Pi покупатель** = цена покупателя Ozon / цена покупателя WB.
- Цена покупателя считается фактом, только если `spp_pct > 0`, `spp_is_estimated = false`,
  `spp_source_date` совпадает с датой и `buyer_price > 0`. Оценочная СПП прошлых дней не учитывается.

Миграция при установке блокирует таблицы-источники (`SHARE ROW EXCLUSIVE`) и делает backfill
`refresh_daily_pi(NULL, NULL)`.

## Миграции

| Файл | Что делает | Учёт |
|---|---|---|
| `migrations/2026_09_14_price_history.sql` | Создаёт `catalog_base_prices_hist` (с переносом текущих цен), `mp_price_daily`, `mp_price_upload_log` | Применена вручную одной транзакцией (по комментарию в файле). В `app_schema_migrations` **нет** |
| `migrations/2026_09_21_daily_pi.sql` | `price_index_daily`, `price_index_revision`, функции и триггеры daily Pi, backfill | Выполняется `daily_pi.migrate()` на старте (advisory lock + проверка записи). В `app_schema_migrations`: `2026_09_21_daily_pi`, 21.09.2026 18:31 МСК — **единственная запись** |
| `migrations/2026_10_12_access_roles.sql` | `app_roles`, `app_role_permissions`, `app_users.role_id`; 8 ролей и права из xlsx (`docs/access_roles.md`) | Выполняется `permissions.migrate()` на старте, как daily Pi. В рабочей БД TimeWeb применена 12.10.2026 под `gen_user` до выкладки (запись в `app_schema_migrations`) |
| `migrations/2026_10_13_price_model.sql` | `price_model_category` — цена категории «Ценовой модели» (лист 13.10) | В рабочей БД TimeWeb применена 13.10.2026 под `gen_user`. С 21.10 код её не применяет: таблицу удаляет `2026_10_21_price_category` |
| `migrations/2026_10_21_price_category.sql` | Удаляет `price_model_category` (10 тестовых значений 13.10, не переносятся — решение владельца 21.10), создаёт `price_category_hist` | `price_model.migrate()` на старте. DROP требует владельца таблицы — в рабочей БД применена 21.10.2026 под `gen_user` до выкладки; снимок старых строк — `~/AUTOPROFI_backups/db/price_model_category_before_drop_2026-10-21.csv` |
| `migrations/2026_10_21_price_model_params.sql` | Создаёт `price_model_hist` (цена категории, плановые ДРР и маржа), переносит строки `price_category_hist` и удаляет её | `price_model.migrate()` на старте (список `MIGRATIONS`, после `2026_10_21_price_category`). DROP требует владельца таблицы — в рабочей БД применена 21.10.2026 под `gen_user` до выкладки (`price_category_hist` была пустой) |
| `migrations/2026_10_21_price_model_rules.sql` | `price_model_rules` — пороги сигнала корректировки (WB, Ozon по 5%) | `price_model.migrate()` на старте (список `MIGRATIONS`). В рабочей БД применена 21.10.2026 под `gen_user` до выкладки (права ролей сайта — по default privileges) |
| `migrations/2026_10_19_abc_rules.sql` | `abc_rules` — пороги рекомендаций ABC с исходными значениями | `abc_rules.migrate()` на старте. В рабочей БД TimeWeb применена 19.10.2026 под `gen_user` до выкладки; права `autoprofi_app`/`autoprofi_ro` проверены |
| `migrations/2026_10_14_wb_payout_with_ads.sql` | `fact_weekly`, `fact_monthly`: колонки WB `wb_payout_with_ads` («Выплата на р/с (с рекл.)»), `wb_ads_balance` («Баланс»), `wb_ads_bonus` («Промо бонусы») | `fact_schema.migrate()` на старте. ALTER существующих таблиц. В рабочей БД TimeWeb применена 14.10.2026 14:32 МСК под `gen_user` до выкладки; права `autoprofi_app`/`autoprofi_ro` на новые колонки есть (табличные) |
| `migrations/2026_10_14_oz_temp_placement.sql` | `fact_weekly`, `fact_monthly`: `oz_temp_placement` (Ozon «Временное размещение») | `fact_schema.migrate()` на старте (список `MIGRATIONS`). ALTER. В рабочей БД TimeWeb применена 15.10.2026 11:07 МСК под `gen_user` до выкладки; права `autoprofi_app`/`autoprofi_ro` есть |
| `migrations/2026_10_23_rnp_comments_marketplace.sql` | `rnp_comments.marketplace` (NOT NULL, CHECK по трём площадкам), прежние комментарии — `Wildberries` (все 8 на 23.10 оставлены в РНП WB, решение владельца); индекс `(marketplace, date, seller_article)` вместо `(date, seller_article)` | `rnp_comments_router.migrate()` на старте. ALTER — в рабочей БД применена 23.10.2026 12:04 МСК под `gen_user` до выкладки (запустил владелец); снимок строк — `~/AUTOPROFI_backups/db/rnp_comments_before_marketplace_2026-10-23.csv`; права `autoprofi_app` на колонку проверены |
| `migrations/2026_10_23_rnp_promos.sql` | `rnp_promos`, `rnp_promo_events`; права №111–114 ролям с такими же правами на комментарии, №115 — РОП и Маркетингу | `rnp_promos_router.migrate()` на старте (после миграции ролей). В рабочей БД применена 23.10.2026 20:05 МСК под `gen_user` до выкладки (запустил владелец); снимок прав ролей до неё — `~/AUTOPROFI_backups/db/app_role_permissions_before_promos_2026-10-23.csv`; у `autoprofi_app` есть права на таблицы и последовательности (по default privileges `gen_user`) |
| `app/migrations/001_sales_plan.sql` | `sales_plan` (без `is_manual`/`prev_qty`), `report_uploads.fact_through_date` | Идемпотентна, применена вручную, не учтена |
| `migrate_incidents.py` | `wb_wh_incidents` + перенос старых статусов `incident` из `wb_warehouses` | Уже выполнена, повторно не запускать; не учтена |

DDL, применённый вручную без файла в репозитории (по истории Perplexity и по расхождению кода
с файлами миграций):
- базовые таблицы: `fact_*`, `*_daily_sales`, `catalog_items`, `catalog_marketplace`, `app_users`,
  `report_uploads`, `catalog_base_prices`, `stock_daily`, `anomaly_rules`, `rnp_comments`,
  склады Ozon (`ozon_stock_*`, `ozon_warehouses`, `ozon_wh_incidents`), `item_cost*`;
- `wb_daily_sales`: колонки `comp_price_avg/min`, `avg_search_position`, `price_index_pi`, `spp_pct`;
  разовые правки данных от 28.07.2026: `ctr_pct` / 100 и NULL → 0 по `ZERO_FILL_COLS`;
- `catalog_marketplace.product_url`; `wb_warehouses.status`, `status_date`, `federal_district`;
- `sales_plan.is_manual`, `prev_qty`;
- `report_uploads.uploaded_by_user_id` (FK на `app_users`, `ON DELETE SET NULL`,
  DEFAULT `NULLIF(current_setting('app.uploader_id', true), '')::int`).

Точные даты выполнения ручного DDL: НЕ ЗНАЮ. Новые изменения схемы оформлять по образцу
`daily_pi.migrate()` (см. `CLAUDE.md`).

## Роли приложения

> **С 12.10.2026 (выложено) роли — из `app_roles`, права по функциям:** `docs/access_roles.md`.
> Таблица ниже — как было до этого (версия до `912dce1`, она же при откате).

Код — `app/auth.py`, `app/routers/users_router.py`.

| Роль / механизм | Как работает | Где применяется |
|---|---|---|
| `user` | Любой вошедший: действующий JWT (HS256, `SECRET_KEY` из окружения, срок по умолчанию 24 ч) и существующая запись в `app_users` (`get_current_user`) | Просмотр аналитики (GET-эндпоинты, кроме пользователей и большинства журналов); загрузка остатков WB/Ozon по складам, себестоимости, прямых цен P1/P2, импорт справочника; ручные правки цен, плана, каталога, складов и инцидентов, правил аномалий, комментариев РНП; журналы `wb_stock` и `cost` |
| `admin` | `role = 'admin'` (`require_admin`), иначе 403 | Загрузки MPPROFIT и РНП, общий склад, остатки Яндекса, базовые цены, план, остальные журналы, управление пользователями (`/api/users`) |
| Владелец | `OWNER_EMAILS` — константа в `app/auth.py` (один адрес) и зависимость `require_owner` | Сейчас **ни один эндпоинт** её не использует. Это не третья роль |

Допустимые роли — `ALLOWED_ROLES = {"admin", "user"}`. Скрытие кнопок на фронтенде серверную
проверку не заменяет.

## Пользователи

На 07.10.2026 в `app_users` 9 записей: 7 `admin`, 2 `user`.

Назначения менеджеров каталога (`catalog_marketplace.manager`; это не должности и не внешний
ключ на `app_users`):

| Менеджер | МП | Артикулов |
|---|---|---:|
| Морозова К. | Ozon | 257 |
| Петрушина Н. | Ozon | 174 |
| Рачаев Н. | Wildberries | 262 |
| Рачаев Н. | Yandex | 207 |
| Рябец И. | Wildberries | 147 |
