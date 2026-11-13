"""The write door records a statement once, and refuses what it cannot trust.

Plan step **bank_import:X-f6a-1**, ruling **R-FP**.  Three properties are what
this leaf is FOR, and all three are graded here:

* **it records what the bank said** -- the lines, with the bank's own posted day;
* **it is idempotent** -- re-importing an overlapping span records the ACT and
  no duplicate lines, which is R-FP's "re-importing a file cannot duplicate"
  read as a property of the door rather than of a key;
* **it moves NO money** -- no transaction, purchase, status or balance is
  touched, which is what makes this leaf separable from the matching that
  follows it.

The refusal tests are FIRING CONTROLS: an account mismatch, a file claimed by
another account, and a restated line are all states no ordinary use produces
(measured: 0 restatements across two real exports twelve days apart), so each
one is planted and its refusal asserted by type.
"""
