"""
Shekel Budget App -- The app's books beside the BANK's own record.

Plan step **bank_import:X-f6e-2**, ruling **R-GF**.  The comparison is an
INSTRUMENT and never a gate, so nothing here refuses anything; what these tests
pin is that it SEES what it is for.

**The load-bearing case is ``TestATrueUpCannotHideADisagreement``**, and it is
the measurement the whole design turns on.  On the developer's own Checking
account, over the 149 days his statement and his records both cover, 35 days
carry a real disagreement between his rows and the bank's lines -- and ELEVEN of
them read as EXACT agreement in the balance difference, because a same-day
balance assertion cancels the error to the cent.  Among the eleven:

* 2026-06-02, ``-$943.41`` -- six ``CC Payback`` rows against a day his bank
  posted no line at all.  That is finding **N-337**, which this step is
  specified to detect, on a day whose two running balances are identical.
* 2026-07-31, ``-$2,090.47``; 2026-08-18, ``-$1,463.04``.
* 2026-05-21 and 2026-06-04, ``-$0.05`` each -- the payroll residue finding
  **N-239** measures.

A report built on the balance difference alone prints "agrees" on every one of
those days.  So the residue is what ``agrees`` is tested on, and this suite
asserts a day whose gap is ZERO and whose residue is not is reported as a
disagreement.
"""
