"""Finding an account under a name nobody would guess.

Found the hard way. Searching "ASU Mountain Home" and "Mountain Home" both
returned nothing, and the record was there under the name "ASUMH". HubSpot's
free-text company search matches tokens by prefix, so a record sharing no
leading token with the query is invisible. That false negative is what put
"no prior outreach" into a work order that was wrong.
"""

from __future__ import annotations

import pytest

from seats_prospecting.tools.hubspot import _acronym, _score, name_variants


def precise(query: str, domain: str | None = None) -> list[str]:
    return name_variants(query, domain)[0]


def fallback(query: str) -> list[str]:
    return name_variants(query)[1]


# --- the case that failed --------------------------------------------------


@pytest.mark.parametrize(
    "spelling",
    [
        "ASU Mountain Home",
        "Arkansas State University Mountain Home",
        "Arkansas State University-Mountain Home",
    ],
)
def test_every_spelling_generates_the_initialism_the_record_uses(spelling):
    assert "ASUMH" in precise(spelling), (
        f"{spelling!r} must reach the record stored as 'ASUMH'"
    )


def test_the_abbreviation_is_expanded():
    assert any(
        "arkansas state university" in v.lower() for v in precise("ASU Mountain Home")
    )


def test_a_known_domain_is_tried_first():
    assert precise("ASU Mountain Home", "asumh.edu")[1] == "asumh.edu"


# --- noise control --------------------------------------------------------


def test_three_letter_initialisms_are_rejected():
    """AMH from "ASU Mountain Home" matched UMass Amherst, a Norwegian college on
    amh.no, and an Israeli high school. Four characters minimum."""
    assert "AMH" not in precise("ASU Mountain Home")
    assert _acronym("ASU Mountain Home") == "AMH"  # generated, then filtered out


def test_single_words_are_a_fallback_not_a_default():
    """Bare "Mountain" finds the right record and ten other mountain colleges."""
    assert "Mountain" not in precise("ASU Mountain Home")
    assert "Mountain" in fallback("ASU Mountain Home")


def test_institution_words_are_not_treated_as_distinctive():
    for word in ("University", "College", "Community", "State"):
        assert word not in fallback("Mountain State Community College")


def test_a_simple_name_produces_no_noise():
    assert precise("Lyon College") == ["Lyon College"]


def test_variant_lists_are_bounded():
    p, f = name_variants("Arkansas State University Mountain Home Technical Campus")
    assert len(p) <= 5
    assert len(f) <= 2


# --- ranking --------------------------------------------------------------


def _row(name: str, domain: str = "", note: str | None = None) -> dict:
    props = {"name": name, "domain": domain}
    if note:
        props["notes_last_updated"] = note
    return {"id": "1", "properties": props}


def test_an_exact_domain_match_outranks_everything():
    right = _row("ASUMH", "asumh.edu")
    wrong = _row("Arkansas State University Mountain Home Foundation", "asumhfoundation.org")
    assert _score(right, "ASU Mountain Home", "asumh.edu") > _score(
        wrong, "ASU Mountain Home", "asumh.edu"
    )


def test_the_initialism_record_outranks_incidental_token_matches():
    """The real failure mode: the answer buried under other mountain colleges."""
    asumh = _row("ASUMH", "asumh.edu")
    noise = _row("Rocky Mountain College", "rocky.edu")
    assert _score(asumh, "ASU Mountain Home", None) > _score(noise, "ASU Mountain Home", None)


def test_a_record_with_a_domain_beats_one_without():
    """The stale A-State Jonesboro duplicate had no domain and no notes."""
    real = _row("Arkansas State University", "astate.edu", "2026-07-31")
    stale = _row("Arkansas State University-Jonesboro", "")
    assert _score(real, "Arkansas State University", None) > _score(stale, "Arkansas State University", None)


def test_an_exact_name_match_scores_highly():
    assert _score(_row("Lyon College", "lyon.edu"), "Lyon College", None) > _score(
        _row("Lyon County Community College", "lccc.edu"), "Lyon College", None
    )
