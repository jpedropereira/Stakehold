from data_store.provider_retry import with_retries


def make_flaky(*, fail_times, error, result="ok"):
    calls = []

    def fn():
        calls.append(None)
        if len(calls) <= fail_times:
            raise error
        return result

    return fn, calls


def test_succeeds_on_first_attempt():
    fn, calls = make_flaky(fail_times=0, error=ValueError("unused"))

    result = with_retries(
        fn, description="x", log_prefix="test", max_retries=3, backoff_base_seconds=0.0
    )

    assert result == "ok"
    assert len(calls) == 1


def test_retries_a_transient_error_then_succeeds():
    fn, calls = make_flaky(fail_times=1, error=TimeoutError("timed out"))

    result = with_retries(
        fn, description="x", log_prefix="test", max_retries=3, backoff_base_seconds=0.0
    )

    assert result == "ok"
    assert len(calls) == 2


def test_gives_up_after_max_retries_and_returns_none(caplog):
    caplog.set_level("WARNING")
    fn, calls = make_flaky(fail_times=10, error=TimeoutError("timed out"))

    result = with_retries(
        fn, description="x", log_prefix="test", max_retries=3, backoff_base_seconds=0.0
    )

    assert result is None
    assert len(calls) == 3
    assert "failed after 3 attempt(s)" in caplog.text


def test_non_transient_error_is_not_retried(caplog):
    caplog.set_level("WARNING")
    fn, calls = make_flaky(fail_times=10, error=ValueError("No data exists for series id: X"))

    result = with_retries(
        fn, description="x", log_prefix="test", max_retries=3, backoff_base_seconds=0.0
    )

    assert result is None
    assert len(calls) == 1
