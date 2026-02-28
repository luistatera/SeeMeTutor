"""
Latency instrumentation helpers.

Provides runtime state + metric aggregators for latency-sensitive events:
- response_start
- interruption_stop
- turn_to_turn
- first_byte
"""

from __future__ import annotations

import math
import time
from typing import Any


LATENCY_BUDGETS: dict[str, dict[str, int]] = {
    "response_start": {"target_ms": 500, "alert_ms": 800},
    "interruption_stop": {"target_ms": 200, "alert_ms": 400},
    "turn_to_turn": {"target_ms": 1500, "alert_ms": 2500},
    "first_byte": {"target_ms": 3000, "alert_ms": 5000},
}

LATENCY_METRIC_ORDER = (
    "response_start",
    "interruption_stop",
    "turn_to_turn",
    "first_byte",
)


class LatencyStats:
    """Maintains running statistics for one latency metric."""

    def __init__(self, name: str, target_ms: float, alert_ms: float):
        self.name = name
        self.target_ms = float(target_ms)
        self.alert_ms = float(alert_ms)
        self.values: list[float] = []

    def record(self, value_ms: float) -> dict[str, Any]:
        """Record a measurement and return current aggregate stats."""
        self.values.append(float(value_ms))
        return self.stats()

    def stats(self) -> dict[str, Any]:
        """Return aggregate stats for this metric."""
        if not self.values:
            return {
                "name": self.name,
                "count": 0,
                "current": 0,
                "avg": 0,
                "min": 0,
                "max": 0,
                "p95": 0,
                "target_ms": round(self.target_ms),
                "alert_ms": round(self.alert_ms),
            }

        sorted_vals = sorted(self.values)
        count = len(sorted_vals)
        p95_idx = max(0, int(math.ceil(count * 0.95)) - 1)
        return {
            "name": self.name,
            "count": count,
            "current": round(self.values[-1]),
            "avg": round(sum(self.values) / count),
            "min": round(sorted_vals[0]),
            "max": round(sorted_vals[-1]),
            "p95": round(sorted_vals[p95_idx]),
            "target_ms": round(self.target_ms),
            "alert_ms": round(self.alert_ms),
        }

    def is_alert(self, value_ms: float) -> bool:
        """Return True when measurement exceeds alert threshold."""
        return float(value_ms) > self.alert_ms


def init_latency_state(session_start_at: float | None = None) -> dict:
    """Return latency runtime keys to merge into runtime_state."""
    trackers = {
        metric: LatencyStats(
            metric,
            LATENCY_BUDGETS[metric]["target_ms"],
            LATENCY_BUDGETS[metric]["alert_ms"],
        )
        for metric in LATENCY_METRIC_ORDER
    }
    return {
        "latency_trackers": trackers,
        "latency_alerts_total": 0,
        "latency_session_start_at": float(session_start_at or time.time()),
        "latency_last_audio_in_at": 0.0,
        "latency_last_student_transcript_at": 0.0,
        "latency_last_turn_complete_at": 0.0,
        "latency_last_barge_in_at": 0.0,
        "latency_first_byte_recorded": False,
        "latency_first_audio_out_at": 0.0,
    }


def record_latency_metric(runtime_state: dict, metric: str, value_ms: float) -> dict[str, Any] | None:
    """Record latency metric and return event payload; returns None when unknown."""
    trackers = runtime_state.get("latency_trackers")
    if not isinstance(trackers, dict):
        return None

    tracker = trackers.get(metric)
    if not isinstance(tracker, LatencyStats):
        return None

    safe_value = max(0.0, float(value_ms))
    stats = tracker.record(safe_value)
    is_alert = tracker.is_alert(safe_value)
    if is_alert:
        runtime_state["latency_alerts_total"] = int(
            runtime_state.get("latency_alerts_total", 0)
        ) + 1

    return {
        "metric": metric,
        "value_ms": round(safe_value),
        "stats": stats,
        "is_alert": is_alert,
        "alerts_total": int(runtime_state.get("latency_alerts_total", 0)),
    }


def build_latency_report(runtime_state: dict, turns: int = 0) -> dict[str, Any]:
    """Build report payload for `latency_report` websocket messages."""
    trackers = runtime_state.get("latency_trackers")
    metrics: dict[str, Any] = {}
    if isinstance(trackers, dict):
        for metric in LATENCY_METRIC_ORDER:
            tracker = trackers.get(metric)
            if isinstance(tracker, LatencyStats):
                metrics[metric] = tracker.stats()

    return {
        "metrics": metrics,
        "turns": int(turns),
        "alerts": int(runtime_state.get("latency_alerts_total", 0)),
    }


def format_latency_summary(runtime_state: dict, turns: int = 0) -> str:
    """Return a concise human-readable session latency summary."""
    report = build_latency_report(runtime_state, turns=turns)
    duration_s = round(time.time() - float(runtime_state.get("latency_session_start_at", 0.0)))
    lines = [
        f"LATENCY SUMMARY ({duration_s}s, {int(report['turns'])} turns, {int(report['alerts'])} alerts):",
        "  Metric                 Count     Avg     P95     Min     Max  Target   Alert",
        "  --------------------   -----   -----   -----   -----   -----  ------  ------",
    ]

    for metric in LATENCY_METRIC_ORDER:
        stats = report["metrics"].get(metric)
        if not isinstance(stats, dict) or int(stats.get("count", 0)) <= 0:
            lines.append(f"  {metric:<20}     -- (no data)")
            continue
        lines.append(
            "  "
            f"{metric:<20} {int(stats['count']):>5} "
            f"{int(stats['avg']):>6}ms {int(stats['p95']):>6}ms "
            f"{int(stats['min']):>6}ms {int(stats['max']):>6}ms "
            f"{int(stats['target_ms']):>6}ms {int(stats['alert_ms']):>6}ms"
        )

    return "\n".join(lines)
