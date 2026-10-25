---
name: charter
description: >
  Josh's standing work brief for executing a task or plan step: trace before
  claiming, verify the plan against the code, decompose across sessions, and
  run a neutral adversarial review before any commit. Invoke at the start of
  any implementation work.
---

# The work charter

CLAUDE.md's Rules, Design doctrine and Multi-session operation sections govern this task; re-read
them now if this session has not. Then hold the work to this brief:

1. **Trace before you build.** Read and trace every code path you will change. Do NOT guess and do
   NOT assume: if you cannot cite the file and line, you cannot claim it.
2. **Trust but verify the plan.** Check the step's specification against the code as committed
   before building it; planning documents lag the code. If the plan and the code disagree, stop and
   report the disagreement rather than building either version.
3. **Improve or surface.** Where the work exposes a chance to make the code more DRY, SOLID,
   normalized, robust, maintainable, future-proof, or financially correct: take it if in scope,
   report it as an opportunity if not. Name the fences the right design would delete.
4. **Decompose.** A multi-leaf step spans sessions: stop at the first leaf boundary and hand off
   rather than pushing on degraded. Suggest a decomposition whenever it would help. At the
   boundary, run `/handoff`.
5. **Adversarial review before commit.** A neutral fresh subagent reviews the design and the diff;
   it grades the fix's own claims about itself hardest. Findings are fixed or reported, never waved
   through. A card's spec is its body in the tracker, and an edit to it counts the moment it is
   saved (R-BAL174), so the review also reads every edit since the work started:
   `python -m tools.quill.quill spec-history plan#N --since card/N` is handed to the reviewer, and a
   spec change the code does not answer is a finding.
6. **Coordinate.** When peer sessions are active: PRs and merges go through the coordinator
   session; pushing a branch to back up work is fine. Suite runs need no slot -- each gets its own
   cluster -- but the wrapper names any peer run it can see, and a gating run of yours slows theirs.
7. **Ask.** An unanswered design question is a STOP, not a fork to take unilaterally. Ask questions
   if anything is unclear.
