"""risk/covariance.py: the ex-ante maths (spec §6.2-6.3) at any frequency, q a parameter.

§6.3's golden test is pinned: weights 60/40, volatilities 20%/10%, correlation 0.3. Its values
were recomputed independently in the 2026-10-20 domain review; if the code disagrees, find out
which side is wrong before changing either.
"""

import numpy as np
import pytest
from portfolio.risk.covariance import covariance_matrix, decompose, effective_n


def _cov(vols, corr):
    vols = np.array(vols)
    c = np.array([[1.0, corr], [corr, 1.0]])
    return np.outer(vols, vols) * c


GOLDEN_W = np.array([0.6, 0.4])
GOLDEN_COV = _cov([0.20, 0.10], 0.3)


def test_golden_sigma_p():
    assert decompose(GOLDEN_W, GOLDEN_COV).sigma_p == pytest.approx(0.137405, abs=5e-7)


def test_golden_marginal_contributions():
    mcr = decompose(GOLDEN_W, GOLDEN_COV).mcr
    assert mcr == pytest.approx([0.192133, 0.055311], abs=5e-7)


def test_golden_contributions():
    cr = decompose(GOLDEN_W, GOLDEN_COV).cr
    assert cr == pytest.approx([0.115280, 0.022124], abs=5e-7)


def test_golden_percent_contributions():
    pcr = decompose(GOLDEN_W, GOLDEN_COV).pcr
    assert pcr == pytest.approx([0.83898, 0.16102], abs=5e-6)


def test_golden_correlation_with_portfolio():
    rho = decompose(GOLDEN_W, GOLDEN_COV).corr_with_portfolio
    assert rho == pytest.approx([0.96067, 0.55311], abs=5e-6)


def test_golden_diversification_ratio():
    assert decompose(GOLDEN_W, GOLDEN_COV).diversification_ratio == pytest.approx(1.16445, abs=5e-6)


def test_golden_volatilities():
    assert decompose(GOLDEN_W, GOLDEN_COV).volatilities == pytest.approx([0.20, 0.10])


def test_contributions_sum_to_sigma_p():
    cov = np.array([[0.04, 0.006, -0.002], [0.006, 0.0225, 0.001], [-0.002, 0.001, 0.01]])
    result = decompose(np.array([0.5, 0.3, 0.2]), cov)
    assert sum(result.cr) == pytest.approx(result.sigma_p)


def test_percent_contributions_sum_to_one():
    cov = np.array([[0.04, 0.006, -0.002], [0.006, 0.0225, 0.001], [-0.002, 0.001, 0.01]])
    assert sum(decompose(np.array([0.5, 0.3, 0.2]), cov).pcr) == pytest.approx(1.0)


def test_a_hedge_has_negative_percent_contribution():
    # (Σw)_2 = 0.8 × (−0.9 × 0.2 × 0.1) + 0.2 × 0.01 = −0.0124 < 0.
    result = decompose(np.array([0.8, 0.2]), _cov([0.20, 0.10], -0.9))
    assert result.pcr[1] < 0
    assert result.pcr[0] > 1
    assert result.corr_with_portfolio[1] < 0


def test_no_variation_has_no_sigma_p():
    assert decompose(np.array([0.5, 0.5]), np.zeros((2, 2))) is None


def test_a_flat_holding_has_no_correlation_with_the_portfolio():
    cov = np.array([[0.04, 0.0], [0.0, 0.0]])
    result = decompose(np.array([0.5, 0.5]), cov)
    assert result.corr_with_portfolio[1] is None
    assert result.corr_with_portfolio[0] == pytest.approx(1.0)
    assert result.pcr[1] == 0.0


def test_covariance_is_the_sample_covariance_times_q():
    returns = np.array([[0.01, 0.02], [-0.01, 0.00], [0.03, -0.01], [0.00, 0.01]])
    expected = np.cov(returns, rowvar=False, ddof=1) * 52
    assert covariance_matrix(returns, 52) == pytest.approx(expected)


def test_a_holding_with_identical_returns_has_exactly_zero_covariance():
    # 0.1 three times doesn't round-trip through its mean: numpy's raw covariances are ~1e-34.
    returns = np.array([[0.01, 0.1], [-0.01, 0.1], [0.03, 0.1]])
    cov = covariance_matrix(returns, 52)
    assert cov[1, 1] == 0.0
    assert cov[0, 1] == cov[1, 0] == 0.0
    assert cov[0, 0] > 0


def test_covariance_rejects_a_gap():
    with pytest.raises(ValueError, match="listwise"):
        covariance_matrix(np.array([[0.01, np.nan], [0.02, 0.01], [0.0, 0.0]]), 52)


def test_covariance_needs_two_observations():
    with pytest.raises(ValueError, match="two"):
        covariance_matrix(np.array([[0.01, 0.02]]), 52)


def test_covariance_of_one_holding_is_a_one_by_one_matrix():
    cov = covariance_matrix(np.array([[0.01], [-0.01], [0.02]]), 52)
    assert cov.shape == (1, 1)


def test_effective_n():
    assert effective_n(np.array([0.6, 0.4])) == pytest.approx(1 / 0.52)
    assert effective_n(np.array([0.25] * 4)) == pytest.approx(4.0)
