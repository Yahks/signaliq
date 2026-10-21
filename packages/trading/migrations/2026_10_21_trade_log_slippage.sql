-- Add entry and exit slippage columns to trade_log.
-- entry_slippage = (actual_entry - intended_entry) * sign
-- exit_slippage  = (actual_exit - intended_exit) * sign
-- sign = +1 for long, -1 for short
-- Positive slippage = cost. Negative slippage = benefit.
ALTER TABLE trade_log
  ADD COLUMN IF NOT EXISTS entry_slippage numeric,
  ADD COLUMN IF NOT EXISTS exit_slippage numeric;

COMMENT ON COLUMN trade_log.entry_slippage IS
  'Actual fill - intended fill, signed by direction. Positive = cost.';
COMMENT ON COLUMN trade_log.exit_slippage IS
  'Actual exit fill - intended exit fill, signed by direction. Positive = cost.';
