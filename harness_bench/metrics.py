"""Pass@k and pass^k metric helpers for benchmark runs."""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from math import comb
from typing import Literal, Protocol


class RunLike(Protocol):
    """Minimal shape needed from a task run."""

    task_id: str
    passed: bool


MetricKind = Literal["pass@", "pass^"]


@dataclass(frozen=True)
class PassMetric:
    """A benchmark-level pass metric averaged across tasks."""

    kind: MetricKind
    k: int
    value: float
    task_count: int

    @property
    def label(self) -> str:
        return f"{self.kind}{self.k}"


def pass_at_k(num_samples: int, num_correct: int, k: int) -> float:
    """Estimate probability that at least one of `k` samples passes."""
    _validate_counts(num_samples, num_correct, k)
    if num_correct == 0:
        return 0.0
    if num_samples - num_correct < k:
        return 1.0
    return 1.0 - (comb(num_samples - num_correct, k) / comb(num_samples, k))


def pass_hat_k(num_samples: int, num_correct: int, k: int) -> float:
    """Estimate probability that all `k` samples pass (`pass^k`)."""
    _validate_counts(num_samples, num_correct, k)
    if num_correct < k:
        return 0.0
    return comb(num_correct, k) / comb(num_samples, k)


def compute_pass_metrics(
    results: Iterable[RunLike],
    *,
    pass_at_ks: Sequence[int] = (),
    pass_hat_ks: Sequence[int] = (),
) -> list[PassMetric]:
    """Compute requested pass metrics from one or more attempts per task.

    `pass@k` is the probability that at least one of `k` independent attempts
    solves a task. `pass^k` is the stricter probability that all `k` attempts
    solve it. Both are averaged over tasks.
    """
    grouped = _group_passes(results)
    metrics: list[PassMetric] = []
    for k in _dedupe_positive(pass_at_ks):
        metrics.append(_compute_group_metric(grouped, "pass@", k))
    for k in _dedupe_positive(pass_hat_ks):
        metrics.append(_compute_group_metric(grouped, "pass^", k))
    return metrics


def default_metric_ks(num_attempts: int) -> tuple[tuple[int, ...], tuple[int, ...]]:
    """Default metrics for CLI summaries."""
    if num_attempts < 1:
        raise ValueError("num_attempts must be positive")
    ks = tuple(range(1, num_attempts + 1))
    return ks, ks


def format_metric(metric: PassMetric) -> str:
    """Format a pass metric as a concise percentage line."""
    return f"{metric.label}: {metric.value * 100:.1f}%"


def _compute_group_metric(
    grouped: OrderedDict[str, list[bool]],
    kind: MetricKind,
    k: int,
) -> PassMetric:
    values: list[float] = []
    for task_id, passes in grouped.items():
        num_samples = len(passes)
        num_correct = sum(passes)
        if k > num_samples:
            raise ValueError(
                f"{kind}{k} requires at least {k} attempts for {task_id}; "
                f"got {num_samples}"
            )
        if kind == "pass@":
            values.append(pass_at_k(num_samples, num_correct, k))
        else:
            values.append(pass_hat_k(num_samples, num_correct, k))
    value = sum(values) / len(values) if values else 0.0
    return PassMetric(kind=kind, k=k, value=value, task_count=len(values))


def _group_passes(results: Iterable[RunLike]) -> OrderedDict[str, list[bool]]:
    grouped: OrderedDict[str, list[bool]] = OrderedDict()
    for result in results:
        grouped.setdefault(result.task_id, []).append(result.passed)
    return grouped


def _dedupe_positive(values: Sequence[int]) -> tuple[int, ...]:
    seen: set[int] = set()
    deduped: list[int] = []
    for value in values:
        if value < 1:
            raise ValueError("metric k must be positive")
        if value not in seen:
            deduped.append(value)
            seen.add(value)
    return tuple(deduped)


def _validate_counts(num_samples: int, num_correct: int, k: int) -> None:
    if num_samples < 1:
        raise ValueError("num_samples must be positive")
    if k < 1:
        raise ValueError("k must be positive")
    if k > num_samples:
        raise ValueError("k cannot exceed num_samples")
    if num_correct < 0 or num_correct > num_samples:
        raise ValueError("num_correct must be between 0 and num_samples")
