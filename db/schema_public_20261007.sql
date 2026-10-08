--
-- PostgreSQL database dump
--

\restrict ZLBVJghGmLk21TmEOZBKgsBkPdjUMGg4KTfJ0JoaLgGdYTOYm4cr2HKl5WTMaf3

-- Dumped from database version 17.10 (Ubuntu 17.10-201-yandex.60090.7e0ac7b2c9)
-- Dumped by pg_dump version 18.6 (Postgres.app)

SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET transaction_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;

--
-- Name: public; Type: SCHEMA; Schema: -; Owner: -
--



--
-- Name: SCHEMA public; Type: COMMENT; Schema: -; Owner: -
--



--
-- Name: daily_pi_changed(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.daily_pi_changed() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
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


--
-- Name: daily_pi_lock(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.daily_pi_lock() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    PERFORM pg_advisory_xact_lock(21092026, 1);
    RETURN NULL;
END $$;


--
-- Name: refresh_daily_pi(text[], date[]); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.refresh_daily_pi(articles text[], dates date[] DEFAULT NULL::date[]) RETURNS void
    LANGUAGE plpgsql
    AS $$
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


SET default_tablespace = '';

SET default_table_access_method = heap;

--
-- Name: anomaly_rules; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.anomaly_rules (
    rule_key text NOT NULL,
    label text NOT NULL,
    kind text NOT NULL,
    direction text,
    threshold numeric,
    threshold2 numeric,
    enabled boolean DEFAULT true NOT NULL,
    ads_only boolean DEFAULT false NOT NULL,
    sort_order integer DEFAULT 0 NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: app_schema_migrations; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.app_schema_migrations (
    name text NOT NULL,
    applied_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: app_users; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.app_users (
    id bigint NOT NULL,
    email text NOT NULL,
    password_hash text NOT NULL,
    display_name text,
    role text DEFAULT 'admin'::text,
    created_at timestamp with time zone DEFAULT now(),
    last_seen_at timestamp with time zone
);


--
-- Name: app_users_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.app_users_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: app_users_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.app_users_id_seq OWNED BY public.app_users.id;


--
-- Name: catalog_base_prices; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.catalog_base_prices (
    seller_article text NOT NULL,
    marketplace text NOT NULL,
    base_price integer,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_by_user_id integer
);


--
-- Name: catalog_base_prices_hist; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.catalog_base_prices_hist (
    seller_article text NOT NULL,
    marketplace text NOT NULL,
    base_price integer,
    valid_from timestamp with time zone NOT NULL,
    valid_to timestamp with time zone,
    updated_by_user_id integer
);


--
-- Name: catalog_items; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.catalog_items (
    seller_article text NOT NULL,
    sample_name text,
    category_l1 text,
    category_l2 text,
    category_l3 text,
    status text,
    updated_at timestamp with time zone DEFAULT now(),
    manager text
);


--
-- Name: catalog_marketplace; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.catalog_marketplace (
    seller_article text NOT NULL,
    marketplace text NOT NULL,
    status text,
    manager text,
    updated_at timestamp with time zone DEFAULT now(),
    product_url text
);


--
-- Name: category_tree; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.category_tree (
    id bigint NOT NULL,
    level integer NOT NULL,
    name text NOT NULL,
    parent_id bigint
);


--
-- Name: category_tree_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.category_tree_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: category_tree_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.category_tree_id_seq OWNED BY public.category_tree.id;


--
-- Name: fact_monthly; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.fact_monthly (
    id bigint NOT NULL,
    month smallint NOT NULL,
    year integer NOT NULL,
    period_text text,
    period_start date,
    period_end date,
    marketplace text NOT NULL,
    row_type text NOT NULL,
    seller_article text,
    sku_ozon text,
    barcode_wb text,
    item_name text,
    brand text,
    category_l1 text,
    category_l2 text,
    category_l3 text,
    status text,
    orders_qty numeric,
    sales_qty numeric,
    returns_qty numeric,
    cancels_qty numeric,
    total_sales_qty numeric,
    revenue numeric,
    sales_gross numeric,
    returns_rub numeric,
    revenue_share_pct numeric,
    avg_sale_price numeric,
    spp_pct numeric,
    buyout_pct numeric,
    cogs numeric,
    cogs_pct numeric,
    commission numeric,
    commission_pct numeric,
    acquiring numeric,
    acquiring_pct numeric,
    logistics_direct numeric,
    logistics_reverse numeric,
    logistics_total numeric,
    logistics_per_unit numeric,
    logistics_pct numeric,
    oz_delivery_to_point numeric,
    oz_item_handout numeric,
    oz_returns_processing numeric,
    storage numeric,
    storage_pct numeric,
    wb_acceptance numeric,
    oz_disposal numeric,
    promo_total numeric,
    promo_pct numeric,
    wb_ads_total numeric,
    wb_drr_pct numeric,
    oz_pay_per_order numeric,
    oz_pay_per_click numeric,
    oz_special_placement numeric,
    wb_other_holds numeric,
    wb_fines numeric,
    wb_defect_compensation numeric,
    holds_total numeric,
    holds_total_pct numeric,
    tax_base numeric,
    tax numeric,
    external_exp_inc numeric,
    external_exp_inc_pct numeric,
    payout numeric,
    profit numeric,
    profit_per_unit numeric,
    margin_pct numeric,
    roi numeric,
    wb_profit_with_ads numeric,
    wb_margin_with_ads_pct numeric,
    wb_roi_with_ads numeric,
    abc_revenue text,
    abc_sales_qty text,
    abc_profit text,
    upload_id bigint,
    created_at timestamp with time zone DEFAULT now(),
    oz_installment numeric,
    orders_rub numeric
);


--
-- Name: fact_monthly_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.fact_monthly_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: fact_monthly_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.fact_monthly_id_seq OWNED BY public.fact_monthly.id;


--
-- Name: fact_weekly; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.fact_weekly (
    id bigint NOT NULL,
    week integer NOT NULL,
    year integer NOT NULL,
    period_text text,
    period_start date,
    period_end date,
    marketplace text NOT NULL,
    row_type text NOT NULL,
    seller_article text,
    sku_ozon text,
    barcode_wb text,
    item_name text,
    brand text,
    category_l1 text,
    category_l2 text,
    category_l3 text,
    status text,
    orders_qty numeric,
    sales_qty numeric,
    returns_qty numeric,
    cancels_qty numeric,
    total_sales_qty numeric,
    revenue numeric,
    sales_gross numeric,
    returns_rub numeric,
    revenue_share_pct numeric,
    avg_sale_price numeric,
    spp_pct numeric,
    buyout_pct numeric,
    cogs numeric,
    cogs_pct numeric,
    commission numeric,
    commission_pct numeric,
    acquiring numeric,
    acquiring_pct numeric,
    logistics_direct numeric,
    logistics_reverse numeric,
    logistics_total numeric,
    logistics_per_unit numeric,
    logistics_pct numeric,
    oz_delivery_to_point numeric,
    oz_item_handout numeric,
    oz_returns_processing numeric,
    storage numeric,
    storage_pct numeric,
    wb_acceptance numeric,
    oz_disposal numeric,
    promo_total numeric,
    promo_pct numeric,
    wb_ads_total numeric,
    wb_drr_pct numeric,
    oz_pay_per_order numeric,
    oz_pay_per_click numeric,
    oz_special_placement numeric,
    wb_other_holds numeric,
    wb_fines numeric,
    wb_defect_compensation numeric,
    holds_total numeric,
    holds_total_pct numeric,
    tax_base numeric,
    tax numeric,
    external_exp_inc numeric,
    external_exp_inc_pct numeric,
    payout numeric,
    profit numeric,
    profit_per_unit numeric,
    margin_pct numeric,
    roi numeric,
    wb_profit_with_ads numeric,
    wb_margin_with_ads_pct numeric,
    wb_roi_with_ads numeric,
    abc_revenue text,
    abc_sales_qty text,
    abc_profit text,
    upload_id bigint,
    created_at timestamp with time zone DEFAULT now(),
    oz_installment numeric,
    orders_rub numeric
);


--
-- Name: fact_weekly_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.fact_weekly_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: fact_weekly_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.fact_weekly_id_seq OWNED BY public.fact_weekly.id;


--
-- Name: item_cost; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.item_cost (
    seller_article text NOT NULL,
    cost_calc numeric,
    cost_rf numeric,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: item_cost_hist; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.item_cost_hist (
    seller_article text NOT NULL,
    cost_calc numeric,
    cost_rf numeric,
    start_date date NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: mp_price_daily; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.mp_price_daily (
    seller_article text NOT NULL,
    marketplace text NOT NULL,
    date date NOT NULL,
    upload_price numeric(12,2),
    upload_source text,
    upload_loaded_at timestamp with time zone,
    upload_loaded_by integer,
    spp_pct numeric(6,4),
    spp_source_date date,
    spp_is_estimated boolean DEFAULT false NOT NULL,
    buyer_price numeric(12,2),
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: mp_price_upload_log; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.mp_price_upload_log (
    id bigint NOT NULL,
    loaded_at timestamp with time zone DEFAULT now() NOT NULL,
    loaded_by_user_id integer,
    marketplace text NOT NULL,
    report_date date NOT NULL,
    file_name text,
    rows_in_file integer,
    rows_upserted integer,
    rows_skipped_unknown integer,
    skipped_sample text,
    status text NOT NULL,
    message text
);


--
-- Name: mp_price_upload_log_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.mp_price_upload_log_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: mp_price_upload_log_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.mp_price_upload_log_id_seq OWNED BY public.mp_price_upload_log.id;


--
-- Name: ozon_daily_sales; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.ozon_daily_sales (
    id bigint NOT NULL,
    date date NOT NULL,
    seller_article text NOT NULL,
    sku_ozon bigint,
    item_name text,
    orders_qty numeric,
    orders_rub numeric,
    cancels_qty numeric,
    price_index_pi numeric,
    spp_rub numeric,
    spp_pct numeric,
    card_visits numeric,
    cr_cart_pct numeric,
    cr_order_pct numeric,
    avg_position numeric,
    rating numeric,
    stock_ozon_qty numeric,
    avg_upload_price numeric,
    price_for_buyer numeric,
    comp_price_avg numeric,
    comp_price_min numeric,
    drr_total_pct numeric,
    ads_expense_rub numeric,
    ctr_pct numeric,
    delivery_time_hours numeric,
    stock_ap_qty numeric,
    upload_id bigint,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: ozon_daily_sales_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.ozon_daily_sales_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: ozon_daily_sales_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.ozon_daily_sales_id_seq OWNED BY public.ozon_daily_sales.id;


--
-- Name: ozon_stock_daily; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.ozon_stock_daily (
    date date NOT NULL,
    seller_article text NOT NULL,
    warehouse_id integer NOT NULL,
    qty numeric
);


--
-- Name: ozon_stock_meta; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.ozon_stock_meta (
    date date NOT NULL,
    seller_article text NOT NULL,
    avail_qty numeric,
    total_hp numeric,
    in_transit numeric,
    returns_qty numeric,
    to_removal numeric
);


--
-- Name: ozon_warehouses; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.ozon_warehouses (
    id integer NOT NULL,
    name text NOT NULL,
    cluster text,
    federal_district text,
    canonical_id integer,
    first_seen_date date,
    is_active boolean DEFAULT true,
    status text,
    status_date date,
    created_at timestamp with time zone DEFAULT now()
);


--
-- Name: ozon_warehouses_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.ozon_warehouses_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: ozon_warehouses_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.ozon_warehouses_id_seq OWNED BY public.ozon_warehouses.id;


--
-- Name: ozon_wh_incidents; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.ozon_wh_incidents (
    id integer NOT NULL,
    wh_id integer NOT NULL,
    start_date date NOT NULL,
    end_date date,
    created_at timestamp with time zone DEFAULT now()
);


--
-- Name: ozon_wh_incidents_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.ozon_wh_incidents_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: ozon_wh_incidents_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.ozon_wh_incidents_id_seq OWNED BY public.ozon_wh_incidents.id;


--
-- Name: price_index_daily; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.price_index_daily (
    seller_article text NOT NULL,
    date date NOT NULL,
    wb_base_pi numeric,
    ozon_base_pi numeric,
    buyer_pi numeric,
    calculated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: price_index_revision; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.price_index_revision (
    singleton boolean DEFAULT true NOT NULL,
    revision bigint DEFAULT 0 NOT NULL,
    CONSTRAINT price_index_revision_singleton_check CHECK (singleton)
);


--
-- Name: report_uploads; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.report_uploads (
    id bigint NOT NULL,
    marketplace text NOT NULL,
    week integer,
    year integer NOT NULL,
    period_text text,
    period_start date,
    period_end date,
    source_file text,
    s3_key text,
    rows_loaded integer,
    status text,
    message text,
    uploaded_by bigint,
    uploaded_at timestamp with time zone DEFAULT now(),
    period_kind text,
    month smallint,
    fact_through_date date,
    uploaded_by_user_id integer DEFAULT (NULLIF(current_setting('app.uploader_id'::text, true), ''::text))::integer
);


--
-- Name: report_uploads_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.report_uploads_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: report_uploads_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.report_uploads_id_seq OWNED BY public.report_uploads.id;


--
-- Name: rnp_comments; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.rnp_comments (
    id bigint NOT NULL,
    seller_article text NOT NULL,
    date date NOT NULL,
    body text NOT NULL,
    author_id bigint NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: rnp_comments_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.rnp_comments_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: rnp_comments_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.rnp_comments_id_seq OWNED BY public.rnp_comments.id;


--
-- Name: sales_plan; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.sales_plan (
    seller_article text NOT NULL,
    marketplace text NOT NULL,
    year integer NOT NULL,
    month integer NOT NULL,
    plan_qty numeric,
    updated_at timestamp with time zone DEFAULT now(),
    is_manual boolean DEFAULT false NOT NULL,
    prev_qty numeric
);


--
-- Name: stock_daily; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.stock_daily (
    date date NOT NULL,
    seller_article text NOT NULL,
    qty numeric,
    updated_at timestamp with time zone DEFAULT now()
);


--
-- Name: wb_daily_sales; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.wb_daily_sales (
    id bigint NOT NULL,
    date date NOT NULL,
    seller_article text NOT NULL,
    nm_id bigint,
    item_name text,
    orders_qty numeric,
    orders_rub numeric,
    cancels_qty numeric,
    cancels_rub numeric,
    buyout_qty numeric,
    buyout_rub numeric,
    card_visits numeric,
    add_to_cart_qty numeric,
    cr_cart_pct numeric,
    cr_order_pct numeric,
    rating numeric,
    stock_wb_qty numeric,
    reviews_qty numeric,
    ads_expense_rub numeric,
    ctr_pct numeric,
    ads_views numeric,
    ads_clicks numeric,
    ads_avg_cpc numeric,
    ads_atbs numeric,
    ads_orders numeric,
    ads_shks numeric,
    ads_sum_price numeric,
    stock_ap_qty numeric,
    upload_id bigint,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    comp_price_avg numeric,
    comp_price_min numeric,
    avg_search_position numeric,
    price_index_pi numeric,
    spp_pct numeric
);


--
-- Name: wb_daily_sales_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.wb_daily_sales ALTER COLUMN id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME public.wb_daily_sales_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: wb_stock_daily; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.wb_stock_daily (
    date date NOT NULL,
    seller_article text NOT NULL,
    warehouse_id integer NOT NULL,
    qty numeric
);


--
-- Name: wb_stock_meta; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.wb_stock_meta (
    date date NOT NULL,
    seller_article text NOT NULL,
    wb_article text,
    volume_l numeric,
    in_transit_to_client numeric,
    in_transit_returns numeric,
    total_on_wh numeric
);


--
-- Name: wb_warehouses; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.wb_warehouses (
    id integer NOT NULL,
    name text NOT NULL,
    canonical_id integer,
    first_seen_date date,
    is_active boolean DEFAULT true,
    created_at timestamp with time zone DEFAULT now(),
    status text,
    status_date date,
    federal_district text
);


--
-- Name: wb_warehouses_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.wb_warehouses_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: wb_warehouses_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.wb_warehouses_id_seq OWNED BY public.wb_warehouses.id;


--
-- Name: wb_wh_incidents; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.wb_wh_incidents (
    id bigint NOT NULL,
    wh_id bigint NOT NULL,
    start_date date NOT NULL,
    end_date date,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: wb_wh_incidents_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.wb_wh_incidents_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: wb_wh_incidents_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.wb_wh_incidents_id_seq OWNED BY public.wb_wh_incidents.id;


--
-- Name: ya_daily_sales; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.ya_daily_sales (
    id bigint NOT NULL,
    date date NOT NULL,
    seller_article text NOT NULL,
    ya_sku text,
    item_name text,
    shows_qty numeric,
    card_visits numeric,
    add_to_cart_qty numeric,
    orders_qty numeric,
    orders_rub numeric,
    cancels_qty numeric,
    cr_cart_pct numeric,
    cr_order_pct numeric,
    ctr_pct numeric,
    spp_pct numeric,
    spp_rub numeric,
    price_index_pi numeric,
    drr_total_pct numeric,
    ads_expense_rub numeric,
    avg_position numeric,
    reviews_qty numeric,
    rating numeric,
    stock_ya_qty numeric,
    stock_ap_qty numeric,
    upload_id bigint,
    created_at timestamp with time zone DEFAULT now()
);


--
-- Name: ya_daily_sales_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.ya_daily_sales ALTER COLUMN id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME public.ya_daily_sales_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: app_users id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.app_users ALTER COLUMN id SET DEFAULT nextval('public.app_users_id_seq'::regclass);


--
-- Name: category_tree id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.category_tree ALTER COLUMN id SET DEFAULT nextval('public.category_tree_id_seq'::regclass);


--
-- Name: fact_monthly id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fact_monthly ALTER COLUMN id SET DEFAULT nextval('public.fact_monthly_id_seq'::regclass);


--
-- Name: fact_weekly id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fact_weekly ALTER COLUMN id SET DEFAULT nextval('public.fact_weekly_id_seq'::regclass);


--
-- Name: mp_price_upload_log id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.mp_price_upload_log ALTER COLUMN id SET DEFAULT nextval('public.mp_price_upload_log_id_seq'::regclass);


--
-- Name: ozon_daily_sales id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ozon_daily_sales ALTER COLUMN id SET DEFAULT nextval('public.ozon_daily_sales_id_seq'::regclass);


--
-- Name: ozon_warehouses id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ozon_warehouses ALTER COLUMN id SET DEFAULT nextval('public.ozon_warehouses_id_seq'::regclass);


--
-- Name: ozon_wh_incidents id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ozon_wh_incidents ALTER COLUMN id SET DEFAULT nextval('public.ozon_wh_incidents_id_seq'::regclass);


--
-- Name: report_uploads id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.report_uploads ALTER COLUMN id SET DEFAULT nextval('public.report_uploads_id_seq'::regclass);


--
-- Name: rnp_comments id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rnp_comments ALTER COLUMN id SET DEFAULT nextval('public.rnp_comments_id_seq'::regclass);


--
-- Name: wb_warehouses id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.wb_warehouses ALTER COLUMN id SET DEFAULT nextval('public.wb_warehouses_id_seq'::regclass);


--
-- Name: wb_wh_incidents id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.wb_wh_incidents ALTER COLUMN id SET DEFAULT nextval('public.wb_wh_incidents_id_seq'::regclass);


--
-- Name: anomaly_rules anomaly_rules_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.anomaly_rules
    ADD CONSTRAINT anomaly_rules_pkey PRIMARY KEY (rule_key);


--
-- Name: app_schema_migrations app_schema_migrations_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.app_schema_migrations
    ADD CONSTRAINT app_schema_migrations_pkey PRIMARY KEY (name);


--
-- Name: app_users app_users_email_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.app_users
    ADD CONSTRAINT app_users_email_key UNIQUE (email);


--
-- Name: app_users app_users_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.app_users
    ADD CONSTRAINT app_users_pkey PRIMARY KEY (id);


--
-- Name: catalog_base_prices_hist catalog_base_prices_hist_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.catalog_base_prices_hist
    ADD CONSTRAINT catalog_base_prices_hist_pkey PRIMARY KEY (seller_article, marketplace, valid_from);


--
-- Name: catalog_base_prices catalog_base_prices_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.catalog_base_prices
    ADD CONSTRAINT catalog_base_prices_pkey PRIMARY KEY (seller_article, marketplace);


--
-- Name: catalog_items catalog_items_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.catalog_items
    ADD CONSTRAINT catalog_items_pkey PRIMARY KEY (seller_article);


--
-- Name: catalog_marketplace catalog_marketplace_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.catalog_marketplace
    ADD CONSTRAINT catalog_marketplace_pkey PRIMARY KEY (seller_article, marketplace);


--
-- Name: category_tree category_tree_level_name_parent_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.category_tree
    ADD CONSTRAINT category_tree_level_name_parent_id_key UNIQUE (level, name, parent_id);


--
-- Name: category_tree category_tree_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.category_tree
    ADD CONSTRAINT category_tree_pkey PRIMARY KEY (id);


--
-- Name: fact_monthly fact_monthly_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fact_monthly
    ADD CONSTRAINT fact_monthly_pkey PRIMARY KEY (id);


--
-- Name: fact_weekly fact_weekly_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fact_weekly
    ADD CONSTRAINT fact_weekly_pkey PRIMARY KEY (id);


--
-- Name: item_cost_hist item_cost_hist_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.item_cost_hist
    ADD CONSTRAINT item_cost_hist_pkey PRIMARY KEY (seller_article, start_date);


--
-- Name: item_cost item_cost_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.item_cost
    ADD CONSTRAINT item_cost_pkey PRIMARY KEY (seller_article);


--
-- Name: mp_price_daily mp_price_daily_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.mp_price_daily
    ADD CONSTRAINT mp_price_daily_pkey PRIMARY KEY (seller_article, marketplace, date);


--
-- Name: mp_price_upload_log mp_price_upload_log_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.mp_price_upload_log
    ADD CONSTRAINT mp_price_upload_log_pkey PRIMARY KEY (id);


--
-- Name: ozon_daily_sales ozon_daily_sales_date_seller_article_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ozon_daily_sales
    ADD CONSTRAINT ozon_daily_sales_date_seller_article_key UNIQUE (date, seller_article);


--
-- Name: ozon_daily_sales ozon_daily_sales_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ozon_daily_sales
    ADD CONSTRAINT ozon_daily_sales_pkey PRIMARY KEY (id);


--
-- Name: ozon_warehouses ozon_warehouses_name_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ozon_warehouses
    ADD CONSTRAINT ozon_warehouses_name_key UNIQUE (name);


--
-- Name: ozon_warehouses ozon_warehouses_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ozon_warehouses
    ADD CONSTRAINT ozon_warehouses_pkey PRIMARY KEY (id);


--
-- Name: ozon_wh_incidents ozon_wh_incidents_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ozon_wh_incidents
    ADD CONSTRAINT ozon_wh_incidents_pkey PRIMARY KEY (id);


--
-- Name: price_index_daily price_index_daily_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.price_index_daily
    ADD CONSTRAINT price_index_daily_pkey PRIMARY KEY (seller_article, date);


--
-- Name: price_index_revision price_index_revision_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.price_index_revision
    ADD CONSTRAINT price_index_revision_pkey PRIMARY KEY (singleton);


--
-- Name: report_uploads report_uploads_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.report_uploads
    ADD CONSTRAINT report_uploads_pkey PRIMARY KEY (id);


--
-- Name: rnp_comments rnp_comments_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rnp_comments
    ADD CONSTRAINT rnp_comments_pkey PRIMARY KEY (id);


--
-- Name: sales_plan sales_plan_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.sales_plan
    ADD CONSTRAINT sales_plan_pkey PRIMARY KEY (seller_article, marketplace, year, month);


--
-- Name: wb_daily_sales wb_daily_sales_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.wb_daily_sales
    ADD CONSTRAINT wb_daily_sales_pkey PRIMARY KEY (id);


--
-- Name: wb_stock_daily wb_stock_daily_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.wb_stock_daily
    ADD CONSTRAINT wb_stock_daily_pkey PRIMARY KEY (date, seller_article, warehouse_id);


--
-- Name: wb_stock_meta wb_stock_meta_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.wb_stock_meta
    ADD CONSTRAINT wb_stock_meta_pkey PRIMARY KEY (date, seller_article);


--
-- Name: wb_warehouses wb_warehouses_name_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.wb_warehouses
    ADD CONSTRAINT wb_warehouses_name_key UNIQUE (name);


--
-- Name: wb_warehouses wb_warehouses_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.wb_warehouses
    ADD CONSTRAINT wb_warehouses_pkey PRIMARY KEY (id);


--
-- Name: wb_wh_incidents wb_wh_incidents_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.wb_wh_incidents
    ADD CONSTRAINT wb_wh_incidents_pkey PRIMARY KEY (id);


--
-- Name: ya_daily_sales ya_daily_sales_date_seller_article_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ya_daily_sales
    ADD CONSTRAINT ya_daily_sales_date_seller_article_key UNIQUE (date, seller_article);


--
-- Name: ya_daily_sales ya_daily_sales_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ya_daily_sales
    ADD CONSTRAINT ya_daily_sales_pkey PRIMARY KEY (id);


--
-- Name: idx_rnp_comments_date_art; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_rnp_comments_date_art ON public.rnp_comments USING btree (date, seller_article);


--
-- Name: idx_sales_plan_mp_year; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_sales_plan_mp_year ON public.sales_plan USING btree (marketplace, year);


--
-- Name: idx_wb_stock_daily_date; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_wb_stock_daily_date ON public.wb_stock_daily USING btree (date);


--
-- Name: idx_wb_stock_daily_wh; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_wb_stock_daily_wh ON public.wb_stock_daily USING btree (warehouse_id);


--
-- Name: idx_wb_stock_meta_date; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_wb_stock_meta_date ON public.wb_stock_meta USING btree (date);


--
-- Name: ix_catalog_l1; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_catalog_l1 ON public.catalog_items USING btree (category_l1);


--
-- Name: ix_catalog_l2; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_catalog_l2 ON public.catalog_items USING btree (category_l2);


--
-- Name: ix_catalog_l3; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_catalog_l3 ON public.catalog_items USING btree (category_l3);


--
-- Name: ix_cbp_hist_lookup; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_cbp_hist_lookup ON public.catalog_base_prices_hist USING btree (seller_article, marketplace, valid_from DESC);


--
-- Name: ix_fact_article; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_fact_article ON public.fact_weekly USING btree (seller_article);


--
-- Name: ix_fact_cat_l1; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_fact_cat_l1 ON public.fact_weekly USING btree (category_l1);


--
-- Name: ix_fact_cat_l2; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_fact_cat_l2 ON public.fact_weekly USING btree (category_l2);


--
-- Name: ix_fact_cat_l3; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_fact_cat_l3 ON public.fact_weekly USING btree (category_l3);


--
-- Name: ix_fact_mp; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_fact_mp ON public.fact_weekly USING btree (marketplace);


--
-- Name: ix_fact_rowtype; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_fact_rowtype ON public.fact_weekly USING btree (row_type);


--
-- Name: ix_fact_week; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_fact_week ON public.fact_weekly USING btree (year, week);


--
-- Name: ix_fmon_article; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_fmon_article ON public.fact_monthly USING btree (seller_article);


--
-- Name: ix_fmon_cat_l1; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_fmon_cat_l1 ON public.fact_monthly USING btree (category_l1);


--
-- Name: ix_fmon_cat_l2; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_fmon_cat_l2 ON public.fact_monthly USING btree (category_l2);


--
-- Name: ix_fmon_cat_l3; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_fmon_cat_l3 ON public.fact_monthly USING btree (category_l3);


--
-- Name: ix_fmon_mp; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_fmon_mp ON public.fact_monthly USING btree (marketplace);


--
-- Name: ix_fmon_rowtype; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_fmon_rowtype ON public.fact_monthly USING btree (row_type);


--
-- Name: ix_fmon_ym; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_fmon_ym ON public.fact_monthly USING btree (year, month);


--
-- Name: ix_item_cost_hist_art; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_item_cost_hist_art ON public.item_cost_hist USING btree (upper(seller_article), start_date);


--
-- Name: ix_mpd_lookup; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_mpd_lookup ON public.mp_price_daily USING btree (seller_article, marketplace, date DESC);


--
-- Name: ix_mpul_mp_date; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_mpul_mp_date ON public.mp_price_upload_log USING btree (marketplace, report_date DESC);


--
-- Name: ix_mpul_recent; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_mpul_recent ON public.mp_price_upload_log USING btree (loaded_at DESC);


--
-- Name: ix_ozon_daily_art; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_ozon_daily_art ON public.ozon_daily_sales USING btree (seller_article);


--
-- Name: ix_ozon_daily_date; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_ozon_daily_date ON public.ozon_daily_sales USING btree (date);


--
-- Name: ix_ozon_stock_daily_art; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_ozon_stock_daily_art ON public.ozon_stock_daily USING btree (upper(seller_article));


--
-- Name: ix_ozon_stock_daily_date; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_ozon_stock_daily_date ON public.ozon_stock_daily USING btree (date);


--
-- Name: ix_ozon_stock_daily_wh; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_ozon_stock_daily_wh ON public.ozon_stock_daily USING btree (warehouse_id);


--
-- Name: ix_ozon_stock_meta_date; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_ozon_stock_meta_date ON public.ozon_stock_meta USING btree (date);


--
-- Name: ix_wb_daily_art; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_wb_daily_art ON public.wb_daily_sales USING btree (seller_article);


--
-- Name: ix_wb_daily_date; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_wb_daily_date ON public.wb_daily_sales USING btree (date);


--
-- Name: ix_wh_incidents_dates; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_wh_incidents_dates ON public.wb_wh_incidents USING btree (wh_id, start_date, end_date);


--
-- Name: ix_wh_incidents_wh; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_wh_incidents_wh ON public.wb_wh_incidents USING btree (wh_id);


--
-- Name: ix_ya_daily_art; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_ya_daily_art ON public.ya_daily_sales USING btree (seller_article);


--
-- Name: ix_ya_daily_date; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_ya_daily_date ON public.ya_daily_sales USING btree (date);


--
-- Name: price_index_daily_date_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX price_index_daily_date_idx ON public.price_index_daily USING btree (date);


--
-- Name: uq_fact_monthly; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_fact_monthly ON public.fact_monthly USING btree (year, month, marketplace, row_type, COALESCE(seller_article, ''::text), COALESCE(sku_ozon, ''::text), COALESCE(barcode_wb, ''::text));


--
-- Name: uq_fact_weekly; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_fact_weekly ON public.fact_weekly USING btree (year, week, marketplace, row_type, COALESCE(seller_article, ''::text), COALESCE(sku_ozon, ''::text), COALESCE(barcode_wb, ''::text));


--
-- Name: ux_stock_daily; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX ux_stock_daily ON public.stock_daily USING btree (date, upper(seller_article));


--
-- Name: wb_daily_sales_date_seller_article_key; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX wb_daily_sales_date_seller_article_key ON public.wb_daily_sales USING btree (date, seller_article);


--
-- Name: catalog_base_prices_hist daily_pi_delete; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER daily_pi_delete AFTER DELETE ON public.catalog_base_prices_hist REFERENCING OLD TABLE AS old_rows FOR EACH STATEMENT EXECUTE FUNCTION public.daily_pi_changed();


--
-- Name: catalog_items daily_pi_delete; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER daily_pi_delete AFTER DELETE ON public.catalog_items REFERENCING OLD TABLE AS old_rows FOR EACH STATEMENT EXECUTE FUNCTION public.daily_pi_changed();


--
-- Name: mp_price_daily daily_pi_delete; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER daily_pi_delete AFTER DELETE ON public.mp_price_daily REFERENCING OLD TABLE AS old_rows FOR EACH STATEMENT EXECUTE FUNCTION public.daily_pi_changed();


--
-- Name: catalog_base_prices_hist daily_pi_insert; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER daily_pi_insert AFTER INSERT ON public.catalog_base_prices_hist REFERENCING NEW TABLE AS new_rows FOR EACH STATEMENT EXECUTE FUNCTION public.daily_pi_changed();


--
-- Name: catalog_items daily_pi_insert; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER daily_pi_insert AFTER INSERT ON public.catalog_items REFERENCING NEW TABLE AS new_rows FOR EACH STATEMENT EXECUTE FUNCTION public.daily_pi_changed();


--
-- Name: mp_price_daily daily_pi_insert; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER daily_pi_insert AFTER INSERT ON public.mp_price_daily REFERENCING NEW TABLE AS new_rows FOR EACH STATEMENT EXECUTE FUNCTION public.daily_pi_changed();


--
-- Name: catalog_base_prices_hist daily_pi_lock; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER daily_pi_lock BEFORE INSERT OR DELETE OR UPDATE ON public.catalog_base_prices_hist FOR EACH STATEMENT EXECUTE FUNCTION public.daily_pi_lock();


--
-- Name: catalog_items daily_pi_lock; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER daily_pi_lock BEFORE INSERT OR DELETE OR UPDATE ON public.catalog_items FOR EACH STATEMENT EXECUTE FUNCTION public.daily_pi_lock();


--
-- Name: mp_price_daily daily_pi_lock; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER daily_pi_lock BEFORE INSERT OR DELETE OR UPDATE ON public.mp_price_daily FOR EACH STATEMENT EXECUTE FUNCTION public.daily_pi_lock();


--
-- Name: catalog_base_prices_hist daily_pi_update; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER daily_pi_update AFTER UPDATE ON public.catalog_base_prices_hist REFERENCING OLD TABLE AS old_rows NEW TABLE AS new_rows FOR EACH STATEMENT EXECUTE FUNCTION public.daily_pi_changed();


--
-- Name: catalog_items daily_pi_update; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER daily_pi_update AFTER UPDATE ON public.catalog_items REFERENCING OLD TABLE AS old_rows NEW TABLE AS new_rows FOR EACH STATEMENT EXECUTE FUNCTION public.daily_pi_changed();


--
-- Name: mp_price_daily daily_pi_update; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER daily_pi_update AFTER UPDATE ON public.mp_price_daily REFERENCING OLD TABLE AS old_rows NEW TABLE AS new_rows FOR EACH STATEMENT EXECUTE FUNCTION public.daily_pi_changed();


--
-- Name: catalog_base_prices catalog_base_prices_updated_by_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.catalog_base_prices
    ADD CONSTRAINT catalog_base_prices_updated_by_user_id_fkey FOREIGN KEY (updated_by_user_id) REFERENCES public.app_users(id) ON DELETE SET NULL;


--
-- Name: category_tree category_tree_parent_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.category_tree
    ADD CONSTRAINT category_tree_parent_id_fkey FOREIGN KEY (parent_id) REFERENCES public.category_tree(id);


--
-- Name: fact_monthly fact_monthly_upload_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fact_monthly
    ADD CONSTRAINT fact_monthly_upload_id_fkey FOREIGN KEY (upload_id) REFERENCES public.report_uploads(id);


--
-- Name: fact_weekly fact_weekly_upload_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fact_weekly
    ADD CONSTRAINT fact_weekly_upload_id_fkey FOREIGN KEY (upload_id) REFERENCES public.report_uploads(id);


--
-- Name: ozon_stock_daily ozon_stock_daily_warehouse_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ozon_stock_daily
    ADD CONSTRAINT ozon_stock_daily_warehouse_id_fkey FOREIGN KEY (warehouse_id) REFERENCES public.ozon_warehouses(id);


--
-- Name: ozon_warehouses ozon_warehouses_canonical_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ozon_warehouses
    ADD CONSTRAINT ozon_warehouses_canonical_id_fkey FOREIGN KEY (canonical_id) REFERENCES public.ozon_warehouses(id);


--
-- Name: ozon_wh_incidents ozon_wh_incidents_wh_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ozon_wh_incidents
    ADD CONSTRAINT ozon_wh_incidents_wh_id_fkey FOREIGN KEY (wh_id) REFERENCES public.ozon_warehouses(id);


--
-- Name: report_uploads report_uploads_uploaded_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.report_uploads
    ADD CONSTRAINT report_uploads_uploaded_by_fkey FOREIGN KEY (uploaded_by) REFERENCES public.app_users(id);


--
-- Name: report_uploads report_uploads_uploaded_by_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.report_uploads
    ADD CONSTRAINT report_uploads_uploaded_by_user_id_fkey FOREIGN KEY (uploaded_by_user_id) REFERENCES public.app_users(id) ON DELETE SET NULL;


--
-- Name: rnp_comments rnp_comments_author_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rnp_comments
    ADD CONSTRAINT rnp_comments_author_id_fkey FOREIGN KEY (author_id) REFERENCES public.app_users(id) ON DELETE CASCADE;


--
-- Name: wb_stock_daily wb_stock_daily_warehouse_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.wb_stock_daily
    ADD CONSTRAINT wb_stock_daily_warehouse_id_fkey FOREIGN KEY (warehouse_id) REFERENCES public.wb_warehouses(id);


--
-- Name: wb_warehouses wb_warehouses_canonical_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.wb_warehouses
    ADD CONSTRAINT wb_warehouses_canonical_id_fkey FOREIGN KEY (canonical_id) REFERENCES public.wb_warehouses(id);


--
-- Name: wb_wh_incidents wb_wh_incidents_wh_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.wb_wh_incidents
    ADD CONSTRAINT wb_wh_incidents_wh_id_fkey FOREIGN KEY (wh_id) REFERENCES public.wb_warehouses(id) ON DELETE CASCADE;


--
-- PostgreSQL database dump complete
--

\unrestrict ZLBVJghGmLk21TmEOZBKgsBkPdjUMGg4KTfJ0JoaLgGdYTOYm4cr2HKl5WTMaf3

