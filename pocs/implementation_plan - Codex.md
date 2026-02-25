Read complete: all `prd.md` and `rules.md` under `/pocs` (no code changes made).

**What needs improvement first (organization + transferability)**

1. PoCs are built as full standalone apps, so core logic is duplicated instead of isolated.
Evidence: repeated websocket/app plumbing across many PoCs (`serve_index`, `health`, `websocket_endpoint`, `_forward_*`) in [01 main](/Users/luisguimaraes/Projects/SeeMeTutor/pocs/01_interruption/main.py#L159), [02 main](/Users/luisguimaraes/Projects/SeeMeTutor/pocs/02_proactive_vision/main.py#L310), [03 main](/Users/luisguimaraes/Projects/SeeMeTutor/pocs/03_multilingual/main.py#L717), [99 main](/Users/luisguimaraes/Projects/SeeMeTutor/pocs/99_full_hero_flow_rehearsal/main.py#L582).
Improve: extract each PoC’s core behavior into capability modules; keep one shared runtime host.

2. WebSocket protocol is drifting between PoCs, which will break merge reliability.
Evidence: `camera_frame/screen_frame` in [POC 10 frontend](/Users/luisguimaraes/Projects/SeeMeTutor/pocs/10_TODO_screen_share_toggle/index.html#L1023) and [POC 10 backend](/Users/luisguimaraes/Projects/SeeMeTutor/pocs/10_TODO_screen_share_toggle/main.py#L490), while main app expects `video` in [backend/main.py](/Users/luisguimaraes/Projects/SeeMeTutor/backend/main.py#L1322).
Improve: define one canonical event schema and adapters.

3. Demo/test scaffolding is mixed into core behavior paths.
Evidence: reconnect simulation/test signals in [POC 06 frontend](/Users/luisguimaraes/Projects/SeeMeTutor/pocs/06_session_resilience/index.html#L626) and [POC 06 backend](/Users/luisguimaraes/Projects/SeeMeTutor/pocs/06_session_resilience/main.py#L666), plus monolithic “all features together” prompt/orchestration in [POC 99 main](/Users/luisguimaraes/Projects/SeeMeTutor/pocs/99_full_hero_flow_rehearsal/main.py#L101).
Improve: split `core capability` vs `demo harness` vs `debug tools`.

4. Rules/docs are inconsistent across PoCs.
Evidence: POC 99 still says “RAG” in [99 rules](/Users/luisguimaraes/Projects/SeeMeTutor/pocs/99_full_hero_flow_rehearsal/rules.md#L3), while POC 05 explicitly defines Google Search grounding in [05 rules](/Users/luisguimaraes/Projects/SeeMeTutor/pocs/05_search_grounding/rules.md#L1).
Improve: one normalized PoC spec template + terminology lock.

5. Post-event PoCs are not uniformly implementation-ready.
Evidence: PRD-only folders for [00 onboarding](/Users/luisguimaraes/Projects/SeeMeTutor/pocs/post-event/00_onboarding/prd.md), [12 final report](/Users/luisguimaraes/Projects/SeeMeTutor/pocs/post-event/12_final_student_report/prd.md), [13 memory](/Users/luisguimaraes/Projects/SeeMeTutor/pocs/post-event/13_memory_management/prd.md) (no `rules.md`/`main.py` yet).
Improve: add `rules.md` + integration contract before coding these.

6. POC 99 should be an integration rehearsal only, not a source of truth for feature code.
Evidence: it explicitly inlines sections “from POC 02/04/05” in [99 main](/Users/luisguimaraes/Projects/SeeMeTutor/pocs/99_full_hero_flow_rehearsal/main.py#L172).
Improve: treat POC 99 as acceptance runner over reusable modules.

**Where each PoC should sit in main app session flow**

1. Pre-session: P00 onboarding, P03 language contract, P13 memory recall injection.
2. Live ingress (browser -> backend): P01 interruption, P06 resilience, P11 idle/away, P10 input-source switching.
3. Live reasoning/orchestration: P02 proactive vision, P09 guardrails, P05 grounding.
4. Live egress (backend -> UI): P04 whiteboard sync, P07 latency instrumentation.
5. Session end/post-session: P08 A2A summary trigger, P12 final report assembly, P13 memory ingestion.

Main app anchor points for this wiring already exist at [websocket entry](/Users/luisguimaraes/Projects/SeeMeTutor/backend/main.py#L622), [ingress loop](/Users/luisguimaraes/Projects/SeeMeTutor/backend/main.py#L1144), [egress loop](/Users/luisguimaraes/Projects/SeeMeTutor/backend/main.py#L1355), [idle orchestrator](/Users/luisguimaraes/Projects/SeeMeTutor/backend/main.py#L951), [whiteboard queue infra](/Users/luisguimaraes/Projects/SeeMeTutor/backend/gemini_live.py#L74), [write_notes tool](/Users/luisguimaraes/Projects/SeeMeTutor/backend/tutor_agent/agent.py#L626), [google search tool](/Users/luisguimaraes/Projects/SeeMeTutor/backend/tutor_agent/agent.py#L893).

Next step I recommend: implement a capability registry + canonical protocol first, then port PoCs one-by-one without copying full PoC apps.
