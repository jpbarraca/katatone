"""Reading the model's reply: tolerant of what is missing, loose or wrong; and the rows it makes."""
from __future__ import annotations

import json

import pytest

from katatone.address.map import load_map
from katatone.ai import parse, spec
from katatone.model.paramset import ParamSet


@pytest.fixture
def ps() -> ParamSet:
    return ParamSet()


def _rows(p, title_start):
    sec = next(s for s in p.sections if s.title.startswith(title_start))
    return {r.key + ("*" if r.role == "switch" else ""): r for r in sec.rows}


FULL = {
    "match": {"song": "Sultans of Swing", "artist": "Dire Straits", "confidence": "high"},
    "original_rig": "Strat", "reasoning": "Clean, bright.", "notes": "Neck pickup",
    "amp": {"type": "Clean", "gain": 30, "volume": 50, "bass": 45, "middle": 55, "treble": 65,
            "presence": 50, "resonance": 40},
    "booster": {"on": False},
    "mod": {"on": True, "type": "CHORUS", "params": {"low_rate": 30}},
    "delay": {"on": True, "type": "ANALOG", "time_ms": 380, "feedback": 20, "level": 25},
    "reverb": {"on": True, "type": "SPRING", "time": 20, "level": 30},
    "eq": {"on": True, "position": "AMP IN", "type": "GE-10", "params": {"500hz": 3, "level": 0}},
    "noise_suppressor": {"on": True, "threshold": 20},
}


def test_a_complete_reply(ps) -> None:
    p = parse.parse(json.dumps(FULL), ps)
    assert (p.song, p.artist, p.confidence, p.rig, p.notes) == (
        "Sultans of Swing", "Dire Straits", "high", "Strat", "Neck pickup")
    assert [s.key for s in p.sections] == ["amp", "booster", "mod", "delay", "reverb", "eq",
                                           "noise_suppressor"]
    assert p.missing == ["FX · GREEN"] and p.skipped == []
    amp = _rows(p, "Amp")
    assert amp["type"].new_text == "Clean" and amp["gain"].new == 30


def test_json_in_a_fence_or_in_chat(ps) -> None:
    body = json.dumps({"amp": {"gain": 10}})
    for text in (f"```json\n{body}\n```", f"Here you go: {body} Enjoy!"):
        assert _rows(parse.parse(text, ps), "Amp")["gain"].new == 10


@pytest.mark.parametrize("text", ["", "no json here", '{"amp": {"gain": ', "[1, 2]"])
def test_not_json(ps, text) -> None:
    with pytest.raises(parse.NotJsonError):
        parse.parse(text, ps)


def test_error_answer_and_empty_answer(ps) -> None:
    with pytest.raises(parse.ReplyError, match="no idea"):
        parse.parse({"error": "no idea what that is"}, ps)
    with pytest.raises(parse.ReplyError, match="no settings"):
        parse.parse({"match": {"song": "x"}, "notes": "hm"}, ps)
    with pytest.raises(parse.ReplyError, match="no settings"):
        parse.parse({"amp": {"nothing": 1}}, ps)


def test_missing_sections_and_keys_are_left_alone(ps) -> None:
    p = parse.parse({"amp": {"gain": 40}}, ps)
    assert [r.key for r in p.sections[0].rows] == ["gain"]
    assert p.missing == ["Booster · GREEN", "Mod · GREEN", "FX · GREEN", "Delay · GREEN",
                         "Reverb · GREEN", "EQ 1", "Noise suppressor"]
    assert p.changes() == [("AMP", 0, 40)]


def test_null_values_count_as_missing(ps) -> None:
    p = parse.parse({"amp": {"gain": None, "bass": 20}, "delay": None}, ps)
    assert [r.key for r in p.sections[0].rows] == ["bass"]
    assert "Delay · GREEN" in p.missing


def test_numbers_are_clamped_and_strings_read(ps) -> None:
    amp = _rows(parse.parse({"amp": {"gain": "35", "volume": 150, "bass": -5, "treble": 61.6,
                                     "middle": "40 %"}}, ps), "Amp")
    assert amp["gain"].new == 35 and amp["gain"].note == ""
    assert amp["volume"].new == 100 and amp["volume"].note == "clamped from 150"
    assert amp["bass"].new == 0 and amp["bass"].note == "clamped from -5"
    assert amp["treble"].new == 62 and amp["middle"].new == 40


def test_names_are_matched_loosely(ps) -> None:
    p = parse.parse({"amp": {"type": " clean "}, "booster": {"type": "t scream"},
                     "reverb": {"type": "Spring"}, "delay": {"type": "tape-echo"}}, ps)
    assert _rows(p, "Amp")["type"].new_text == "Clean"
    assert _rows(p, "Booster")["type"].new_text == "T-SCREAM"
    assert _rows(p, "Reverb")["type"].new_text == "SPRING"
    assert _rows(p, "Delay")["type"].new_text == "TAPE ECHO"


def test_a_frequency_is_matched_by_its_value(ps) -> None:
    eq = {"type": "PARAMETRIC EQ", "params": {"lowmid_freq": "500 Hz", "high_cut": "4k", "lowmid_q": 2}}
    rows = _rows(parse.parse({"eq": eq}, ps), "EQ")
    assert rows["lowmid_freq"].new_text == "500Hz"
    assert rows["high_cut"].new_text == "4.00kHz"
    assert rows["lowmid_q"].new_text == "2"


def test_unusable_values_are_listed_not_fatal(ps) -> None:
    p = parse.parse({"amp": {"type": "Heavy Metal", "gain": "lots", "volume": 50, "bogus": 1},
                     "delay": {"type": "ANALOG", "on": "maybe"}}, ps)
    assert [r.key for r in p.sections[0].rows] == ["volume"]
    text = "\n".join(p.skipped)
    assert "Amp: type: 'Heavy Metal' is not one of" in text
    assert "Amp: gain: 'lots' is not a number" in text
    assert "Amp: bogus: unknown key" in text
    assert "Delay · GREEN: on: 'maybe' is not on or off" in text


def test_on_off_spellings(ps) -> None:
    for v, want in ((True, 1), ("On", 1), ("off", 0), (0, 0), ("true", 1)):
        row = _rows(parse.parse({"booster": {"on": v, "type": "RAT"}}, ps), "Booster")["on*"]
        assert row.new == want


def test_settings_of_another_effect_are_skipped(ps) -> None:
    p = parse.parse({"mod": {"type": "CHORUS", "params": {"low_rate": 30, "manual": 50}}}, ps)
    assert [r.key for r in p.sections[0].rows] == ["type", "low_rate"]
    assert p.skipped == ["Mod · GREEN: manual: not a setting of CHORUS"]


def test_settings_without_a_type_use_the_current_one(ps) -> None:
    region = load_map()["PATCH"]
    ps.set_param(region, "FX(1)", 0, 14)                              # PHASER
    p = parse.parse({"mod": {"on": True, "params": {"rate": 40}}}, ps)
    rows = _rows(p, "Mod")
    assert rows["rate"].new == 40 and rows["rate"].needs is None
    assert p.skipped == []


def test_settings_of_an_unusable_type_are_left_out(ps) -> None:
    p = parse.parse({"amp": {"gain": 1}, "mod": {"type": "Space Echo", "params": {"rate": 40}}}, ps)
    assert [s.key for s in p.sections] == ["amp"]
    assert any("type" in s for s in p.skipped) and any("params" in s for s in p.skipped)


def test_loose_keys_of_an_effect_are_taken_as_settings(ps) -> None:
    p = parse.parse({"fx": {"on": True, "type": "TREMOLO", "rate": 60, "depth": 40}}, ps)
    assert {r.key for r in p.sections[0].rows} == {"on", "type", "rate", "depth"}


def test_section_with_only_unusable_keys_counts_as_missing(ps) -> None:
    p = parse.parse({"amp": {"gain": 5}, "reverb": {"type": "Cathedral"}}, ps)
    assert "Reverb · GREEN" in p.missing and len(p.sections) == 1
