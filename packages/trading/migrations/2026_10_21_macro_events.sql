CREATE TABLE IF NOT EXISTS macro_events (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    event_date date NOT NULL,
    event_type text NOT NULL,
    description text,
    created_at timestamptz DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_macro_events_date
  ON macro_events (event_date);
COMMENT ON TABLE macro_events IS
  'FOMC, CPI, NFP, GDP, PPI, PCE — dates that invalidate model assumptions.';
