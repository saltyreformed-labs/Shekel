"""The statement page shows what the bank said, and its POST records one.

Plan step **bank_import:X-f6a-1**.  The route's own subjects, none of which the
service tests can see: OWNERSHIP (the security response rule's 404 for both "not
found" and "not yours"), the UPLOAD itself, the unit of work (a refusal must
leave nothing behind after a real request), and the FLASH that tells the user
what happened.

**The ownership tests are firing controls against an IDOR.**  A statement is a
record of a real person's bank activity, so a route that answered for another
user's account would disclose their spending -- and it would do so on a GET, the
cheapest thing to probe.  Both the read and the write are tested against a
second user's account, because the two decorators are applied independently and
one of them being right proves nothing about the other.
"""
