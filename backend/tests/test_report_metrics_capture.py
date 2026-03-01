"""Tests for telemetry capture additions in SessionReport."""

from test_report import SessionReport


def test_checkpoint_attempt_skip_and_success_metrics_are_captured():
    report = SessionReport("session-m1", "student-1")
    report.record_backlog_sent()

    report.record_memory_checkpoint_attempt(reason="interval_turn")
    report.record_memory_checkpoint_skipped(reason="interval_guardrail")
    report.record_memory_checkpoint_attempt(reason="session_end")
    report.record_memory_checkpoint(saved_cells=3, reason="session_end")

    report.finalize("student_ended")

    memory = report.data["memory"]
    derived = report.data["prd_scorecard"]["derived_metrics"]

    assert memory["checkpoint_attempts"] == 2
    assert memory["checkpoint_skipped"] == 1
    assert memory["checkpoints_saved"] == 1
    assert memory["checkpoint_reasons"]["session_end"] == 1
    assert memory["checkpoint_skip_reasons"]["interval_guardrail"] == 1
    assert derived["memory_checkpoint_success_rate_percent"] == 50.0


def test_run_config_and_retry_backoff_metrics_are_captured():
    report = SessionReport("session-m2", "student-2")
    report.record_backlog_sent()
    report.record_run_config(
        {
            "compression_enabled": True,
            "compression_field": "context_window_compression",
            "resumption_enabled": True,
            "resumption_field": "session_resumption",
        },
        resumption_requested=True,
    )
    report.record_stream_retry_attempt(1, "temporary stream error")
    report.record_stream_retry_backoff(1, 0.65)
    report.record_stream_reconnect_success(1)
    report.record_context_compression(34000, 32000, target_tokens=16000)

    report.finalize("student_ended")

    run_cfg = report.data["run_config"]
    resilience = report.data["resilience"]
    compression = report.data["compression"]
    derived = report.data["prd_scorecard"]["derived_metrics"]

    assert run_cfg["compression_enabled"] is True
    assert run_cfg["resumption_requested"] is True
    assert resilience["retry_backoff_seconds"] == [0.65]
    assert compression["target_tokens"] == 16000
    assert derived["stream_retry_success_rate_percent"] == 100.0
    assert derived["stream_retry_backoff_avg_seconds"] == 0.65


def test_memory_recall_checks_capture_zero_selection():
    report = SessionReport("session-m3", "student-3")
    report.record_backlog_sent()
    report.record_memory_recall_applied(selected_count=0, token_estimate=0, candidate_count=5)

    report.finalize("student_ended")

    memory = report.data["memory"]
    derived = report.data["prd_scorecard"]["derived_metrics"]

    assert memory["recall_checks"] == 1
    assert memory["recalls_applied"] == 0
    assert memory["recall_candidates_total"] == 5
    assert memory["recall_selected_total"] == 0
    assert derived["memory_recall_avg_tokens"] == 0.0
