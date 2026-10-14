-- 2026-10-11 — psychology_trade_log
--
-- A per-org, per-coordinate pool of model-written psychology-log
-- entries. Six rows per batch. One row is cited per psychology
-- article; the other five wait for the next five runs.
--
-- The numbers (date, instrument, contracts, result, hold_time,
-- exit_reason) are the model's, but constrained: same instrument
-- pool, weekday-only dates across 2026, no US market holidays.
-- The narrative is the model's. Once a row is cited
-- (used_in_article_id IS NOT NULL), it is never re-read.
--
-- This table is read ONLY by the trading_psychology subblock.
-- trade_log is untouched.

BEGIN;

CREATE TABLE IF NOT EXISTS psychology_trade_log (
    id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id             uuid NOT NULL REFERENCES orgs(id) ON DELETE CASCADE,
    coord              text NOT NULL,
    batch_id           uuid NOT NULL,
    reading_key        text NOT NULL,
    narrative          text NOT NULL,

    traded_at          timestamptz NOT NULL,
    instrument         text NOT NULL,
    direction          text NOT NULL,
    contracts          integer NOT NULL,
    entry              numeric,
    stop               numeric,
    target             numeric,
    exit               numeric,
    exit_reason        text NOT NULL,
    result             numeric NOT NULL,
    hold_time          text NOT NULL,

    source             text NOT NULL DEFAULT 'model',
    generated_at       timestamptz NOT NULL DEFAULT now(),

    used_in_article_id uuid REFERENCES trading_posts(id) ON DELETE SET NULL,
    used_at            timestamptz,

    deleted_at         timestamptz
);

CREATE INDEX IF NOT EXISTS ix_psych_trade_log_lookup
    ON psychology_trade_log (org_id, coord, deleted_at, used_in_article_id);

CREATE INDEX IF NOT EXISTS ix_psych_trade_log_batch
    ON psychology_trade_log (org_id, batch_id);

CREATE INDEX IF NOT EXISTS ix_psych_trade_log_unused
    ON psychology_trade_log (org_id, coord, traded_at DESC)
    WHERE deleted_at IS NULL AND used_in_article_id IS NULL;

ALTER TABLE psychology_trade_log ENABLE ROW LEVEL SECURITY;
ALTER TABLE psychology_trade_log FORCE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS tenant_isolation_psych_trade_log
    ON psychology_trade_log;
CREATE POLICY tenant_isolation_psych_trade_log
    ON psychology_trade_log
    USING (org_id = (current_setting('app.current_org_id', true))::uuid);

COMMIT;
