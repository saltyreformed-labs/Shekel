"""The bank feed's doors: claim, list, map, disconnect (plan step X-f6b-2, leaf 3c).

Rulings **R-BI12**, **R-BI26**, **R-BI27**, **R-BI28**, **R-BI29**.  Bridge is a fake at
``requests.post`` / ``requests.get``, the shape ``test_auth_service``'s HIBP
tests stub.  Every fake URL names Bridge's REAL host, because the host pin
(R-BI28) refuses any other; so a forgotten stub would reach the real Bridge,
and :func:`block_real_requests` -- autouse here and in the route module --
turns that into a failure at ``requests.Session.send``, the one call every
``requests.post``/``get`` goes through and the spy sits above.  (Until the
pin, the fakes lived under RFC 2606's ``.invalid`` and a forgotten stub
failed at DNS instead.)

**The three claims these tests exist to grade, because the code's own words
cannot**:

* **a setup token is never burned for nothing** -- the claim refuses an
  unreadable paste, a foreign host and a standing feed BEFORE the request,
  and the spy counts zero calls on each;
* **the credential never reaches a sentence or a record** -- the fake
  raises the ``HTTPError`` ``requests`` really raises, its text carrying
  the URL's userinfo, and every refusal sentence and every captured log
  record is asserted free of it;
* **the host that is checked is the host that is reached** -- the pin is
  fed the URL whose host ``urlsplit`` and ``requests`` read differently, and
  refuses it; and no redirect is followed (R-BI29): every call carries
  ``allow_redirects=False`` and a ``3xx`` answer is a refusal of its own.
"""
