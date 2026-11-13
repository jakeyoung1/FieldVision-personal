"""Check outcome claims in a generated report against the play-by-play they came from.

Evidence verification catches a quote the model invented. It does not catch a
summary that describes a real at-bat wrongly: "struck out on 0-1" when the 0-1
at-bat was a groundout. That exact error shipped in the April 2026 capstone
evaluation report. This module parses play-by-play lines into events and flags
report sentences whose outcome, count, or frequency the events do not support.

Scope: plate-appearance outcomes, stolen bases, and advancing on a wild pitch.
A sentence is checked only when it names exactly one player, so a claim is
never pinned on the wrong person. Anything outside that scope is not checked,
which is different from verified.
"""
from __future__ import annotations

import re
from difflib import SequenceMatcher

# Order matters: the first matching phrase names the event.
_EVENT_PHRASES = [
    ("strikeout", r"struck out"),
    ("groundout", r"grounded (?:out|into)"),
    ("flyout",    r"flied out|fouled out|popped (?:up|out)|lined out"),
    ("hit",       r"singled|doubled|tripled|homered"),
    ("walk",      r"walked(?![- ](?:off|up)\b)"),
    ("hbp",       r"hit by pitch"),
    ("reached",   r"reached"),
    ("steal",     r"stole"),
    ("advance",   r"advanced|scored|out at"),
]
PLATE_APPEARANCES = {"strikeout", "groundout", "flyout", "hit", "walk", "hbp", "reached"}

_NAME = r"([A-Z][A-Za-z'\-]+),?\s+([A-Z][A-Za-z'\-]+)"
_EVENT_RE = re.compile(
    _NAME + r"\s+(" + "|".join(p for _, p in _EVENT_PHRASES) + r")"
)
_COUNT_RE = re.compile(r"(?<![\d-])([0-3])-([0-2])(?![\d-])")
# "3-2" is a full count or a final score. The words around it say which.
_COUNT_AFTER = re.compile(r"\s*(?:in the\s+)?(?:count|pitch|offering)\b", re.I)
_SCORE_AFTER = re.compile(
    r"\s*(?:win|loss|lead|victory|final|deficit|game|score|decision|defeat|advantage|margin|comeback)\b", re.I)
# Words that only describe a game result. "up", "down", "ahead", "behind" and
# "fell" are not here: a hitter is "down 0-2" and "fell behind 0-2" far more
# often than a team is, so on their own they say nothing about a score.
_SCORE_BEFORE = re.compile(
    r"\b(?:won|wins?|winning|lost|loses?|losing|led(?!\s+off)|leads?(?!\s+off)|leading(?!\s+off)|"
    r"trailed|trails?|trailing|score[ds]?|final|tied|ties?|beat|beats|beating|"
    r"defeated|defeats?|victory)\s+(?:[\w'.]+\s+){0,3}$", re.I)
# "SMC fell 3-2" and "fell to CRE 3-2" are results; "fell behind 0-2" is a count.
_SCORE_FELL = re.compile(r"\b(?:fell|falls?)\s+(?:to\s+[\w'.]+\s+)?$", re.I)
# A team that is up, down, ahead or behind is a score: "SMC up 2-1", "they
# were down 3-0". Case-sensitive, so a team code is told from an ordinary word;
# position and stat abbreviations are not teams.
_TEAM_MARGIN = re.compile(
    r"(?:\b(?!(?:RHP|LHP|RBI|ERA|HBP|OF|IF|SS|CF|LF|RF|DH|BB|SO|FB|CB|SL|CH)\b)[A-Z]{2,5}\b"
    r"|\b(?:[Ww]e|[Tt]hey|[Tt]eam|[Cc]lub|[Ss]quad)\b)\s+"
    r"(?:(?:is|are|was|were|went|go|goes|stayed|stays|pulled|pulls|jumped|jumps|fell|falls?)\s+)?"
    r"(?:up|down|ahead|behind)\s+(?:by\s+)?$")
# Phrases that put a hitter or pitcher in a count.
_COUNT_IDIOM = re.compile(
    r"\b(?:popp?ed up|f(?:e|a)lls? behind|falling behind|g[eo]ts? (?:ahead|behind)|getting (?:ahead|behind)|"
    r"(?:worked|battled|came|fought) back (?:to|from))\s+$", re.I)


def _counts_in(text: str) -> list[str]:
    """Ball-strike counts in `text`, leaving out game scores.

    "struck out on a 3-2 pitch" is a count. "lost 3-2" and "a 3-2 win" are
    scores, and reading them as counts produced flags about a plate
    appearance that never existed. Checked in this order, first match wins:

      1. "count" or "pitch" next to the number   -> count
      2. a team that is up/down/ahead/behind     -> score
      3. a count idiom ("fell behind", "popped up") -> count
      4. a result word (won, lost, lead, final)  -> score
      5. nothing either way                      -> count, as before
    """
    counts = []
    for m in _COUNT_RE.finditer(text):
        before, after = text[max(0, m.start() - 40):m.start()], text[m.end():m.end() + 25]
        if _COUNT_AFTER.match(after) or "count" in before[-25:].lower():
            is_count = True
        elif _TEAM_MARGIN.search(before):
            is_count = False
        elif _COUNT_IDIOM.search(before):
            is_count = True
        else:
            is_count = not (_SCORE_AFTER.match(after) or _SCORE_BEFORE.search(before)
                            or _SCORE_FELL.search(before))
        if is_count:
            counts.append(f"{m.group(1)}-{m.group(2)}")
    return counts


_INNING_RE = re.compile(r"\b(\d)(?:st|nd|rd|th)\s+[Ii]nning|\bof\s+(\d)(?:st|nd|rd|th)\b")

# How a report phrases each checkable outcome.
_CLAIM_PHRASES = {
    "strikeout":  r"struck out|strikeouts?|strikes out|punched out",
    "groundout":  r"grounded out|groundouts?|ground(?:ed)? into",
    # "walk-off" and "walk-up" are a game-ending hit and a batter's music, not a base on balls.
    "walk":       r"\bwalk(?:ed|s)?\b(?![- ](?:offs?|ups?)\b)|base on balls",
    "hbp":        r"hit by (?:a )?pitch",
    "steal":      r"\bstole\b|stolen bases?|steals?\b",
    "wild_pitch": r"wild pitch(?:es)?",
}
_NUMBER_WORDS = {"once": 1, "twice": 2, "three times": 3, "four times": 4}


def _event_kind(phrase: str) -> str:
    for kind, pattern in _EVENT_PHRASES:
        if re.fullmatch(pattern, phrase):
            return kind
    return "other"


def _similar(a: str, b: str) -> float:
    return SequenceMatcher(None, a.lower(), b.lower()).ratio()


def _same_player(a: tuple[str, str], b: tuple[str, str]) -> bool:
    """OCR mangles names ("Sniffen" / "Snittam"), so match on one exact part
    plus a loosely similar other part, or two strongly similar parts."""
    last_a, first_a = a
    last_b, first_b = b
    # An exact first name plus a last name sharing its opening letters:
    # "Makoa Sniffen" / "Makoa Snittam". Two Nicks with unrelated surnames
    # stay apart.
    if (first_a.lower() == first_b.lower()
            and last_a[:2].lower() == last_b[:2].lower()
            and _similar(last_a, last_b) >= 0.4):
        return True
    if last_a.lower() == last_b.lower() and _similar(first_a, first_b) >= 0.5:
        return True
    if _similar(last_a, last_b) >= 0.85 and _similar(first_a, first_b) >= 0.5:
        return True
    return _similar(last_a, last_b) >= 0.75 and _similar(first_a, first_b) >= 0.75


def parse(text: str) -> list[dict]:
    """Play-by-play text -> ordered events, one per player clause."""
    flat = re.sub(r"\s+", " ", text)
    innings = [(m.start(), int(m.group(1) or m.group(2))) for m in _INNING_RE.finditer(flat)]
    matches = list(_EVENT_RE.finditer(flat))
    events = []
    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(flat)
        clause = flat[m.start():end]
        counts = _counts_in(clause)
        inning = None
        for pos, n in innings:
            if pos <= m.start():
                inning = n
        kind = _event_kind(m.group(3))
        events.append({
            "name": (m.group(1), m.group(2)),
            "kind": kind,
            "count": counts[0] if counts and kind in PLATE_APPEARANCES else None,
            "inning": inning,
            "wild_pitch": "wild pitch" in clause.lower(),
            "text": clause.strip(" ;"),
        })
    _assign_players(events)
    return events


def _assign_players(events: list[dict]) -> None:
    """Give every event a player id, merging OCR variants of one name."""
    players: list[tuple[str, str]] = []
    for e in events:
        for pid, name in enumerate(players):
            if _same_player(e["name"], name):
                e["player"] = pid
                break
        else:
            players.append(e["name"])
            e["player"] = len(players) - 1


def roster(events: list[dict]) -> dict[int, set[str]]:
    """player id -> every spelling of every name part seen for that player."""
    out: dict[int, set[str]] = {}
    for e in events:
        out.setdefault(e["player"], set()).update(e["name"])
    return out


def _players_named(sentence: str, names: dict[int, set[str]]) -> set[int]:
    found = set()
    for pid, parts in names.items():
        if any(re.search(rf"\b{re.escape(p)}\b", sentence) for p in parts if len(p) > 2):
            found.add(pid)
    return found


def _label(event: dict) -> str:
    return "advanced on a wild pitch" if event["wild_pitch"] else {
        "strikeout": "a strikeout", "groundout": "a groundout", "flyout": "a fly or pop out",
        "hit": "a hit", "walk": "a walk", "hbp": "a hit by pitch", "reached": "reached base",
        "steal": "a stolen base", "advance": "a baserunning advance",
    }.get(event["kind"], event["kind"])


def check_report(report: str, source: str) -> list[dict]:
    """Flags for outcome claims in `report` that `source` does not support."""
    events = parse(source)
    if not events:
        return []
    names = roster(events)
    flags = []
    for sentence in _sentences(report):
        # Quoted text is evidence lifted from the notes; evidence verification
        # already checks it verbatim. Only the report's own words are claims.
        claim_text = _QUOTED_RE.sub(" ", sentence)
        named = _players_named(claim_text, names)
        if len(named) != 1:
            continue
        pid = named.pop()
        player = ", ".join(sorted(names[pid]))
        mine = [e for e in events if e["player"] == pid]
        claimed = [c for c, p in _CLAIM_PHRASES.items() if re.search(p, claim_text, re.I)]
        # A count can only be tied to an outcome when the sentence claims one
        # kind of plate appearance: "a strikeout and a groundout (1-2, 0-1)"
        # does not say which count went with which.
        pa_claims = [c for c in claimed if c in PLATE_APPEARANCES]
        counts = _counts_in(claim_text) if len(pa_claims) == 1 else []
        for claim in claimed:
            if claim == "wild_pitch":
                support = [e for e in mine if e["wild_pitch"]]
            else:
                support = [e for e in mine if e["kind"] == claim]
            if not support:
                flags.append(_flag(sentence, player, claim,
                                   f"the play-by-play has no {claim.replace('_', ' ')} for this player"))
                continue
            if claim in PLATE_APPEARANCES:
                for c in counts:
                    at_count = [e for e in mine if e["count"] == c]
                    if at_count and not any(e["kind"] == claim for e in at_count):
                        actual = "; ".join(e["text"] for e in at_count)
                        flags.append(_flag(sentence, player, claim,
                                           f"the {c} plate appearance was {_label(at_count[0])}, "
                                           f"not a {claim}: \"{actual}\""))
                    elif not at_count:
                        flags.append(_flag(sentence, player, claim,
                                           f"no plate appearance on a {c} count in the play-by-play"))
            for word, n in _NUMBER_WORDS.items():
                if len(claimed) == 1 and re.search(rf"\b{word}\b", claim_text, re.I) and len(support) != n:
                    flags.append(_flag(sentence, player, claim,
                                       f"report says {word}, play-by-play shows {len(support)}"))
    return flags


_QUOTED_RE = re.compile(r"\"[^\"]*\"|“[^”]*”")
# A markdown heading, bullet, or numbered item starts a new unit even without
# a full stop; any other line break is just wrapping inside a sentence.
_BLOCK_BREAK_RE = re.compile(r"\n\s*\n|\n(?=\s*(?:#|[-*>]\s|\d+[.)]\s))")


def _sentences(report: str) -> list[str]:
    out = []
    for block in _BLOCK_BREAK_RE.split(report):
        block = re.sub(r"\s*\n\s*", " ", block)
        out += [s for s in re.split(r"(?<=[.!?])\s+", block) if s.strip()]
    return out


def _flag(sentence: str, player: str, claim: str, problem: str) -> dict:
    plain = re.sub(r"\*\*|__|^\s*(?:#+|[-*>]|\d+[.)])\s*", "", sentence).strip()
    return {"sentence": plain, "player": player, "claim": claim, "problem": problem}
