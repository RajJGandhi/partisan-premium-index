"""Statistical analysis for a scripts/run_shadow_experiment.py results file.

Pure computation over a local JSON file -- never touches the database, never touches
DATABASE_URL, never writes anything back into the shadow experiment's own results file.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from statistics import mean as _mean
from statistics import stdev as _stdev
from statistics import variance as _variance
from typing import Any, Sequence

EPSILON = 1e-9
TARGET_PROBABILITY = 0.45


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text())


def _round_bucket(value: float, decimals: int = 6) -> float:
    """Collapses floating-point noise so 0.4500000001 and 0.45 count as one unique value."""
    return round(value, decimals)


def numeric_values(results: list[dict[str, Any]], arm: str, market_slug: str | None = None) -> list[float]:
    """Fair values from OK/ABSTAINED rows only (FAILED rows have no real value to analyze)."""
    return [
        r["fair_value"]
        for r in results
        if r["arm"] == arm
        and r["status"] in {"OK", "ABSTAINED"}
        and r["fair_value"] is not None
        and (market_slug is None or r["market_slug"] == market_slug)
    ]


def markets_in_arm(results: list[dict[str, Any]], arm: str) -> list[str]:
    seen: dict[str, None] = {}
    for r in results:
        if r["arm"] == arm and r["market_slug"] is not None:
            seen.setdefault(r["market_slug"], None)
    return list(seen.keys())


def mean_stdev(values: list[float]) -> tuple[float | None, float | None]:
    if not values:
        return None, None
    if len(values) == 1:
        return values[0], None
    return _mean(values), _stdev(values)


def unique_value_count(values: list[float]) -> int:
    return len({_round_bucket(v) for v in values})


def frequency_at_target(values: list[float], target: float = TARGET_PROBABILITY) -> int:
    return sum(1 for v in values if abs(v - target) < EPSILON)


def frequency_divisible_by(values: list[float], step: float = 0.05) -> int:
    count = 0
    for v in values:
        ratio = v / step
        if abs(ratio - round(ratio)) < 1e-6:
            count += 1
    return count


def within_market_variance(results: list[dict[str, Any]], arm: str) -> float | None:
    """Average, across markets, of the variance of that market's repetitions -- how much a
    single market's answer wobbles across independent generations under identical inputs."""
    variances = []
    for slug in markets_in_arm(results, arm):
        values = numeric_values(results, arm, slug)
        if len(values) >= 2:
            variances.append(_variance(values))
    return _mean(variances) if variances else None


def between_market_variance(results: list[dict[str, Any]], arm: str) -> float | None:
    """Variance of each market's mean probability -- how much markets differ from each other,
    as opposed to how much a single market wobbles across repetitions."""
    means = []
    for slug in markets_in_arm(results, arm):
        values = numeric_values(results, arm, slug)
        if values:
            means.append(_mean(values))
    return _variance(means) if len(means) >= 2 else None


def pearson_correlation(xs: Sequence[float], ys: Sequence[float]) -> float | None:
    if len(xs) != len(ys) or len(xs) < 2:
        return None
    mx, my = _mean(xs), _mean(ys)
    numerator = sum((x - mx) * (y - my) for x, y in zip(xs, ys, strict=True))
    denom_x = sum((x - mx) ** 2 for x in xs) ** 0.5
    denom_y = sum((y - my) ** 2 for y in ys) ** 0.5
    if denom_x == 0 or denom_y == 0:
        return None
    return numerator / (denom_x * denom_y)


def evidence_count_by_market(frozen_inputs: list[dict[str, Any]]) -> dict[str, int]:
    return {row["market_slug"]: row.get("evidence_count", len(row.get("evidence_items", []))) for row in frozen_inputs}


def evidence_overlap_degree_by_market(frozen_inputs: list[dict[str, Any]]) -> dict[str, int]:
    """Number of OTHER markets a given market shares at least one evidence content_hash with."""
    hashes_by_market: dict[str, set[str]] = {}
    for row in frozen_inputs:
        hashes_by_market[row["market_slug"]] = {
            item.get("content_hash") for item in row.get("evidence_items", []) if item.get("content_hash")
        }
    degree: dict[str, int] = {}
    for slug, hashes in hashes_by_market.items():
        degree[slug] = sum(
            1 for other_slug, other_hashes in hashes_by_market.items() if other_slug != slug and hashes & other_hashes
        )
    return degree


def per_market_stats(results: list[dict[str, Any]], arm: str) -> dict[str, dict[str, Any]]:
    stats = {}
    for slug in markets_in_arm(results, arm):
        values = numeric_values(results, arm, slug)
        mean, stdev = mean_stdev(values)
        failed = sum(1 for r in results if r["arm"] == arm and r["market_slug"] == slug and r["status"] == "FAILED")
        stats[slug] = {
            "values": values,
            "mean": mean,
            "stdev": stdev,
            "unique_values": unique_value_count(values),
            "count_at_target": frequency_at_target(values),
            "failed_count": failed,
        }
    return stats


def arm_summary(results: list[dict[str, Any]], arm: str, frozen_inputs: list[dict[str, Any]]) -> dict[str, Any]:
    values = numeric_values(results, arm)
    per_market = per_market_stats(results, arm)
    evidence_counts = evidence_count_by_market(frozen_inputs)
    overlap_degrees = evidence_overlap_degree_by_market(frozen_inputs)

    distance_from_half = []
    evidence_count_list = []
    overlap_list = []
    concentration_list = []
    for slug, stat in per_market.items():
        if stat["mean"] is not None:
            distance_from_half.append(abs(stat["mean"] - 0.5))
            evidence_count_list.append(evidence_counts.get(slug, 0))
        if stat["stdev"] is not None:
            overlap_list.append(overlap_degrees.get(slug, 0))
            # "concentration" defined as the inverse of spread: higher stdev = lower concentration.
            concentration_list.append(-stat["stdev"])

    return {
        "arm": arm,
        "forecast_count": len(values),
        "unique_probability_count": unique_value_count(values),
        "count_at_target_probability": frequency_at_target(values),
        "target_probability": TARGET_PROBABILITY,
        "frequency_divisible_by_0_05": frequency_divisible_by(values),
        "within_market_variance": within_market_variance(results, arm),
        "between_market_variance": between_market_variance(results, arm),
        "correlation_evidence_count_vs_distance_from_half": pearson_correlation(
            evidence_count_list, distance_from_half
        ),
        "correlation_evidence_overlap_vs_concentration": pearson_correlation(overlap_list, concentration_list),
        "per_market": per_market,
    }


def reproduction_check(
    results: list[dict[str, Any]], arm: str, original_cluster_slugs: set[str], target: float = TARGET_PROBABILITY
) -> dict[str, Any]:
    """Per market: how many of its 5 repetitions landed at the target probability, and whether
    that market is one of the original 9 that clustered in job_run_id=21."""
    per_market = per_market_stats(results, arm)
    market_hits = {
        slug: {
            "reps_at_target": stat["count_at_target"],
            "total_reps": len(stat["values"]),
            "was_in_original_cluster": slug in original_cluster_slugs,
        }
        for slug, stat in per_market.items()
    }
    majority_reproduced = {
        slug
        for slug, hit in market_hits.items()
        if hit["total_reps"] > 0 and hit["reps_at_target"] / hit["total_reps"] >= 0.5
    }
    return {
        "per_market": market_hits,
        "markets_reproducing_majority_at_target": sorted(majority_reproduced),
        "overlap_with_original_cluster": sorted(majority_reproduced & original_cluster_slugs),
        "original_cluster_size": len(original_cluster_slugs),
        "reproduced_count": len(majority_reproduced & original_cluster_slugs),
    }


def group_comparison(results: list[dict[str, Any]], arm: str, cluster_slugs: set[str], other_slugs: set[str]) -> dict:
    per_market = per_market_stats(results, arm)
    cluster_means = [per_market[s]["mean"] for s in cluster_slugs if s in per_market and per_market[s]["mean"] is not None]
    other_means = [per_market[s]["mean"] for s in other_slugs if s in per_market and per_market[s]["mean"] is not None]
    cluster_stdevs = [per_market[s]["stdev"] for s in cluster_slugs if s in per_market and per_market[s]["stdev"] is not None]
    other_stdevs = [per_market[s]["stdev"] for s in other_slugs if s in per_market and per_market[s]["stdev"] is not None]
    return {
        "cluster_group_mean_of_means": _mean(cluster_means) if cluster_means else None,
        "other_group_mean_of_means": _mean(other_means) if other_means else None,
        "cluster_group_mean_within_market_stdev": _mean(cluster_stdevs) if cluster_stdevs else None,
        "other_group_mean_within_market_stdev": _mean(other_stdevs) if other_stdevs else None,
    }


def build_report(
    experiment: dict[str, Any], frozen_inputs: list[dict[str, Any]], original_cluster_slugs: set[str]
) -> dict[str, Any]:
    results = experiment["results"]
    arms = sorted({r["arm"] for r in results})
    all_slugs = set(markets_in_arm(results, arms[0])) if arms else set()
    other_slugs = all_slugs - original_cluster_slugs

    return {
        "arms_analyzed": arms,
        "market_count": len(all_slugs),
        "original_cluster_slugs": sorted(original_cluster_slugs),
        "escaped_slugs": sorted(other_slugs),
        "by_arm": {
            arm: {
                **arm_summary(results, arm, frozen_inputs),
                "reproduction": reproduction_check(results, arm, original_cluster_slugs),
                "group_comparison": group_comparison(results, arm, original_cluster_slugs, other_slugs),
            }
            for arm in arms
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Analyze a shadow experiment results file")
    parser.add_argument("--results", type=Path, required=True)
    parser.add_argument("--frozen-inputs", type=Path, required=True)
    parser.add_argument("--original-cluster-slugs", type=str, required=True, help="Comma-separated market slugs")
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()

    experiment = load_json(args.results)
    frozen_report = load_json(args.frozen_inputs)
    frozen_inputs: list[dict[str, Any]] = frozen_report.get("forecasts", frozen_report)
    original_cluster_slugs = {s.strip() for s in args.original_cluster_slugs.split(",") if s.strip()}

    report = build_report(experiment, frozen_inputs, original_cluster_slugs)
    payload = json.dumps(report, indent=2, default=str)
    if args.output:
        args.output.write_text(payload)
    else:
        print(payload)


if __name__ == "__main__":
    main()
