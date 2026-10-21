"""HttpPortfolioClient.backtest's `risk` section (RK-8, parsed in RK-9): the per-window risk
figures for the mix and each asset, the risk-free status and the mix's drawdown series, and
which malformed shapes become PortfolioUnavailable. The JSON is written out in the shape
portfolio's BacktestRiskResponse serves: figures are JSON numbers, not the backtest's
fixed-point strings, and fractions (0.10 is 10%)."""

from datetime import date
from decimal import Decimal

import httpx
import pytest
from web.portfolio_client.base import (
    BACKTEST_PERIODS,
    BACKTEST_RISK_WINDOWS,
    RISK_FIGURE_KEYS,
    BacktestRisk,
    BacktestRowRisk,
    BacktestWindowRisk,
    DrawdownSeries,
    RiskFigure,
    RiskFreeStatus,
)
from web.portfolio_client.errors import PortfolioUnavailable
from web.portfolio_client.http_client import HttpPortfolioClient

pytestmark = pytest.mark.anyio

INTERNAL_SECRET = "test-secret"  # pragma: allowlist secret
END = "2026-10-09"
COMMON_START = "2019-07-29"


def _client(body) -> HttpPortfolioClient:
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json=body))
    http_client = httpx.AsyncClient(base_url="http://portfolio.internal", transport=transport)
    return HttpPortfolioClient(http_client, INTERNAL_SECRET)


def _figure(value=0.1, **changes):
    return {
        "value": value,
        "window_start": "2025-10-09",
        "window_end": END,
        "frequency": "daily",
        "q": 252.0,
        "n_obs": 252,
        "n_gaps": 0,
        "flags": [],
        "reason": None,
        "standard_error": None,
        **changes,
    }


def _window(**changes):
    return {
        "start": "2025-10-09",
        "end": END,
        "covered": True,
        "reason": None,
        **{key: _figure() for key in RISK_FIGURE_KEYS},
        "peak_date": "2026-02-02",
        "trough_date": "2026-04-07",
        "recovery_date": None,
        "max_days_under_water": 249,
        **changes,
    }


def _uncovered(reason):
    return {
        "start": None,
        "end": END,
        "covered": False,
        "reason": reason,
        **{key: None for key in RISK_FIGURE_KEYS},
        "peak_date": None,
        "trough_date": None,
        "recovery_date": None,
        "max_days_under_water": None,
    }


def _row_risk(ticker, **windows):
    all_windows = {w: _window() for w in BACKTEST_RISK_WINDOWS}
    all_windows.update(windows)
    return {"ticker": ticker, "windows": all_windows}


def _risk(**changes):
    return {
        "label": "buy & hold (drifting)",
        "end_date": END,
        "common_start": COMMON_START,
        "limited_by": ["VWCE.DE"],
        "risk_free": {"available": True, "reason": None, "first_date": "1999-01-04"},
        "mix": _row_risk(None),
        "assets": [_row_risk("VWCE.DE")],
        "drawdown": {
            "period": "1M",
            "dates": ["2026-09-09", "2026-09-10", END],
            "values": [0.0, None, -0.0125],
        },
        **changes,
    }


def _period():
    return {
        "start": "2026-09-09",
        "end": END,
        "covered": True,
        "return_pct": "0.01000000",
        "gaps": 0,
        "left_out": [],
    }


def _row(ticker, weight):
    return {"ticker": ticker, "weight": weight, "periods": {p: _period() for p in BACKTEST_PERIODS}}


def _body(risk):
    return {
        "end_date": END,
        "mix": _row(None, "1"),
        "assets": [_row("VWCE.DE", "1")],
        "growth": None,
        "statuses": [{"ticker": "VWCE.DE", "status": "included", "reason": None}],
        "risk": risk,
    }


async def _backtest(risk):
    return await _client(_body(risk)).backtest(assets=[("VWCE.DE", Decimal("1"))], period="1M")


def _expected_figure(value=0.1, **changes):
    fields = {
        "value": value,
        "window_start": date(2025, 10, 9),
        "window_end": date(2026, 10, 9),
        "frequency": "daily",
        "q": 252.0,
        "n_obs": 252,
        "n_gaps": 0,
        "flags": (),
        "reason": None,
        "standard_error": None,
    }
    fields.update(changes)
    return RiskFigure(**fields)


def _expected_window():
    return BacktestWindowRisk(
        start=date(2025, 10, 9),
        end=date(2026, 10, 9),
        covered=True,
        reason=None,
        figures={key: _expected_figure() for key in RISK_FIGURE_KEYS},
        peak_date=date(2026, 2, 2),
        trough_date=date(2026, 4, 7),
        recovery_date=None,
        max_days_under_water=249,
    )


async def test_parses_a_full_risk_section():
    result = await _backtest(_risk())

    windows = {w: _expected_window() for w in BACKTEST_RISK_WINDOWS}
    assert result.risk == BacktestRisk(
        label="buy & hold (drifting)",
        end_date=date(2026, 10, 9),
        common_start=date(2019, 7, 29),
        limited_by=("VWCE.DE",),
        risk_free=RiskFreeStatus(available=True, reason=None, first_date=date(1999, 1, 4)),
        mix=BacktestRowRisk(ticker=None, windows=windows),
        assets=(BacktestRowRisk(ticker="VWCE.DE", windows=windows),),
        drawdown=DrawdownSeries(
            period="1M",
            dates=(date(2026, 9, 9), date(2026, 9, 10), date(2026, 10, 9)),
            values=(0.0, None, -0.0125),
        ),
    )


async def test_windows_keep_the_standard_order():
    # portfolio's JSON object order isn't the contract; the table's columns are.
    reordered = _row_risk(None)
    reordered["windows"] = dict(reversed(list(reordered["windows"].items())))

    result = await _backtest(_risk(mix=reordered))

    assert tuple(result.risk.mix.windows) == BACKTEST_RISK_WINDOWS


async def test_a_null_risk_section_is_none():
    # portfolio sends null exactly when no asset could be included.
    result = await _backtest(None)

    assert result.risk is None


async def test_a_greyed_figure_keeps_its_reason_and_has_no_value():
    reason = "insufficient history (have 40, need 52)"
    window = _window(volatility=_figure(None, frequency="weekly", q=52.0, n_obs=40, reason=reason))

    result = await _backtest(_risk(mix=_row_risk(None, **{"1Y": window})))

    figure = result.risk.mix.windows["1Y"].figures["volatility"]
    assert figure.value is None
    assert figure.reason == reason
    assert figure.n_obs == 40
    assert figure.frequency == "weekly"


async def test_a_sharpe_carries_its_standard_error_and_flags():
    window = _window(
        sharpe=_figure(0.86, frequency="weekly", standard_error=0.59, flags=["data_gaps"])
    )

    result = await _backtest(_risk(mix=_row_risk(None, **{"3Y": window})))

    figure = result.risk.mix.windows["3Y"].figures["sharpe"]
    assert figure.value == 0.86
    assert figure.standard_error == 0.59
    assert figure.flags == ("data_gaps",)


async def test_an_integer_value_is_read_as_a_float():
    # JSON has one number type: a 0 drawdown may arrive as 0, not 0.0.
    window = _window(max_drawdown=_figure(0))

    result = await _backtest(_risk(mix=_row_risk(None, **{"1Y": window})))

    value = result.risk.mix.windows["1Y"].figures["max_drawdown"].value
    assert value == 0.0
    assert isinstance(value, float)


async def test_an_uncovered_window_has_its_reason_and_no_figures():
    reason = "limited by VWCE.DE (history from 2019-07-29)"

    result = await _backtest(_risk(mix=_row_risk(None, **{"10Y": _uncovered(reason)})))

    window = result.risk.mix.windows["10Y"]
    assert window.covered is False
    assert window.reason == reason
    assert window.start is None
    assert set(window.figures) == set(RISK_FIGURE_KEYS)
    assert all(figure is None for figure in window.figures.values())


async def test_no_risk_free_data_and_no_drawdown_parse():
    risk_free = {"available": False, "reason": "no risk-free rate data", "first_date": None}

    result = await _backtest(_risk(risk_free=risk_free, drawdown=None, common_start=None))

    assert result.risk.risk_free == RiskFreeStatus(
        available=False, reason="no risk-free rate data", first_date=None
    )
    assert result.risk.drawdown is None
    assert result.risk.common_start is None


def _without(mapping, key):
    return {k: v for k, v in mapping.items() if k != key}


@pytest.mark.parametrize(
    "body",
    [
        # portfolio always sends the key; a missing one is a contract break, not "no risk".
        pytest.param(_without(_body(_risk()), "risk"), id="no-risk-key"),
        pytest.param(
            _body(
                _risk(mix={"ticker": None, "windows": _without(_row_risk(None)["windows"], "5Y")})
            ),
            id="missing-window",
        ),
        pytest.param(
            _body(_risk(mix=_row_risk(None, **{"1Y": _without(_window(), "sortino")}))),
            id="missing-figure",
        ),
        pytest.param(
            _body(_risk(mix=_row_risk(None, **{"1Y": _window(cagr=_figure(n_obs=True))}))),
            id="bool-n-obs",
        ),
        pytest.param(
            _body(_risk(mix=_row_risk(None, **{"1Y": _window(cagr=_figure("0.1"))}))),
            id="string-value",
        ),
        pytest.param(
            _body(_risk(mix=_row_risk(None, **{"1Y": _window(cagr=_figure(True))}))),
            id="bool-value",
        ),
        pytest.param(
            _body(_risk(drawdown={"period": "1M", "dates": [END], "values": [0.0, -0.1]})),
            id="drawdown-length",
        ),
        pytest.param(_body(_risk(limited_by="VWCE.DE")), id="limited-by-not-a-list"),
    ],
)
async def test_a_malformed_risk_section_is_unavailable(body):
    client = _client(body)

    with pytest.raises(PortfolioUnavailable, match="malformed portfolio response"):
        await client.backtest(assets=[("VWCE.DE", Decimal("1"))], period="1M")
