# Trial Balance Reconciliation

Compares a client's internal trial balance against the audited trial balance for the
same period to confirm no accounts were missed and balances tie out — a post-audit
safety check. There are no account numbers, only account names, so matching is fuzzy:

1. **Exact/fuzzy pass** (`rapidfuzz`) auto-matches accounts whose names are identical or
   near-identical (token-sorted similarity ≥ 90).
2. **AI pass** (Claude API) handles whatever's left — abbreviations, reordering, rollups,
   synonyms — and proposes matches with a confidence score and rationale. Anything the AI
   can't confidently match stays flagged as missing on one side.

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
flags everything the fuzzy matcher couldn't resolve as missing.

## How it works

- `backend/parsing.py` — extracts `(account_name, balance)` rows from uploads or pasted
  text, guessing the account-name and balance columns from headers.
- `backend/matching.py` — runs the hybrid rapidfuzz → Claude matching pipeline and
  produces matched pairs, balance differences, and missing-account lists.
- `backend/main.py` — FastAPI app: serves the UI and exposes `POST /api/reconcile`,
  which returns the reconciliation JSON plus a base64-encoded Excel workbook
  (`Matched` / `Missing in Audited` / `Missing in Internal` sheets) for download.
- `frontend/` — single-page UI: upload-or-paste inputs for each trial balance, a results
  table with match method/confidence, and a download button.

Nothing is persisted server-side — each run is stateless.
