"""Checks anti-bug.

Cada test construye a mano el escenario que el check debe atrapar, y su
contraparte legítima que no debe disparar. Un check que salta siempre es tan
inútil como uno que no salta nunca.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sportstar.validation.sanity import (
    BacktestSample,
    Severity,
    check_closing_coverage,
    check_duplicate_events,
    check_edge_distribution,
    check_feature_leakage,
    check_market_overround,
    check_odds_after_start,
    check_roi_vs_sample_size,
    check_win_rate,
    run_sanity_checks,
)

T0 = datetime(2026, 8, 19, 18, 0, tzinfo=UTC)


class TestRoiVsSampleSize:
    def test_flags_spectacular_roi_on_a_tiny_sample(self) -> None:
        findings = check_roi_vs_sample_size(n_bets=80, roi=0.30)
        assert len(findings) == 1
        assert findings[0].severity is Severity.FATAL

    def test_allows_the_same_roi_with_enough_bets(self) -> None:
        assert check_roi_vs_sample_size(n_bets=6000, roi=0.30) == []

    def test_allows_a_plausible_roi_on_a_small_sample(self) -> None:
        # +4% sobre 80 apuestas es ruido, no una alarma de bug.
        assert check_roi_vs_sample_size(n_bets=80, roi=0.04) == []


class TestWinRate:
    def test_flags_impossible_win_rate_at_pickem_prices(self) -> None:
        findings = check_win_rate(n_bets=400, win_rate=0.68, avg_decimal_odds=1.95)
        assert findings and findings[0].severity is Severity.FATAL

    def test_allows_high_win_rate_on_heavy_favourites(self) -> None:
        # Acertar el 68% apostando a -300 es lo esperado, no una anomalía.
        assert check_win_rate(n_bets=400, win_rate=0.68, avg_decimal_odds=1.33) == []

    def test_stays_quiet_below_the_minimum_sample(self) -> None:
        assert check_win_rate(n_bets=40, win_rate=0.70, avg_decimal_odds=2.0) == []


class TestFeatureLeakage:
    def test_flags_features_built_from_later_data(self) -> None:
        findings = check_feature_leakage([(T0, T0 + timedelta(hours=1))])
        assert findings and findings[0].severity is Severity.FATAL

    def test_flags_equal_timestamps(self) -> None:
        # observed_at == as_of ya es leakage: el dato no estaba disponible
        # estrictamente antes del corte.
        assert check_feature_leakage([(T0, T0)])

    def test_accepts_strictly_earlier_data(self) -> None:
        assert check_feature_leakage([(T0, T0 - timedelta(seconds=1))]) == []

    def test_any_violation_is_fatal_regardless_of_proportion(self) -> None:
        # No existe una cantidad aceptable de leakage.
        samples = [(T0, T0 - timedelta(hours=1))] * 999 + [(T0, T0 + timedelta(hours=1))]
        findings = check_feature_leakage(samples)
        assert findings and findings[0].severity is Severity.FATAL


class TestOddsAfterStart:
    def test_flags_in_play_prices_used_as_pregame(self) -> None:
        findings = check_odds_after_start([(T0 + timedelta(minutes=5), T0)])
        assert findings and findings[0].severity is Severity.FATAL

    def test_accepts_prices_captured_before_the_first_pitch(self) -> None:
        assert check_odds_after_start([(T0 - timedelta(minutes=5), T0)]) == []


class TestMarketOverround:
    def test_flags_apparent_arbitrage(self) -> None:
        findings = check_market_overround([("evt-1", [0.48, 0.48])])
        assert findings and findings[0].severity is Severity.FATAL

    def test_accepts_a_normal_vigged_market(self) -> None:
        assert check_market_overround([("evt-1", [0.5238, 0.5238])]) == []


class TestDuplicateEvents:
    def test_flags_a_repeated_event_key(self) -> None:
        key = ("mlb", "2026-08-19", "NYY", "BOS")
        findings = check_duplicate_events([key, key])
        assert findings and findings[0].severity is Severity.FATAL

    def test_accepts_a_clean_slate(self) -> None:
        assert check_duplicate_events([("mlb", "2026-08-19", "NYY", "BOS")]) == []


class TestEdgeDistribution:
    def test_flags_systematically_positive_edge(self) -> None:
        # Un modelo calibrado centra el edge cerca de 0: para cada lado con edge
        # positivo, el contrario debería tenerlo negativo. Una media de +6 puntos
        # sobre 400 candidates es un error de vig, no una ventaja.
        findings = check_edge_distribution([0.06] * 400)
        assert findings and findings[0].severity is Severity.FATAL

    def test_accepts_an_edge_distribution_centred_near_zero(self) -> None:
        assert check_edge_distribution(([0.04] * 200) + ([-0.04] * 200)) == []

    def test_stays_quiet_on_small_samples(self) -> None:
        assert check_edge_distribution([0.10] * 10) == []


class TestClosingCoverage:
    def test_warns_below_the_threshold(self) -> None:
        findings = check_closing_coverage(captured=800, total=1000)
        assert findings and findings[0].severity is Severity.WARNING

    def test_accepts_full_coverage(self) -> None:
        assert check_closing_coverage(captured=1000, total=1000) == []

    def test_handles_an_empty_slate(self) -> None:
        assert check_closing_coverage(captured=0, total=0) == []


class TestRunner:
    def test_clean_backtest_passes(self) -> None:
        report = run_sanity_checks(
            BacktestSample(n_bets=2000, roi=0.03, win_rate=0.53, avg_decimal_odds=1.95)
        )
        assert report.passed
        assert report.blocking == []

    def test_a_single_fatal_blocks_the_whole_report(self) -> None:
        report = run_sanity_checks(
            BacktestSample(
                n_bets=2000,
                roi=0.03,
                win_rate=0.53,
                avg_decimal_odds=1.95,
                feature_as_of_pairs=[(T0, T0 + timedelta(hours=1))],
            )
        )
        assert not report.passed
        assert len(report.blocking) == 1

    def test_a_warning_alone_does_not_block(self) -> None:
        report = run_sanity_checks(
            BacktestSample(
                n_bets=2000,
                roi=0.03,
                win_rate=0.53,
                avg_decimal_odds=1.95,
                closing_captured=800,
                closing_total=1000,
            )
        )
        assert report.passed
        assert len(report.findings) == 1

    def test_the_too_good_to_be_true_backtest_is_rejected(self) -> None:
        """El escenario del brief: 70% win rate y 30% ROI.

        Debe tratarse como un bug hasta demostrar lo contrario, y el sistema debe
        decirlo por sí solo en vez de depender de que el operador se acuerde.
        """
        report = run_sanity_checks(
            BacktestSample(
                n_bets=300,
                roi=0.30,
                win_rate=0.70,
                avg_decimal_odds=2.0,
                edges=[0.08] * 400,
            )
        )
        assert not report.passed
        checks = {f.check for f in report.blocking}
        assert {"roi_vs_sample_size", "win_rate_vs_price", "edge_distribution"} <= checks

    def test_report_serializes_for_persistence(self) -> None:
        report = run_sanity_checks(
            BacktestSample(n_bets=80, roi=0.30, win_rate=0.53, avg_decimal_odds=1.95)
        )
        payload = report.as_dict()
        assert payload and payload[0]["check"] == "roi_vs_sample_size"
        assert payload[0]["severity"] == "fatal"
