"""CLI tests: that argv maps to the right job call, and a result maps to the right exit code."""

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from portfolio import cli


def test_db_init_dispatches_to_the_db_init_job(monkeypatch):
    called = {}

    def fake_run_db_init():
        called["run_db_init"] = True
        return {"status": "ok", "rows_saved": 4}

    monkeypatch.setattr(cli.jobs, "run_db_init", fake_run_db_init)

    assert cli.main(["db", "init"]) == 0
    assert called == {"run_db_init": True}


def test_db_init_prints_the_table_count_to_stdout(monkeypatch, capsys):
    monkeypatch.setattr(cli.jobs, "run_db_init", lambda: {"status": "ok", "rows_saved": 4})

    cli.main(["db", "init"])

    assert capsys.readouterr().out == "4 table(s) created\n"


def test_db_init_exits_zero_when_nothing_to_do(monkeypatch):
    monkeypatch.setattr(
        cli.jobs, "run_db_init", lambda: {"status": "nothing_to_do", "rows_saved": 0}
    )

    assert cli.main(["db", "init"]) == 0


def test_db_init_exits_nonzero_when_partial(monkeypatch):
    monkeypatch.setattr(cli.jobs, "run_db_init", lambda: {"status": "partial", "rows_saved": 2})

    assert cli.main(["db", "init"]) == 1


def test_missing_command_exits_nonzero():
    with pytest.raises(SystemExit) as exc_info:
        cli.main([])

    assert exc_info.value.code != 0


def test_missing_db_subcommand_exits_nonzero():
    with pytest.raises(SystemExit) as exc_info:
        cli.main(["db"])

    assert exc_info.value.code != 0


def test_a_settings_error_prints_a_clean_message_and_exits_nonzero(monkeypatch, capsys):
    """Missing/invalid configuration (e.g. no PORTFOLIO_DATABASE_URL set) is an operator mistake,
    not a domain failure -- it must not surface as a raw traceback."""

    def boom():
        raise cli.SettingsError("PORTFOLIO_DATABASE_URL is not set")

    monkeypatch.setattr(cli.jobs, "run_db_init", boom)

    assert cli.main(["db", "init"]) == 1
    assert "PORTFOLIO_DATABASE_URL is not set" in capsys.readouterr().err


def test_a_domain_error_prints_a_clean_message_and_exits_nonzero(monkeypatch, capsys):
    """jobs.py deliberately lets a PortfolioError (e.g. an unknown account label) propagate
    rather than swallowing it into a partial result -- main() is what maps it to a one-line
    stderr message and a nonzero exit, not a raw traceback."""

    def boom(**kw):
        raise cli.PortfolioError("account not found: 'nope'")

    monkeypatch.setattr(cli.jobs, "run_add_account", boom)

    assert (
        cli.main(["account", "add", "--user", str(uuid.uuid4()), "--broker", "x", "--label", "m"])
        == 1
    )
    assert "account not found: 'nope'" in capsys.readouterr().err


# --- account add ---


def test_account_add_dispatches_with_converted_types(monkeypatch):
    captured = {}

    def fake_run_add_account(*, user_id, broker, account_label):
        captured.update(user_id=user_id, broker=broker, account_label=account_label)
        return {"status": "ok", "rows_saved": 1, "accounts_created": 1}

    monkeypatch.setattr(cli.jobs, "run_add_account", fake_run_add_account)
    user_id = uuid.uuid4()

    exit_code = cli.main(
        ["account", "add", "--user", str(user_id), "--broker", "acme", "--label", "m"]
    )

    assert exit_code == 0
    assert captured == {"user_id": user_id, "broker": "acme", "account_label": "m"}
    assert isinstance(captured["user_id"], uuid.UUID)


def test_account_add_prints_the_created_count(monkeypatch, capsys):
    monkeypatch.setattr(
        cli.jobs,
        "run_add_account",
        lambda **kw: {"status": "ok", "rows_saved": 1, "accounts_created": 1},
    )

    cli.main(["account", "add", "--user", str(uuid.uuid4()), "--broker", "acme", "--label", "m"])

    assert capsys.readouterr().out == "1 account(s) created\n"


def test_account_add_exits_zero_when_nothing_to_do(monkeypatch):
    monkeypatch.setattr(
        cli.jobs,
        "run_add_account",
        lambda **kw: {"status": "nothing_to_do", "rows_saved": 0, "accounts_created": 0},
    )

    exit_code = cli.main(
        ["account", "add", "--user", str(uuid.uuid4()), "--broker", "acme", "--label", "m"]
    )

    assert exit_code == 0


def test_account_add_rejects_a_malformed_user_id():
    with pytest.raises(SystemExit):
        cli.main(["account", "add", "--user", "not-a-uuid", "--broker", "acme", "--label", "m"])


def test_account_add_requires_all_flags():
    with pytest.raises(SystemExit):
        cli.main(["account", "add", "--user", str(uuid.uuid4()), "--broker", "acme"])


# --- account list ---


def test_account_list_dispatches_with_converted_user_id(monkeypatch):
    captured = {}

    def fake_run_list_accounts(*, user_id):
        captured["user_id"] = user_id
        return {"status": "nothing_to_do", "rows_saved": 0, "accounts_listed": 0, "accounts": []}

    monkeypatch.setattr(cli.jobs, "run_list_accounts", fake_run_list_accounts)
    user_id = uuid.uuid4()

    cli.main(["account", "list", "--user", str(user_id)])

    assert captured == {"user_id": user_id}


def test_account_list_prints_one_line_per_account_then_the_count(monkeypatch, capsys):
    monkeypatch.setattr(
        cli.jobs,
        "run_list_accounts",
        lambda **kw: {
            "status": "ok",
            "rows_saved": 0,
            "accounts_listed": 2,
            "accounts": [
                {
                    "id": 1,
                    "account_label": "acme-main",
                    "broker": "acme",
                    "created_at": "2026-01-01T00:00:00+00:00",
                },
                {
                    "id": 2,
                    "account_label": "globex-main",
                    "broker": "globex",
                    "created_at": "2026-01-02T00:00:00+00:00",
                },
            ],
        },
    )

    cli.main(["account", "list", "--user", str(uuid.uuid4())])

    out = capsys.readouterr().out
    assert out == (
        "1\tacme-main\tacme\t2026-01-01\n" "2\tglobex-main\tglobex\t2026-01-02\n" "2 account(s)\n"
    )


def test_account_list_prints_only_the_count_when_empty(monkeypatch, capsys):
    monkeypatch.setattr(
        cli.jobs,
        "run_list_accounts",
        lambda **kw: {
            "status": "nothing_to_do",
            "rows_saved": 0,
            "accounts_listed": 0,
            "accounts": [],
        },
    )

    cli.main(["account", "list", "--user", str(uuid.uuid4())])

    assert capsys.readouterr().out == "0 account(s)\n"


def test_account_list_exits_zero_when_empty(monkeypatch):
    monkeypatch.setattr(
        cli.jobs,
        "run_list_accounts",
        lambda **kw: {
            "status": "nothing_to_do",
            "rows_saved": 0,
            "accounts_listed": 0,
            "accounts": [],
        },
    )

    assert cli.main(["account", "list", "--user", str(uuid.uuid4())]) == 0


def test_missing_account_subcommand_exits_nonzero():
    with pytest.raises(SystemExit) as exc_info:
        cli.main(["account"])

    assert exc_info.value.code != 0


# --- broker list ---


def test_broker_list_prints_one_line_per_broker_then_the_count(monkeypatch, capsys):
    monkeypatch.setattr(
        cli.jobs,
        "run_list_available_brokers",
        lambda: {
            "status": "ok",
            "rows_saved": 0,
            "brokers_available": 2,
            "brokers": ["acme", "globex"],
        },
    )

    cli.main(["broker", "list"])

    assert capsys.readouterr().out == "acme\nglobex\n2 broker parser(s) available\n"


def test_broker_list_prints_only_the_count_when_empty(monkeypatch, capsys):
    monkeypatch.setattr(
        cli.jobs,
        "run_list_available_brokers",
        lambda: {"status": "nothing_to_do", "rows_saved": 0, "brokers_available": 0, "brokers": []},
    )

    cli.main(["broker", "list"])

    assert capsys.readouterr().out == "0 broker parser(s) available\n"


def test_broker_list_exits_zero_when_empty(monkeypatch):
    monkeypatch.setattr(
        cli.jobs,
        "run_list_available_brokers",
        lambda: {"status": "nothing_to_do", "rows_saved": 0, "brokers_available": 0, "brokers": []},
    )

    assert cli.main(["broker", "list"]) == 0


def test_missing_broker_subcommand_exits_nonzero():
    with pytest.raises(SystemExit) as exc_info:
        cli.main(["broker"])

    assert exc_info.value.code != 0


# --- add-transaction ---

_BASE_TXN_ARGS = [
    "add-transaction",
    "--account",
    "acme-main",
    "--ref",
    "manual-1",
    "--type",
    "BUY",
    "--datetime",
    "2026-03-01T10:00:00+00:00",
    "--instrument",
    "IE00B4L5Y983",
    "--instrument-kind",
    "ISIN",
    "--quantity",
    "10",
    "--price",
    "95.50",
    "--currency",
    "EUR",
    "--amount",
    "-955.00",
]


def test_add_transaction_dispatches_with_converted_types(monkeypatch):
    captured = {}

    def fake_run_add_transaction(**kw):
        captured.update(kw)
        return {"status": "ok", "rows_saved": 1, "transactions_saved": 1}

    monkeypatch.setattr(cli.jobs, "run_add_transaction", fake_run_add_transaction)
    user_id = uuid.uuid4()

    exit_code = cli.main(["add-transaction", "--user", str(user_id), *_BASE_TXN_ARGS[1:]])

    assert exit_code == 0
    assert captured["user_id"] == user_id
    assert isinstance(captured["user_id"], uuid.UUID)
    assert captured["account_label"] == "acme-main"
    assert captured["broker_transaction_ref"] == "manual-1"
    assert captured["transaction_type"] == "BUY"
    assert captured["trade_datetime"] == datetime(2026, 3, 1, 10, 0, tzinfo=UTC)
    assert captured["instrument_identifier"] == "IE00B4L5Y983"
    assert captured["instrument_identifier_kind"] == "ISIN"
    assert captured["quantity"] == Decimal("10")
    assert isinstance(captured["quantity"], Decimal)
    assert captured["price"] == Decimal("95.50")
    assert captured["native_currency"] == "EUR"
    assert captured["native_amount"] == Decimal("-955.00")
    assert isinstance(captured["native_amount"], Decimal)


def test_add_transaction_defaults_optional_fields_to_none(monkeypatch):
    captured = {}

    def fake_run_add_transaction(**kw):
        captured.update(kw)
        return {"status": "ok", "rows_saved": 1, "transactions_saved": 1}

    monkeypatch.setattr(cli.jobs, "run_add_transaction", fake_run_add_transaction)

    cli.main(
        [
            "add-transaction",
            "--user",
            str(uuid.uuid4()),
            "--account",
            "acme-main",
            "--ref",
            "deposit-1",
            "--type",
            "DEPOSIT",
            "--datetime",
            "2026-03-01T10:00:00+00:00",
            "--currency",
            "EUR",
            "--amount",
            "1000.00",
        ]
    )

    assert captured["instrument_identifier"] is None
    assert captured["instrument_identifier_kind"] is None
    assert captured["quantity"] is None
    assert captured["price"] is None
    assert captured["fx_rate"] is None
    assert captured["fees_native"] is None


def test_add_transaction_passes_through_fx_rate_and_fees(monkeypatch):
    captured = {}

    def fake_run_add_transaction(**kw):
        captured.update(kw)
        return {"status": "ok", "rows_saved": 1, "transactions_saved": 1}

    monkeypatch.setattr(cli.jobs, "run_add_transaction", fake_run_add_transaction)

    cli.main(
        [
            "add-transaction",
            "--user",
            str(uuid.uuid4()),
            "--account",
            "globex-main",
            "--ref",
            "manual-2",
            "--type",
            "BUY",
            "--datetime",
            "2026-03-03T14:00:00+00:00",
            "--instrument",
            "AAPL",
            "--instrument-kind",
            "SYMBOL",
            "--quantity",
            "5",
            "--price",
            "200",
            "--currency",
            "USD",
            "--amount",
            "-1000.00",
            "--fx-rate",
            "1.0850",
            "--fees",
            "1.50",
        ]
    )

    assert captured["fx_rate"] == Decimal("1.0850")
    assert captured["fees_native"] == Decimal("1.50")


def test_add_transaction_prints_the_saved_count(monkeypatch, capsys):
    monkeypatch.setattr(
        cli.jobs,
        "run_add_transaction",
        lambda **kw: {"status": "ok", "rows_saved": 1, "transactions_saved": 1},
    )

    cli.main(["add-transaction", "--user", str(uuid.uuid4()), *_BASE_TXN_ARGS[1:]])

    assert capsys.readouterr().out == "1 transaction(s) saved\n"


def test_add_transaction_exits_zero_when_nothing_to_do(monkeypatch):
    monkeypatch.setattr(
        cli.jobs,
        "run_add_transaction",
        lambda **kw: {"status": "nothing_to_do", "rows_saved": 0, "transactions_saved": 0},
    )

    exit_code = cli.main(["add-transaction", "--user", str(uuid.uuid4()), *_BASE_TXN_ARGS[1:]])

    assert exit_code == 0


def test_add_transaction_propagates_a_domain_error_and_exits_nonzero(monkeypatch, capsys):
    """run_add_transaction can no longer return `partial` itself -- jobs.py lets a PortfolioError
    (e.g. an unknown account label) propagate instead of catching it, so this is what main()'s
    generic domain-error handling actually has to cover for this command."""

    def boom(**kw):
        raise cli.PortfolioError("account not found: 'm'")

    monkeypatch.setattr(cli.jobs, "run_add_transaction", boom)

    exit_code = cli.main(["add-transaction", "--user", str(uuid.uuid4()), *_BASE_TXN_ARGS[1:]])

    assert exit_code == 1
    assert "account not found: 'm'" in capsys.readouterr().err


def test_add_transaction_rejects_a_malformed_datetime():
    args = ["--user", str(uuid.uuid4()), *_BASE_TXN_ARGS[1:]]
    args[args.index("2026-03-01T10:00:00+00:00")] = "not-a-date"

    with pytest.raises(SystemExit):
        cli.main(["add-transaction", *args])


def test_add_transaction_rejects_a_naive_datetime():
    args = ["--user", str(uuid.uuid4()), *_BASE_TXN_ARGS[1:]]
    args[args.index("2026-03-01T10:00:00+00:00")] = "2026-03-01T10:00:00"

    with pytest.raises(SystemExit):
        cli.main(["add-transaction", *args])


def test_add_transaction_rejects_a_malformed_amount():
    args = ["--user", str(uuid.uuid4()), *_BASE_TXN_ARGS[1:]]
    args[args.index("-955.00")] = "not-a-number"

    with pytest.raises(SystemExit):
        cli.main(["add-transaction", *args])


def test_add_transaction_requires_all_mandatory_flags():
    with pytest.raises(SystemExit):
        cli.main(["add-transaction", "--user", str(uuid.uuid4())])


def test_add_transaction_rejects_an_unrecognized_type():
    args = ["--user", str(uuid.uuid4()), *_BASE_TXN_ARGS[1:]]
    args[args.index("BUY")] = "TRANSFER"

    with pytest.raises(SystemExit):
        cli.main(["add-transaction", *args])


# --- import-transactions ---


def test_import_transactions_dispatches_with_converted_types(monkeypatch):
    captured = {}

    def fake_run_import_transactions(**kw):
        captured.update(kw)
        return {"status": "ok", "rows_saved": 82, "transactions_saved": 82}

    monkeypatch.setattr(cli.jobs, "run_import_transactions", fake_run_import_transactions)
    user_id = uuid.uuid4()

    exit_code = cli.main(
        [
            "import-transactions",
            "--user",
            str(user_id),
            "--file",
            "dev/porfolio/acme.csv",
            "--account",
            "acme-main",
        ]
    )

    assert exit_code == 0
    assert captured["user_id"] == user_id
    assert isinstance(captured["user_id"], uuid.UUID)
    assert captured["account_label"] == "acme-main"
    assert captured["file_path"] == Path("dev/porfolio/acme.csv")
    assert isinstance(captured["file_path"], Path)


def test_import_transactions_prints_the_saved_count(monkeypatch, capsys):
    monkeypatch.setattr(
        cli.jobs,
        "run_import_transactions",
        lambda **kw: {"status": "ok", "rows_saved": 82, "transactions_saved": 82},
    )

    cli.main(
        [
            "import-transactions",
            "--user",
            str(uuid.uuid4()),
            "--file",
            "acme.csv",
            "--account",
            "acme-main",
        ]
    )

    assert capsys.readouterr().out == "82 transaction(s) saved\n"


def test_import_transactions_exits_zero_when_nothing_to_do(monkeypatch):
    monkeypatch.setattr(
        cli.jobs,
        "run_import_transactions",
        lambda **kw: {"status": "nothing_to_do", "rows_saved": 0, "transactions_saved": 0},
    )

    exit_code = cli.main(
        [
            "import-transactions",
            "--user",
            str(uuid.uuid4()),
            "--file",
            "acme.csv",
            "--account",
            "acme-main",
        ]
    )

    assert exit_code == 0


def test_import_transactions_propagates_a_domain_error_and_exits_nonzero(monkeypatch, capsys):
    """run_import_transactions can no longer return `partial` itself -- jobs.py lets a
    PortfolioError (e.g. an unparseable broker file) propagate instead of catching it, so this
    is what main()'s generic domain-error handling actually has to cover for this command."""

    def boom(**kw):
        raise cli.PortfolioError("could not parse acme.csv")

    monkeypatch.setattr(cli.jobs, "run_import_transactions", boom)

    exit_code = cli.main(
        [
            "import-transactions",
            "--user",
            str(uuid.uuid4()),
            "--file",
            "acme.csv",
            "--account",
            "acme-main",
        ]
    )

    assert exit_code == 1
    assert "could not parse acme.csv" in capsys.readouterr().err


def test_import_transactions_requires_all_flags():
    with pytest.raises(SystemExit):
        cli.main(["import-transactions", "--user", str(uuid.uuid4())])


def test_import_transactions_rejects_a_malformed_user_id():
    with pytest.raises(SystemExit):
        cli.main(
            [
                "import-transactions",
                "--user",
                "not-a-uuid",
                "--file",
                "acme.csv",
                "--account",
                "acme-main",
            ]
        )


# --- list-unmapped-instruments ---


def test_list_unmapped_instruments_dispatches_with_converted_user_id(monkeypatch):
    captured = {}

    def fake_run(*, user_id):
        captured["user_id"] = user_id
        return {
            "status": "nothing_to_do",
            "rows_saved": 0,
            "instruments_unmapped": 0,
            "unmapped_instruments": [],
        }

    monkeypatch.setattr(cli.jobs, "run_list_unmapped_instruments", fake_run)
    user_id = uuid.uuid4()

    cli.main(["list-unmapped-instruments", "--user", str(user_id)])

    assert captured == {"user_id": user_id}


def test_list_unmapped_instruments_prints_one_line_per_instrument_then_the_count(
    monkeypatch, capsys
):
    monkeypatch.setattr(
        cli.jobs,
        "run_list_unmapped_instruments",
        lambda **kw: {
            "status": "ok",
            "rows_saved": 0,
            "instruments_unmapped": 2,
            "unmapped_instruments": [
                {"kind": "ISIN", "identifier": "IE00B4L5Y983", "broker": "acme"},
                {"kind": "SYMBOL", "identifier": "IWDA", "broker": "globex"},
            ],
        },
    )

    cli.main(["list-unmapped-instruments", "--user", str(uuid.uuid4())])

    out = capsys.readouterr().out
    assert out == (
        "ISIN\tIE00B4L5Y983\tacme\n" "SYMBOL\tIWDA\tglobex\n" "2 instrument(s) unmapped\n"
    )


def test_list_unmapped_instruments_prints_only_the_count_when_empty(monkeypatch, capsys):
    monkeypatch.setattr(
        cli.jobs,
        "run_list_unmapped_instruments",
        lambda **kw: {
            "status": "nothing_to_do",
            "rows_saved": 0,
            "instruments_unmapped": 0,
            "unmapped_instruments": [],
        },
    )

    cli.main(["list-unmapped-instruments", "--user", str(uuid.uuid4())])

    assert capsys.readouterr().out == "0 instrument(s) unmapped\n"


def test_list_unmapped_instruments_exits_zero_when_empty(monkeypatch):
    monkeypatch.setattr(
        cli.jobs,
        "run_list_unmapped_instruments",
        lambda **kw: {
            "status": "nothing_to_do",
            "rows_saved": 0,
            "instruments_unmapped": 0,
            "unmapped_instruments": [],
        },
    )

    assert cli.main(["list-unmapped-instruments", "--user", str(uuid.uuid4())]) == 0


def test_missing_user_for_list_unmapped_instruments_exits_nonzero():
    with pytest.raises(SystemExit):
        cli.main(["list-unmapped-instruments"])


# --- import-cashflow ---


def test_import_cashflow_dispatches_with_converted_types(monkeypatch):
    captured = {}

    def fake_run(**kw):
        captured.update(kw)
        return {"status": "ok", "rows_saved": 339, "transactions_saved": 339}

    monkeypatch.setattr(cli.jobs, "run_import_cashflow", fake_run)
    user_id = uuid.uuid4()

    exit_code = cli.main(
        [
            "import-cashflow",
            "--user",
            str(user_id),
            "--file",
            "dev/porfolio/acme_account.csv",
            "--account",
            "acme-main",
        ]
    )

    assert exit_code == 0
    assert captured["user_id"] == user_id
    assert isinstance(captured["user_id"], uuid.UUID)
    assert captured["account_label"] == "acme-main"
    assert captured["file_path"] == Path("dev/porfolio/acme_account.csv")
    assert isinstance(captured["file_path"], Path)


def test_import_cashflow_prints_the_saved_count(monkeypatch, capsys):
    monkeypatch.setattr(
        cli.jobs,
        "run_import_cashflow",
        lambda **kw: {"status": "ok", "rows_saved": 339, "transactions_saved": 339},
    )

    cli.main(
        [
            "import-cashflow",
            "--user",
            str(uuid.uuid4()),
            "--file",
            "acme_account.csv",
            "--account",
            "acme-main",
        ]
    )

    assert capsys.readouterr().out == "339 transaction(s) saved\n"


def test_import_cashflow_exits_zero_when_nothing_to_do(monkeypatch):
    monkeypatch.setattr(
        cli.jobs,
        "run_import_cashflow",
        lambda **kw: {"status": "nothing_to_do", "rows_saved": 0, "transactions_saved": 0},
    )

    exit_code = cli.main(
        [
            "import-cashflow",
            "--user",
            str(uuid.uuid4()),
            "--file",
            "acme_account.csv",
            "--account",
            "acme-main",
        ]
    )

    assert exit_code == 0


def test_import_cashflow_propagates_a_domain_error_and_exits_nonzero(monkeypatch, capsys):
    """run_import_cashflow can no longer return `partial` itself -- jobs.py lets a PortfolioError
    (e.g. data-store unreachable) propagate instead of catching it, so this is what main()'s
    generic domain-error handling actually has to cover for this command."""

    def boom(**kw):
        raise cli.PortfolioError("data-store unreachable")

    monkeypatch.setattr(cli.jobs, "run_import_cashflow", boom)

    exit_code = cli.main(
        [
            "import-cashflow",
            "--user",
            str(uuid.uuid4()),
            "--file",
            "acme_account.csv",
            "--account",
            "acme-main",
        ]
    )

    assert exit_code == 1
    assert "data-store unreachable" in capsys.readouterr().err


def test_import_cashflow_requires_all_flags():
    with pytest.raises(SystemExit):
        cli.main(["import-cashflow", "--user", str(uuid.uuid4())])


def test_import_cashflow_rejects_a_malformed_user_id():
    with pytest.raises(SystemExit):
        cli.main(
            [
                "import-cashflow",
                "--user",
                "not-a-uuid",
                "--file",
                "acme_account.csv",
                "--account",
                "acme-main",
            ]
        )


# --- rebuild-positions ---


def test_rebuild_positions_dispatches_with_converted_user_id_and_no_account(monkeypatch):
    captured = {}

    def fake_run(*, user_id, account_label):
        captured["user_id"] = user_id
        captured["account_label"] = account_label
        return {
            "status": "nothing_to_do",
            "rows_saved": 0,
            "positions_rebuilt": 0,
            "positions_removed": 0,
            "instruments_blocked": 0,
            "positions_with_anomalies": 0,
            "pairs_failed": 0,
            "blocked_instruments": [],
            "failed_pairs": [],
        }

    monkeypatch.setattr(cli.jobs, "run_rebuild_positions", fake_run)
    user_id = uuid.uuid4()

    cli.main(["rebuild-positions", "--user", str(user_id)])

    assert captured == {"user_id": user_id, "account_label": None}


def test_rebuild_positions_passes_the_optional_account_label(monkeypatch):
    captured = {}

    def fake_run(*, user_id, account_label):
        captured["account_label"] = account_label
        return {
            "status": "ok",
            "rows_saved": 1,
            "positions_rebuilt": 1,
            "positions_removed": 0,
            "instruments_blocked": 0,
            "positions_with_anomalies": 0,
            "pairs_failed": 0,
            "blocked_instruments": [],
            "failed_pairs": [],
        }

    monkeypatch.setattr(cli.jobs, "run_rebuild_positions", fake_run)

    cli.main(["rebuild-positions", "--user", str(uuid.uuid4()), "--account", "main"])

    assert captured["account_label"] == "main"


def test_rebuild_positions_prints_blocked_instruments_then_the_count(monkeypatch, capsys):
    monkeypatch.setattr(
        cli.jobs,
        "run_rebuild_positions",
        lambda **kw: {
            "status": "partial",
            "rows_saved": 1,
            "positions_rebuilt": 1,
            "positions_removed": 0,
            "instruments_blocked": 1,
            "positions_with_anomalies": 0,
            "pairs_failed": 0,
            "blocked_instruments": [
                {"kind": "ISIN", "identifier": "IE00B4L5Y983", "broker": "acme"}
            ],
            "failed_pairs": [],
        },
    )

    cli.main(["rebuild-positions", "--user", str(uuid.uuid4())])

    out = capsys.readouterr().out
    assert out == "ISIN\tIE00B4L5Y983\tacme\n1 position snapshot(s) saved\n"


def test_rebuild_positions_prints_failed_pairs_then_the_count(monkeypatch, capsys):
    monkeypatch.setattr(
        cli.jobs,
        "run_rebuild_positions",
        lambda **kw: {
            "status": "partial",
            "rows_saved": 0,
            "positions_rebuilt": 0,
            "positions_removed": 0,
            "instruments_blocked": 0,
            "positions_with_anomalies": 0,
            "pairs_failed": 1,
            "blocked_instruments": [],
            "failed_pairs": [
                {"account_label": "m", "ticker": "IWDA.AS", "error": "connection reset"}
            ],
        },
    )

    cli.main(["rebuild-positions", "--user", str(uuid.uuid4())])

    out = capsys.readouterr().out
    assert out == "m\tIWDA.AS\tconnection reset\n0 position snapshot(s) saved\n"


def test_rebuild_positions_prints_only_the_count_when_nothing_blocked(monkeypatch, capsys):
    monkeypatch.setattr(
        cli.jobs,
        "run_rebuild_positions",
        lambda **kw: {
            "status": "ok",
            "rows_saved": 3,
            "positions_rebuilt": 1,
            "positions_removed": 0,
            "instruments_blocked": 0,
            "positions_with_anomalies": 0,
            "pairs_failed": 0,
            "blocked_instruments": [],
            "failed_pairs": [],
        },
    )

    cli.main(["rebuild-positions", "--user", str(uuid.uuid4())])

    assert capsys.readouterr().out == "3 position snapshot(s) saved\n"


def test_rebuild_positions_exits_zero_when_nothing_to_do(monkeypatch):
    monkeypatch.setattr(
        cli.jobs,
        "run_rebuild_positions",
        lambda **kw: {
            "status": "nothing_to_do",
            "rows_saved": 0,
            "positions_rebuilt": 0,
            "positions_removed": 0,
            "instruments_blocked": 0,
            "positions_with_anomalies": 0,
            "pairs_failed": 0,
            "blocked_instruments": [],
            "failed_pairs": [],
        },
    )

    assert cli.main(["rebuild-positions", "--user", str(uuid.uuid4())]) == 0


def test_rebuild_positions_exits_nonzero_when_partial(monkeypatch):
    monkeypatch.setattr(
        cli.jobs,
        "run_rebuild_positions",
        lambda **kw: {
            "status": "partial",
            "rows_saved": 0,
            "positions_rebuilt": 0,
            "positions_removed": 0,
            "instruments_blocked": 1,
            "positions_with_anomalies": 0,
            "pairs_failed": 0,
            "blocked_instruments": [
                {"kind": "ISIN", "identifier": "IE00B4L5Y983", "broker": "acme"}
            ],
            "failed_pairs": [],
        },
    )

    assert cli.main(["rebuild-positions", "--user", str(uuid.uuid4())]) == 1


def test_rebuild_positions_requires_the_user_flag():
    with pytest.raises(SystemExit):
        cli.main(["rebuild-positions"])


def test_rebuild_positions_rejects_a_malformed_user_id():
    with pytest.raises(SystemExit):
        cli.main(["rebuild-positions", "--user", "not-a-uuid"])


# --- rebuild-returns ---


def test_rebuild_returns_dispatches_with_converted_user_id_and_no_account(monkeypatch):
    captured = {}

    def fake_run(*, user_id, account_label):
        captured["user_id"] = user_id
        captured["account_label"] = account_label
        return {
            "status": "nothing_to_do",
            "rows_saved": 0,
            "tickers_rebuilt": 0,
            "tickers_blocked": 0,
            "pairs_failed": 0,
            "blocked_tickers": [],
            "failed_pairs": [],
        }

    monkeypatch.setattr(cli.jobs, "run_rebuild_returns", fake_run)
    user_id = uuid.uuid4()

    cli.main(["rebuild-returns", "--user", str(user_id)])

    assert captured == {"user_id": user_id, "account_label": None}


def test_rebuild_returns_passes_the_optional_account_label(monkeypatch):
    captured = {}

    def fake_run(*, user_id, account_label):
        captured["account_label"] = account_label
        return {
            "status": "ok",
            "rows_saved": 1,
            "tickers_rebuilt": 1,
            "tickers_blocked": 0,
            "pairs_failed": 0,
            "blocked_tickers": [],
            "failed_pairs": [],
        }

    monkeypatch.setattr(cli.jobs, "run_rebuild_returns", fake_run)

    cli.main(["rebuild-returns", "--user", str(uuid.uuid4()), "--account", "main"])

    assert captured["account_label"] == "main"


def test_rebuild_returns_prints_blocked_tickers_then_the_count(monkeypatch, capsys):
    monkeypatch.setattr(
        cli.jobs,
        "run_rebuild_returns",
        lambda **kw: {
            "status": "partial",
            "rows_saved": 1,
            "tickers_rebuilt": 1,
            "tickers_blocked": 1,
            "pairs_failed": 0,
            "blocked_tickers": [{"account_label": "m", "ticker": "IWDA.AS"}],
            "failed_pairs": [],
        },
    )

    cli.main(["rebuild-returns", "--user", str(uuid.uuid4())])

    out = capsys.readouterr().out
    assert out == "m\tIWDA.AS\n1 return snapshot(s) saved\n"


def test_rebuild_returns_prints_failed_pairs_then_the_count(monkeypatch, capsys):
    monkeypatch.setattr(
        cli.jobs,
        "run_rebuild_returns",
        lambda **kw: {
            "status": "partial",
            "rows_saved": 0,
            "tickers_rebuilt": 0,
            "tickers_blocked": 0,
            "pairs_failed": 1,
            "blocked_tickers": [],
            "failed_pairs": [
                {"account_label": "m", "ticker": "IWDA.AS", "error": "connection reset"}
            ],
        },
    )

    cli.main(["rebuild-returns", "--user", str(uuid.uuid4())])

    out = capsys.readouterr().out
    assert out == "m\tIWDA.AS\tconnection reset\n0 return snapshot(s) saved\n"


def test_rebuild_returns_prints_only_the_count_when_nothing_blocked(monkeypatch, capsys):
    monkeypatch.setattr(
        cli.jobs,
        "run_rebuild_returns",
        lambda **kw: {
            "status": "ok",
            "rows_saved": 3,
            "tickers_rebuilt": 1,
            "tickers_blocked": 0,
            "pairs_failed": 0,
            "blocked_tickers": [],
            "failed_pairs": [],
        },
    )

    cli.main(["rebuild-returns", "--user", str(uuid.uuid4())])

    assert capsys.readouterr().out == "3 return snapshot(s) saved\n"


def test_rebuild_returns_exits_zero_when_nothing_to_do(monkeypatch):
    monkeypatch.setattr(
        cli.jobs,
        "run_rebuild_returns",
        lambda **kw: {
            "status": "nothing_to_do",
            "rows_saved": 0,
            "tickers_rebuilt": 0,
            "tickers_blocked": 0,
            "pairs_failed": 0,
            "blocked_tickers": [],
            "failed_pairs": [],
        },
    )

    assert cli.main(["rebuild-returns", "--user", str(uuid.uuid4())]) == 0


def test_rebuild_returns_exits_nonzero_when_partial(monkeypatch):
    monkeypatch.setattr(
        cli.jobs,
        "run_rebuild_returns",
        lambda **kw: {
            "status": "partial",
            "rows_saved": 0,
            "tickers_rebuilt": 0,
            "tickers_blocked": 1,
            "pairs_failed": 0,
            "blocked_tickers": [{"account_label": "m", "ticker": "IWDA.AS"}],
            "failed_pairs": [],
        },
    )

    assert cli.main(["rebuild-returns", "--user", str(uuid.uuid4())]) == 1


def test_rebuild_returns_requires_the_user_flag():
    with pytest.raises(SystemExit):
        cli.main(["rebuild-returns"])


def test_rebuild_returns_rejects_a_malformed_user_id():
    with pytest.raises(SystemExit):
        cli.main(["rebuild-returns", "--user", "not-a-uuid"])


# --- serve ---


def test_serve_dispatches_to_uvicorn_with_settings(monkeypatch):
    captured = {}

    fake_settings = type(
        "S", (), {"api_host": "127.0.0.1", "api_port": 9002, "database_url": "postgresql://x"}
    )()
    fake_app = object()

    monkeypatch.setattr("portfolio.settings.load_settings", lambda: fake_settings)
    monkeypatch.setattr("portfolio.api.app.create_app", lambda settings: fake_app)

    def fake_run(app, *, host, port):
        captured["app"] = app
        captured["host"] = host
        captured["port"] = port

    monkeypatch.setattr("uvicorn.run", fake_run)

    assert cli.main(["serve"]) == 0
    assert captured == {"app": fake_app, "host": "127.0.0.1", "port": 9002}


def test_add_transaction_amount_is_optional(monkeypatch):
    """A BUY/SELL's amount is derived from price * quantity + fees when omitted -- see
    pipeline.add_manual_transaction."""
    captured = {}

    def fake_run_add_transaction(**kw):
        captured.update(kw)
        return {"status": "ok", "rows_saved": 1, "transactions_saved": 1}

    monkeypatch.setattr(cli.jobs, "run_add_transaction", fake_run_add_transaction)
    args = [a for a in _BASE_TXN_ARGS if a not in ("--amount", "-955.00")]

    exit_code = cli.main([args[0], "--user", str(uuid.uuid4()), *args[1:]])

    assert exit_code == 0
    assert captured["native_amount"] is None


# --- list-unmapped-instruments --all ---


def test_list_unmapped_instruments_all_lists_every_users_instruments(monkeypatch, capsys):
    monkeypatch.setattr(
        cli.jobs,
        "run_list_all_unmapped_instruments",
        lambda: {
            "status": "ok",
            "rows_saved": 0,
            "instruments_unmapped": 2,
            "unmapped_instruments": [
                {"kind": "ISIN", "identifier": "IE00B4L5Y983", "broker": None},
                {"kind": "SYMBOL", "identifier": "IWDA", "broker": "globex"},
            ],
        },
    )

    assert cli.main(["list-unmapped-instruments", "--all"]) == 0

    assert capsys.readouterr().out == (
        "ISIN\tIE00B4L5Y983\t-\n" "SYMBOL\tIWDA\tglobex\n" "2 instrument(s) unmapped\n"
    )


def test_list_unmapped_instruments_needs_exactly_one_of_user_or_all():
    for argv in (
        ["list-unmapped-instruments"],
        ["list-unmapped-instruments", "--all", "--user", str(uuid.uuid4())],
    ):
        with pytest.raises(SystemExit) as exc_info:
            cli.main(argv)
        assert exc_info.value.code != 0


# --- ticker-mapping ---

_ADMIN = "00000000-0000-7000-8000-000000000001"
_MAPPING = {
    "id": 7,
    "identifier_kind": "SYMBOL",
    "identifier": "IWDA",
    "broker": "globex",
    "canonical_ticker": "IWDA.AS",
    "updated_by": _ADMIN,
    "created_at": "2026-10-01T00:00:00+00:00",
    "updated_at": "2026-10-06T00:00:00+00:00",
}


def _write_result(**overrides):
    result = {
        "status": "ok",
        "rows_saved": 1,
        "accounts_rebuilt": 2,
        "rebuilds_failed": 0,
        "returns_rebuilds_failed": 0,
        "mapping": _MAPPING,
    }
    result.update(overrides)
    return result


def test_ticker_mapping_list_prints_one_line_per_mapping_then_the_count(monkeypatch, capsys):
    isin = {
        **_MAPPING,
        "id": 3,
        "identifier_kind": "ISIN",
        "identifier": "IE00B4L5Y983",
        "broker": None,
    }
    monkeypatch.setattr(cli.jobs, "get_ticker_mappings", lambda: [isin, _MAPPING])

    assert cli.main(["ticker-mapping", "list"]) == 0

    assert capsys.readouterr().out == (
        "3\tISIN\tIE00B4L5Y983\t-\tIWDA.AS\n"
        "7\tSYMBOL\tIWDA\tglobex\tIWDA.AS\n"
        "2 ticker mapping(s)\n"
    )


def test_ticker_mapping_set_dispatches_and_reports_the_rebuild(monkeypatch, capsys):
    captured = {}

    def fake_set(**kw):
        captured.update(kw)
        return _write_result()

    monkeypatch.setattr(cli.jobs, "run_set_ticker_mapping", fake_set)

    exit_code = cli.main(
        [
            "ticker-mapping",
            "set",
            "--kind",
            "SYMBOL",
            "--identifier",
            "IWDA",
            "--broker",
            "globex",
            "--ticker",
            "IWDA.AS",
            "--user",
            _ADMIN,
        ]
    )

    assert exit_code == 0
    assert captured == {
        "identifier_kind": "SYMBOL",
        "identifier": "IWDA",
        "broker": "globex",
        "canonical_ticker": "IWDA.AS",
        "updated_by": uuid.UUID(_ADMIN),
    }
    assert capsys.readouterr().out == (
        "SYMBOL IWDA (globex) -> IWDA.AS\n" "positions rebuilt for 2 account(s), 0 failed\n"
    )


def test_ticker_mapping_set_without_broker_passes_none(monkeypatch):
    captured = {}

    def fake_set(**kw):
        captured.update(kw)
        return _write_result()

    monkeypatch.setattr(cli.jobs, "run_set_ticker_mapping", fake_set)

    cli.main(
        ["ticker-mapping", "set", "--kind", "ISIN", "--identifier", "IE00B4L5Y983"]
        + ["--ticker", "IWDA.AS", "--user", _ADMIN]
    )

    assert captured["broker"] is None


def test_ticker_mapping_set_only_accepts_mappable_kinds():
    with pytest.raises(SystemExit) as exc_info:
        cli.main(
            ["ticker-mapping", "set", "--kind", "TICKER", "--identifier", "X"]
            + ["--ticker", "X", "--user", _ADMIN]
        )

    assert exc_info.value.code != 0


def test_ticker_mapping_set_exits_nonzero_when_a_rebuild_failed(monkeypatch):
    monkeypatch.setattr(
        cli.jobs, "run_set_ticker_mapping", lambda **kw: _write_result(rebuilds_failed=1)
    )

    exit_code = cli.main(
        ["ticker-mapping", "set", "--kind", "ISIN", "--identifier", "IE00B4L5Y983"]
        + ["--ticker", "IWDA.AS", "--user", _ADMIN]
    )

    # The mapping is saved, but a rebuild that needs retrying must not look like a clean run.
    assert exit_code == 1


def test_ticker_mapping_delete_dispatches_and_reports(monkeypatch, capsys):
    captured = {}

    def fake_delete(**kw):
        captured.update(kw)
        return _write_result(accounts_rebuilt=1)

    monkeypatch.setattr(cli.jobs, "run_delete_ticker_mapping", fake_delete)

    exit_code = cli.main(["ticker-mapping", "delete", "--id", "7", "--user", _ADMIN])

    assert exit_code == 0
    assert captured == {"mapping_id": 7, "updated_by": uuid.UUID(_ADMIN)}
    assert capsys.readouterr().out == (
        "deleted SYMBOL IWDA (globex) -> IWDA.AS\n" "positions rebuilt for 1 account(s), 0 failed\n"
    )


# --- rebuild-returns --all ---


def test_rebuild_returns_all_dispatches_and_prints_the_summary(monkeypatch, capsys):
    monkeypatch.setattr(
        cli.jobs,
        "run_rebuild_returns_all",
        lambda: {
            "status": "ok",
            "rows_saved": 15,
            "users_rebuilt": 2,
            "users_failed": 0,
            "tickers_blocked": 0,
            "pairs_failed": 0,
        },
    )

    assert cli.main(["rebuild-returns", "--all"]) == 0
    assert capsys.readouterr().out == (
        "returns rebuilt for 2 user(s), 0 failed\n" "15 return snapshot(s) saved\n"
    )


def test_rebuild_returns_all_exits_nonzero_when_partial(monkeypatch):
    monkeypatch.setattr(
        cli.jobs,
        "run_rebuild_returns_all",
        lambda: {
            "status": "partial",
            "rows_saved": 5,
            "users_rebuilt": 1,
            "users_failed": 1,
            "tickers_blocked": 0,
            "pairs_failed": 0,
        },
    )

    assert cli.main(["rebuild-returns", "--all"]) == 1


def test_rebuild_returns_needs_exactly_one_of_user_or_all():
    for argv in (
        ["rebuild-returns"],
        ["rebuild-returns", "--all", "--user", str(uuid.uuid4())],
        ["rebuild-returns", "--all", "--account", "main"],
    ):
        with pytest.raises(SystemExit) as exc_info:
            cli.main(argv)
        assert exc_info.value.code != 0


def test_ticker_mapping_set_exits_nonzero_when_a_returns_rebuild_failed(monkeypatch, capsys):
    monkeypatch.setattr(
        cli.jobs,
        "run_set_ticker_mapping",
        lambda **kw: _write_result(returns_rebuilds_failed=1),
    )

    exit_code = cli.main(
        ["ticker-mapping", "set", "--kind", "ISIN", "--identifier", "IE00B4L5Y983"]
        + ["--ticker", "IWDA.AS", "--user", _ADMIN]
    )

    assert exit_code == 1
    assert "returns failed for 1 account(s)" in capsys.readouterr().out
