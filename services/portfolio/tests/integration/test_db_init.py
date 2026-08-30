"""What only a real Postgres can answer: does the DDL apply, rerun cleanly, and constrain."""

import os
import uuid

import psycopg
import pytest
from portfolio import cli

pytestmark = pytest.mark.integration

_TABLES = ("accounts", "position_snapshots", "return_snapshots", "transactions")


def _connect():
    return psycopg.connect(os.environ["PORTFOLIO_DATABASE_URL"])


@pytest.fixture(autouse=True)
def _clean_tables():
    cli.main(["db", "init"])
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "TRUNCATE return_snapshots, position_snapshots, transactions, accounts CASCADE"
            )
        conn.commit()
    yield


def _insert_account(cur, *, user_id, label="degiro-main", broker="degiro"):
    cur.execute(
        "INSERT INTO accounts (user_id, broker, account_label) VALUES (%s, %s, %s) RETURNING id",
        (user_id, broker, label),
    )
    return cur.fetchone()[0]


def _insert_transaction(cur, *, account_id, ref, **overrides):
    values = {
        "transaction_type": "BUY",
        "trade_datetime": "2026-01-15T10:30:00+00:00",
        "instrument_identifier": "IE00B4L5Y983",
        "instrument_identifier_kind": "ISIN",
        "quantity": "10.000000",
        "price": "95.500000",
        "native_currency": "EUR",
        "native_amount": "-955.0000",
        "eur_amount": "-955.0000",
    }
    values.update(overrides)
    columns = ", ".join(["account_id", "broker_transaction_ref", *values])
    placeholders = ", ".join(["%s"] * (2 + len(values)))
    cur.execute(
        f"INSERT INTO transactions ({columns}) VALUES ({placeholders})",
        (account_id, ref, *values.values()),
    )


def test_db_init_creates_expected_tables():
    exit_code = cli.main(["db", "init"])

    assert exit_code == 0

    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = 'public' AND table_name = ANY(%s) ORDER BY table_name",
                (list(_TABLES),),
            )
            tables = [row[0] for row in cur.fetchall()]

    assert tables == list(_TABLES)


def test_db_init_is_idempotent_and_reports_nothing_to_do(capsys):
    cli.main(["db", "init"])
    capsys.readouterr()

    exit_code = cli.main(["db", "init"])

    assert exit_code == 0
    assert capsys.readouterr().out == "0 table(s) created\n"


def test_account_label_is_unique_per_user_not_globally():
    user_a, user_b = uuid.uuid4(), uuid.uuid4()

    with _connect() as conn:
        with conn.cursor() as cur:
            _insert_account(cur, user_id=user_a, label="main")
            # Same label, different owner: allowed -- the label is the owner's own handle.
            _insert_account(cur, user_id=user_b, label="main")
        conn.commit()

        with pytest.raises(psycopg.errors.UniqueViolation):
            with conn.cursor() as cur:
                _insert_account(cur, user_id=user_a, label="main")
            conn.commit()


def test_reimporting_the_same_broker_ref_is_a_no_op():
    """The idempotent-import contract: ON CONFLICT DO NOTHING, keyed by account, not broker."""
    with _connect() as conn:
        with conn.cursor() as cur:
            account_id = _insert_account(cur, user_id=uuid.uuid4())
            _insert_transaction(cur, account_id=account_id, ref="order-1")
            cur.execute(
                "INSERT INTO transactions "
                "(account_id, broker_transaction_ref, transaction_type, trade_datetime, "
                " native_currency, native_amount, eur_amount) "
                "VALUES (%s, %s, 'BUY', now(), 'EUR', -1.0, -1.0) "
                "ON CONFLICT (account_id, broker_transaction_ref) DO NOTHING",
                (account_id, "order-1"),
            )
        conn.commit()

        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM transactions WHERE account_id = %s", (account_id,))
            assert cur.fetchone()[0] == 1


def test_the_same_broker_ref_in_two_accounts_is_two_transactions():
    """Why the key is account-scoped: IBKR's content hash can collide across accounts."""
    with _connect() as conn:
        with conn.cursor() as cur:
            user_id = uuid.uuid4()
            account_a = _insert_account(cur, user_id=user_id, label="ibkr-a", broker="ibkr")
            account_b = _insert_account(cur, user_id=user_id, label="ibkr-b", broker="ibkr")
            _insert_transaction(cur, account_id=account_a, ref="same-hash")
            _insert_transaction(cur, account_id=account_b, ref="same-hash")
        conn.commit()

        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM transactions")
            assert cur.fetchone()[0] == 2


def test_a_transaction_cannot_reference_a_missing_account():
    with _connect() as conn:
        with pytest.raises(psycopg.errors.ForeignKeyViolation):
            with conn.cursor() as cur:
                _insert_transaction(cur, account_id=999_999, ref="orphan")
            conn.commit()


def test_cash_only_transactions_may_omit_instrument_and_quantity():
    with _connect() as conn:
        with conn.cursor() as cur:
            account_id = _insert_account(cur, user_id=uuid.uuid4())
            _insert_transaction(
                cur,
                account_id=account_id,
                ref="deposit-1",
                transaction_type="DEPOSIT",
                instrument_identifier=None,
                instrument_identifier_kind=None,
                quantity=None,
                price=None,
                native_amount="1000.0000",
                eur_amount="1000.0000",
            )
        conn.commit()

        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM transactions WHERE account_id = %s", (account_id,))
            assert cur.fetchone()[0] == 1


def test_position_snapshot_key_rejects_a_duplicate_date():
    with _connect() as conn:
        with conn.cursor() as cur:
            account_id = _insert_account(cur, user_id=uuid.uuid4())
            cur.execute(
                "INSERT INTO position_snapshots (account_id, canonical_ticker, as_of_date, "
                "quantity, average_cost_native, native_currency, average_cost_eur, "
                "cost_basis_eur) VALUES (%s, 'IWDA.AS', '2026-01-15', 10, 95.5, 'EUR', 95.5, 955)",
                (account_id,),
            )
        conn.commit()

        with pytest.raises(psycopg.errors.UniqueViolation):
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO position_snapshots (account_id, canonical_ticker, as_of_date, "
                    "quantity, average_cost_native, native_currency, average_cost_eur, "
                    "cost_basis_eur) VALUES (%s, 'IWDA.AS', '2026-01-15', 20, 96, 'EUR', 96, 1920)",
                    (account_id,),
                )
            conn.commit()


def test_return_snapshot_percentage_is_nullable_when_cost_basis_is_zero():
    """A position sold down to zero gets a row with an undefined -- not zero -- return."""
    with _connect() as conn:
        with conn.cursor() as cur:
            account_id = _insert_account(cur, user_id=uuid.uuid4())
            cur.execute(
                "INSERT INTO return_snapshots (account_id, canonical_ticker, price_date, "
                "quantity, market_value_native, market_value_eur, cost_basis_eur, "
                "unrealized_return_eur, unrealized_return_pct) "
                "VALUES (%s, 'IWDA.AS', '2026-01-15', 0, 0, 0, 0, 0, NULL)",
                (account_id,),
            )
        conn.commit()

        with conn.cursor() as cur:
            cur.execute(
                "SELECT unrealized_return_pct FROM return_snapshots WHERE account_id = %s",
                (account_id,),
            )
            assert cur.fetchone()[0] is None
