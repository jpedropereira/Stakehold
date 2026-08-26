from datetime import date
from pathlib import Path

import pytest
from data_store.config_loader import ConfigError
from data_store.regimes.loader import RegimeConfig, load_regimes


def write_csv(tmp_path, content):
    path = tmp_path / "regimes.csv"
    path.write_text(content)
    return path


def test_loads_valid_regimes(tmp_path):
    path = write_csv(
        tmp_path,
        """regime_id,dimension,scope,name,start_date,end_date,known_from,severity,source,notes
ea_rec_1974,Economic,US,Oil Crisis Recession,1973-11-01,1975-03-01,1975-03-01,severe,NBER,
gfc,Financial,Global,Global Financial Crisis,2007-09-01,2009-03-01,2009-03-01,extreme,Fed,
""",
    )

    regimes = load_regimes(path)

    assert len(regimes) == 2
    assert regimes[0].regime_id == "ea_rec_1974"
    assert regimes[0].dimension == "Economic"
    assert regimes[0].start_date == date(1973, 11, 1)
    assert regimes[0].end_date == date(1975, 3, 1)
    assert regimes[0].known_from == date(1975, 3, 1)
    assert regimes[0].severity == "severe"
    assert regimes[0].source == "NBER"
    assert regimes[0].notes is None

    assert regimes[1].regime_id == "gfc"
    assert regimes[1].end_date == date(2009, 3, 1)


def test_converts_blank_strings_to_none(tmp_path):
    path = write_csv(
        tmp_path,
        """regime_id,dimension,scope,name,start_date,end_date,known_from,severity,source,notes
ongoing_war,Geopolitical,Europe,Ukraine War,2022-02-24,,,,Bloomberg,
""",
    )

    regimes = load_regimes(path)

    assert len(regimes) == 1
    assert regimes[0].end_date is None
    assert regimes[0].known_from is None
    assert regimes[0].severity is None
    assert regimes[0].notes is None


def test_rejects_missing_required_field(tmp_path):
    path = write_csv(
        tmp_path,
        """regime_id,dimension,scope,start_date,end_date,known_from,severity,source,notes
ea_rec_1974,Economic,US,1973-11-01,1975-03-01,1975-03-01,severe,NBER,
""",
    )

    with pytest.raises(ConfigError, match="name"):
        load_regimes(path)


def test_rejects_blank_required_field(tmp_path):
    path = write_csv(
        tmp_path,
        """regime_id,dimension,scope,name,start_date,end_date,known_from,severity,source,notes
ea_rec_1974,,US,,1973-11-01,1975-03-01,1975-03-01,severe,NBER,
""",
    )

    with pytest.raises(ConfigError, match="dimension"):
        load_regimes(path)


def test_rejects_ragged_row_with_missing_trailing_columns(tmp_path):
    path = write_csv(
        tmp_path,
        """regime_id,dimension,scope,name,start_date,end_date,known_from,severity,source,notes
ea_rec_1974,Economic,US,Oil Crisis Recession,1973-11-01
""",
    )

    with pytest.raises(ConfigError, match="(?i)source"):
        load_regimes(path)


def test_rejects_duplicate_regime_ids(tmp_path):
    path = write_csv(
        tmp_path,
        """regime_id,dimension,scope,name,start_date,end_date,known_from,severity,source,notes
gfc,Financial,Global,Global Financial Crisis,2007-09-01,2009-03-01,2009-03-01,extreme,Fed,
gfc,Financial,Europe,European Financial Crisis,2008-01-01,2010-01-01,2010-01-01,severe,ECB,
""",
    )

    with pytest.raises(ConfigError, match="(?i)duplicate.*regime_id"):
        load_regimes(path)


def test_rejects_invalid_severity(tmp_path):
    path = write_csv(
        tmp_path,
        """regime_id,dimension,scope,name,start_date,end_date,known_from,severity,source,notes
bad_severity,Economic,US,Bad Severity,1973-11-01,1975-03-01,1975-03-01,catastrophic,NBER,
""",
    )

    with pytest.raises(ConfigError, match="(?i)severity"):
        load_regimes(path)


def test_rejects_invalid_date_format(tmp_path):
    path = write_csv(
        tmp_path,
        """regime_id,dimension,scope,name,start_date,end_date,known_from,severity,source,notes
bad_date,Economic,US,Bad Date,1973/11/01,1975-03-01,1975-03-01,severe,NBER,
""",
    )

    with pytest.raises(ConfigError, match="(?i)date"):
        load_regimes(path)


def test_missing_file_returns_empty_list():
    regimes = load_regimes(Path("/does/not/exist.csv"))
    assert regimes == []


def test_returns_regime_config_dataclass(tmp_path):
    path = write_csv(
        tmp_path,
        """regime_id,dimension,scope,name,start_date,end_date,known_from,severity,source,notes
gfc,Financial,Global,GFC,2007-09-01,2009-03-01,,extreme,Fed,
""",
    )

    regimes = load_regimes(path)

    assert isinstance(regimes[0], RegimeConfig)
    assert regimes[0].regime_id == "gfc"
