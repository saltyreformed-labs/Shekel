"""The MERCHANT RULE: where the owner says a merchant's spending goes.

Plan step **bank_import:X-f6a-3d**.  **It MOVES NO MONEY and the first class
below is what pins that**, because it is the property the whole design rests
on: a rule is read to SUGGEST a destination, and the only thing that records
a purchase is an explicit destination submitted for one specific line.  Ruling
**R-FZ**'s *the destination select IS the tick* survives only while that holds.

Measured on the developer's own 2026-08-16 statement against a 2026-08-18
production clone: the 91 unexplained outflows are **21 merchants**, and six
stated answers place **48 of the 91** -- so the sweep those placements feed
turns 48 decisions into one press.  Two of those answers are the ones a
per-line screen could never express: Capital One Credit Card is *never a
purchase* (9 lines, `-$7,412.94` the app already holds as payback rows), and
``Amazon -> Groceries`` reaches ten different pay periods through one template.

Every refusal below is a FIRING CONTROL, written to fail if the refusal were
deleted.
"""
