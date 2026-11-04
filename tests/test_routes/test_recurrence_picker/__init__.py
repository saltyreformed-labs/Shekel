"""
Shekel Budget App -- the recurrence picker offers only what can be STORED (R7b-2)

The form used to pick a NAME from the closed pattern set.  It authors the two
axes the write door takes -- how often (``interval_n`` plus ``unit``) and which
paycheck funds an occurrence (``placement``).

**The claim this file exists to hold the code to is that the offer set is
DERIVED from the producer that would otherwise REFUSE the cadence.**  Before
plan step R7b-2 the picker iterated the closed pattern set while the encoder
read its own table, so "nothing offers an unauthorable cadence" held only
because the two sets happened to coincide -- protected by no gate.
``authorable_cadences`` is that producer's own answer and ``_picker`` words it,
so the refusal is unreachable through the form rather than fenced behind it
(developer ruling 2026-08-12).

**Plan step R7c-c changed WHICH producer binds, and two properties with it.**
Storage was the constraint while a cadence had to have a name; every reading
can be stored now, so what is left is whether the application can HONOUR it.
The interval is a free number box for every unit, and the
``(unit, interval)`` pair dependency is gone -- which is plan ledger row
**D32**'s defect ceasing to exist.

**Plan step R8-a changed the gate itself.**  R7c-c left it on
``anchor_family``, a three-valued router selecting between first-occurrence
derivations ruling **R-R16** had deleted; R8-a replaced it with two rules over
live facts, which widened the offer set by exactly one reading (a year-scale
cadence funded from a later paycheck) and left one withheld (the ``WEEK``
unit, until plan step R5).

Four properties, each of which fails differently:

1. every offered ``(unit, placement)`` is one the write door accepts -- swept
   over the whole rendered offer set, not sampled;
2. the "Funded from" row is RENDERED for every cadence, and says so when the
   cadence admits one funding rule (the rest of D32, developer ruling
   2026-08-16): hiding it is how a bill's funding changed with nothing on
   screen saying so;
3. the one ``interval_n`` input is enabled exactly when a cadence is chosen,
   because a disabled control does not submit and an interval beside no unit
   states half a cadence;
4. an EDIT form starts on the cadence its rule actually means, and a rule whose
   stored cadence the application cannot read renders the controls unset above
   a flashed explanation -- and cannot be destroyed by saving it unchanged.

The state property 4 needs is manufactured here: ``ref_cache.init`` requires
every ENUM member to have a ``ref`` row and says nothing about the reverse, so
a surplus row is a state the schema permits.
"""
