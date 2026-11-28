"""`portfolio lab list|save|delete` (LF-1): argument parsing, the job it calls, and what it
prints. Kept out of test_cli.py for size."""

import uuid
from decimal import Decimal

import pytest
from portfolio import cli
from portfolio.errors import SavedLabNameTaken

USER = uuid.uuid4()


def test_lab_list_prints_one_line_per_lab_then_the_count(monkeypatch, capsys):
    monkeypatch.setattr(
        cli.saved_lab_jobs,
        "run_list_saved_labs",
        lambda **kw: {
            "status": "ok",
            "rows_saved": 0,
            "labs_listed": 2,
            "labs": [
                {
                    "name": "Core",
                    "assets": [
                        {"ticker": "VWCE.DE", "weight": "60"},
                        {"ticker": "AAPL", "weight": "40"},
                    ],
                    "created_at": "2026-11-01T09:00:00+00:00",
                    "updated_at": "2026-11-02T09:00:00+00:00",
                },
                {
                    "name": "Tech",
                    "assets": [{"ticker": "MSFT", "weight": "100"}],
                    "created_at": "2026-11-01T09:00:00+00:00",
                    "updated_at": "2026-11-01T09:00:00+00:00",
                },
            ],
        },
    )

    exit_code = cli.main(["lab", "list", "--user", str(USER)])

    assert exit_code == 0
    assert capsys.readouterr().out == (
        "Core\tVWCE.DE=60 AAPL=40\t2026-11-02\n" "Tech\tMSFT=100\t2026-11-01\n" "2 lab(s)\n"
    )


def test_lab_list_prints_only_the_count_when_empty_and_exits_zero(monkeypatch, capsys):
    monkeypatch.setattr(
        cli.saved_lab_jobs,
        "run_list_saved_labs",
        lambda **kw: {"status": "nothing_to_do", "rows_saved": 0, "labs_listed": 0, "labs": []},
    )

    assert cli.main(["lab", "list", "--user", str(USER)]) == 0
    assert capsys.readouterr().out == "0 lab(s)\n"


def test_lab_save_parses_each_asset_and_reports_a_new_lab(monkeypatch, capsys):
    captured = {}

    def fake_save(**kw):
        captured.update(kw)
        return {
            "status": "ok",
            "rows_saved": 2,
            "labs_created": 1,
            "labs_replaced": 0,
            "name": "Core",
        }

    monkeypatch.setattr(cli.saved_lab_jobs, "run_save_lab", fake_save)

    exit_code = cli.main(
        [
            "lab",
            "save",
            "--user",
            str(USER),
            "--name",
            "Core",
            "--asset",
            "VWCE.DE=60",
            "--asset",
            "AAPL=40%",
        ]
    )

    assert exit_code == 0
    assert captured == {
        "user_id": USER,
        "name": "Core",
        "assets": [("VWCE.DE", Decimal("60")), ("AAPL", Decimal("40"))],
        "replace": False,
    }
    assert capsys.readouterr().out == "Lab 'Core' created with 2 asset(s)\n"


def test_lab_save_with_replace_reports_a_replaced_lab(monkeypatch, capsys):
    captured = {}

    def fake_save(**kw):
        captured.update(kw)
        return {
            "status": "ok",
            "rows_saved": 1,
            "labs_created": 0,
            "labs_replaced": 1,
            "name": "Core",
        }

    monkeypatch.setattr(cli.saved_lab_jobs, "run_save_lab", fake_save)

    cli.main(
        ["lab", "save", "--user", str(USER), "--name", "Core", "--asset", "MSFT=100", "--replace"]
    )

    assert captured["replace"] is True
    assert capsys.readouterr().out == "Lab 'Core' replaced with 1 asset(s)\n"


@pytest.mark.parametrize("asset", ["VWCE.DE", "VWCE.DE=", "VWCE.DE=lots"])
def test_lab_save_rejects_an_asset_that_is_not_ticker_equals_weight(asset, capsys):
    with pytest.raises(SystemExit) as exc:
        cli.main(["lab", "save", "--user", str(USER), "--name", "Core", "--asset", asset])

    assert exc.value.code == 2
    assert "expected TICKER=WEIGHT" in capsys.readouterr().err


def test_lab_save_reports_a_taken_name_and_exits_one(monkeypatch, capsys):
    def fake_save(**kw):
        raise SavedLabNameTaken("You already have a lab called 'Core'.")

    monkeypatch.setattr(cli.saved_lab_jobs, "run_save_lab", fake_save)

    exit_code = cli.main(
        ["lab", "save", "--user", str(USER), "--name", "Core", "--asset", "MSFT=100"]
    )

    assert exit_code == 1
    assert "You already have a lab called 'Core'." in capsys.readouterr().err


def test_lab_delete_reports_the_deleted_lab(monkeypatch, capsys):
    captured = {}

    def fake_delete(**kw):
        captured.update(kw)
        return {"status": "ok", "rows_saved": 0, "labs_deleted": 1}

    monkeypatch.setattr(cli.saved_lab_jobs, "run_delete_saved_lab", fake_delete)

    exit_code = cli.main(["lab", "delete", "--user", str(USER), "--name", "Core"])

    assert exit_code == 0
    assert captured == {"user_id": USER, "name": "Core"}
    assert capsys.readouterr().out == "1 lab(s) deleted\n"
