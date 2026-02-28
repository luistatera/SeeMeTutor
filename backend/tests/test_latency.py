"""Unit tests for modules/latency.py."""

from modules.latency import (
    LATENCY_BUDGETS,
    LatencyStats,
    build_latency_report,
    format_latency_summary,
    init_latency_state,
    record_latency_metric,
)


class TestLatencyStats:
    def test_records_and_computes_stats(self):
        stat = LatencyStats("response_start", target_ms=500, alert_ms=800)
        stat.record(120)
        stat.record(240)
        stats = stat.record(360)

        assert stats["count"] == 3
        assert stats["min"] == 120
        assert stats["max"] == 360
        assert stats["avg"] == 240
        assert stats["p95"] == 360

    def test_alert_threshold(self):
        stat = LatencyStats("response_start", target_ms=500, alert_ms=800)
        assert stat.is_alert(900) is True
        assert stat.is_alert(500) is False


class TestLatencyModule:
    def test_init_latency_state_has_trackers(self):
        state = init_latency_state(session_start_at=123.0)
        assert "latency_trackers" in state
        assert set(state["latency_trackers"].keys()) == set(LATENCY_BUDGETS.keys())
        assert state["latency_alerts_total"] == 0

    def test_record_metric_updates_alert_counter(self):
        state = init_latency_state()
        payload = record_latency_metric(state, "response_start", 900)

        assert payload is not None
        assert payload["metric"] == "response_start"
        assert payload["is_alert"] is True
        assert state["latency_alerts_total"] == 1

    def test_unknown_metric_returns_none(self):
        state = init_latency_state()
        assert record_latency_metric(state, "unknown_metric", 100) is None

    def test_build_latency_report_structure(self):
        state = init_latency_state()
        record_latency_metric(state, "response_start", 250)
        report = build_latency_report(state, turns=2)

        assert report["turns"] == 2
        assert "response_start" in report["metrics"]
        assert report["metrics"]["response_start"]["count"] == 1

    def test_format_latency_summary_contains_headers(self):
        state = init_latency_state()
        record_latency_metric(state, "response_start", 250)
        summary = format_latency_summary(state, turns=1)

        assert "LATENCY SUMMARY" in summary
        assert "response_start" in summary
