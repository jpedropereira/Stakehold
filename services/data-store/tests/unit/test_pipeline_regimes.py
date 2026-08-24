from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch

from data_store.pipeline import ingest_regimes
from data_store.regimes.loader import RegimeConfig


def test_ingest_regimes_logs_start(caplog):
    import logging

    caplog.set_level(logging.INFO)
    conn = MagicMock()
    csv_path = Path("test.csv")

    with (
        patch("data_store.pipeline.load_regimes") as mock_load,
        patch("data_store.pipeline.save_regimes") as mock_save,
    ):
        mock_load.return_value = [
            RegimeConfig(
                regime_id="test",
                dimension="test",
                scope="test",
                name="test",
                start_date=date(2020, 1, 1),
                end_date=None,
                known_from=None,
                severity=None,
                source="test",
                notes=None,
            )
        ]
        mock_save.return_value = 1

        ingest_regimes(conn, csv_path)

        assert f"loading regimes from {csv_path}" in caplog.text
        assert "loaded 1 regime(s)" in caplog.text


def test_ingest_regimes_calls_loader(caplog):
    import logging

    caplog.set_level(logging.INFO)
    conn = MagicMock()
    csv_path = Path("test.csv")

    with (
        patch("data_store.pipeline.load_regimes") as mock_load,
        patch("data_store.pipeline.save_regimes") as mock_save,
    ):
        mock_load.return_value = []
        mock_save.return_value = 0

        ingest_regimes(conn, csv_path)

        mock_load.assert_called_once_with(csv_path)


def test_ingest_regimes_calls_repository(caplog):
    import logging

    caplog.set_level(logging.INFO)
    conn = MagicMock()
    csv_path = Path("test.csv")
    regimes = [
        RegimeConfig(
            regime_id="test",
            dimension="test",
            scope="test",
            name="test",
            start_date=date(2020, 1, 1),
            end_date=None,
            known_from=None,
            severity=None,
            source="test",
            notes=None,
        )
    ]

    with (
        patch("data_store.pipeline.load_regimes") as mock_load,
        patch("data_store.pipeline.save_regimes") as mock_save,
    ):
        mock_load.return_value = regimes
        mock_save.return_value = 1

        result = ingest_regimes(conn, csv_path)

        mock_save.assert_called_once_with(conn, regimes)
        assert result["rows_saved"] == 1


def test_ingest_regimes_logs_completion(caplog):
    import logging

    caplog.set_level(logging.INFO)
    conn = MagicMock()
    csv_path = Path("test.csv")

    with (
        patch("data_store.pipeline.load_regimes") as mock_load,
        patch("data_store.pipeline.save_regimes") as mock_save,
    ):
        mock_load.return_value = []
        mock_save.return_value = 0

        ingest_regimes(conn, csv_path)

        assert "ingest-regimes complete: 0 regime row(s)" in caplog.text


def test_ingest_regimes_returns_result():
    conn = MagicMock()
    csv_path = Path("test.csv")

    with (
        patch("data_store.pipeline.load_regimes") as mock_load,
        patch("data_store.pipeline.save_regimes") as mock_save,
    ):
        mock_load.return_value = [
            RegimeConfig(
                regime_id="test",
                dimension="test",
                scope="test",
                name="test",
                start_date=date(2020, 1, 1),
                end_date=None,
                known_from=None,
                severity=None,
                source="test",
                notes=None,
            )
        ]
        mock_save.return_value = 1

        result = ingest_regimes(conn, csv_path)

        assert result["status"] == "ok"
        assert result["rows_saved"] == 1
