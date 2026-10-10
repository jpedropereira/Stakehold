"""Two Protocols implementations register under, plus the type alias the second one needs:
BrokerParser (one implementation per broker export-file format) and CashflowParser (one
implementation per cash-ledger export format that needs a live FX-rate lookup mid-parse).
Implementations are discovered via entry points (broker_import/discovery.py), not hand-registered
here -- see docs/broker-import.md.
"""

from collections.abc import Callable
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Protocol

from portfolio.transactions.base import CanonicalTransaction


class BrokerParser(Protocol):
    broker_key: str  # the registry key (e.g. "acme"). accounts.broker is the persisted
    # source of truth for which broker an account belongs to; nothing on CanonicalTransaction
    # repeats it.

    def parse(self, file_path: Path, account_id: int) -> list[CanonicalTransaction]:
        """Parse one broker export file into normalized transactions for one account.

        account_id is the resolved accounts.id, supplied by the caller after resolving
        --account through accounts/repository.py's get_by_label -- not discovered from the
        file itself. Neither known export format self-identifies its account.
        """
        ...


ResolveFxRate = Callable[[str, date], Decimal | None]


class CashflowParser(Protocol):
    broker_key: str  # same registry-key role as BrokerParser.broker_key, in the separate
    # CASHFLOW_PARSERS registry -- a broker can appear in one registry, the other, both, or
    # neither, independently.

    def parse(
        self, file_path: Path, account_id: int, resolve_fx_rate: ResolveFxRate
    ) -> list[CanonicalTransaction]:
        """Parse one cash-ledger export into normalized transactions for one account.

        Unlike BrokerParser.parse, this carries resolve_fx_rate explicitly -- CashflowParser is
        only for formats whose rows carry no EUR-equivalent at all and need a live FX lookup
        mid-parse. BrokerParser stays free of that dependency so parsers that never need one
        don't carry it. account_id is the resolved accounts.id, same as BrokerParser.
        """
        ...
