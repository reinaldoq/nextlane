-- Mission Control: allow the full set of terminal run outcomes on agent_runs.status.
-- The initial mission_control migration's inline CHECK omitted `cannot_reproduce`
-- (enforced-repro triage) and `review_rejected`, so finish_run's PATCH would be
-- rejected for those outcomes. Recreate the constraint with every outcome the
-- loop can journal.
alter table agent_runs drop constraint if exists agent_runs_status_check;
alter table agent_runs add constraint agent_runs_status_check check (
  status in (
    'running', 'pr_opened', 'gate_failed', 'no_changes',
    'timeout', 'error', 'completed_no_pr', 'cannot_reproduce', 'review_rejected'
  )
);
