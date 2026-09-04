"""rebuild-returns against live Postgres: the true-upsert contract a fake repository can't prove
(a re-run overwrites the values at an already-written date rather than duplicating rows), the
bounded-window rule for a closed position, and the DIVIDEND transactions -> mappings ->
compute_returns path end to end -- see docs/architecture.md's testing section.

Seeds accounts/transactions and position_snapshots through the real CLI stack (add-transaction,
rebuild-positions) -- this test isn't about positions/repository.py's own contract, already
covered by test_rebuild_positions.py. A stub DataStoreClient, monkeypatched in for
jobs.HttpDataStoreClient, stands in for a live data-store instance -- per task 8's own scope
note, only http_client.py's own unit tests (a faked requests.get) cover the real HTTP path; this
suite exercises the real settings check, connection lifecycle, and CLI exit codes around it.
"""

import uuid
from datetime import date
from decimal import Decimal

import pytest
import yaml
from portfolio import cli, jobs
from portfolio.datastore_client.base import PricePoint, TickerInfo

pytestmark = pytest.mark.integration

_ISIN = "IE00B4L5Y983"


class StubDataStoreClient:
    def __init__(self, price_series=None, fx_series=None, ticker_info=None):
        self._price_series = price_series or {}
        self._fx_series = fx_series or {}
        self._ticker_info = ticker_info or {}

    def get_fx_rate(self, base, quote, on_date):
        return None

    def get_price_series(self, ticker, start, end):
        return [p for p in self._price_series.get(ticker, []) if start <= p.date <= end]

    def get_fx_rate_series(self, base, quote, start, end):
        return [p for p in self._fx_series.get((base, quote), []) if start <= p.date <= end]

    def get_ticker_info(self, ticker):
        return self._ticker_info.get(ticker, TickerInfo(exists=True, has_returns=True))


@pytest.fixture(autouse=True)
def _ticker_mappings(monkeypatch, tmp_path):
    """The default configs/ticker_mappings.yaml is real, gitignored broker data (task 15) and
    doesn't exist in a dev checkout -- point PORTFOLIO_TICKER_MAPPINGS_PATH at a throwaway file
    with just what this test needs, the same absolute-path override settings.py already supports.
    """
    path = tmp_path / "ticker_mappings.yaml"
    path.write_text(yaml.dump({"isin": {_ISIN: "IWDA.AS"}}))
    monkeypatch.setenv("PORTFOLIO_TICKER_MAPPINGS_PATH", str(path))
    return path


@pytest.fixture(autouse=True)
def _datastore_settings(monkeypatch):
    """run_rebuild_returns requires both set (same guard run_import_cashflow uses); the values
    themselves are never used for a real request since jobs.HttpDataStoreClient is monkeypatched
    to build the stub below instead."""
    monkeypatch.setenv("DATASTORE_API_URL", "http://stub-datastore")
    monkeypatch.setenv("INTERNAL_API_SECRET", "stub-secret")  # pragma: allowlist secret


def _add_account(user_id, *, label="m", broker="acme"):
    exit_code = cli.main(
        ["account", "add", "--user", str(user_id), "--broker", broker, "--label", label]
    )
    assert exit_code == 0


def _add_transaction(user_id, *, account_label, ref, type_, trade_date, extra):
    exit_code = cli.main(
        [
            "add-transaction",
            "--user",
            str(user_id),
            "--account",
            account_label,
            "--ref",
            ref,
            "--type",
            type_,
            "--datetime",
            f"{trade_date}T10:00:00+00:00",
            "--currency",
            "EUR",
        ]
        + extra
    )
    assert exit_code == 0


def _add_buy(user_id, *, account_label, ref, trade_date="2026-01-05", amount="-950.00"):
    _add_transaction(
        user_id,
        account_label=account_label,
        ref=ref,
        type_="BUY",
        trade_date=trade_date,
        extra=[
            "--instrument",
            _ISIN,
            "--instrument-kind",
            "ISIN",
            "--quantity",
            "10",
            "--price",
            "95.00",
            "--amount",
            amount,
        ],
    )


def _add_sell(user_id, *, account_label, ref, trade_date, amount):
    _add_transaction(
        user_id,
        account_label=account_label,
        ref=ref,
        type_="SELL",
        trade_date=trade_date,
        extra=[
            "--instrument",
            _ISIN,
            "--instrument-kind",
            "ISIN",
            "--quantity",
            "-10",
            "--price",
            "100.00",
            "--amount",
            amount,
        ],
    )


def _add_dividend(user_id, *, account_label, ref, trade_date, amount):
    _add_transaction(
        user_id,
        account_label=account_label,
        ref=ref,
        type_="DIVIDEND",
        trade_date=trade_date,
        extra=["--instrument", _ISIN, "--instrument-kind", "ISIN", "--amount", amount],
    )


def _rebuild_positions(user_id, *, account_label=None):
    args = ["rebuild-positions", "--user", str(user_id)]
    if account_label is not None:
        args += ["--account", account_label]
    assert cli.main(args) == 0


def _rebuild_returns(monkeypatch, user_id, *, account_label=None, client=None):
    monkeypatch.setattr(
        jobs,
        "HttpDataStoreClient",
        lambda base_url, internal_api_secret: client or StubDataStoreClient(),
    )
    args = ["rebuild-returns", "--user", str(user_id)]
    if account_label is not None:
        args += ["--account", account_label]
    return cli.main(args)


def test_rebuild_returns_writes_snapshots_for_an_open_position(monkeypatch, pg_connect, capsys):
    user_id = uuid.uuid4()
    _add_account(user_id)
    _add_buy(user_id, account_label="m", ref="t1")
    _rebuild_positions(user_id)
    capsys.readouterr()
    client = StubDataStoreClient(
        price_series={
            "IWDA.AS": [PricePoint(date=date(2026, 1, 6), split_adjusted_close=Decimal("100.00"))]
        }
    )

    exit_code = _rebuild_returns(monkeypatch, user_id, client=client)
    out = capsys.readouterr().out

    assert exit_code == 0
    assert out == "1 return snapshot(s) saved\n"
    with pg_connect() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT quantity, market_value_native, market_value_eur, cost_basis_eur, "
            "unrealized_return_eur, dividends_received_eur, total_return_eur "
            "FROM return_snapshots"
        )
        rows = cur.fetchall()
    assert rows == [(10, 1000, 1000, 950, 50, 0, 50)]


def test_rebuilding_returns_twice_overwrites_rather_than_duplicating(monkeypatch, pg_connect):
    """The behaviour a fake repository can't prove: a true upsert, not delete-then-insert."""
    user_id = uuid.uuid4()
    _add_account(user_id)
    _add_buy(user_id, account_label="m", ref="t1")
    _rebuild_positions(user_id)
    first_client = StubDataStoreClient(
        price_series={
            "IWDA.AS": [PricePoint(date=date(2026, 1, 6), split_adjusted_close=Decimal("100.00"))]
        }
    )
    _rebuild_returns(monkeypatch, user_id, client=first_client)

    second_client = StubDataStoreClient(
        price_series={
            "IWDA.AS": [PricePoint(date=date(2026, 1, 6), split_adjusted_close=Decimal("110.00"))]
        }
    )
    exit_code = _rebuild_returns(monkeypatch, user_id, client=second_client)

    assert exit_code == 0
    with pg_connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT COUNT(*), MAX(market_value_native) FROM return_snapshots")
        count, max_value = cur.fetchone()
    assert count == 1  # one date, one row -- overwritten in place, not a second row
    assert max_value == 1100


def test_a_closed_positions_window_stops_at_the_closing_date(monkeypatch, pg_connect):
    user_id = uuid.uuid4()
    _add_account(user_id)
    _add_buy(user_id, account_label="m", ref="t1")
    _add_sell(user_id, account_label="m", ref="t2", trade_date="2026-01-10", amount="1000.00")
    _rebuild_positions(user_id)
    client = StubDataStoreClient(
        price_series={
            "IWDA.AS": [
                PricePoint(date=date(2026, 1, 6), split_adjusted_close=Decimal("100.00")),
                # Beyond the closing date -- must never be fetched or written.
                PricePoint(date=date(2026, 1, 12), split_adjusted_close=Decimal("105.00")),
            ]
        }
    )

    exit_code = _rebuild_returns(monkeypatch, user_id, client=client)

    assert exit_code == 0
    with pg_connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT price_date FROM return_snapshots")
        rows = [row[0] for row in cur.fetchall()]
    assert rows == [date(2026, 1, 6)]


def test_an_untracked_ticker_blocks_its_rebuild_and_exits_nonzero(monkeypatch, pg_connect, capsys):
    user_id = uuid.uuid4()
    _add_account(user_id)
    _add_buy(user_id, account_label="m", ref="t1")
    _rebuild_positions(user_id)
    capsys.readouterr()
    client = StubDataStoreClient(
        ticker_info={"IWDA.AS": TickerInfo(exists=False, has_returns=False)}
    )

    exit_code = _rebuild_returns(monkeypatch, user_id, client=client)
    out = capsys.readouterr().out

    assert exit_code == 1
    assert "m\tIWDA.AS" in out
    with pg_connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM return_snapshots")
        (count,) = cur.fetchone()
    assert count == 0


def test_rebuild_returns_is_scoped_to_one_account_via_the_account_flag(monkeypatch, pg_connect):
    user_id = uuid.uuid4()
    _add_account(user_id, label="a")
    _add_account(user_id, label="b")
    _add_buy(user_id, account_label="a", ref="t1")
    _add_buy(user_id, account_label="b", ref="t2")
    _rebuild_positions(user_id)
    client = StubDataStoreClient(
        price_series={
            "IWDA.AS": [PricePoint(date=date(2026, 1, 6), split_adjusted_close=Decimal("100.00"))]
        }
    )

    exit_code = _rebuild_returns(monkeypatch, user_id, account_label="a", client=client)

    assert exit_code == 0
    with pg_connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM return_snapshots")
        (count,) = cur.fetchone()
    assert count == 1


def test_rebuild_returns_is_isolated_per_user(monkeypatch, pg_connect):
    user_a, user_b = uuid.uuid4(), uuid.uuid4()
    _add_account(user_a, label="a")
    _add_account(user_b, label="b")
    _add_buy(user_a, account_label="a", ref="t1")
    _add_buy(user_b, account_label="b", ref="t2")
    _rebuild_positions(user_a)
    _rebuild_positions(user_b)
    client = StubDataStoreClient(
        price_series={
            "IWDA.AS": [PricePoint(date=date(2026, 1, 6), split_adjusted_close=Decimal("100.00"))]
        }
    )

    exit_code = _rebuild_returns(monkeypatch, user_a, client=client)

    assert exit_code == 0
    with pg_connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT account_id FROM return_snapshots")
        rows = cur.fetchall()
        cur.execute(
            "SELECT id FROM accounts WHERE user_id = %s AND account_label = %s", (user_a, "a")
        )
        (account_a_id,) = cur.fetchone()
    assert rows == [(account_a_id,)]


def test_a_dividend_transaction_is_reflected_in_dividends_received_and_total_return(
    monkeypatch, pg_connect
):
    user_id = uuid.uuid4()
    _add_account(user_id)
    _add_buy(user_id, account_label="m", ref="t1")
    _add_dividend(user_id, account_label="m", ref="d1", trade_date="2026-01-08", amount="12.00")
    _rebuild_positions(user_id)
    client = StubDataStoreClient(
        price_series={
            "IWDA.AS": [PricePoint(date=date(2026, 1, 9), split_adjusted_close=Decimal("100.00"))]
        }
    )

    exit_code = _rebuild_returns(monkeypatch, user_id, client=client)

    assert exit_code == 0
    with pg_connect() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT unrealized_return_eur, dividends_received_eur, total_return_eur "
            "FROM return_snapshots"
        )
        rows = cur.fetchall()
    assert rows == [(50, 12, 62)]
