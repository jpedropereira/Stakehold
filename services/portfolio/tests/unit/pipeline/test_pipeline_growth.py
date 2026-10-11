"""pipeline.list_growth against the fake repositories in conftest.py: which transactions become
money flows, how each resolves to a ticker, and that the account is resolved for this user.
The growth arithmetic itself is tests/unit/returns/test_performance.py's job.
"""

import uuid
from datetime import UTC, date, datetime, timedelta, timezone
from decimal import Decimal

import pytest
from portfolio import pipeline
from portfolio.errors import AccountNotFound
from portfolio.returns.calculator import ReturnSnapshot
from portfolio.returns.performance import GrowthPoint
from portfolio.ticker_mapping.base import TickerMappings
from portfolio.transactions.base import CanonicalTransaction

_ISIN = "IE00B4L5Y983"
D1 = date(2026, 1, 5)
D2 = date(2026, 1, 6)
_NO_MAPPINGS = TickerMappings(isin={}, symbol={})


def _seed_account(accounts_repo, *, user_id, label="m", broker="acme"):
    accounts_repo.insert_account(None, user_id=user_id, broker=broker, account_label=label)
    return accounts_repo.get_by_label(None, user_id=user_id, account_label=label)


def _txn(account_id, *, ref, on=D1, amount="-1000", **overrides):
    kwargs = dict(
        account_id=account_id,
        broker_transaction_ref=ref,
        transaction_type="BUY",
        trade_datetime=datetime(on.year, on.month, on.day, 10, 0, tzinfo=UTC),
        instrument_identifier="IWDA.AS",
        instrument_identifier_kind="TICKER",
        quantity=Decimal("10"),
        price=Decimal("100.00"),
        native_currency="EUR",
        native_amount=Decimal(amount),
        eur_amount=Decimal(amount),
        fx_rate=None,
        fx_rate_source=None,
        fees_native=None,
    )
    kwargs.update(overrides)
    return CanonicalTransaction(**kwargs)


def _row(account_id, price_date, market_value_eur, *, ticker="IWDA.AS", dividends="0"):
    mv = Decimal(market_value_eur)
    return ReturnSnapshot(
        account_id=account_id,
        canonical_ticker=ticker,
        price_date=price_date,
        quantity=Decimal("10.000000"),
        market_value_native=mv,
        market_value_eur=mv,
        cost_basis_eur=Decimal("1000.0000"),
        unrealized_return_eur=mv - Decimal("1000.0000"),
        unrealized_return_pct=None,
        dividends_received_eur=Decimal(dividends),
        total_return_eur=mv - Decimal("1000.0000") + Decimal(dividends),
        total_return_pct=None,
    )


def _seed_rows(returns_repo, rows):
    for row in rows:
        returns_repo.snapshots.setdefault((row.account_id, row.canonical_ticker), {})[
            row.price_date
        ] = row


def _growth(points):
    return [(p.price_date, p.growth_pct) for p in points]


def _pct(value):
    return Decimal(value).quantize(Decimal("0.00000001"))


def test_a_manual_buy_grows_with_its_tickers_value(repos, returns_repo, conn):
    accounts_repo, transactions_repo, _ = repos
    user_id = uuid.uuid4()
    account = _seed_account(accounts_repo, user_id=user_id)
    transactions_repo.saved.append(_txn(account.id, ref="buy"))
    _seed_rows(returns_repo, [_row(account.id, D1, "1000"), _row(account.id, D2, "1100")])

    points = pipeline.list_growth(conn, user_id=user_id, account_label="m", mappings=_NO_MAPPINGS)

    assert points == [
        GrowthPoint(price_date=D1, growth_pct=_pct("0")),
        GrowthPoint(price_date=D2, growth_pct=_pct("0.1")),
    ]


def test_an_imported_trade_resolves_through_the_ticker_mappings(repos, returns_repo, conn):
    accounts_repo, transactions_repo, _ = repos
    user_id = uuid.uuid4()
    account = _seed_account(accounts_repo, user_id=user_id)
    transactions_repo.saved.append(
        _txn(account.id, ref="buy", instrument_identifier=_ISIN, instrument_identifier_kind="ISIN")
    )
    _seed_rows(returns_repo, [_row(account.id, D1, "1000"), _row(account.id, D2, "1100")])
    mappings = TickerMappings(isin={_ISIN: "IWDA.AS"}, symbol={})

    points = pipeline.list_growth(conn, user_id=user_id, account_label="m", mappings=mappings)

    assert _growth(points) == [(D1, _pct("0")), (D2, _pct("0.1"))]


def test_a_symbol_resolves_through_the_accounts_own_broker(repos, returns_repo, conn):
    accounts_repo, transactions_repo, _ = repos
    user_id = uuid.uuid4()
    account = _seed_account(accounts_repo, user_id=user_id, broker="globex")
    transactions_repo.saved.append(
        _txn(
            account.id, ref="buy", instrument_identifier="IWDA", instrument_identifier_kind="SYMBOL"
        )
    )
    _seed_rows(returns_repo, [_row(account.id, D1, "1000"), _row(account.id, D2, "1100")])
    # The same symbol at another broker names something else entirely.
    mappings = TickerMappings(
        isin={}, symbol={"globex": {"IWDA": "IWDA.AS"}, "acme": {"IWDA": "OTHER.AS"}}
    )

    points = pipeline.list_growth(conn, user_id=user_id, account_label="m", mappings=mappings)

    assert _growth(points) == [(D1, _pct("0")), (D2, _pct("0.1"))]


def test_a_sell_counts_as_money_out(repos, returns_repo, conn):
    accounts_repo, transactions_repo, _ = repos
    user_id = uuid.uuid4()
    account = _seed_account(accounts_repo, user_id=user_id)
    transactions_repo.saved.extend(
        [
            _txn(account.id, ref="buy"),
            _txn(account.id, ref="sell", on=D2, transaction_type="SELL", amount="1100"),
        ]
    )
    _seed_rows(returns_repo, [_row(account.id, D1, "1000"), _row(account.id, D2, "0")])

    points = pipeline.list_growth(conn, user_id=user_id, account_label="m", mappings=_NO_MAPPINGS)

    assert _growth(points) == [(D1, _pct("0")), (D2, _pct("0.1"))]


def test_cash_and_dividend_transactions_are_not_money_flows(repos, returns_repo, conn):
    # A dividend already reaches growth through the rows' dividends_received_eur; counting its
    # transaction too would count it twice. Deposits move cash, which isn't modelled.
    accounts_repo, transactions_repo, _ = repos
    user_id = uuid.uuid4()
    account = _seed_account(accounts_repo, user_id=user_id)
    transactions_repo.saved.extend(
        [
            _txn(account.id, ref="buy"),
            _txn(account.id, ref="div", on=D2, transaction_type="DIVIDEND", amount="20"),
            _txn(
                account.id,
                ref="dep",
                on=D2,
                transaction_type="DEPOSIT",
                amount="5000",
                instrument_identifier=None,
                instrument_identifier_kind=None,
                quantity=None,
                price=None,
            ),
        ]
    )
    _seed_rows(
        returns_repo,
        [_row(account.id, D1, "1000"), _row(account.id, D2, "1000", dividends="20")],
    )

    points = pipeline.list_growth(conn, user_id=user_id, account_label="m", mappings=_NO_MAPPINGS)

    assert _growth(points) == [(D1, _pct("0")), (D2, _pct("0.02"))]


def test_an_unmapped_trade_is_left_out(repos, returns_repo, conn):
    accounts_repo, transactions_repo, _ = repos
    user_id = uuid.uuid4()
    account = _seed_account(accounts_repo, user_id=user_id)
    transactions_repo.saved.extend(
        [
            _txn(account.id, ref="buy"),
            _txn(
                account.id,
                ref="unmapped",
                instrument_identifier=_ISIN,
                instrument_identifier_kind="ISIN",
            ),
        ]
    )
    _seed_rows(returns_repo, [_row(account.id, D1, "1000"), _row(account.id, D2, "1100")])

    points = pipeline.list_growth(conn, user_id=user_id, account_label="m", mappings=_NO_MAPPINGS)

    assert _growth(points) == [(D1, _pct("0")), (D2, _pct("0.1"))]


def test_a_trade_lands_on_its_utc_date(repos, returns_repo, conn):
    # 23:30 on D1 at UTC-2 is 01:30 on D2 in UTC, so the second buy is D2's money, which is
    # when the row's value first includes it.
    accounts_repo, transactions_repo, _ = repos
    user_id = uuid.uuid4()
    account = _seed_account(accounts_repo, user_id=user_id)
    late_evening = datetime(2026, 1, 5, 23, 30, tzinfo=timezone(timedelta(hours=-2)))
    transactions_repo.saved.extend(
        [
            _txn(account.id, ref="buy-1"),
            _txn(account.id, ref="buy-2", trade_datetime=late_evening),
        ]
    )
    _seed_rows(returns_repo, [_row(account.id, D1, "1000"), _row(account.id, D2, "2200")])

    points = pipeline.list_growth(conn, user_id=user_id, account_label="m", mappings=_NO_MAPPINGS)

    assert _growth(points) == [(D1, _pct("0")), (D2, _pct("0.1"))]


def test_another_accounts_trades_and_rows_are_not_used(repos, returns_repo, conn):
    accounts_repo, transactions_repo, _ = repos
    user_id = uuid.uuid4()
    account = _seed_account(accounts_repo, user_id=user_id)
    other = _seed_account(accounts_repo, user_id=user_id, label="other")
    transactions_repo.saved.extend(
        [_txn(account.id, ref="buy"), _txn(other.id, ref="other-buy", on=D2, amount="-9000")]
    )
    _seed_rows(
        returns_repo,
        [
            _row(account.id, D1, "1000"),
            _row(account.id, D2, "1100"),
            _row(other.id, D2, "9000"),
        ],
    )

    points = pipeline.list_growth(conn, user_id=user_id, account_label="m", mappings=_NO_MAPPINGS)

    assert _growth(points) == [(D1, _pct("0")), (D2, _pct("0.1"))]


def test_an_account_with_no_return_rows_has_no_points(repos, returns_repo, conn):
    accounts_repo, transactions_repo, _ = repos
    user_id = uuid.uuid4()
    account = _seed_account(accounts_repo, user_id=user_id)
    transactions_repo.saved.append(_txn(account.id, ref="buy"))

    points = pipeline.list_growth(conn, user_id=user_id, account_label="m", mappings=_NO_MAPPINGS)

    assert points == []


def test_an_unknown_account_label_raises(repos, returns_repo, conn):
    with pytest.raises(AccountNotFound):
        pipeline.list_growth(
            conn, user_id=uuid.uuid4(), account_label="nope", mappings=_NO_MAPPINGS
        )


def test_another_users_account_is_not_found(repos, returns_repo, conn):
    accounts_repo, _, _ = repos
    _seed_account(accounts_repo, user_id=uuid.uuid4())

    with pytest.raises(AccountNotFound):
        pipeline.list_growth(conn, user_id=uuid.uuid4(), account_label="m", mappings=_NO_MAPPINGS)
