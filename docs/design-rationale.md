# Design Rationale — why the rails are built this way

The AI rails (`rails/`) are a **deterministic harness** that drives headless coding-agent sessions (Claude Code / Codex / Gemini) through a fixed pipeline. The design follows the 2024–2026 consensus from the leading labs and the strongest empirical work on agent reliability. This document grounds each major decision in that evidence.

## 1. Deterministic harness, agent-as-node ("code is the harness")
Anthropic ("Building Effective Agents") and OpenAI ("A Practical Guide to Building Agents") both prescribe deterministic-by-default orchestration: prefer predefined code paths; reserve autonomous agentic control only for genuinely open-ended steps. Our loop (worktree → build → gate → review → PR) is exactly this — fixed control flow in Python, with the model's freedom confined to inside a single "build" step.

## 2. The gate is a hard, blocking check — not an optimistic guardrail
Anthropic recommends programmatic "gate" checks on intermediate steps. OpenAI's SDK runs guardrails optimistically/concurrently — and their own issue tracker shows optimistic guardrails don't hard-stop. Our gate BLOCKS: a PR can never open on a red gate (single, verified call site).

## 3. Single-writer + isolated, diff-only cross-vendor review
Cognition/Devin: "writes stay single-threaded… the review agent only looks at the diff and re-discovers context." Our loop has one builder (single writer) and a read-only reviewer from the *other* engine that sees only the diff. (Open question the field hasn't measured: does cross-vendor review beat same-vendor? We test the hypothesis.)

## 4. Verification over trust: enforced red→green for bug fixes
The strongest empirical lever. TDFlow (EACL 2026) frames bug-fixing as "obtain a failing reproduction test, then make it pass," hits 93–94% on SWE-Bench, and concludes the bottleneck is *writing* the failing test, not fixing. JetBrains runs the same CI-as-harness pattern for its Junie agent. Our triage enforces this mechanically: the agent must produce a reproduction test that the harness RUNS and confirms FAILS before any fix is allowed, then confirms it PASSES with no regressions after — a machine-checked red→green proof, not a trusted claim. Honest caveat: TDFlow's 93–94% is the *oracle* condition (human-written tests); self-generated reproduction drops to ~68%, so writing a valid failing test is the hard part — which is exactly why we VERIFY the red state rather than trust it.

**What's machine-checked, and the honest remaining gap.** The proof enforces *three* facts: (a) phase 1's gate has a failing `pytest` step **and** a change under `tests/`/`web/e2e/` (a genuine failing test was added); (b) phase 2's *full* gate is green; and (c) the phase-1 reproduction test file(s) **survive** in the phase-2 diff — a fix session that deleted or reverted the repro test to make the suite green no longer earns `repro_confirmed` (the run still ships, since the gate is green, but honestly as `repro_confirmed=False`). This closes the "delete the repro test and the suite is still green" hole (`_changed_test_files` captured after phase 1, re-checked after phase 2). The *remaining* gap, stated plainly, is finer-grained: a phase-2 session that *weakens but keeps* the reproduction test (edits its body to a trivial assertion) would still pass both the full gate and the survival check. Fully closing that needs test-node-id-level pinning — capturing phase 1's failing node-ids and re-asserting those exact ids go red→green — which is deferred over parsing pytest node-ids from gate output. This is the same "valid-failing-test generation is the real bottleneck" ceiling the research names, and it's called out here rather than papered over.

## 5. Observability is instrumentation, not enforcement
Langfuse / LangSmith / Phoenix are retrospective — Langfuse's docs defer blocking to external guardrail libraries; the tracing layer "does not directly influence agent behavior during execution." So our journal + Mission Control OBSERVE and provide evidence/evals; they are deliberately NOT the mechanism that ensures steps. Enforcement is the harness + the gate + red→green.

## Failure modes we designed against
- Optimistic (non-blocking) guardrails → our gate hard-blocks.
- Monolithic long-context agents (weakest subtask caps success) → phases + bounded retries.
- Free-form agent swarms → single driver, isolated reviewer.
- Procedural "do TDD" prompting without the concrete test → we verify the actual failing test, not just instruct.

**Sources:** Anthropic, "Building Effective Agents"; OpenAI, "A Practical Guide to Building Agents"; Cognition, "Don't Build Multi-Agents"; TDFlow (arXiv 2510.23761, EACL 2026); "runtime observability vs enforcement" (arXiv 2603.00495); JetBrains, "Testing AI coding agents with TeamCity and SWE-bench"; Langfuse observability docs.
