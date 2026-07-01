# Trial Balance Reconciliation

Compares a client's internal trial balance against the audited working trial balance for
the same period to confirm no accounts were missed and balances tie out — a post-audit
safety check. There are no account numbers consistent across both sides, only account
names, so matching is fuzzy:

1. **Exact/fuzzy pass** (`rapidfuzz`) auto-matches accounts whose names are identical or
   near-identical (token-sorted similarity ≥ 90).
2. **AI pass** (Claude API) handles whatever's left — abbreviations, reordering, rollups,
   deeply nested client account paths mapping to a short audit label, synonyms — and
   proposes matches with a confidence score and rationale, using balance similarity as
   supporting evidence. Anything the AI can't confidently match stays flagged as missing
   on one side.

The output mirrors a standard audit tie-out memo: a **Summary** sheet (counts, conclusion,
notes) plus a **Comparison** sheet (per-account detail, ordered by the audit workpaper,
with a TOTAL row and material differences highlighted).

Each trial balance can be supplied as a **file** (CSV, XLSX, or PDF) or as **pasted text**.

## Setup

```bash
cd backend
pip install -r requirements.txt
export ANTHROPIC_API_KEY=sk-ant-...   # required for the AI matching pass
uvicorn main:app --reload --port 8000
```

Open http://localhost:8000.

Without `ANTHROPIC_API_KEY` set, the tool still runs — it just skips the AI pass and
flags everything the fuzzy matcher couldn't resolve as missing on one side.

## Supported formats

- **Audit working trial balance**: a Code / Account / Description header row plus one or
  more balance columns (Unadjusted, Adjusted, "Report <date>", ...). The UI lets you pick
  which balance column to compare once a file is uploaded (`/api/inspect`). Category
  header rows and "<code> Total" subtotal rows are excluded automatically — using a hidden
  tag column (e.g. "System Type") when present, or a heuristic fallback otherwise.
- **Client trial balance export**: a Full name / Debit / Credit header row (XLSX or CSV),
  netted into a signed balance (Credit − Debit), terminated by a "TOTAL" row.
- **Generic CSV/XLSX/PDF/pasted text**: a single name column and a single balance column,
  guessed from headers.
- **Anything else**: if none of the above match with confidence, the file falls back to a
  manual mapping flow — the AI pass proposes a header row + column mapping when an
  `ANTHROPIC_API_KEY` is set, and the UI always shows a grid preview with column pickers
  (header row, name/code/balance or debit/credit columns, an optional stop-row marker) so
  you can confirm or correct the mapping before running the reconciliation.

## How it works

- `backend/parsing.py` — detects the format of each upload (`inspect_upload`) and extracts
  `(account_name, balance, account_code)` rows (`parse_upload` / `parse_pasted_text`).
  Unrecognized files get a grid preview plus an AI-suggested or user-supplied column
  mapping (`ColumnMapping` / `parse_with_mapping`) instead of being dropped.
- `backend/matching.py` — runs the hybrid rapidfuzz → Claude matching pipeline
  (`build_comparison`) and produces a `ComparisonReport`: matched/unmatched rows, balance
  differences, summary counts, a plain-English conclusion, and supporting notes.
- `backend/excel_export.py` — renders a `ComparisonReport` into a styled two-sheet
  workbook (`build_workbook`): Summary (health-check counts with green/red highlighting,
  conclusion, notes) and Comparison (per-account detail with conditional formatting on
  material differences and a TOTAL row).
- `backend/main.py` — FastAPI app: serves the UI and exposes:
  - `POST /api/inspect` — upload either file, get back its detected format: audit balance
    columns to pick from, or (for unrecognized files) a grid preview and suggested mapping.
  - `POST /api/reconcile` — runs the full pipeline and returns the same JSON used to
    render the in-app preview plus a base64-encoded Excel workbook, so what you preview
    and what you download are guaranteed identical. Accepts an optional `client_mapping` /
    `audit_mapping` JSON field to override detection for unrecognized files.
- `frontend/` — single-page UI: client name/period inputs, upload-or-paste inputs for each
  trial balance, a balance-column picker for audit workpapers, a manual column-mapping
  panel for unrecognized files, a Summary/Comparison sheet preview, and a download button
  for the matching workbook.

Nothing is persisted server-side — each run is stateless.
