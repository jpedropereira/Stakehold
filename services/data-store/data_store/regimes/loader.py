import csv
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from data_store.config_loader import ConfigError

VALID_SEVERITIES = {"mild", "moderate", "severe", "extreme"}


@dataclass(frozen=True)
class RegimeConfig:
    regime_id: str
    dimension: str
    scope: str
    name: str
    start_date: date
    end_date: date | None
    known_from: date | None
    severity: str | None
    source: str
    notes: str | None


def load_regimes(path: Path) -> list[RegimeConfig]:
    if not path.is_file():
        return []

    try:
        with open(path) as f:
            reader = csv.DictReader(f)
            if reader.fieldnames is None:
                raise ConfigError("CSV file is empty")

            required_fields = {
                "regime_id",
                "dimension",
                "scope",
                "name",
                "start_date",
                "severity",
                "source",
            }
            if not required_fields.issubset(set(reader.fieldnames)):
                missing = required_fields - set(reader.fieldnames)
                raise ConfigError(f"CSV missing required columns: {missing}")

            regimes = []
            seen_ids = set()

            for row_num, row in enumerate(reader, start=2):
                regime_id = row["regime_id"].strip()

                if not regime_id:
                    raise ConfigError(f"Row {row_num}: regime_id is required")

                if regime_id in seen_ids:
                    raise ConfigError(f"Row {row_num}: duplicate regime_id '{regime_id}'")
                seen_ids.add(regime_id)

                try:
                    start_date = _parse_date(row["start_date"], "start_date", row_num)
                    end_date = _parse_optional_date(row.get("end_date", ""), "end_date", row_num)
                    known_from = _parse_optional_date(
                        row.get("known_from", ""), "known_from", row_num
                    )
                except ValueError as e:
                    raise ConfigError(str(e))

                severity = row.get("severity", "").strip() or None
                if severity is not None and severity not in VALID_SEVERITIES:
                    raise ConfigError(
                        f"Row {row_num}: invalid severity '{severity}'. "
                        f"Must be one of: {', '.join(sorted(VALID_SEVERITIES))}"
                    )

                notes = row.get("notes", "").strip() or None

                regimes.append(
                    RegimeConfig(
                        regime_id=regime_id,
                        dimension=row["dimension"].strip(),
                        scope=row["scope"].strip(),
                        name=row["name"].strip(),
                        start_date=start_date,
                        end_date=end_date,
                        known_from=known_from,
                        severity=severity,
                        source=row["source"].strip(),
                        notes=notes,
                    )
                )

            return regimes

    except ConfigError:
        raise
    except Exception as e:
        raise ConfigError(f"Failed to parse regimes CSV: {e}")


def _parse_date(value: str, field: str, row_num: int) -> date:
    value = value.strip()
    if not value:
        raise ValueError(f"Row {row_num}: {field} is required")
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise ValueError(f"Row {row_num}: {field} has invalid date format: {value}")


def _parse_optional_date(value: str, field: str, row_num: int) -> date | None:
    value = value.strip()
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise ValueError(f"Row {row_num}: {field} has invalid date format: {value}")
