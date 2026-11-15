"""Turn the model's reply into a :class:`~katatone.ai.proposal.Proposal`.

The reply is not trusted to be complete or exact: a section or key that is missing is left
alone, a name that is spelled a little differently is matched, a number out of range is
clamped, and what cannot be used is listed in ``Proposal.skipped`` with the reason; only
a reply that is not JSON, or holds no setting at all, is an error.
"""
from __future__ import annotations

import difflib
import json
import re

from ..model.paramset import ParamSet
from . import spec
from .proposal import Proposal, Row, Section

CLOSE_MATCH = 0.85          # how alike a name must be to be taken for an option's


class ReplyError(Exception):
    """The reply cannot be used; the message is fit to show the user."""


class NotJsonError(ReplyError):
    """The reply holds no JSON object (asking again may help)."""


def load_json(text: str) -> dict:
    """The JSON object in a reply, which may be wrapped in a markdown fence or in other text.

    Raises:
        ReplyError: there is none.
    """
    text = text.strip()
    candidates = [text]
    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, re.S | re.I)
    if fenced:
        candidates.append(fenced.group(1))
    if "{" in text and "}" in text:
        candidates.append(text[text.index("{"):text.rindex("}") + 1])
    for c in candidates:
        try:
            data = json.loads(c)
        except ValueError:
            continue
        if isinstance(data, dict):
            return data
    raise NotJsonError("The reply is not a JSON object (it may have been cut off). Ask again.")


def _on_off(v) -> bool | None:
    """An on/off value as a bool, None when it is neither."""
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)) and v in (0, 1):
        return bool(v)
    if isinstance(v, str) and v.strip().lower() in ("on", "true", "yes", "off", "false", "no"):
        return v.strip().lower() in ("on", "true", "yes")
    return None


def _number(v) -> float | None:
    """A number from a number or a string such as "7" or "40 ms"; None for anything else."""
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        m = re.fullmatch(r"\s*(-?\d+(?:\.\d+)?)\s*(?:ms|%|db)?\s*", v, re.I)
        return float(m.group(1)) if m else None
    return None


def _option(names: tuple[str, ...], v) -> int | None:
    """The index of the option ``v`` names, None when it names none."""
    want = spec.normal(v)
    if not want:
        return None
    for i, n in enumerate(names):
        if spec.normal(n) == want:
            return i
    num = spec.number_of(v) if isinstance(v, (str, int, float)) else None
    if num is not None:
        for i, n in enumerate(names):
            if spec.number_of(n) == num:
                return i
    close = difflib.get_close_matches(want, [spec.normal(n) for n in names], n=2,
                                      cutoff=CLOSE_MATCH)
    if len(close) == 1:
        return [spec.normal(n) for n in names].index(close[0])
    return None


def _value(f: spec.Field, v) -> tuple[int | None, str]:
    """The parameter's display value for the reply's ``v``, and a note (or the reason it is None)."""
    p = f.param
    if f.names:
        i = _option(f.names, v)
        if i is None:
            return None, f"{v!r} is not one of {', '.join(f.names[:6])}…"
        return p.min + i, ""
    n = _number(v)
    if n is None:
        return None, f"{v!r} is not a number"
    value = max(p.min, min(p.max, round(n)))
    return value, (f"clamped from {round(n)}" if value != round(n) else "")


class _Builder:
    """Collects the rows of one section."""

    def __init__(self, ps: ParamSet, proposal: Proposal, res: spec.Resolved) -> None:
        self.ps, self.proposal, self.res = ps, proposal, res
        self.section = Section(res.key, res.title)
        self.region = spec._region()

    def skip(self, what: str, why: str) -> None:
        self.proposal.skipped.append(f"{self.res.title}: {what}: {why}")

    def row(self, f: spec.Field, value: int, role: str, note: str = "") -> Row:
        old = self.ps.get_param(self.region, f.block, f.p_index)
        r = Row(f.key, f.label if role == "value" else
                {"switch": "On", "type": "Effect" if self.res.key in ("mod", "fx") else "Type"}[role],
                f.block, f.p_index, old, value, f.text(old), f.text(value), role, note,
                selected=old != value)
        self.section.rows.append(r)
        return r

    def put(self, f: spec.Field, v, role: str = "value") -> Row | None:
        """Add the row for key ``f`` set to ``v``; None (and a skip note) when ``v`` is unusable."""
        value, note = _value(f, v)
        if value is None:
            self.skip(f.key, note)
            return None
        return self.row(f, value, role, note)


def _section(ps: ParamSet, proposal: Proposal, key: str, data: dict) -> None:
    res = spec.resolve(key, ps)
    b = _Builder(ps, proposal, res)
    region = b.region
    on = data.get("on")
    if res.switch is not None and on is not None:
        flag = _on_off(on)
        if flag is None:
            b.skip("on", f"{on!r} is not on or off")
        else:
            b.row(res.switch, int(flag), "switch")
    type_row = None
    for k, v in data.items():
        k = str(k).lower()
        if k in ("on", "params") or v is None:
            continue
        f = res.plain.get(k)
        if f is not None:
            row = b.put(f, v, "type" if f is res.type_field else "value")
            if f is res.type_field:
                type_row = row
        elif res.detail is None:
            b.skip(k, "unknown key")
    if res.detail is not None:
        _params(b, res, data, type_row)
    if b.section.rows:
        proposal.sections.append(b.section)
    else:
        proposal.missing.append(res.title)


def _params(b: _Builder, res: spec.Resolved, data: dict, type_row: Row | None) -> None:
    """The settings of the effect (or EQ) type: from ``params``, or loose keys of the section."""
    params = data.get("params") if isinstance(data.get("params"), dict) else {}
    loose = {k: v for k, v in data.items()
             if str(k).lower() not in ("on", "params") and str(k).lower() not in res.plain}
    given = {str(k).lower(): v for k, v in {**loose, **params}.items() if v is not None}
    if not given:
        return
    current = b.ps.get_param(b.region, res.type_field.block, res.type_field.p_index) \
        if res.type_field else 0
    if type_row is not None:
        t = type_row.new
    elif "type" in data and data["type"] is not None:       # a type was given but is unusable
        b.skip("params", "the type was not understood, so its settings were left out")
        return
    else:
        t = current
    fields = res.detail(t)
    for k, v in given.items():
        f = fields.get(k)
        if f is None:
            b.skip(k, f"not a setting of {res.type_field.text(t) if res.type_field else 'this section'}")
            continue
        row = b.put(f, v)
        if row is not None and type_row is not None:
            row.needs = type_row


def parse(reply: str | dict, ps: ParamSet) -> Proposal:
    """Parse a reply against the patch it is meant for.

    Args:
        reply: the reply text, or its JSON object.
        ps: the patch in the editor (the current values the rows start from).
    Returns:
        The proposal; sections the reply lacks are in ``missing``.
    Raises:
        ReplyError: the reply is not JSON, is an error, or has no setting.
    """
    data = reply if isinstance(reply, dict) else load_json(reply)
    if data.get("error"):
        raise ReplyError(f"The model could not help: {data['error']}")
    match = data.get("match") if isinstance(data.get("match"), dict) else {}
    p = Proposal(song=str(match.get("song") or ""), artist=str(match.get("artist") or ""),
                 confidence=str(match.get("confidence") or "").lower(),
                 rig=str(data.get("original_rig") or ""), reasoning=str(data.get("reasoning") or ""),
                 notes=str(data.get("notes") or ""))
    for key in spec.SECTIONS:
        section = data.get(key)
        if isinstance(section, dict) and section:
            _section(ps, p, key, section)
        else:
            p.missing.append(spec.resolve(key, ps).title)
    if not p.sections:
        raise ReplyError("The reply has no settings for the amp. Ask again.")
    return p
