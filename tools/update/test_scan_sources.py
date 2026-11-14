"""The sources of the daily image scan's database (:mod:`tools.update.scan`, its paragraph on its
sources; ruling plan#4064): a scan is refused unless its database names Debian's records, NVD's,
GitHub's advisories and CISA's list of CVEs known to be exploited, each captured no longer
before the scan than the age Grype was run allowing its database, read back from the scan's own
record of how Grype ran.  Each case is the recorded scan (``recorded/scan.json``, Grype v0.121.2
on dev's image 2026-11-13) with its descriptor's instants or age changed; Grype writes the
scan's instant in Go's RFC 3339 to the nanosecond, trailing zeros dropped, and each capture's to
the second (read 2026-11-14 at v0.121.2)."""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from tools.update import scan

#: The trimmed copy of a real scan.
RECORDED = Path(__file__).resolve().parent / "recorded" / "scan.json"
#: The four sources the scan relies on, by the provider Grype names each (ruling plan#4064).
_RELIED_ON = ("debian", "nvd", "github", "kev")
#: The age the recorded scan's Grype was run allowing its database: 120 hours, in nanoseconds
#: (5 * 24 * 3600 s = 432,000 s, * 10**9).
_AGE = 432_000_000_000_000
#: A scan's instant, and the capture exactly that age before it, five days earlier.
_SCANNED = "2026-11-13T13:56:35Z"
_AT_THE_AGE = "2026-11-08T13:56:35Z"
#: The CVE of the one match the recorded scan counts, its interpreter's CRITICAL one.
_COUNTED = ["CVE-2026-1999903"]


def _described(stamp: object = _SCANNED, captured: object = _AT_THE_AGE,
               age: object = _AGE) -> dict:
    """The recorded scan, its timestamp ``stamp``, each provider captured at ``captured`` and
    its Grype run allowing its database ``age``."""
    found = json.loads(RECORDED.read_text(encoding="utf-8"))
    descriptor = found["descriptor"]
    descriptor["timestamp"] = stamp
    descriptor["configuration"]["db"]["max-allowed-built-age"] = age
    for provider in descriptor["db"]["providers"].values():
        provider["captured"] = captured
    return found


def _captured(found: dict, name: str, captured: object) -> dict:
    """``found``, its provider ``name`` captured at ``captured``."""
    found["descriptor"]["db"]["providers"][name]["captured"] = captured
    return found


def _counted(found: dict) -> list[str]:
    """The CVE of each match the scan counts in ``found``, judged whole."""
    return [item.vulnerability for item in scan.judged(found)]


def test_the_recorded_scan_names_each_source_captured_within_the_age():
    """The real scan: each source captured under 14 hours before it, and its Grype run allowing
    its database the 120 hours ``docker-compose.scan.yml`` sets."""
    found = json.loads(RECORDED.read_text(encoding="utf-8"))
    descriptor = found["descriptor"]
    assert set(scan.SOURCES) == set(_RELIED_ON)
    assert descriptor["configuration"]["db"]["max-allowed-built-age"] == _AGE
    assert descriptor["timestamp"] == "2026-11-13T13:56:35.68669319Z"
    assert [descriptor["db"]["providers"][name]["captured"] for name in _RELIED_ON] == [
        "2026-11-13T00:22:00Z", "2026-11-13T00:22:41Z", "2026-11-13T00:23:01Z",
        "2026-11-13T00:22:01Z"]
    assert _counted(found) == _counted(_described()) == _COUNTED


@pytest.mark.parametrize("name", _RELIED_ON)
def test_a_scan_whose_database_names_no_source_it_relies_on_is_refused(name):
    """Each source missing refuses the scan, named with what its records give it."""
    found = _described()
    del found["descriptor"]["db"]["providers"][name]
    with pytest.raises(scan.NotAScan, match=re.escape(
            f"the scan's database names no provider {name}, so it read no {scan.SOURCES[name]}")):
        scan.judged(found)


def test_every_source_missing_is_named_and_kev_as_its_refusal_said():
    """Each missing source is named in one refusal, CISA's list's in the words its own
    refusal says (plan#3830)."""
    found = _described()
    for name in ("debian", "kev"):
        del found["descriptor"]["db"]["providers"][name]
    with pytest.raises(scan.NotAScan) as refused:
        scan.judged(found)
    assert str(refused.value) == (
        "the scan's database names no provider debian, so it read no records of Debian's and "
        "could match none of the image's Debian packages; and names no provider kev, so it read "
        "no list of CVEs known to be exploited and could mark none")


@pytest.mark.parametrize("name", _RELIED_ON)
def test_a_source_captured_a_second_past_the_age_refuses_the_scan(name):
    """A source captured 120 hours and one second before the scan is refused, naming it, its
    instant and the scan's; the others, captured at the age, are not named."""
    with pytest.raises(scan.NotAScan) as refused:
        scan.judged(_captured(_described(), name, "2026-11-08T13:56:34Z"))
    assert str(refused.value) == (
        f"the scan's database's provider {name} was captured at 2026-11-08T13:56:34Z, more than "
        "5 days, 0:00:00, the age its database is allowed, before the scan at "
        "2026-11-13T13:56:35Z")


def test_a_source_captured_at_the_age_is_fresh():
    """Grype allows a database built the age ago, refusing one older (``validateAge``, an age
    greater than the one allowed): each source captured exactly 120 hours before is read."""
    assert _counted(_described()) == _counted(_described(captured=_SCANNED)) == _COUNTED


def test_the_age_is_compared_to_the_nanosecond():
    """The scan's instant one nanosecond past the age, as Grype writes it, refuses it; one
    nanosecond short of it does not."""
    with pytest.raises(scan.NotAScan, match="provider debian was captured at "):
        scan.judged(_described(stamp="2026-11-13T13:56:35.000000001Z"))
    assert _counted(_described(stamp="2026-11-13T13:56:34.999999999Z")) == _COUNTED


def test_every_stale_source_is_named():
    """Two sources stale: both named, in the order the scan relies on them."""
    found = _captured(_captured(_described(), "nvd", "2026-11-01T00:00:00Z"), "github",
                      "2026-10-01T00:00:00Z")
    with pytest.raises(scan.NotAScan) as refused:
        scan.judged(found)
    assert re.findall(r"provider (\w+) was captured at (\S+),", str(refused.value)) == [
        ("nvd", "2026-11-01T00:00:00Z"), ("github", "2026-10-01T00:00:00Z")]


def test_an_instant_in_another_zone_is_read_in_it():
    """Go writes a time not in UTC with its offset: 08:56:35 at -05:00 is 13:56:35 UTC, the
    capture at the age, so it is fresh, though 08:56:35 UTC would not be; and 19:26:34 at +05:30
    is 13:56:34 UTC, a second past it, so it is stale, though 19:26:34 UTC would not be."""
    assert _counted(_described(captured="2026-11-08T08:56:35-05:00")) == _COUNTED
    with pytest.raises(scan.NotAScan, match=r"captured at 2026-11-08T19:26:34\+05:30, more than"):
        scan.judged(_described(captured="2026-11-08T19:26:34+05:30"))


def test_the_age_is_the_one_grype_was_run_with():
    """The age is read from the scan, not held here: allowed 10 days, a capture 7 days before
    the scan is fresh; allowed 120 hours, it is not."""
    week = "2026-11-06T13:56:35Z"
    assert _counted(_described(captured=week, age=2 * _AGE)) == _COUNTED
    with pytest.raises(scan.NotAScan, match="was captured at 2026-11-06T13:56:35Z"):
        scan.judged(_described(captured=week))


@pytest.mark.parametrize("spelled", [
    "2026-11-13T13:56:35", "2026-11-13", "2026-11-13 13:56:35Z", "2026-11-13T13:56Z",
    "2026-11-13T13:56:35.6866931900Z", "2026-11-13T13:56:35.Z", "2026-02-30T13:56:35Z",
    "2026-11-13T13:56:35+0530", "２026-11-13T13:56:35Z", 1794550821, None, ""],
    ids=["no_zone", "a_day", "a_space", "no_seconds", "ten_digits", "an_empty_fraction",
         "no_such_day", "an_offset_with_no_colon", "a_wide_digit", "a_number", "none", "empty"])
def test_an_instant_not_as_grype_writes_it_is_refused(spelled):
    """The scan's timestamp, or a source's capture, spelled other than Go's RFC 3339 with a
    zone is refused, never read as some instant; a capture Grype leaves out (it omits a zero
    one) is refused so too."""
    with pytest.raises(scan.NotAScan, match=re.escape(
            f"the scan's timestamp is {spelled!r}, which is no instant as Grype writes one")):
        scan.judged(_described(stamp=spelled))
    with pytest.raises(scan.NotAScan, match=re.escape(
            f"the scan's database's provider nvd's capture is {spelled!r}, which is no instant "
            "as Grype writes one")):
        scan.judged(_captured(_described(), "nvd", spelled))


def test_a_capture_or_a_timestamp_left_out_is_refused():
    """Grype leaves out a capture it holds no instant for, and the scan's instant where it is
    run with none: each refuses the scan."""
    found = _described()
    del found["descriptor"]["db"]["providers"]["github"]["captured"]
    with pytest.raises(scan.NotAScan, match="provider github's capture is None"):
        scan.judged(found)
    found = _described()
    del found["descriptor"]["timestamp"]
    with pytest.raises(scan.NotAScan, match="the scan's timestamp is None"):
        scan.judged(found)


@pytest.mark.parametrize(("age", "said"), [
    (432000.0, "the scan's database configuration holds no max-allowed-built-age that is a int"),
    ("120h", "the scan's database configuration holds no max-allowed-built-age that is a int"),
    (None, "the scan's database configuration holds no max-allowed-built-age that is a int"),
], ids=["a_float", "a_duration_as_text", "none"])
def test_an_age_not_as_grype_writes_it_is_refused(age, said):
    """Grype writes its database's allowed age as a whole count of nanoseconds; any other value
    refuses the scan, never read as some age."""
    with pytest.raises(scan.NotAScan, match=re.escape(said)):
        scan.judged(_described(age=age))


def test_a_scan_with_no_record_of_how_grype_ran_is_refused():
    """With no configuration, the age is unknown, so the scan is refused."""
    found = _described()
    del found["descriptor"]["configuration"]
    with pytest.raises(scan.NotAScan, match="the scan's descriptor holds no configuration"):
        scan.judged(found)
