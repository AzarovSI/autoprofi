-- Daily derived prices. Applied transactionally by app.daily_pi.migrate().
-- No modifications to source price or RNP rows.
CREATE TABLE price_index_daily (
    seller_article text NOT NULL,
    date date NOT NULL,
    wb_base_pi numeric,
    ozon_base_pi numeric,
    buyer_pi numeric,
    calculated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (seller_article, date)
);
CREATE INDEX price_index_daily_date_idx ON price_index_daily(date);
CREATE TABLE price_index_revision (
    singleton boolean PRIMARY KEY DEFAULT true CHECK (singleton),
    revision bigint NOT NULL DEFAULT 0
);
INSERT INTO price_index_revision VALUES (true, 0);

-- Serialize price-affecting statements, including concurrent WB/Ozon imports.
-- BEFORE statement lock is acquired before any source rows are changed.
CREATE FUNCTION daily_pi_lock() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    PERFORM pg_advisory_xact_lock(21092026, 1);
    RETURN NULL;
END $$;

CREATE FUNCTION refresh_daily_pi(articles text[], dates date[] DEFAULT NULL)
RETURNS void LANGUAGE plpgsql AS $$
BEGIN
    IF cardinality(articles) IS NOT DISTINCT FROM 0 THEN RETURN; END IF;
    PERFORM pg_advisory_xact_lock(21092026, 1);
    DELETE FROM price_index_daily p
    WHERE (articles IS NULL OR p.seller_article = ANY(articles))
      AND (dates IS NULL OR p.date = ANY(dates));

    INSERT INTO price_index_daily(seller_article, date, wb_base_pi, ozon_base_pi, buyer_pi)
    WITH keys AS (
        SELECT DISTINCT seller_article, date
        FROM mp_price_daily
        WHERE marketplace IN ('Ozon', 'Wildberries')
          AND (articles IS NULL OR seller_article = ANY(articles))
          AND (dates IS NULL OR date = ANY(dates))
    ), inputs AS (
        SELECT k.*, w.upload_price AS wu, o.upload_price AS ou,
            -- Legacy estimated SPP is not a fact for this date.
            CASE WHEN w.spp_pct > 0 AND NOT w.spp_is_estimated
                       AND w.spp_source_date = k.date AND w.buyer_price > 0
                 THEN w.buyer_price END AS wb,
            CASE WHEN o.spp_pct > 0 AND NOT o.spp_is_estimated
                       AND o.spp_source_date = k.date AND o.buyer_price > 0
                 THEN o.buyer_price END AS ob,
            bw.base_price AS bw, bo.base_price AS bo,
            position('АВТОПРОФИ' in upper(coalesce(ci.category_l1, ''))) > 0 AS ap
        FROM keys k
        LEFT JOIN mp_price_daily w ON w.seller_article = k.seller_article
            AND w.date = k.date AND w.marketplace = 'Wildberries'
        LEFT JOIN mp_price_daily o ON o.seller_article = k.seller_article
            AND o.date = k.date AND o.marketplace = 'Ozon'
        LEFT JOIN catalog_items ci ON ci.seller_article = k.seller_article
        LEFT JOIN LATERAL (
            SELECT h.base_price FROM catalog_base_prices_hist h
            WHERE h.seller_article = k.seller_article AND h.marketplace = 'Wildberries'
              AND h.valid_from < ((k.date + 1)::timestamp AT TIME ZONE 'Europe/Moscow')
              AND (h.valid_to IS NULL OR
                   h.valid_to >= ((k.date + 1)::timestamp AT TIME ZONE 'Europe/Moscow'))
            ORDER BY h.valid_from DESC LIMIT 1
        ) bw ON true
        LEFT JOIN LATERAL (
            SELECT h.base_price FROM catalog_base_prices_hist h
            WHERE h.seller_article = k.seller_article AND h.marketplace = 'Ozon'
              AND h.valid_from < ((k.date + 1)::timestamp AT TIME ZONE 'Europe/Moscow')
              AND (h.valid_to IS NULL OR
                   h.valid_to >= ((k.date + 1)::timestamp AT TIME ZONE 'Europe/Moscow'))
            ORDER BY h.valid_from DESC LIMIT 1
        ) bo ON true
    )
    SELECT seller_article, date,
        CASE WHEN bw > 0 THEN (CASE WHEN ap THEN wb ELSE wu END) / bw END,
        CASE WHEN bo > 0 THEN (CASE WHEN ap THEN ob ELSE ou END) / bo END,
        CASE WHEN wb > 0 THEN ob / wb END
    FROM inputs;
    UPDATE price_index_revision SET revision = revision + 1 WHERE singleton;
END $$;

-- Transition tables make recalculation batch-oriented, not one query per row.
CREATE FUNCTION daily_pi_changed() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE arts text[]; ds date[];
BEGIN
    IF TG_OP = 'INSERT' THEN
        SELECT array_agg(DISTINCT seller_article) INTO arts FROM new_rows;
        IF TG_TABLE_NAME = 'mp_price_daily' THEN
            SELECT array_agg(DISTINCT date) INTO ds FROM new_rows;
        END IF;
    ELSIF TG_OP = 'DELETE' THEN
        SELECT array_agg(DISTINCT seller_article) INTO arts FROM old_rows;
        IF TG_TABLE_NAME = 'mp_price_daily' THEN
            SELECT array_agg(DISTINCT date) INTO ds FROM old_rows;
        END IF;
    ELSE
        SELECT array_agg(DISTINCT seller_article) INTO arts FROM (
            SELECT seller_article FROM new_rows UNION SELECT seller_article FROM old_rows
        ) a;
        IF TG_TABLE_NAME = 'mp_price_daily' THEN
            SELECT array_agg(DISTINCT date) INTO ds FROM (
                SELECT date FROM new_rows UNION SELECT date FROM old_rows
            ) d;
        END IF;
    END IF;
    -- Empty transition table (e.g. INSERT part of an UPSERT) is not a full rebuild.
    IF arts IS NOT NULL THEN PERFORM refresh_daily_pi(arts, ds); END IF;
    RETURN NULL;
END $$;

DO $$
DECLARE tbl text;
BEGIN
    FOREACH tbl IN ARRAY ARRAY['mp_price_daily', 'catalog_base_prices_hist', 'catalog_items'] LOOP
        EXECUTE format('CREATE TRIGGER daily_pi_lock BEFORE INSERT OR UPDATE OR DELETE ON %I
            FOR EACH STATEMENT EXECUTE FUNCTION daily_pi_lock()', tbl);
        EXECUTE format('CREATE TRIGGER daily_pi_insert AFTER INSERT ON %I
            REFERENCING NEW TABLE AS new_rows FOR EACH STATEMENT EXECUTE FUNCTION daily_pi_changed()', tbl);
        EXECUTE format('CREATE TRIGGER daily_pi_update AFTER UPDATE ON %I
            REFERENCING OLD TABLE AS old_rows NEW TABLE AS new_rows
            FOR EACH STATEMENT EXECUTE FUNCTION daily_pi_changed()', tbl);
        EXECUTE format('CREATE TRIGGER daily_pi_delete AFTER DELETE ON %I
            REFERENCING OLD TABLE AS old_rows FOR EACH STATEMENT EXECUTE FUNCTION daily_pi_changed()', tbl);
    END LOOP;
END $$;

-- Backfill only dates present in saved source reports, no fabricated days.
SELECT refresh_daily_pi(NULL, NULL);
