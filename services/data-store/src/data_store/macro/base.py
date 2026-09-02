from typing import Protocol

import pandas as pd


class MacroIndicatorSource(Protocol):
    name: str  # stamped into raw_macro_indicators.source

    def fetch(self, regions: list[str]) -> pd.DataFrame:
        """Long-form rows: indicator_code, region, observation_date, indicator_type, frequency,
        value, unit. `regions` is the one thing orchestration controls per source --
        FredMacroIndicatorSource only ever serves ['US'] and raises on anything else;
        EurostatMacroIndicatorSource serves whatever config/macro_regions.yaml resolves to."""
        ...
