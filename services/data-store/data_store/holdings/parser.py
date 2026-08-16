from io import StringIO

import pandas as pd
import requests


class ParseError(Exception):
    """Raised when the holdings CSV table can't be located or parsed from the response text."""


def fetch_holdings_csv(csv_url: str) -> pd.DataFrame:
    response = requests.get(csv_url)
    response.raise_for_status()
    response.encoding = "utf-8"
    return parse_holdings_csv(response.text)


def parse_holdings_csv(content: str) -> pd.DataFrame:
    """The export has a short preamble (fund name / as-of date) before the actual holdings
    table, so the table is located by finding the header row itself (starting with
    "Ticker,") rather than assuming a fixed number of lines to skip.
    """
    lines = content.splitlines()
    header_idx = next(
        (i for i, line in enumerate(lines) if line.strip().lower().startswith("ticker,")),
        None,
    )
    if header_idx is None:
        raise ParseError(
            "Could not locate the holdings table header row (starting with 'Ticker,'); "
            "the export format may have changed."
        )

    data_segment = "\n".join(lines[header_idx:])
    df = pd.read_csv(StringIO(data_segment))
    df.columns = [str(c).strip().lower() for c in df.columns]
    return df
