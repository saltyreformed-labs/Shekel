"""
Shekel Budget App -- Analytics Taxes Report Service Tests (T-P3)

Hand-confirmed assertions for ``tax_report_service.compute_tax_report``: the
producer that fuses the T-P1 annual liability and the T-P2
withholding-to-date into the refund, hero chips, hybrid W-2 preview, and
Schedule A check the analytics Taxes tab (T-P4) renders.

The law is the shipped one, the default every test prices under (ruling
salary:R-SAL80) -- the same figures the signup seed copied to every user until
plan step salary:X-at-1 -- so every figure anchors on 2026's (single, NC,
standard deduction 16,100; brackets 10/12/22/24/32/35/37; NC flat 3.99%, std
ded 12,750; FICA ss_wage_base 184,500).

Where the withholding total is delegated to ``project_salary`` (Pub 15-T
per-period, not hand-summable), the tests pin it against an INDEPENDENT
``project_salary`` oracle over the same periods -- the T-P2 pattern -- and
hand-compute everything the producer itself derives (the annual liability,
the refund identity, the rate chips, the W-2 boxes, the next-payday hint).

Periods are 26 biweekly paydays starting 2026-01-02 (indices 1..26), built
directly (the seed_user bootstrap sits in 2024 and is out of the 2026
year-periods query the producer runs):

    i=0 01-02  i=1 01-16  i=2 01-30 ... i=12 06-19  i=13 07-03 ... i=25 12-18
"""
