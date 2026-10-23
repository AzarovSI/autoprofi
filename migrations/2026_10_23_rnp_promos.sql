-- Акции товаров в РНП (решение владельца 23.10.2026): менеджер отмечает вход в акцию и плановый
-- выход, может завершить раньше. Своя акция у каждой площадки; у товара одна акция одновременно
-- (проверяет app/routers/rnp_promos_router.py). Записи не удаляются: отменённая помечается, история
-- изменений — в rnp_promo_events (журнал и статистика). Автор — id из app_users без внешнего ключа,
-- чтобы удаление пользователя не стирало и не обезличивало историю.
CREATE TABLE IF NOT EXISTS rnp_promos (
    id             bigserial   PRIMARY KEY,
    marketplace    text        NOT NULL CHECK (marketplace IN ('Wildberries', 'Ozon', 'Yandex')),
    seller_article text        NOT NULL,
    name           text        NOT NULL,
    date_from      date        NOT NULL,
    -- Последний день акции по плану и фактически (досрочное завершение); оба включительно.
    date_to_plan   date        NOT NULL CHECK (date_to_plan >= date_from),
    date_to_fact   date        CHECK (date_to_fact >= date_from AND date_to_fact <= date_to_plan),
    created_by     bigint,
    created_at     timestamptz NOT NULL DEFAULT now(),
    updated_at     timestamptz NOT NULL DEFAULT now(),
    closed_by      bigint,
    closed_at      timestamptz,
    cancelled_by   bigint,
    cancelled_at   timestamptz
);
CREATE INDEX IF NOT EXISTS idx_rnp_promos_mp_art ON rnp_promos (marketplace, seller_article, date_from);

-- История: create — открыта, edit — изменены название или даты, close — завершена раньше,
-- cancel — отменена. changes — {поле: [было, стало]}.
CREATE TABLE IF NOT EXISTS rnp_promo_events (
    id       bigserial   PRIMARY KEY,
    promo_id bigint      NOT NULL REFERENCES rnp_promos (id) ON DELETE CASCADE,
    action   text        NOT NULL CHECK (action IN ('create', 'edit', 'close', 'cancel')),
    changes  jsonb       NOT NULL DEFAULT '{}',
    user_id  bigint,
    at       timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_rnp_promo_events_promo ON rnp_promo_events (promo_id, at);

-- Права (решение владельца 23.10.2026): акции — тем же ролям, что и комментарии (свои / чужие),
-- журнал — РОП и Маркетингу. Администратору строки не нужны.
INSERT INTO app_role_permissions (role_id, permission)
SELECT role_id, replace(permission, 'rnp.comments.', 'rnp.promos.')
FROM app_role_permissions
WHERE permission IN ('rnp.comments.view', 'rnp.comments.add', 'rnp.comments.edit_own', 'rnp.comments.edit_any')
ON CONFLICT DO NOTHING;

INSERT INTO app_role_permissions (role_id, permission)
SELECT id, 'rnp.promos.journal' FROM app_roles WHERE name IN ('РОП', 'Маркетинг')
ON CONFLICT DO NOTHING;
