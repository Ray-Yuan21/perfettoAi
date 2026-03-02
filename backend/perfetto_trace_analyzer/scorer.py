"""Performance scoring engine."""

from __future__ import annotations

from typing import Any

from .models import CategoryReport, PerformanceScore, ScoringConfig

_SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3}


class PerformanceScorer:
    """Computes weighted overall performance score from category reports."""

    def __init__(self, config: ScoringConfig):
        self.default_weights = dict(config.weights)

    def compute_score(self, reports: list[CategoryReport]) -> PerformanceScore:
        """Compute overall score from individual category reports."""
        scored_reports = {
            r.analyzer_name: r for r in reports if r.status == "success" and r.score is not None
        }

        if not scored_reports:
            all_issues = []
            for r in reports:
                all_issues.extend(r.issues)
            return PerformanceScore(
                overall=0.0,
                category_scores={},
                weights_used={},
                ranked_issues=rank_issues(all_issues),
            )

        active = list(scored_reports.keys())
        weights = self._redistribute_weights(active)

        category_scores: dict[str, float] = {}
        overall = 0.0
        for name, weight in weights.items():
            report = scored_reports[name]
            score = max(0.0, min(100.0, report.score))
            category_scores[name] = score
            overall += score * weight

        overall = max(0.0, min(100.0, overall))

        all_issues: list[dict[str, Any]] = []
        for r in reports:
            all_issues.extend(r.issues)

        return PerformanceScore(
            overall=round(overall, 1),
            category_scores=category_scores,
            weights_used=weights,
            ranked_issues=rank_issues(all_issues),
        )

    def _redistribute_weights(self, active_categories: list[str]) -> dict[str, float]:
        """Redistribute weights when some categories are skipped.

        Maintains the relative proportions of active categories while
        ensuring the total sums to 1.0.
        """
        active_weights = {
            k: v for k, v in self.default_weights.items() if k in active_categories
        }

        total = sum(active_weights.values())
        if total == 0:
            # Equal distribution fallback
            n = len(active_categories)
            return {k: 1.0 / n for k in active_categories}

        return {k: v / total for k, v in active_weights.items()}


def rank_issues(issues: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Deduplicate and sort issues by severity: critical > high > medium > low."""
    seen: set[str] = set()
    unique: list[dict[str, Any]] = []
    for iss in issues:
        uid = f"{iss.get('severity', 'low')}:{iss.get('description', '')}"
        if uid not in seen:
            seen.add(uid)
            unique.append(iss)
    return sorted(
        unique,
        key=lambda x: _SEVERITY_ORDER.get(x.get("severity", "low"), 99),
    )
