"""Hybrid matching: rapidfuzz for clear matches, Claude API for ambiguous ones."""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field

from rapidfuzz import fuzz, process

from parsing import TBEntry

FUZZY_AUTO_THRESHOLD = 90  # auto-accept above this score
FUZZY_CANDIDATE_THRESHOLD = 55  # below this, don't even bother asking the AI
BALANCE_TOLERANCE = 0.01

_STOPWORDS = {"the", "and", "of", "a"}


@dataclass
class MatchResult:
    internal_name: str | None
    internal_balance: float | None
    audited_name: str | None
    audited_balance: float | None
    method: str  # exact | fuzzy | ai | unmatched
    confidence: int | None
    rationale: str | None = None

    @property
    def difference(self) -> float | None:
        if self.internal_balance is None or self.audited_balance is None:
            return None
        return round(self.internal_balance - self.audited_balance, 2)

    @property
    def balances_agree(self) -> bool | None:
        diff = self.difference
        if diff is None:
            return None
        return abs(diff) <= BALANCE_TOLERANCE


@dataclass
class MatchReport:
    matched: list[MatchResult] = field(default_factory=list)
    missing_in_audited: list[MatchResult] = field(default_factory=list)
    missing_in_internal: list[MatchResult] = field(default_factory=list)


def _normalize(name: str) -> str:
    text = name.lower()
    text = re.sub(r"[^a-z0-9\s]", " ", text)
    words = [w for w in text.split() if w not in _STOPWORDS]
    return " ".join(words)


def _fuzzy_pass(
    internal: list[TBEntry], audited: list[TBEntry]
) -> tuple[list[MatchResult], list[TBEntry], list[TBEntry]]:
    norm_internal = [_normalize(e.account_name) for e in internal]
    norm_audited = [_normalize(e.account_name) for e in audited]

    candidates = []
    for i, ni in enumerate(norm_internal):
        if not audited:
            break
        result = process.extractOne(ni, norm_audited, scorer=fuzz.token_sort_ratio)
        if result is None:
            continue
        _, score, j = result
        if score >= FUZZY_CANDIDATE_THRESHOLD:
            candidates.append((score, i, j))

    candidates.sort(key=lambda c: -c[0])
    used_internal: set[int] = set()
    used_audited: set[int] = set()
    matched: list[MatchResult] = []

    for score, i, j in candidates:
        if i in used_internal or j in used_audited:
            continue
        if score < FUZZY_AUTO_THRESHOLD:
            continue
        used_internal.add(i)
        used_audited.add(j)
        method = "exact" if score == 100 else "fuzzy"
        matched.append(
            MatchResult(
                internal_name=internal[i].account_name,
                internal_balance=internal[i].balance,
                audited_name=audited[j].account_name,
                audited_balance=audited[j].balance,
                method=method,
                confidence=int(score),
            )
        )

    remaining_internal = [e for i, e in enumerate(internal) if i not in used_internal]
    remaining_audited = [e for j, e in enumerate(audited) if j not in used_audited]
    return matched, remaining_internal, remaining_audited


_AI_SYSTEM_PROMPT = """You are an audit assistant reconciling two trial balances that use \
different account naming conventions (e.g. one client's internal chart of accounts vs. the \
auditor's adjusted trial balance). You are given a list of unmatched internal accounts and a \
list of unmatched audited accounts. Match accounts that clearly represent the same underlying \
general ledger account, even if named differently (abbreviations, reordering, synonyms, \
sub-account rollups). Do NOT force a match if no reasonable counterpart exists — leaving an \
account unmatched is correct when it is genuinely missing from the other side.

Respond with ONLY a JSON object of this shape:
{
  "matches": [
    {"internal": "<exact internal name>", "audited": "<exact audited name>", "confidence": 0-100, "rationale": "<short reason>"}
  ]
}
Only include pairs you are reasonably confident represent the same account (confidence >= 60). \
Do not include unmatched accounts in the output.
"""


def _ai_pass(internal: list[TBEntry], audited: list[TBEntry]) -> list[MatchResult]:
    if not internal or not audited:
        return []

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return []

    try:
        import anthropic
    except ImportError:
        return []

    client = anthropic.Anthropic(api_key=api_key)
    user_payload = {
        "unmatched_internal_accounts": [e.account_name for e in internal],
        "unmatched_audited_accounts": [e.account_name for e in audited],
    }

    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=2000,
        system=_AI_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": json.dumps(user_payload)}],
    )

    text = "".join(block.text for block in response.content if block.type == "text")
    try:
        parsed = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if not match:
            return []
        parsed = json.loads(match.group(0))

    internal_by_name = {e.account_name: e for e in internal}
    audited_by_name = {e.account_name: e for e in audited}

    results: list[MatchResult] = []
    for pair in parsed.get("matches", []):
        in_entry = internal_by_name.get(pair.get("internal"))
        au_entry = audited_by_name.get(pair.get("audited"))
        if in_entry is None or au_entry is None:
            continue
        results.append(
            MatchResult(
                internal_name=in_entry.account_name,
                internal_balance=in_entry.balance,
                audited_name=au_entry.account_name,
                audited_balance=au_entry.balance,
                method="ai",
                confidence=int(pair.get("confidence", 60)),
                rationale=pair.get("rationale"),
            )
        )
    return results


def reconcile(internal: list[TBEntry], audited: list[TBEntry]) -> MatchReport:
    matched, remaining_internal, remaining_audited = _fuzzy_pass(internal, audited)

    ai_matched = _ai_pass(remaining_internal, remaining_audited)
    matched.extend(ai_matched)

    ai_internal_names = {m.internal_name for m in ai_matched}
    ai_audited_names = {m.audited_name for m in ai_matched}
    remaining_internal = [e for e in remaining_internal if e.account_name not in ai_internal_names]
    remaining_audited = [e for e in remaining_audited if e.account_name not in ai_audited_names]

    report = MatchReport(matched=matched)
    report.missing_in_audited = [
        MatchResult(
            internal_name=e.account_name,
            internal_balance=e.balance,
            audited_name=None,
            audited_balance=None,
            method="unmatched",
            confidence=None,
        )
        for e in remaining_internal
    ]
    report.missing_in_internal = [
        MatchResult(
            internal_name=None,
            internal_balance=None,
            audited_name=e.account_name,
            audited_balance=e.balance,
            method="unmatched",
            confidence=None,
        )
        for e in remaining_audited
    ]
    return report
