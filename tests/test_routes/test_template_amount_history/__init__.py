"""
Shekel Budget App -- Amount-history route tests (plan step X-au-a)

The write DOOR as the two recurring-definition forms drive it, and the panel
that makes what it wrote visible and correctable.

The date the edit form asks for used to be "Regenerate effective from": it
bounded one delete-and-recreate sweep and was then discarded, so the app could
never say what a definition cost last March.  It is now stored as a version of
the amount, and it still bounds the same sweep -- so what the series says a bill
is worth and which bills an edit rebuilds are read off ONE value.  These tests
assert the stored consequence of each form action, not just its status code.

Both kinds are covered here rather than in the two per-kind route suites,
because the door and the panel are ONE implementation over two forms
(:mod:`app.services.template_amount_service`,
:mod:`app.routes._amount_version_actions`) and a per-kind split would test it
twice and its shared rules nowhere.
"""
