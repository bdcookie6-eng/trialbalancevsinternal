# Example data

A ready-made demo pair for a fictional company, **Harborview Consulting, LLC**.
The **"Fill with example data"** button at the top of the app loads this pair into
the upload inputs in one click (served by `GET /api/example-data`); the files are
also here if you want to drag them in manually or open them in Excel:

| File | Upload into | Format it demonstrates |
| --- | --- | --- |
| `Harborview Consulting LLC Working Trial Balance 12-31-2025.xlsx` | **Audit Working Trial Balance** | Audit workpaper: Code/Account/Description header, a hidden `System Type` tag column, category headers and subtotal rows (excluded automatically), and three balance columns to pick from |
| `Harborview Consulting LLC TB Detail 12-31-2025.csv` | **Client Records** | Client export: Full name / Debit / Credit, terminated by a TOTAL row |

Both trial balances internally balance (debits = credits), like real ones would.

## Demo script

1. Click **Fill with example data** (or drag each file into its upload zone).
   Point out that the **Client name**
   ("Harborview Consulting, LLC") and **Period label** ("December 31, 2025") fill in
   automatically from the filenames, and the audit side offers a **balance column
   picker** (Unadjusted / Adjusted / Report 12/31/2025 — the Report column is
   pre-selected, and differs from Unadjusted by a depreciation adjusting entry).
2. Run the reconciliation. The example is seeded so every matching path and every
   result state shows up at once:
   - **13 exact-name matches**, including "Furniture & Equipment" ↔ "Furniture and
     Equipment" (punctuation/stopwords normalized away).
   - **1 fuzzy match**: "Insurance Expenses" ↔ "Insurance Expense".
   - **3 AI matches** (requires `ANTHROPIC_API_KEY`): differently-named accounts the
     fuzzy pass can't catch —
     - "Owner's Equity" ↔ "Members' Equity"
     - "Service Income:Consulting Fees" ↔ "Consulting Revenue"
     - "Operating Expenses:Occupancy:Facility Depreciation" ↔ "Depreciation of
       Equipment" (deep client account path → short audit label)
   - **1 material difference**: Professional Fees is $49,760.00 per the client but
     $51,750.00 per the audit — a $1,990.00 difference, highlighted in the
     Comparison sheet.
   - **1 account only in client records**: Bank Service Charges ($740.00).
   - **1 account only on the audit TB**: Interest Income ($1,250.00).
   - The three exceptions offset exactly, so the **net difference is $0** while
     material items still exist — a nice illustration of why "the totals agree" is
     not the same as "the accounts tie".
3. Download the Excel workbook and show that all three sheets (Adjusting Journal Entry,
   TB Comparison, Summary) match the in-app preview. The AJE sheet books each difference
   as a debit or credit under the client's own account name, and its TOTALS row (live SUM
   formulas plus a debit-minus-credit check cell) balances at $676,240 on each side.

Without an `ANTHROPIC_API_KEY`, the three AI-pair accounts are flagged as missing on
each side instead of matched — which itself demos the no-key fallback.
