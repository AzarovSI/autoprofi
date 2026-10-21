-- «Цены → Ценовая модель»: пороги сигнала корректировки загружаемой цены (кнопка ⚙, решение владельца 21.10.2026).
-- Сигнал — когда загружаемая цена в кабинете площадки отклоняется от расчётной (цена категории / (1 − СПП))
-- больше порога. Порог у каждой площадки свой, доля (0.05 = 5%), общий для всех сотрудников.
CREATE TABLE IF NOT EXISTS price_model_rules (
    marketplace        text         PRIMARY KEY CHECK (marketplace IN ('Wildberries', 'Ozon')),
    threshold          numeric(6,4) NOT NULL CHECK (threshold > 0 AND threshold <= 0.5),
    updated_at         timestamptz  NOT NULL DEFAULT now(),
    updated_by_user_id integer
);

INSERT INTO price_model_rules (marketplace, threshold) VALUES ('Wildberries', 0.05), ('Ozon', 0.05)
ON CONFLICT (marketplace) DO NOTHING;
