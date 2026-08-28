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
                # `row.get(field) or ""` rather than `row.get(field, "")`: a ragged row (fewer
                # trailing columns than the header) makes DictReader fill the missing ones with
                # None, and dict.get's default only applies when the key is absent, not when it
                # maps to None -- so a plain `.get(field, "")` would still hand None downstream.
                regime_id = (row.get("regime_id") or "").strip()
                dimension = (row.get("dimension") or "").strip()
                scope = (row.get("scope") or "").strip()
                name = (row.get("name") or "").strip()
                source = (row.get("source") or "").strip()

                for field, value in (
                    ("regime_id", regime_id),
                    ("dimension", dimension),
                    ("scope", scope),
                    ("name", name),
                    ("source", source),
                ):
                    if not value:
                        raise ConfigError(f"Row {row_num}: {field} is required")

                if regime_id in seen_ids:
                    raise ConfigError(f"Row {row_num}: duplicate regime_id '{regime_id}'")
                seen_ids.add(regime_id)

                try:
                    start_date = _parse_date(row.get("start_date"), "start_date", row_num)
                    end_date = _parse_optional_date(row.get("end_date"), "end_date", row_num)
                    known_from = _parse_optional_date(row.get("known_from"), "known_from", row_num)
                except ValueError as e:
                    raise ConfigError(str(e))

                severity = (row.get("severity") or "").strip() or None
                if severity is not None and severity not in VALID_SEVERITIES:
                    raise ConfigError(
                        f"Row {row_num}: invalid severity '{severity}'. "
                        f"Must be one of: {', '.join(sorted(VALID_SEVERITIES))}"
                    )

                notes = (row.get("notes") or "").strip() or None

                regimes.append(
                    RegimeConfig(
                        regime_id=regime_id,
                        dimension=dimension,
                        scope=scope,
                        name=name,
                        start_date=start_date,
                        end_date=end_date,
                        known_from=known_from,
                        severity=severity,
                        source=source,
                        notes=notes,
                    )
                )

            return regimes

    except ConfigError:
        raise
    except (OSError, csv.Error) as e:
        raise ConfigError(f"Failed to parse regimes CSV: {e}")


def _parse_date(value: str | None, field: str, row_num: int) -> date:
    parsed = _parse_optional_date(value, field, row_num)
    if parsed is None:
        raise ValueError(f"Row {row_num}: {field} is required")
    return parsed


def _parse_optional_date(value: str | None, field: str, row_num: int) -> date | None:
    value = (value or "").strip()
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise ValueError(f"Row {row_num}: {field} has invalid date format: {value}")
