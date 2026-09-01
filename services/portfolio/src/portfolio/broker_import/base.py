"""The BrokerParser Protocol -- one implementation per broker export-file format, registered in
pipeline.py's BROKER_PARSERS. See docs/architecture.md's "Extension points" section.
"""

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
