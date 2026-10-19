-- ABC: пороги рекомендаций, которые настраивает владелец (кнопка ⚙ в разделе ABC).
-- Рублёвые пороги — в месяц и по площадке (клиент умножает на число месяцев периода);
-- marketplace = 'all' — общие параметры. Решение владельца 19.10.2026.
CREATE TABLE IF NOT EXISTS abc_rules (
    param              text        NOT NULL,
    marketplace        text        NOT NULL CHECK (marketplace IN ('ozon', 'vb', 'ya', 'all')),
    value              numeric     NOT NULL CHECK (value >= 0),
    updated_at         timestamptz NOT NULL DEFAULT now(),
    updated_by_user_id integer,
    PRIMARY KEY (param, marketplace)
);

INSERT INTO abc_rules (param, marketplace, value) VALUES
    ('tail_rev',          'ozon', 30000), ('tail_rev',          'vb', 30000), ('tail_rev',          'ya', 15000),
    ('low_margin_profit', 'ozon', 30000), ('low_margin_profit', 'vb', 30000), ('low_margin_profit', 'ya', 15000),
    ('remove_here_rev',   'ozon', 100000), ('remove_here_rev',  'vb', 100000),
    ('low_margin_pct',    'all', 0.05),
    ('remove_here_mult',  'all', 5),
    ('consider_mult',     'all', 3)
ON CONFLICT (param, marketplace) DO NOTHING;
