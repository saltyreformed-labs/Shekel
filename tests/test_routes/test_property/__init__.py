"""Route tests for the Property (physical-asset) feature.

Covers create -> auto-create appreciation params + setup-page redirect (and
the regression that a Property is NOT treated as an investment), the
Property detail page, the appreciation-rate update, the loan-side "secured
by" collateral link, deletion behaviour (DB SET NULL + params cleanup), and
the equity-over-time chart producer + its route context contract
(``docs/design/account_detail_audit.md``, "Property equity chart" + the
2026-07-11 Loop A lock / Loop B build contract).
"""
