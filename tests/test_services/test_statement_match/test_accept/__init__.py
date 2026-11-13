"""The ACCEPT door: what it refuses, and what it moves when it does not.

Plan step **bank_import:X-f6a-2**, rulings **R-FS**, **R-FP** and **R-FV**.
This is the only door in the arc that moves money, so every one of its
refusals is a FIRING CONTROL here -- written to fail if the refusal were
deleted, which is the standard ``docs/plans/verification.md`` sets and which
this project has twice measured the absence of.

**What the door promises, and what each class below pins:**

* a group's two sides SUM, or nothing is written and the difference is named;
* every member row takes the bank's day -- settling a Projected row and
  correcting a settled one;
* it does NOT write ``reconciled_by_id`` (ruling **R-FV**) and it RELEASES any
  link it moves a day out from under;
* an id outside the owner's own candidate set is refused, not skipped;
* a release restores the QUESTION and leaves the days alone.
"""
