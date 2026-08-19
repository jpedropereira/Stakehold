# Logging (data-store)

## Use `logger`, not `print`

Every entry point calls `logging_config.configure_logging()`, which installs a stderr handler on
the root logger. So `logger.info` is visible — in a plain CLI run, and in Airflow's captured task
output.

```python
logger = logging.getLogger(__name__)
logger.info("ingest-ohlcv: saved %d price row(s).", saved)
```

This replaces an earlier `print(..., flush=True)` convention, which existed only because nothing
configured logging and `logger.info` was therefore silently dropped. That constraint is gone;
don't reintroduce `print` for progress.

## stdout is for results, stderr is for everything else

The one legitimate `print` is a command's actual output — the row count `cli.py` writes at the
end of a run. That goes to stdout so `data-store ingest-ohlcv` stays pipeable. Progress,
warnings and errors go to stderr through the logger.

Airflow sees both regardless: `BashOperator` runs the subprocess with `stderr=STDOUT` (verified
against the installed `SubprocessHook`), and a `@task.external_python` task captures both streams.

A side benefit worth preserving: because progress and warnings now share one stream, they are
ordered relative to each other. They previously did not — progress on stdout, warnings on
stderr — so a warning could appear in a captured log nowhere near the work it concerned.

## Levels

- `info` — progress and completion summaries. The normal narration of a run.
- `warning` — a real anomaly the run survived: a skipped ticker, a failed group, a fund that
  could not be scraped. Anything counted as `failed` in a run result should also have been
  warned about.
- `error` / `exception` — a run that could not continue.

Don't promote routine progress to `warning` to make it stand out, and don't demote a genuine
anomaly to `info` to keep a log tidy.

## What every run must still emit

Unchanged from the repo-level rule, and the reason this file exists:

- **Start**: what is about to happen and its scope.
- **Checkpoints**: one line per natural unit of work for anything slow.
- **Completion summary**: counts of what happened — processed, saved, skipped, failed.
- A run that did nothing must say so as clearly as a run that did a lot, and
  "legitimately nothing to do" must never look like "something failed silently".

Since phase 2 that last point is also carried structurally, in the `status` field of the run
result (`run_result.py`). The log line and the status must agree — if a run logs "nothing to do"
its status should be `nothing_to_do`, not `ok`.

## Testing

Assert on log output with `caplog`, and remember to set the level:

```python
def test_something(caplog):
    caplog.set_level(logging.INFO)
    ...
    assert "nothing to do" in caplog.text
```

Use `capsys` only for the stdout result line — that is, for `cli.py`'s output, not the pipeline's.
See `tests/unit/test_pipeline.py` for the `caplog` pattern and `tests/unit/test_cli.py` for the
`capsys` one.
