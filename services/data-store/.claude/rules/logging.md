# Logging (data-store)

data-store's CLI subcommands are invoked two ways: directly, and as a subprocess shelled out to
from Airflow `BashOperator` tasks (see `dags/*.py`). In both cases only stdout/stderr is
captured — Python's logging handlers are not.

## Use `print(..., flush=True)`, not `logger.info`

This service has no `logging.basicConfig` configured anywhere, so `logger.info(...)` is silently
dropped by Python's default logging config — confirmed via a live Airflow task run that produced
no output despite `logger.info` calls in the code. `print(..., flush=True)` is the only style
confirmed to actually surface, both in a local CLI run and in Airflow's captured task logs.

`logger.warning`/`logger.error` remain correct for genuine anomalies (already visible via
Python's default stderr handler, e.g. `pipeline._resolve_yfinance_ticker`'s unmapped-exchange
warning) — don't downgrade a real warning to `print`, and don't upgrade routine progress to
`logger.warning` just to make it visible.

If a shared `logging.basicConfig` setup is ever added across data-store's entrypoints, this rule
should be revisited.

See `pipeline.py`'s ingestion/backfill functions for the reference shape (resolved-scope line →
per-group progress → final summary; backfill functions additionally log per-ticker).

## Testing

Assert on this output with pytest's `capsys` fixture — see
`tests/unit/test_pipeline.py::test_ingest_dividend_events_logs_run_summary` and
`::test_ingest_daily_ohlcv_logs_no_price_data_distinctly` for the pattern.
