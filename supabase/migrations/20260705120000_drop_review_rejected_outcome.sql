-- Drop the dead `review_rejected` value from the agent_runs.status CHECK.
-- The rails loop never journals it: on a green gate it always opens a PR with
-- the honest review verdict recorded (there is no terminal "rejected" outcome),
-- so it was an allowed-but-unused value. Keep the constraint in lockstep with
-- rails.journal.VALID_OUTCOMES. No existing row uses it, so the drop is safe.
alter table agent_runs drop constraint if exists agent_runs_status_check;
alter table agent_runs add constraint agent_runs_status_check check (
  status in (
    'running', 'pr_opened', 'gate_failed', 'no_changes',
    'timeout', 'error', 'completed_no_pr', 'cannot_reproduce'
  )
);
