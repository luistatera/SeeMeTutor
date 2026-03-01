# Session Orchestration + Mastery Contracts (Steps 1-2)

## Scope

This document defines the exact data contracts for:

1. Session chooser + setup/capture/planning gates.
2. Deterministic mastery validation (hybrid: tutor proposes, backend decides).

It is designed to extend the current model without breaking existing `students/{id}/tracks/{track_id}/topics/{topic_id}` progress storage.

## Conventions

- IDs are lowercase slugs or UUIDs.
- Timestamps use Unix epoch seconds as `float` (same pattern already used in backend).
- No raw audio/video/image payloads are persisted.
- `student_id` in storage is normalized lowercase.

## Firestore Schema

### 1) `students/{student_id}` (extended)

Required existing fields remain unchanged. Add:

```json
{
  "session_defaults": {
    "preferred_setup_source": "camera",
    "auto_generate_milestones": true
  },
  "mastery_policy": {
    "policy_version": "v1",
    "min_mastered_milestones_ratio": 1.0,
    "min_independent_correct_per_milestone": 2,
    "max_heavy_hint_ratio": 0.4,
    "require_final_transfer_check": true,
    "min_final_transfer_score": 0.8
  },
  "updated_at": 0.0
}
```

Notes:

- `mastery_policy` is optional per student; backend uses defaults when missing.
- This policy is for deterministic approval only; tutor text style remains separate.

### 2) `sessions/{session_id}` (extended canonical session doc)

```json
{
  "session_id": "uuid",
  "student_id": "student-123",
  "track_id": "general-track",
  "topic_id": "fractions",
  "topic_title": "Fractions",
  "status": "open",
  "phase": "setup",

  "started_at": 0.0,
  "updated_at": 0.0,
  "closed_at": null,
  "ended_reason": null,
  "duration_seconds": null,

  "setup": {
    "mode": "new",
    "session_goal": "Master simplifying fractions from worksheet page 23",
    "student_context_text": "Using school worksheet chapter 4",
    "resource_refs": [],
    "confirmed": false,
    "confirmed_at": null
  },

  "capture": {
    "source": "camera",
    "summary_text": "",
    "artifacts_count": 0,
    "confirmed": false,
    "confirmed_at": null
  },

  "planning": {
    "milestones_count": 0,
    "approved": false,
    "approved_at": null
  },

  "mastery": {
    "state": "not_started",
    "last_proposed_at": null,
    "last_evaluated_at": null,
    "last_outcome": null,
    "approved_at": null
  }
}
```

Enums:

- `status`: `open | closed`
- `phase`: `setup | capture | planning | active | review | closed`
- `setup.mode`: `new | resumed`
- `capture.source`: `camera | screen | mixed | none`
- `mastery.state`: `not_started | candidate | approved | rejected`

### 3) `sessions/{session_id}/milestones/{milestone_id}`

```json
{
  "milestone_id": "m1-identify-numerator-denominator",
  "title": "Identify numerator and denominator",
  "description": "Student identifies parts of a fraction in 3 examples",
  "order_index": 1,
  "status": "in_progress",
  "mastery_evidence_count": 0,
  "attempts_total": 0,
  "attempts_correct": 0,
  "attempts_independent_correct": 0,
  "attempts_heavy_hint": 0,
  "created_at": 0.0,
  "updated_at": 0.0
}
```

Enums:

- `status`: `not_started | in_progress | ready_for_check | mastered | blocked`

### 4) `sessions/{session_id}/attempts/{attempt_id}`

```json
{
  "attempt_id": "att-uuid",
  "milestone_id": "m1-identify-numerator-denominator",
  "attempt_type": "practice_check",
  "prompt_text": "What is the numerator in 7/9?",
  "student_response_text": "7",
  "evaluation": {
    "outcome": "correct",
    "score": 1.0,
    "independence_level": "independent",
    "hint_level": "none"
  },
  "created_at": 0.0
}
```

Enums:

- `attempt_type`: `practice_check | retrieval_check | transfer_check | recap_check`
- `evaluation.outcome`: `correct | partially_correct | incorrect | not_evaluable`
- `evaluation.independence_level`: `independent | light_hint | heavy_hint | model_led`
- `evaluation.hint_level`: `none | light | heavy`

### 5) `sessions/{session_id}/evidence/{evidence_id}`

```json
{
  "evidence_id": "ev-uuid",
  "type": "capture_summary",
  "source": "camera",
  "summary_text": "Page shows exercises 1-6 on equivalent fractions.",
  "grounding_urls": [],
  "created_at": 0.0
}
```

Enums:

- `type`: `capture_summary | grounding_source | tutor_note | student_question`

### 6) `sessions/{session_id}/mastery_evaluations/{evaluation_id}`

```json
{
  "evaluation_id": "me-uuid",
  "topic_id": "fractions",
  "proposed_by": "tutor_agent",
  "proposed_reason": "Student solved all worksheet items with minimal help.",
  "rubric_snapshot": {
    "mastered_milestones_ratio": 1.0,
    "required_mastered_milestones_ratio": 1.0,
    "independent_correct_min_per_milestone_ok": true,
    "heavy_hint_ratio": 0.2,
    "max_heavy_hint_ratio": 0.4,
    "final_transfer_required": true,
    "final_transfer_score": 1.0,
    "min_final_transfer_score": 0.8
  },
  "outcome": "approved",
  "gaps": [],
  "created_at": 0.0
}
```

Enums:

- `outcome`: `approved | rejected`

### 7) `sessions/{session_id}/events/{event_id}` (audit trail)

```json
{
  "event_id": "evt-uuid",
  "type": "phase_transition",
  "data": {
    "from": "capture",
    "to": "planning"
  },
  "created_at": 0.0
}
```

Enums:

- `type`: `phase_transition | setup_confirmed | capture_confirmed | milestones_approved | mastery_proposed | mastery_approved | mastery_rejected`

## Required Firestore Indexes

1. Collection: `sessions`
- Fields: `student_id` (ASC), `status` (ASC), `updated_at` (DESC)
- Use: session chooser (open/recent)

2. Collection: `sessions`
- Fields: `student_id` (ASC), `phase` (ASC), `updated_at` (DESC)
- Use: operations dashboards and recovery

3. Collection group: `milestones`
- Fields: `status` (ASC), `updated_at` (DESC)
- Use: analytics/debug views across sessions

4. Collection group: `mastery_evaluations`
- Fields: `topic_id` (ASC), `created_at` (DESC)
- Use: quality tracking per topic

## API Contracts (Step 1: Session Orchestration)

### GET `/api/profiles/{student_id}/sessions`

Query params:

- `status`: `open | closed | all` (default `open`)
- `limit`: int, default `20`, max `50`
- `cursor`: opaque pagination token (optional)

Response `200`:

```json
{
  "sessions": [
    {
      "session_id": "uuid",
      "topic_title": "Fractions",
      "status": "open",
      "phase": "active",
      "updated_at": 1730000000.0,
      "started_at": 1730000000.0
    }
  ],
  "next_cursor": null
}
```

Errors:

- `400`: invalid params
- `404`: unknown student

### POST `/api/profiles/{student_id}/sessions`

Creates a new session placeholder before WebSocket starts.

Request:

```json
{
  "track_id": "general-track",
  "topic_id": "fractions",
  "topic_title": "Fractions",
  "setup": {
    "session_goal": "Master exercises on page 23",
    "student_context_text": "Worksheet chapter 4",
    "resource_refs": []
  }
}
```

Response `201`:

```json
{
  "session_id": "uuid",
  "status": "open",
  "phase": "setup"
}
```

Errors:

- `400`: invalid payload
- `404`: unknown student

### PATCH `/api/sessions/{session_id}/setup`

Request:

```json
{
  "session_goal": "Master exercises on page 23",
  "student_context_text": "I am using chapter 4 worksheet",
  "resource_refs": ["resource-1"],
  "confirmed": true
}
```

Response `200`:

```json
{
  "session_id": "uuid",
  "phase": "capture",
  "setup": {
    "confirmed": true,
    "confirmed_at": 1730000000.0
  }
}
```

Behavior:

- If `confirmed=true`, backend transitions `phase: setup -> capture`.

### POST `/api/sessions/{session_id}/capture/confirm`

Request:

```json
{
  "source": "camera",
  "summary_text": "Exercises 1-6 on equivalent fractions",
  "confirmed": true
}
```

Response `200`:

```json
{
  "session_id": "uuid",
  "phase": "planning",
  "capture": {
    "confirmed": true,
    "confirmed_at": 1730000000.0
  }
}
```

Behavior:

- Stores a `capture_summary` evidence entry.
- If `confirmed=true`, backend transitions `phase: capture -> planning`.

### PUT `/api/sessions/{session_id}/milestones`

Upserts full milestone plan.

Request:

```json
{
  "milestones": [
    {
      "milestone_id": "m1-identify-numerator-denominator",
      "title": "Identify numerator and denominator",
      "description": "Student identifies parts in 3 examples",
      "order_index": 1
    },
    {
      "milestone_id": "m2-simplify-fractions",
      "title": "Simplify fractions correctly",
      "description": "Student simplifies 5 exercises with explanation",
      "order_index": 2
    }
  ],
  "approved": true
}
```

Response `200`:

```json
{
  "session_id": "uuid",
  "planning": {
    "milestones_count": 2,
    "approved": true,
    "approved_at": 1730000000.0
  },
  "phase": "active"
}
```

Behavior:

- If `approved=true`, backend transitions `phase: planning -> active`.

### POST `/api/sessions/{session_id}/attempts`

Request:

```json
{
  "milestone_id": "m2-simplify-fractions",
  "attempt_type": "practice_check",
  "prompt_text": "Simplify 12/18",
  "student_response_text": "2/3",
  "evaluation": {
    "outcome": "correct",
    "score": 1.0,
    "independence_level": "independent",
    "hint_level": "none"
  }
}
```

Response `201`:

```json
{
  "attempt_id": "att-uuid",
  "milestone_id": "m2-simplify-fractions"
}
```

Behavior:

- Atomically updates milestone counters:
  - `attempts_total`
  - `attempts_correct`
  - `attempts_independent_correct`
  - `attempts_heavy_hint`

### WebSocket handshake update

Endpoint remains `/ws`, but contract changes:

- Required query params:
  - `student_id`
  - `session_id`
  - `code` (if demo access enabled)

Reject when `session_id` missing or does not belong to `student_id`.

Server error payload:

```json
{
  "type": "error",
  "data": "Please select or create a session before starting."
}
```

## API Contracts (Step 2: Deterministic Mastery)

### POST `/api/sessions/{session_id}/mastery/propose`

Called by tutor tool when it believes topic is mastered.

Request:

```json
{
  "topic_id": "fractions",
  "proposed_reason": "Student solved all tasks and explained each step.",
  "candidate_milestone_ids": [
    "m1-identify-numerator-denominator",
    "m2-simplify-fractions"
  ]
}
```

Response `200`:

```json
{
  "outcome": "approved",
  "mastery_state": "approved",
  "rubric": {
    "mastered_milestones_ratio": 1.0,
    "required_mastered_milestones_ratio": 1.0,
    "independent_correct_min_per_milestone_ok": true,
    "heavy_hint_ratio": 0.2,
    "max_heavy_hint_ratio": 0.4,
    "final_transfer_required": true,
    "final_transfer_score": 1.0,
    "min_final_transfer_score": 0.8
  },
  "gaps": [],
  "next_action": "mark_topic_mastered"
}
```

Rejected example:

```json
{
  "outcome": "rejected",
  "mastery_state": "rejected",
  "rubric": {
    "mastered_milestones_ratio": 0.5,
    "required_mastered_milestones_ratio": 1.0,
    "independent_correct_min_per_milestone_ok": false,
    "heavy_hint_ratio": 0.7,
    "max_heavy_hint_ratio": 0.4,
    "final_transfer_required": true,
    "final_transfer_score": 0.5,
    "min_final_transfer_score": 0.8
  },
  "gaps": [
    "milestone_not_mastered:m2-simplify-fractions",
    "insufficient_independent_correct:m2-simplify-fractions",
    "heavy_hint_ratio_too_high",
    "final_transfer_below_threshold"
  ],
  "next_action": "assign_targeted_remediation"
}
```

Backend side effects on `approved`:

1. `sessions/{session_id}.mastery.state = approved`
2. `sessions/{session_id}.phase = review`
3. Update `students/{student_id}/tracks/{track_id}/topics/{topic_id}`:
   - `status = mastered`
   - `checkpoint_open = false`
   - `updated_at = now`
4. Append `sessions/{session_id}/progress` event with `status = mastery_approved`

Backend side effects on `rejected`:

1. `sessions/{session_id}.mastery.state = rejected`
2. Keep `phase = active`
3. Append `sessions/{session_id}/events` `type=mastery_rejected`

### GET `/api/sessions/{session_id}/mastery`

Response `200`:

```json
{
  "mastery_state": "rejected",
  "last_evaluation": {
    "outcome": "rejected",
    "gaps": [
      "insufficient_independent_correct:m2-simplify-fractions"
    ],
    "created_at": 1730000000.0
  }
}
```

## Deterministic Rubric (Backend)

Evaluation formula:

1. `mastered_milestones_ratio`
- `# milestones with status=mastered / total milestones`
- must be `>= min_mastered_milestones_ratio`

2. `independent_correct_min_per_milestone_ok`
- for each milestone: `attempts_independent_correct >= min_independent_correct_per_milestone`

3. `heavy_hint_ratio`
- `total heavy_hint attempts / total attempts`
- must be `<= max_heavy_hint_ratio`

4. `final_transfer_check`
- required when `require_final_transfer_check=true`
- uses latest `attempt_type=transfer_check` score
- must be `>= min_final_transfer_score`

All checks must pass for `approved`.

## Backward Compatibility

- Existing `sessions/{session_id}` fields remain valid.
- Existing `log_progress(..., "mastered")` should be replaced by:
  - `propose_mastery` tool -> `/mastery/propose` endpoint.
- Existing topic progress location remains authoritative:
  - `students/{student_id}/tracks/{track_id}/topics/{topic_id}`

## Non-goals for Steps 1-2

- Resource ingestion/indexing (NotebookLM-like flow) is Step 3.
- Language policy refactor is a separate stream; this contract is subject-agnostic.
