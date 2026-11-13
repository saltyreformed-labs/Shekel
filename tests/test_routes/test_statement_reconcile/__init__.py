"""
Shekel Budget App -- The RECONCILE page, through HTTP

Plan step **bank_import:X-gj-1b**; rulings **bank_import:R-HP** through
**R-HX**.  One page on four verbs, replacing the review queue, the register
and the hand-build workbench.

**The cases that matter most read the page and post its own bytes back.**  A
hand-picked payload is written by the same person as the template, so the two
agree about a mistake as readily as about the truth -- and this arc has paid
for that twice, once shipping a destination arm that was DEAD in a browser.
:func:`~tests.test_routes._statement_forms.reconcile_form_fields` scrapes what
a browser would submit, unticked checkboxes and all.

**The ownership 404s are PAIRED with a case asserting the URL still routes.**
A 404 from the URL MAP and a 404 from the ownership gate are indistinguishable
in a response, so moving or renaming a route leaves its IDOR control passing
and guarding nothing -- measured once on a door that DESTROYS budget rows.
"""
