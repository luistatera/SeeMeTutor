# Main PRD Metrics (Unified Scorecard)

Source PRDs reviewed:
- `pocs/01_interruption/prd.md`
- `pocs/02_proactive_vision/prd.md`
- `pocs/03_multilingual/prd.md`
- `pocs/04_whiteboard_sync/prd.md`
- `pocs/05_search_grounding/prd.md`
- `pocs/06_session_resilience/prd.md`
- `pocs/07_latency_instrumentation_and_budget/prd.md`
- `pocs/09_safety_scope_guardrails/prd.md`
- `pocs/10_screen_share_toggle/prd.md`
- `pocs/post-event/00_onboarding/prd.md`
- `pocs/post-event/08_tool_action_moment/prd.md`
- `pocs/post-event/11_idle_orchestration/prd.md`
- `pocs/post-event/12_final_student_report/prd.md`
- `pocs/post-event/13_memory_management/prd.md`
- `pocs/post-event/temp_flow_rehearsal/prd.md`

## Goal

Every test session now writes a JSON with:

1. Raw runtime metrics (audio/video/tools/latency/language/guardrails/etc.)
2. Per-POC checks (`pass` / `fail` / `not_tested`)
3. Unified product summary (`auto_pass_rate_percent`, POC status counts)

This is generated in `backend/test_report.py` under:

- `prd_scorecard.pocs.<poc_id>.checks[]`
- `prd_scorecard.summary`
- `prd_scorecard.derived_metrics`

## JSON Output Contract

When a session ends, the report in `backend/test_results/*.json` includes:

- `latency.events`, `latency.latest_report`, `latency.reports`
- `language.events`, `language.latest_metric`
- `resilience.stream_retry_attempts|successes|failures`
- `whiteboard.delivery_latency_ms`, sync mode counters
- `guardrails.prompt_injections`
- `prd_scorecard` (main combined evaluation)

## Per-POC Metric Mapping (Auto Checks)

| POC | Key checks in JSON |
|---|---|
| `poc_00_onboarding` | context injection proxy (`connection.backlog_context_sent`) |
| `poc_01_interruption` | interruption p95 latency, student-heard proxy, interruption observed |
| `poc_02_proactive_vision` | proactive trigger count, question ratio, max question streak |
| `poc_03_multilingual` | language purity, guided adherence, fallback latency, L2 ratio |
| `poc_04_whiteboard_sync` | notes created, whiteboard delivery p95, audio continuity proxy |
| `poc_05_search_grounding` | grounding events, citation render rate, query logging |
| `poc_06_session_resilience` | reconnect success rate, retry cap |
| `poc_07_latency` | response/interruption/turn-gap/first-byte latency budgets |
| `poc_08_tool_action_moment` | placeholder (`not_tested` until post-event pipeline exists) |
| `poc_09_safety_guardrails` | answer leak rate, socratic compliance, prompt-injection detection |
| `poc_10_screen_share_toggle` | source switches, continuity proxy, stop-sharing path |
| `poc_11_idle_orchestration` | idle nag limits, away/resume flow |
| `poc_12_final_student_report` | placeholder (`not_tested` until post-event pipeline exists) |
| `poc_13_memory_management` | placeholder (`not_tested` until post-event pipeline exists) |
| `poc_99_hero_flow_rehearsal` | integrated checklist (proactive, whiteboard, interruption, grounding, action moment, reconnect) |

## Product-Level Success View

Use:

- `prd_scorecard.summary.auto_pass_rate_percent`
- `prd_scorecard.summary.poc_status_counts`
- `prd_scorecard.pocs.poc_99_hero_flow_rehearsal.checklist_completed`

Target for hackathon rehearsal runs:

1. `auto_pass_rate_percent >= 85`
2. `poc_99_hero_flow_rehearsal.checklist_completed == 6`
3. No `fail` in POCs 01, 02, 03, 04, 05, 07, 09, 10

## Notes

- Some post-event POCs (08/12/13) remain `not_tested` by design until those pipelines exist in BE.
- `not_tested` means missing runtime evidence in that session, not necessarily broken functionality.
