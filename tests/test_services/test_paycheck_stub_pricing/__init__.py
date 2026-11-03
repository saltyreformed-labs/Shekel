"""Paychecks priced from pay stubs (plan step salary:S11-c-2c).

Rulings **R-SAL42** (fork 1, "Latest same-lines stub"), **R-SAL54** ("Nearest
stub, then formulas"), **R-SAL55** ("Never below $0.00"), **R-SAL58** ("Stub
records its kind"), **R-SAL77** ("Each on its own year") and **R-SAL100**
("Name the pricing stub"), through the read pass's pricer on real rows.  Each
tax is the stub's figure plus what the formulas say changed between the
stub's paycheck and this one, so every expected figure below is worked by
hand in its case's docstring.

**The made-up law** (every figure here is made up): federal rules that
withhold ``$0.00`` (so federal is the stub's figure, copied), a flat North
Carolina rate with no standard deduction -- 5% in 2026 and 4% in 2027 -- and
FICA at 6.2% on a ``$176,100.00`` base and 1.45%.  With no state deduction
the formulas' state line is ``rate x taxable`` exactly, and FICA is
``rate x gross`` while no line is "Pre-tax, all payroll taxes" (the owner
below; ``TestAnAllPayrollTaxesLineThroughAStub`` re-picks one).

**The owner**: a ``$3,000.00`` paycheck every 14 days from 2026-01-02, a
pre-tax Health line of ``$200.00`` and a taxable Phone allowance of
``$60.00``, so every paycheck's gross is ``$3,060.00`` and its taxable wage
``$2,860.00``::

    formulas on that paycheck (2026)   state  2,860.00 x 5%    = 143.00
                                       SS     3,060.00 x 6.2%  = 189.72
                                       Med    3,060.00 x 1.45% =  44.37
"""
