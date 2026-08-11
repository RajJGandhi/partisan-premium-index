from __future__ import annotations

import pytest

from scripts.analyze_shadow_experiment import (
    arm_summary,
    build_report,
    evidence_count_by_market,
    evidence_overlap_degree_by_market,
    frequency_at_target,
    frequency_divisible_by,
    group_comparison,
    mean_stdev,
    pearson_correlation,
    reproduction_check,
    unique_value_count,
)


def _result(arm, market_slug, fair_value, status="OK"):
    return {"arm": arm, "market_slug": market_slug, "fair_value": fair_value, "status": status}


def test_mean_stdev_basic():
    mean, stdev = mean_stdev([0.4, 0.5, 0.6])
    assert mean == 0.5
    assert stdev is not None and stdev > 0

    assert mean_stdev([]) == (None, None)
    assert mean_stdev([0.5]) == (0.5, None)


def test_unique_value_count_collapses_float_noise():
    assert unique_value_count([0.45, 0.45, 0.45]) == 1
    assert unique_value_count([0.45, 0.5, 0.55]) == 3
    assert unique_value_count([0.45, 0.4500000001]) == 1


def test_frequency_at_target_exact_match_only():
    assert frequency_at_target([0.45, 0.45, 0.5, 0.6], target=0.45) == 2
    assert frequency_at_target([0.44, 0.46], target=0.45) == 0


def test_frequency_divisible_by_0_05():
    assert frequency_divisible_by([0.45, 0.5, 0.55, 0.6], step=0.05) == 4
    assert frequency_divisible_by([0.47, 0.52, 0.63], step=0.05) == 0
    assert frequency_divisible_by([0.45, 0.47], step=0.05) == 1


def test_pearson_correlation_perfect_positive_and_negative():
    assert pearson_correlation([1, 2, 3], [1, 2, 3]) == pytest.approx(1.0)
    assert pearson_correlation([1, 2, 3], [3, 2, 1]) == pytest.approx(-1.0)
    assert pearson_correlation([1, 1, 1], [1, 2, 3]) is None  # zero variance in x
    assert pearson_correlation([1, 2], []) is None  # mismatched lengths


def test_evidence_count_and_overlap_degree_by_market():
    frozen = [
        {
            "market_slug": "m1",
            "evidence_count": 2,
            "evidence_items": [{"content_hash": "h1"}, {"content_hash": "h2"}],
        },
        {
            "market_slug": "m2",
            "evidence_count": 1,
            "evidence_items": [{"content_hash": "h1"}],  # shares h1 with m1
        },
        {
            "market_slug": "m3",
            "evidence_count": 1,
            "evidence_items": [{"content_hash": "h3"}],  # shares nothing
        },
    ]
    counts = evidence_count_by_market(frozen)
    assert counts == {"m1": 2, "m2": 1, "m3": 1}

    overlap = evidence_overlap_degree_by_market(frozen)
    assert overlap["m1"] == 1  # overlaps with m2 only
    assert overlap["m2"] == 1  # overlaps with m1 only
    assert overlap["m3"] == 0  # overlaps with nobody


def test_arm_summary_computes_clustering_metrics():
    results = [
        *[_result("A", "m1", 0.45) for _ in range(5)],  # perfectly clustered at target
        *[_result("A", "m2", v) for v in [0.6, 0.62, 0.58, 0.61, 0.59]],  # varied, near 0.6
    ]
    frozen = [
        {"market_slug": "m1", "evidence_count": 1, "evidence_items": [{"content_hash": "h1"}]},
        {"market_slug": "m2", "evidence_count": 5, "evidence_items": [{"content_hash": "h2"}]},
    ]
    summary = arm_summary(results, "A", frozen)
    assert summary["forecast_count"] == 10
    assert summary["count_at_target_probability"] == 5
    assert summary["per_market"]["m1"]["mean"] == 0.45
    assert summary["per_market"]["m1"]["stdev"] == 0  # identical values, zero spread
    assert summary["per_market"]["m2"]["stdev"] > 0
    assert summary["within_market_variance"] is not None
    assert summary["between_market_variance"] is not None


def test_reproduction_check_flags_markets_matching_original_cluster():
    results = [
        *[_result("A", "m1", 0.45) for _ in range(5)],  # reproduces every time
        *[_result("A", "m2", v) for v in [0.45, 0.45, 0.6, 0.6, 0.6]],  # minority at target
        *[_result("A", "m3", 0.7) for _ in range(5)],  # never at target
    ]
    check = reproduction_check(results, "A", original_cluster_slugs={"m1", "m2"}, target=0.45)
    assert "m1" in check["markets_reproducing_majority_at_target"]
    assert "m2" not in check["markets_reproducing_majority_at_target"]  # only 2/5, not majority
    assert check["overlap_with_original_cluster"] == ["m1"]
    assert check["reproduced_count"] == 1
    assert check["original_cluster_size"] == 2


def test_group_comparison_averages_each_group_separately():
    results = [
        *[_result("A", "clustered_market", 0.45) for _ in range(5)],
        *[_result("A", "escaped_market", v) for v in [0.6, 0.62, 0.58, 0.6, 0.6]],
    ]
    comparison = group_comparison(results, "A", {"clustered_market"}, {"escaped_market"})
    assert comparison["cluster_group_mean_of_means"] == 0.45
    assert comparison["other_group_mean_of_means"] > 0.55


def test_build_report_covers_every_arm():
    results = [_result("A", "m1", 0.45), _result("B", "m1", 0.5)]
    experiment = {"results": results}
    frozen = [{"market_slug": "m1", "evidence_count": 1, "evidence_items": []}]
    report = build_report(experiment, frozen, original_cluster_slugs={"m1"})
    assert report["arms_analyzed"] == ["A", "B"]
    assert "A" in report["by_arm"]
    assert "B" in report["by_arm"]
    assert report["by_arm"]["A"]["reproduction"]["reproduced_count"] == 1
