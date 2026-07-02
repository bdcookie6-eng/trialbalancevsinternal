"""Hybrid matching: rapidfuzz for clear matches, Claude API for ambiguous ones.

Produces a Summary + Comparison report shaped like a standard audit tie-out memo:
a result/health-check summary (counts, conclusion, notes) plus a per-account detail
table ordered by the audited trial balance, with unmatched client-only accounts
appended at the end and a TOTAL row.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field

from rapidfuzz import fuzz, process

from parsing import TBEntry

FUZZY_AUTO_THRESHOLD = 90  # auto-accept above this score, no AI needed
FUZZY_CANDIDATE_THRESHOLD = 55  # below this, don't even bother asking the AI
MATERIAL_THRESHOLD = 1.0  # dollars; differences under this are rounding noise

_STOPWORDS = {"the", "and", "of", "a"}


@dataclass
class _RawMatch:
    internal_name: str
    internal_balance: float
    audited_name: str
    audited_balance: float
    audited_code: str | None
    method: str  # exact | fuzzy | ai


def _normalize(name: str) -> str:
    text = name.lower()
    text = re.sub(r"[^a-z0-9\s]", " ", text)
    words = [w for w in text.split() if w not in _STOPWORDS]
    return " ".join(words)


def _fuzzy_pass(
    internal: list[TBEntry], audited: list[TBEntry]
) -> tuple[list[_RawMatch], list[TBEntry], list[TBEntry]]:
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
    matched: list[_RawMatch] = []

    for score, i, j in candidates:
        if i in used_internal or j in used_audited:
            continue
        if score < FUZZY_AUTO_THRESHOLD:
            continue
        used_internal.add(i)
        used_audited.add(j)
        method = "exact" if score == 100 else "fuzzy"
        matched.append(
            _RawMatch(
                internal_name=internal[i].account_name,
                internal_balance=internal[i].balance,
                audited_name=audited[j].account_name,
                audited_balance=audited[j].balance,
                audited_code=audited[j].account_code,
                method=method,
            )
        )

    remaining_internal = [e for i, e in enumerate(internal) if i not in used_internal]
    remaining_audited = [e for j, e in enumerate(audited) if j not in used_audited]
    return matched, remaining_internal, remaining_audited


_AI_SYSTEM_PROMPT = """You are an audit assistant reconciling two trial balances that use \
different account naming conventions (e.g. one client's internal chart of accounts vs. the \
auditor's working trial balance). You are given a list of unmatched internal/client accounts \
and a list of unmatched audited accounts. Match accounts that clearly represent the same \
underlying general ledger account, even if named completely differently — including cases where \
a short audit label (e.g. "Depreciation of Equipment") maps to a deeply nested client account \
path (e.g. "Educational Expenses:Occupancy Expense:Facility Depreciation Expense"). Use account \
balances as supporting evidence: if two unmatched accounts have the same or very similar balance, \
that strengthens the case for a match. Do NOT force a match if no reasonable counterpart exists — \
leaving an account unmatched is correct when it is genuinely missing from the other side.

Respond with ONLY a JSON object of this shape:
{
  "matches": [
    {"internal": "<exact internal account name>", "audited": "<exact audited account name>", "confidence": 0-100, "rationale": "<short reason>"}
  ]
}
Only include pairs you are reasonably confident represent the same account (confidence >= 60). \
Do not include unmatched accounts in the output.
"""


_AI_BATCH_SIZE = 40  # accounts per side, per call — keeps prompts fast and well within token limits


def _ai_pass_single(
    client, internal: list[TBEntry], audited: list[TBEntry]
) -> list[_RawMatch]:
    user_payload = {
        "unmatched_internal_accounts": [
            {"name": e.account_name, "balance": e.balance} for e in internal
        ],
        "unmatched_audited_accounts": [
            {"name": e.account_name, "balance": e.balance} for e in audited
        ],
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

    results: list[_RawMatch] = []
    for pair in parsed.get("matches", []):
        in_entry = internal_by_name.get(pair.get("internal"))
        au_entry = audited_by_name.get(pair.get("audited"))
        if in_entry is None or au_entry is None:
            continue
        results.append(
            _RawMatch(
                internal_name=in_entry.account_name,
                internal_balance=in_entry.balance,
                audited_name=au_entry.account_name,
                audited_balance=au_entry.balance,
                audited_code=au_entry.account_code,
                method="ai",
            )
        )
    return results


def _ai_pass(internal: list[TBEntry], audited: list[TBEntry]) -> list[_RawMatch]:
    """Runs the AI matching pass in batches of `_AI_BATCH_SIZE` internal accounts at a
    time (each batch still sees the full remaining audited list, shrinking as accounts
    get matched) so large trial balances don't blow past prompt/time limits in one call."""
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

    results: list[_RawMatch] = []
    remaining_audited = list(audited)
    for start in range(0, len(internal), _AI_BATCH_SIZE):
        if not remaining_audited:
            break
        batch_internal = internal[start : start + _AI_BATCH_SIZE]
        batch_results = _ai_pass_single(client, batch_internal, remaining_audited)
        results.extend(batch_results)
        matched_audited_names = {m.audited_name for m in batch_results}
        remaining_audited = [e for e in remaining_audited if e.account_name not in matched_audited_names]
    return results


@dataclass
class ComparisonRow:
    account_code: str | None
    account_name: str
    client_balance: float | None
    audit_balance: float | None
    method: str  # exact | fuzzy | ai | audit_only | client_only
    note: str | None = None
    client_account_name: str | None = None  # name in the client's books, for the AJE

    @property
    def difference(self) -> float:
        """Signed as audit working TB minus client records (memo convention)."""
        client = self.client_balance if self.client_balance is not None else 0.0
        audit = self.audit_balance if self.audit_balance is not None else 0.0
        return round(audit - client, 2)

    @property
    def is_material(self) -> bool:
        return abs(self.difference) >= MATERIAL_THRESHOLD


def _leaf(name: str) -> str:
    return name.split(":")[-1].strip()


@dataclass
class AJELine:
    """One line of the adjusting journal entry that brings client records to
    the audited balances: difference > 0 books a debit, < 0 a credit."""
    account_name: str
    debit: float | None
    credit: float | None


@dataclass
class ComparisonReport:
    rows: list[ComparisonRow]
    compared_column: str
    accounts_compared: int
    accounts_tied: int
    material_count: int
    only_in_client_count: int
    only_in_audit_count: int
    total_client: float
    total_audit: float
    net_difference: float
    conclusion: str
    notes: list[str] = field(default_factory=list)
    aje_rows: list[AJELine] = field(default_factory=list)


def _build_conclusion(
    material_count: int,
    only_client: int,
    only_audit: int,
    has_zero_only: bool,
    matched_exact: int,
    matched_fuzzy: int,
    matched_ai: int,
) -> str:
    matched_total = matched_exact + matched_fuzzy + matched_ai
    match_detail = ""
    if matched_total:
        breakdown = []
        if matched_exact:
            breakdown.append(f"{matched_exact} by exact name")
        if matched_fuzzy:
            breakdown.append(f"{matched_fuzzy} by close name match")
        if matched_ai:
            breakdown.append(f"{matched_ai} by AI-assisted cross-system mapping")
        match_detail = f" {matched_total} account(s) were matched ({', '.join(breakdown)})."

    if material_count == 0 and only_client == 0 and only_audit == 0:
        text = (
            "We're set! Every account carrying a balance ties between the two trial balances, "
            "and both trial balances are in balance (total debits = total credits)."
            + match_detail
        )
        if has_zero_only:
            text += " Remaining unmatched accounts are inactive $0 accounts that exist in only one chart of accounts."
        return text

    parts = [f"Not everything adds up yet.{match_detail} Here's what didn't tie out:"]
    if material_count:
        parts.append(
            f"{material_count} account(s) show a material difference (≥ $1) between the "
            "client records and the audited trial balance."
        )
    if only_client:
        parts.append(
            f"{only_client} account(s) carry a balance only in the client's records and are "
            "missing from the audited trial balance."
        )
    if only_audit:
        parts.append(
            f"{only_audit} account(s) carry a balance only on the audited trial balance and are "
            "missing from the client's records."
        )
    parts.append("These should be investigated before relying on the trial balance tie-out.")
    return " ".join(parts)


def build_comparison(
    internal: list[TBEntry], audited: list[TBEntry], compared_column: str
) -> ComparisonReport:
    matched, remaining_internal, remaining_audited = _fuzzy_pass(internal, audited)
    ai_matched = _ai_pass(remaining_internal, remaining_audited)
    matched.extend(ai_matched)

    ai_internal_names = {m.internal_name for m in ai_matched}
    ai_audited_names = {m.audited_name for m in ai_matched}
    remaining_internal = [e for e in remaining_internal if e.account_name not in ai_internal_names]
    remaining_audited = [e for e in remaining_audited if e.account_name not in ai_audited_names]

    audited_order = {e.account_name: idx for idx, e in enumerate(audited)}

    rows: list[ComparisonRow] = []
    mapping_notes: list[str] = []

    for m in matched:
        note = None
        if m.method == "ai":
            note = f'Mapped to client account "{m.internal_name}"'
            mapping_notes.append(
                f'${m.audited_balance:,.0f} balance maps across systems: client '
                f'"{_leaf(m.internal_name)}" = audit "{_leaf(m.audited_name)}".'
            )
        rows.append(
            ComparisonRow(
                account_code=m.audited_code,
                account_name=m.audited_name,
                client_balance=m.internal_balance,
                audit_balance=m.audited_balance,
                method=m.method,
                note=note,
                client_account_name=m.internal_name,
            )
        )

    audit_only_rows = [
        ComparisonRow(
            account_code=e.account_code,
            account_name=e.account_name,
            client_balance=None,
            audit_balance=e.balance,
            method="audit_only",
            note="Not in client records",
        )
        for e in remaining_audited
    ]

    # Matched + audit-only rows follow the original audit workpaper order.
    ordered = sorted(
        rows + audit_only_rows,
        key=lambda r: audited_order.get(r.account_name, len(audited_order)),
    )

    client_only_rows = [
        ComparisonRow(
            account_code=None,
            account_name=e.account_name,
            client_balance=e.balance,
            audit_balance=None,
            method="client_only",
            note="Not on audit working trial balance",
            client_account_name=e.account_name,
        )
        for e in remaining_internal
    ]

    all_rows = ordered + client_only_rows

    accounts_compared = len(matched)
    accounts_tied = sum(1 for m in matched if abs(m.internal_balance - m.audited_balance) < MATERIAL_THRESHOLD)
    material_count = accounts_compared - accounts_tied
    only_in_client_count = sum(1 for e in remaining_internal if abs(e.balance) >= 0.005)
    only_in_audit_count = sum(1 for e in remaining_audited if abs(e.balance) >= 0.005)
    # + 0.0 folds float dust's -0.0 into 0.0 so it never renders with a sign.
    total_client = round(sum(r.client_balance or 0.0 for r in all_rows), 2) + 0.0
    total_audit = round(sum(r.audit_balance or 0.0 for r in all_rows), 2) + 0.0
    net_difference = round(sum(r.difference for r in all_rows), 2) + 0.0
    has_zero_only = bool(remaining_internal or remaining_audited) and not (
        only_in_client_count or only_in_audit_count
    )
    matched_exact = sum(1 for m in matched if m.method == "exact")
    matched_fuzzy = sum(1 for m in matched if m.method == "fuzzy")
    matched_ai = sum(1 for m in matched if m.method == "ai")

    notes = [
        "Client balances are shown signed (debit positive, credit negative) to match the audit column convention.",
        f'The "{compared_column}" column differences under $1 are treated as rounding, not material.',
    ]
    notes.extend(mapping_notes)
    numbered_notes = [f"{i}. {n}" for i, n in enumerate(notes, start=1)]

    # Adjusting journal entry: one line per account whose difference isn't zero,
    # booked under the client's account name (the client posts this entry).
    aje_rows = [
        AJELine(
            account_name=r.client_account_name or r.account_name,
            debit=r.difference if r.difference > 0 else None,
            credit=-r.difference if r.difference < 0 else None,
        )
        for r in all_rows
        if abs(r.difference) >= 0.005
    ]

    return ComparisonReport(
        rows=all_rows,
        compared_column=compared_column,
        accounts_compared=accounts_compared,
        accounts_tied=accounts_tied,
        material_count=material_count,
        only_in_client_count=only_in_client_count,
        only_in_audit_count=only_in_audit_count,
        total_client=total_client,
        total_audit=total_audit,
        net_difference=net_difference,
        conclusion=_build_conclusion(
            material_count,
            only_in_client_count,
            only_in_audit_count,
            has_zero_only,
            matched_exact,
            matched_fuzzy,
            matched_ai,
        ),
        notes=numbered_notes,
        aje_rows=aje_rows,
    )
