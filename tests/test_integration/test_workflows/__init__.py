"""
Shekel Budget App -- End-to-End Workflow Tests

Tests multi-step workflows that span services and routes:
  - Salary profile → income transactions in grid
  - Template + recurrence → transactions in correct periods
  - Transfer template → balance calculator includes effects
  - Credit workflow → payback in next period → balance unaffected
  - Anchor balance change → downstream balances recalculate
  - Carry forward → projected items moved to target period
"""
