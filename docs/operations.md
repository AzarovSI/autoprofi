# Эксплуатация и переезд

Обновлено: 07.10.2026.

## План переезда (согласован с владельцем 07.10.2026)

Цель: сайт работает так же, как сейчас, но на собственном сервере в Yandex Cloud
и под сопровождением Claude, без потери данных, правил и истории.

| # | Задача | Статус |
|---|---|---|
| 1 | Настроить папку проекта: CLAUDE.md, docs/, .claude/, git | готово 07.10.2026 |
| 2 | Тестовое восстановление на тестовой БД, отчёт по чек-листу приёмки Perplexity | готово 07.10.2026 — `docs/acceptance_20261007.md` |
| 3 | Переезд: сервер, рабочая БД, HTTPS по IP, параллельная работа, переключение сотрудников | в работе: с 07.10.2026 сайт работает на https://51.250.30.154 с рабочей БД, идёт параллельная проверка |
| 4 | Выкладка через GitHub, полный ZIP кода на Google Диск после каждой доработки | после задачи 3 |
| 5 | Регулярная работа по задачам владельца | после задачи 3 |

Пока сайт не переключён, production работает на https://autoprofi.pplx.app,
а код в Perplexity заморожен. Если там всё же что-то изменили, понадобится
новый FULL_RECOVERY и сверка с этим репозиторием.

## Инфраструктура

**Сейчас:**
- приложение на хостинге Perplexity;
- статика отдаётся из S3, API — через префикс `/port/8000`;
- переменные `DB_DSN`, `SECRET_KEY`, `TOKEN_TTL_HOURS` передаются в run_command публикации.

**База данных:**
- Yandex Cloud Managed PostgreSQL 17.10, БД `avtoprofi`, пользователь `app`, около 160 МиБ;
- порт 6432 (PgBouncer), `sslmode=require`;
- хост и объёмы таблиц — в `docs/database.md`;
- облако/каталог `autoprofi` (до 07.10.2026 — `cloud-azar7`), кластер `default`;
- в этой же БД `avtoprofi` (схема `azarov_capital`, 20 таблиц) под пользователем `app` работает сервис владельца azarov_capital — не задевать, АВТОПРОФИ только схема `public`;
- пользователи для Claude: `autoprofi_test` (владелец тестовой БД `autoprofi_test`)
  и `autoprofi_ro` (только SELECT на таблицы АВТОПРОФИ в `avtoprofi`), созданы 07.10.2026;
  подключения на Mac — `~/.pg_service.conf` (сервисы `autoprofi_test`, `autoprofi_ro`), пароли в `~/.pgpass`;
- резервирует БД Yandex Cloud, копии БД на Google Диск не нужны.

**Цель:**
- собственная VM в Yandex Cloud в одной сети с кластером;
- HTTPS по IP: домена пока нет, сертификат Let's Encrypt на IP, как у проекта vilka.

**Нужно от владельца:**
1. Тестовая БД `autoprofi_test` в том же кластере.
2. Отдельный пользователь БД только на чтение рабочей базы — для диагностики.
3. Доступ к серверу.

**Тогда же:**
- сменить пароль пользователя `app` (он хранился открытым текстом в старых документах Perplexity);
- задать новый `SECRET_KEY`: все пользователи войдут заново.

## Локальная разработка (Mac)

Настроено 07.10.2026:
- `uv` и Python 3.12.15, `.venv` с зависимостями из `requirements.txt` (с PyPI;
  wheels из `recovery/wheels` — только для Linux x86_64);
- Postgres.app 18.6: клиенты `psql`/`pg_dump` и локальный сервер для тестов
  (данные в `~/.local/share/autoprofi-pgdata`, 127.0.0.1:5432, БД `wb_price_test`,
  пользователь `wb_test`);
- `~/.pg_service.conf`: `autoprofi_test` (облачная тестовая БД) и `autoprofi_ro`
  (рабочая БД, только чтение), пароли в `~/.pgpass`, CA Yandex Cloud в `~/.postgresql/root.crt`.

Команды — в `CLAUDE.md`. Тесты только на локальной `wb_price_test` (делают TRUNCATE).
Прогон 07.10.2026 на Mac: 98 passed, 4 skipped, 85 subtests passed — как у Perplexity.

Тестовая облачная БД `autoprofi_test`: 07.10.2026 развёрнута структура схемы `public`
рабочей БД (`pg_dump -n public --schema-only` через `autoprofi_ro`): 31 таблица,
3 функции, 12 триггеров daily Pi. Данные перенесены COPY-скриптом (31 таблица, числа строк
совпали). Для входа — отдельный QA-администратор, только в тестовой БД; учётка в
`~/.local/share/autoprofi-qa/qa_admin.env` (вне репозитория). Обновлять копию — повтором
этой процедуры (TRUNCATE тестовой БД + COPY), QA-админа создать заново.

## Бэкап кода на Google Диск

Одна команда (скил `backup`): `bash backup_to_drive.sh`.
- Требует чистый git: бэкап = конкретный коммит.
- Собирает ZIP через `recovery/build_backup.py`: allowlist, SHA-256-манифест, без секретов
  и без `docs/archive/perplexity_export_*`; внутри код, `docs/`, `CLAUDE.md`, `.claude/`,
  офлайн-зависимости для Linux.
- Загружает через `rclone` (Mac, `~/.local/bin/rclone`, remote `gdrive`, доступ
  `drive.file` — только к своим файлам) в папку **`autoprofi_backups`** на Google Диске
  владельца и сверяет MD5.
- Старая папка бэкапов Perplexity (`1MFXb-PykO7yUOvneqAzLkWcHrOZ0xf2E`) с `drive.file`
  недоступна; её копии остаются как есть.

Первый бэкап: 07.10.2026, `avtoprofi_FULL_RECOVERY_20261007_193343.zip`, 11 МБ, MD5 сверен.
Архив проверен восстановлением: распакован, манифест сошёлся, тесты из копии — 100 passed.

## Сервер (с 07.10.2026)

- VM `autoprofi-web` в облаке/каталоге `autoprofi`, зона `ru-central1-b`, Ubuntu 24.04,
  2 vCPU (100%), 4 ГБ RAM, SSD 20 ГБ, swap 2 ГБ. Публичный IP **51.250.30.154** (статический),
  внутренний 10.129.0.26; БД доступна по внутренней сети (10.129.0.5:6432).
- Вход: `ssh -i ~/.ssh/autoprofi_admin autoprofi@51.250.30.154` (ключ только на Mac владельца;
  пароли SSH и вход root выключены). Группа безопасности — `default-sg-…` (общая с кластером БД);
  порт 22 пока открыт всем — сузить до IP владельца (как `ssh-allow-here.sh` у vilka).
- Настройка сервера — `deploy/server/setup.sh` (повторяемая): nginx + Let's Encrypt на IP
  (короткий сертификат ~7 дней, продление `certbot-renew.timer`), служба `autoprofi`
  (uvicorn **один процесс** — кэш в памяти), пользователь службы `autoprofi-app`.
- Секреты: `/etc/autoprofi/env` (DB_DSN без пароля, SECRET_KEY — свой, новый), пароль БД
  в `/etc/autoprofi/pgpass` (0600, задаётся `sudo /srv/autoprofi/bin/set-db-password`).
  Пользователь БД сайта — `autoprofi_app`: SELECT/INSERT/UPDATE/DELETE на 31 таблицу `public`,
  их счётчики, CREATE в `public`; схема `azarov_capital` ему недоступна.
- Логи: `sudo journalctl -u autoprofi -f`.

## Выкладка

С Mac: `bash deploy/deploy.sh` — архив текущего коммита по SSH в
`/srv/autoprofi/releases/<sha>`, офлайн-установка зависимостей по `recovery/requirements.lock`
(окружение переиспользуется, пока lock не менялся), переключение `current`, проверка `/health`,
при сбое — автоматический возврат. Откат вручную: `bash deploy/deploy.sh --rollback`.
Хранятся 5 последних версий.

Задача Д (позже) — автоматическая выкладка через GitHub по образцу vilka:
- GitHub проверяет тесты и кладёт версию в бакет;
- сервер раз в минуту сам её забирает и откатывается, если проверка `/health` не прошла.

**До переключения:**
- выложить что-либо в production технически нельзя: публикация на pplx.app доступна только Perplexity;
- `dist/public` и `rebuild_index.py` нужны только хостингу Perplexity, после переезда их можно убрать.
