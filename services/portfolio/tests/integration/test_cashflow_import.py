"""import_acme_cashflows against live Postgres: the idempotent-reimport contract, using a stub
DataStoreClient rather than a live data-store instance -- that boundary is the API contract
itself (datastore_client/http_client.py's own unit tests), not something this service's suite
should own verifying end-to-end. See docs/architecture.md's testing section.

Routed through pipeline.import_acme_cashflows directly rather than the CLI/jobs layer, so the
stub client can be injected without a live data-store instance or real DATASTORE_API_URL/
INTERNAL_API_SECRET -- jobs.run_import_acme_cashflows's own construction of the real
HttpDataStoreClient from settings is covered by tests/unit/test_jobs.py instead.
"""

import uuid
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from portfolio import cli, pipeline
from portfolio.errors import BrokerMismatch

pytestmark = pytest.mark.integration

_FIXTURE = (
    Path(__file__).parents[1] / "unit" / "broker_import" / "fixtures" / "acme_account_sample.csv"
)

# EUR/<currency> rates covering every non-EUR row the fixture needs resolved.
_RATES = {
    ("PLN", date(2024, 3, 20)): Decimal("4.30"),
    ("PLN", date(2026, 5, 27)): Decimal("4.25"),
    ("PLN", date(2022, 7, 11)): Decimal("4.70"),
    ("PLN", date(2026, 7, 4)): Decimal("4.20"),
    ("USD", date(2026, 7, 14)): Decimal("1.16"),
    ("USD", date(2026, 7, 1)): Decimal("1.17"),
    ("USD", date(2025, 7, 11)): Decimal("1.15"),
}


class StubDataStoreClient:
    def get_fx_rate(self, base, quote, on_date):
        assert base == "EUR"
        return _RATES.get((quote, on_date))


def _add_account(user_id, *, label="acme-main", broker="acme"):
    exit_code = cli.main(
        ["account", "add", "--user", str(user_id), "--broker", broker, "--label", label]
    )
    assert exit_code == 0


def _import(pg_connect, user_id, *, label="acme-main"):
    with pg_connect() as conn:
        saved = pipeline.import_acme_cashflows(
            conn,
            user_id=user_id,
            account_label=label,
            file_path=_FIXTURE,
            datastore_client=StubDataStoreClient(),
        )
        conn.commit()
    return saved


def test_importing_a_cashflow_file_twice_is_idempotent(pg_connect):
    user_id = uuid.uuid4()
    _add_account(user_id)

    first = _import(pg_connect, user_id)
    second = _import(pg_connect, user_id)

    assert first == 14  # every real cash event in the fixture -- see test_acme_cashflow.py
    assert second == 0


def test_cashflow_import_requires_a_acme_account(pg_connect):
    user_id = uuid.uuid4()
    _add_account(user_id, label="globex-main", broker="globex")

    with pg_connect() as conn:
        with pytest.raises(BrokerMismatch):
            pipeline.import_acme_cashflows(
                conn,
                user_id=user_id,
                account_label="globex-main",
                file_path=_FIXTURE,
                datastore_client=StubDataStoreClient(),
            )
