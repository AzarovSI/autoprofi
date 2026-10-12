-- Права доступа: роли с площадками, права ролей, роль у пользователя.
-- Схема согласована владельцем 12.10.2026 (docs/access_roles.md).
-- Выполняется app/permissions.py:migrate() одной транзакцией, учёт в app_schema_migrations.
-- 8 ролей и их права — из docs/access/roles_matrix_20261012.xlsx (коды функций — app/permissions.py).

CREATE TABLE app_roles (
  id bigserial PRIMARY KEY,
  name text NOT NULL UNIQUE,
  -- Роль «Администратор»: всегда все права, строк в app_role_permissions нет.
  is_admin boolean NOT NULL DEFAULT false,
  marketplaces text[] NOT NULL DEFAULT '{}'
    CONSTRAINT app_roles_marketplaces_check
    CHECK (marketplaces <@ ARRAY['Wildberries', 'Ozon', 'Yandex']::text[]),
  created_at timestamptz NOT NULL DEFAULT now()
);
-- Роль «Администратор» в базе только одна.
CREATE UNIQUE INDEX app_roles_single_admin ON app_roles (is_admin) WHERE is_admin;

-- Только разрешённые функции: есть строка — можно, нет — нельзя.
CREATE TABLE app_role_permissions (
  role_id bigint NOT NULL REFERENCES app_roles (id) ON DELETE CASCADE,
  permission text NOT NULL,
  PRIMARY KEY (role_id, permission)
);

-- Роль пользователя. Старая колонка role (admin/user) остаётся для отката на прежнюю версию.
-- Роль с сотрудниками удалить нельзя.
ALTER TABLE app_users ADD COLUMN role_id bigint REFERENCES app_roles (id) ON DELETE RESTRICT;

INSERT INTO app_roles (id, name, is_admin, marketplaces) VALUES
  (1, 'Администратор', true, '{Wildberries,Ozon,Yandex}'),
  (2, 'РОП', false, '{Wildberries,Ozon,Yandex}'),
  (3, 'Продакт', false, '{Wildberries,Ozon,Yandex}'),
  (4, 'Маркетинг', false, '{Wildberries,Ozon,Yandex}'),
  (5, 'Менеджер WB', false, '{Wildberries}'),
  (6, 'Менеджер OZ', false, '{Ozon}'),
  (7, 'Менеджер YA', false, '{Yandex}'),
  (8, 'Ассистент', false, '{}');
SELECT setval(pg_get_serial_sequence('app_roles', 'id'), (SELECT max(id) FROM app_roles));

INSERT INTO app_role_permissions (role_id, permission) VALUES
  -- РОП: 92
  (2, 'login'), (2, 'rnp.open'), (2, 'rnp.tab.wb'), (2, 'rnp.tab.ozon'), (2, 'rnp.tab.ya'), (2, 'rnp.table'),
  (2, 'rnp.unit_popup'), (2, 'rnp.product_link'), (2, 'rnp.status.edit'), (2, 'rnp.comments.view'), (2, 'rnp.comments.add'), (2, 'rnp.comments.edit_own'),
  (2, 'rnp.comments.edit_any'), (2, 'rnp.anomalies.view'), (2, 'rnp.anomalies.rules'), (2, 'rnp.uploads.open'), (2, 'rnp.upload.ozon_daily'), (2, 'rnp.upload.ozon_ads'),
  (2, 'rnp.upload.ozon_reviews'), (2, 'rnp.upload.ozon_priceva'), (2, 'rnp.upload.wb_daily'), (2, 'rnp.upload.wb_reviews'), (2, 'rnp.upload.wb_ads'), (2, 'rnp.upload.wb_priceva'),
  (2, 'rnp.upload.wb_pi'), (2, 'rnp.upload.ya_daily'), (2, 'rnp.upload.ya_stock'), (2, 'rnp.upload.stock_1c'), (2, 'rnp.uploads.log'), (2, 'unit.open'),
  (2, 'unit.tab.wb'), (2, 'unit.tab.ozon'), (2, 'unit.tab.ya'), (2, 'unit.table'), (2, 'unit.margin'), (2, 'unit.status.edit'),
  (2, 'unit.uploads.open'), (2, 'unit.upload.ozon'), (2, 'unit.upload.wb'), (2, 'unit.upload.ya'), (2, 'unit.uploads.log'), (2, 'prices.open'),
  (2, 'prices.list.view'), (2, 'prices.list.table'), (2, 'prices.list.cost'), (2, 'prices.list.export'), (2, 'prices.index.view'), (2, 'prices.index.export'),
  (2, 'prices.upload.ozon'), (2, 'prices.upload.wb'), (2, 'prices.uploads.log'), (2, 'sales.open'), (2, 'sales.tab.wb'), (2, 'sales.tab.ozon'),
  (2, 'sales.tab.ya'), (2, 'sales.tab.summary'), (2, 'sales.table'), (2, 'sales.summary.export'), (2, 'abc.open'), (2, 'abc.tab.overview'),
  (2, 'abc.tab.ozon'), (2, 'abc.tab.wb'), (2, 'abc.tab.ya'), (2, 'abc.tab.compare'), (2, 'abc.tab.actions'), (2, 'abc.common'),
  (2, 'abc.status.edit'), (2, 'abc.actions.export'), (2, 'stock.open'), (2, 'stock.tab.wb'), (2, 'stock.tab.ozon'), (2, 'stock.table'),
  (2, 'stock.quality'), (2, 'stock.incidents.edit'), (2, 'stock.incidents.delete'), (2, 'stock.wh.rename'), (2, 'stock.wh.merge'), (2, 'stock.wh.status'),
  (2, 'stock.wh.district'), (2, 'stock.wh.cluster'), (2, 'stock.upload.wb'), (2, 'stock.upload.ozon'), (2, 'stock.upload.cost'), (2, 'stock.uploads.log'),
  (2, 'catalog.open'), (2, 'catalog.table'), (2, 'catalog.edit'), (2, 'catalog.add'), (2, 'catalog.export'), (2, 'catalog.import'),
  (2, 'users.open'), (2, 'users.add'),
  -- Продакт: 65
  (3, 'login'), (3, 'rnp.open'), (3, 'rnp.tab.wb'), (3, 'rnp.tab.ozon'), (3, 'rnp.tab.ya'), (3, 'rnp.table'),
  (3, 'rnp.unit_popup'), (3, 'rnp.product_link'), (3, 'rnp.status.edit'), (3, 'rnp.comments.view'), (3, 'rnp.comments.add'), (3, 'rnp.comments.edit_own'),
  (3, 'rnp.anomalies.view'), (3, 'rnp.anomalies.rules'), (3, 'rnp.uploads.open'), (3, 'rnp.uploads.log'), (3, 'unit.open'), (3, 'unit.tab.wb'),
  (3, 'unit.tab.ozon'), (3, 'unit.tab.ya'), (3, 'unit.table'), (3, 'unit.margin'), (3, 'unit.status.edit'), (3, 'prices.open'),
  (3, 'prices.list.view'), (3, 'prices.list.table'), (3, 'prices.list.cost'), (3, 'prices.list.edit'), (3, 'prices.list.export'), (3, 'prices.list.import'),
  (3, 'prices.index.view'), (3, 'prices.index.export'), (3, 'prices.upload.ozon'), (3, 'prices.upload.wb'), (3, 'prices.uploads.log'), (3, 'sales.open'),
  (3, 'sales.tab.wb'), (3, 'sales.tab.ozon'), (3, 'sales.tab.ya'), (3, 'sales.tab.summary'), (3, 'sales.table'), (3, 'sales.plan.edit'),
  (3, 'sales.plan.export'), (3, 'sales.plan.import'), (3, 'sales.summary.export'), (3, 'abc.open'), (3, 'abc.tab.overview'), (3, 'abc.tab.ozon'),
  (3, 'abc.tab.wb'), (3, 'abc.tab.ya'), (3, 'abc.tab.compare'), (3, 'abc.tab.actions'), (3, 'abc.common'), (3, 'abc.status.edit'),
  (3, 'abc.actions.export'), (3, 'stock.open'), (3, 'stock.tab.wb'), (3, 'stock.tab.ozon'), (3, 'stock.table'), (3, 'catalog.open'),
  (3, 'catalog.table'), (3, 'catalog.edit'), (3, 'catalog.add'), (3, 'catalog.export'), (3, 'catalog.import'),
  -- Маркетинг: 49
  (4, 'login'), (4, 'rnp.open'), (4, 'rnp.tab.wb'), (4, 'rnp.tab.ozon'), (4, 'rnp.tab.ya'), (4, 'rnp.table'),
  (4, 'rnp.unit_popup'), (4, 'rnp.product_link'), (4, 'rnp.comments.view'), (4, 'rnp.comments.add'), (4, 'rnp.comments.edit_own'), (4, 'rnp.anomalies.view'),
  (4, 'rnp.anomalies.rules'), (4, 'rnp.uploads.open'), (4, 'rnp.uploads.log'), (4, 'unit.open'), (4, 'unit.tab.wb'), (4, 'unit.tab.ozon'),
  (4, 'unit.tab.ya'), (4, 'unit.table'), (4, 'unit.margin'), (4, 'prices.open'), (4, 'prices.list.view'), (4, 'prices.list.table'),
  (4, 'prices.index.view'), (4, 'prices.index.export'), (4, 'sales.open'), (4, 'sales.tab.wb'), (4, 'sales.tab.ozon'), (4, 'sales.tab.ya'),
  (4, 'sales.tab.summary'), (4, 'sales.table'), (4, 'sales.summary.export'), (4, 'abc.open'), (4, 'abc.tab.overview'), (4, 'abc.tab.ozon'),
  (4, 'abc.tab.wb'), (4, 'abc.tab.ya'), (4, 'abc.tab.compare'), (4, 'abc.tab.actions'), (4, 'abc.common'), (4, 'abc.status.edit'),
  (4, 'abc.actions.export'), (4, 'stock.open'), (4, 'stock.tab.wb'), (4, 'stock.tab.ozon'), (4, 'stock.table'), (4, 'catalog.open'),
  (4, 'catalog.table'),
  -- Менеджер WB: 49
  (5, 'login'), (5, 'rnp.open'), (5, 'rnp.tab.wb'), (5, 'rnp.tab.ozon'), (5, 'rnp.tab.ya'), (5, 'rnp.table'),
  (5, 'rnp.unit_popup'), (5, 'rnp.product_link'), (5, 'rnp.comments.view'), (5, 'rnp.comments.add'), (5, 'rnp.comments.edit_own'), (5, 'rnp.anomalies.view'),
  (5, 'rnp.anomalies.rules'), (5, 'rnp.uploads.open'), (5, 'rnp.upload.wb_daily'), (5, 'rnp.upload.wb_reviews'), (5, 'rnp.upload.wb_ads'), (5, 'rnp.upload.wb_priceva'),
  (5, 'rnp.upload.wb_pi'), (5, 'rnp.upload.stock_1c'), (5, 'rnp.uploads.log'), (5, 'unit.open'), (5, 'unit.tab.wb'), (5, 'unit.table'),
  (5, 'unit.margin'), (5, 'prices.open'), (5, 'prices.list.view'), (5, 'prices.list.table'), (5, 'prices.index.view'), (5, 'prices.upload.wb'),
  (5, 'prices.uploads.log'), (5, 'sales.open'), (5, 'sales.tab.wb'), (5, 'sales.table'), (5, 'stock.open'), (5, 'stock.tab.wb'),
  (5, 'stock.table'), (5, 'stock.incidents.edit'), (5, 'stock.incidents.delete'), (5, 'stock.wh.merge'), (5, 'stock.wh.status'), (5, 'stock.wh.district'),
  (5, 'stock.upload.wb'), (5, 'stock.uploads.log'), (5, 'catalog.open'), (5, 'catalog.table'), (5, 'catalog.edit'), (5, 'catalog.export'),
  (5, 'catalog.import'),
  -- Менеджер OZ: 49
  (6, 'login'), (6, 'rnp.open'), (6, 'rnp.tab.wb'), (6, 'rnp.tab.ozon'), (6, 'rnp.tab.ya'), (6, 'rnp.table'),
  (6, 'rnp.unit_popup'), (6, 'rnp.product_link'), (6, 'rnp.comments.view'), (6, 'rnp.comments.add'), (6, 'rnp.comments.edit_own'), (6, 'rnp.anomalies.view'),
  (6, 'rnp.anomalies.rules'), (6, 'rnp.uploads.open'), (6, 'rnp.upload.ozon_daily'), (6, 'rnp.upload.ozon_ads'), (6, 'rnp.upload.ozon_reviews'), (6, 'rnp.upload.ozon_priceva'),
  (6, 'rnp.upload.stock_1c'), (6, 'rnp.uploads.log'), (6, 'unit.open'), (6, 'unit.tab.ozon'), (6, 'unit.table'), (6, 'unit.margin'),
  (6, 'prices.open'), (6, 'prices.list.view'), (6, 'prices.list.table'), (6, 'prices.index.view'), (6, 'prices.upload.ozon'), (6, 'prices.uploads.log'),
  (6, 'sales.open'), (6, 'sales.tab.ozon'), (6, 'sales.table'), (6, 'stock.open'), (6, 'stock.tab.ozon'), (6, 'stock.table'),
  (6, 'stock.incidents.edit'), (6, 'stock.incidents.delete'), (6, 'stock.wh.merge'), (6, 'stock.wh.status'), (6, 'stock.wh.district'), (6, 'stock.wh.cluster'),
  (6, 'stock.upload.ozon'), (6, 'stock.uploads.log'), (6, 'catalog.open'), (6, 'catalog.table'), (6, 'catalog.edit'), (6, 'catalog.export'),
  (6, 'catalog.import'),
  -- Менеджер YA: 32
  (7, 'login'), (7, 'rnp.open'), (7, 'rnp.tab.wb'), (7, 'rnp.tab.ozon'), (7, 'rnp.tab.ya'), (7, 'rnp.table'),
  (7, 'rnp.unit_popup'), (7, 'rnp.product_link'), (7, 'rnp.comments.view'), (7, 'rnp.comments.add'), (7, 'rnp.comments.edit_own'), (7, 'rnp.anomalies.view'),
  (7, 'rnp.anomalies.rules'), (7, 'rnp.uploads.open'), (7, 'rnp.upload.ya_daily'), (7, 'rnp.upload.ya_stock'), (7, 'rnp.upload.stock_1c'), (7, 'rnp.uploads.log'),
  (7, 'unit.open'), (7, 'unit.tab.ya'), (7, 'unit.table'), (7, 'unit.margin'), (7, 'prices.open'), (7, 'prices.list.view'),
  (7, 'prices.list.table'), (7, 'prices.index.view'), (7, 'sales.open'), (7, 'sales.tab.ya'), (7, 'sales.table'), (7, 'catalog.open'),
  (7, 'catalog.table'), (7, 'catalog.edit'),
  -- Ассистент: 38
  (8, 'login'), (8, 'rnp.open'), (8, 'rnp.tab.wb'), (8, 'rnp.tab.ozon'), (8, 'rnp.tab.ya'), (8, 'rnp.table'),
  (8, 'rnp.product_link'), (8, 'rnp.comments.view'), (8, 'rnp.anomalies.rules'), (8, 'rnp.uploads.open'), (8, 'rnp.upload.ozon_daily'), (8, 'rnp.upload.ozon_ads'),
  (8, 'rnp.upload.ozon_reviews'), (8, 'rnp.upload.ozon_priceva'), (8, 'rnp.upload.wb_daily'), (8, 'rnp.upload.wb_reviews'), (8, 'rnp.upload.wb_ads'), (8, 'rnp.upload.wb_priceva'),
  (8, 'rnp.upload.wb_pi'), (8, 'rnp.upload.ya_daily'), (8, 'rnp.upload.ya_stock'), (8, 'rnp.upload.stock_1c'), (8, 'rnp.uploads.log'), (8, 'prices.open'),
  (8, 'prices.upload.ozon'), (8, 'prices.upload.wb'), (8, 'prices.uploads.log'), (8, 'stock.open'), (8, 'stock.tab.wb'), (8, 'stock.tab.ozon'),
  (8, 'stock.upload.wb'), (8, 'stock.upload.ozon'), (8, 'stock.uploads.log'), (8, 'catalog.open'), (8, 'catalog.table'), (8, 'catalog.edit'),
  (8, 'catalog.export'), (8, 'catalog.import');
