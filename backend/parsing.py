"""Parse trial balance input (file upload or pasted text) into normalized records.

Handles two real-world shapes in addition to generic CSV/pasted data:

1. Audit workpaper ("Working Trial Balance"): Code / Account / Description columns
   plus several balance columns (Unadjusted, Adjusted, Report <date>, ...). Category
   header rows and "<code> Total" subtotal rows must be excluded. Some exports include
   a hidden tag column (e.g. "System Type") whose values start with "Account_" for real
   account rows, "SubTotal_"/"Header_"/"Code_..." for structural rows, etc. — when present
   this is used to identify account rows precisely; otherwise we fall back to heuristics.

2. Client TB export: Full name / Debit / Credit columns, terminated by a "TOTAL" row.
   The signed balance is Debit - Credit.
"""
from __future__ import annotations

import csv
import io
import json
import os
import re
from dataclasses import asdict, dataclass

import openpyxl
import pandas as pd
import pdfplumber


@dataclass
class TBEntry:
    account_name: str
    balance: float
    account_code: str | None = None


_AMOUNT_RE = re.compile(r"-?\(?\$?\s*-?[\d,]+\.?\d*\)?")


def _to_float(value) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if not text:
        return None
    negative = text.startswith("(") and text.endswith(")")
    text = text.strip("()").replace("$", "").replace(",", "").strip()
    if not text:
        return None
    try:
        amount = float(text)
    except ValueError:
        return None
    return -amount if negative else amount


def _load_workbook(raw: bytes):
    try:
        wb = openpyxl.load_workbook(io.BytesIO(raw), data_only=True)
    except Exception as exc:
        raise ValueError(
            "Could not read this as an Excel file — it may be corrupted, password-protected, "
            "or not actually an .xlsx file."
        ) from exc
    if not wb.worksheets:
        raise ValueError("This Excel file has no sheets.")
    return wb


def _load_grid(filename: str, raw: bytes) -> list[list]:
    """Load a file into a plain 2D grid of cell values, 1-indexed by row/column
    position for callers. Shared by the client-format detector and the
    column-mapping fallback so both work across xlsx and csv alike."""
    lower = filename.lower()
    if lower.endswith((".xlsx", ".xls")):
        wb = _load_workbook(raw)
        ws = wb.worksheets[0]
        return [[c.value for c in row] for row in ws.iter_rows()]
    if lower.endswith(".csv"):
        try:
            text = raw.decode("utf-8-sig", errors="replace")
        except Exception as exc:
            raise ValueError("Could not read this CSV file's text encoding.") from exc
        return list(csv.reader(io.StringIO(text)))
    raise ValueError(f"Cannot grid-load file type: {filename}")


# ---------------------------------------------------------------------------
# Audit workpaper format
# ---------------------------------------------------------------------------

_TAG_ACCOUNT_PREFIXES = ("Account_",)
_TAG_NON_ACCOUNT_PREFIXES = (
    "Header_",
    "SubcodeBlankRow_",
    "SubTotal",
    "Code_",
    "GrandTotalRow",
    "NetIncomeRow",
    "TotalAssets",
    "TotalLiabilities",
    "TotalEquity",
    "TotalRevenue",
    "TotalExpense",
)


def _find_header_row(ws) -> int | None:
    for row in ws.iter_rows(min_row=1, max_row=min(30, ws.max_row)):
        values = [str(c.value).strip().lower() if c.value is not None else "" for c in row]
        if "code" in values and "account" in values and "description" in values:
            return row[0].row
    return None


def inspect_audit_workpaper(raw: bytes) -> dict:
    """Return detected balance columns so the caller can choose which to compare."""
    wb = _load_workbook(raw)
    ws = wb.worksheets[0]
    header_row_idx = _find_header_row(ws)
    if header_row_idx is None:
        return {"is_audit_workpaper": False, "columns": []}

    header_cells = next(ws.iter_rows(min_row=header_row_idx, max_row=header_row_idx))
    headers = [str(c.value).strip() if c.value is not None else "" for c in header_cells]

    balance_keywords = ("report", "adjusted", "unadjusted", "balance")
    candidates = [
        h
        for h in headers
        if h
        and any(k in h.lower() for k in balance_keywords)
        and "tie-out" not in h.lower()
        and "tie out" not in h.lower()
    ]

    # Prefer the right-most column whose header looks like "Report <date>".
    report_cols = [h for h in candidates if h.lower().startswith("report")]
    default = report_cols[-1] if report_cols else (candidates[-1] if candidates else None)

    return {"is_audit_workpaper": True, "columns": candidates, "default_column": default}


def _classify_rows_by_tag(ws, header_row_idx: int) -> list[int] | None:
    """If a tag column (e.g. 'System Type') exists, return row indices that are real accounts."""
    header_cells = next(ws.iter_rows(min_row=header_row_idx, max_row=header_row_idx))
    tag_col_idx = None
    for c in header_cells:
        if c.value and "system type" in str(c.value).strip().lower():
            tag_col_idx = c.column
            break
    if tag_col_idx is None:
        return None

    account_rows = []
    for row in ws.iter_rows(min_row=header_row_idx + 1, max_row=ws.max_row):
        tag_cell = row[tag_col_idx - 1]
        tag = tag_cell.value
        if isinstance(tag, str) and tag.startswith(_TAG_ACCOUNT_PREFIXES):
            account_rows.append(tag_cell.row)
    return account_rows


def parse_audit_workpaper(raw: bytes, balance_column: str | None = None) -> list[TBEntry]:
    wb = _load_workbook(raw)
    ws = wb.worksheets[0]
    header_row_idx = _find_header_row(ws)
    if header_row_idx is None:
        raise ValueError("Could not find a Code/Account/Description header row.")

    header_cells = next(ws.iter_rows(min_row=header_row_idx, max_row=header_row_idx))
    headers = {str(c.value).strip(): c.column for c in header_cells if c.value is not None}

    code_col = headers.get("Code")
    account_col = headers.get("Account")
    description_col = headers.get("Description")
    if description_col is None:
        raise ValueError("Audit workpaper is missing a Description column.")

    if balance_column is None:
        info = inspect_audit_workpaper(raw)
        balance_column = info.get("default_column")
    balance_col = headers.get(balance_column)
    if balance_col is None:
        raise ValueError(f"Balance column '{balance_column}' not found in workpaper.")

    account_rows = _classify_rows_by_tag(ws, header_row_idx)

    entries: list[TBEntry] = []
    if account_rows is not None:
        for row_idx in account_rows:
            description = ws.cell(row=row_idx, column=description_col).value
            balance = _to_float(ws.cell(row=row_idx, column=balance_col).value)
            code = ws.cell(row=row_idx, column=account_col).value if account_col else None
            if description and balance is not None:
                entries.append(
                    TBEntry(
                        account_name=str(description).strip(),
                        balance=balance,
                        account_code=str(code).strip() if code else None,
                    )
                )
    else:
        # Heuristic fallback: a real account row has a non-blank Account code and
        # a Description, and its Code column does NOT contain "Total".
        for row in ws.iter_rows(min_row=header_row_idx + 1, max_row=ws.max_row):
            code_val = row[code_col - 1].value if code_col else None
            account_val = row[account_col - 1].value if account_col else None
            description_val = row[description_col - 1].value
            if code_val and "total" in str(code_val).lower():
                continue
            if not account_val or not description_val:
                continue
            balance = _to_float(row[balance_col - 1].value)
            if balance is None:
                continue
            entries.append(
                TBEntry(
                    account_name=str(description_val).strip(),
                    balance=balance,
                    account_code=str(account_val).strip(),
                )
            )
    return entries


# ---------------------------------------------------------------------------
# Client TB (debit/credit) format
# ---------------------------------------------------------------------------


def _find_client_header_row(grid: list[list]) -> int | None:
    for i, row in enumerate(grid[: min(20, len(grid))], start=1):
        values = [str(c).strip().lower() if c is not None else "" for c in row]
        has_name = any(v in ("full name", "account name", "account") for v in values)
        has_debit_credit = "debit" in values and "credit" in values
        if has_name and has_debit_credit:
            return i
    return None


def is_client_debit_credit_format(filename: str, raw: bytes) -> bool:
    return _find_client_header_row(_load_grid(filename, raw)) is not None


def parse_client_debit_credit(filename: str, raw: bytes) -> list[TBEntry]:
    grid = _load_grid(filename, raw)
    header_row_idx = _find_client_header_row(grid)
    if header_row_idx is None:
        raise ValueError("Could not find a Full name/Debit/Credit header row.")

    header = [str(c).strip().lower() if c is not None else "" for c in grid[header_row_idx - 1]]

    def col_index(*names: str) -> int | None:
        for name in names:
            if name in header:
                return header.index(name)
        return None

    name_idx = col_index("full name", "account name", "account")
    debit_idx = col_index("debit")
    credit_idx = col_index("credit")

    entries: list[TBEntry] = []
    for row in grid[header_row_idx:]:
        name = row[name_idx] if name_idx is not None and name_idx < len(row) else None
        if not name:
            continue
        name = str(name).strip()
        if name.upper() == "TOTAL":
            break
        debit = _to_float(row[debit_idx]) if debit_idx is not None and debit_idx < len(row) else None
        credit = _to_float(row[credit_idx]) if credit_idx is not None and credit_idx < len(row) else None
        entries.append(TBEntry(account_name=name, balance=(debit or 0.0) - (credit or 0.0)))
    return entries


# ---------------------------------------------------------------------------
# Generic fallback (arbitrary CSV/XLSX/PDF/pasted text with one balance column)
# ---------------------------------------------------------------------------


def _pick_columns(df: pd.DataFrame) -> tuple[str, str]:
    name_col = None
    balance_col = None
    for col in df.columns:
        label = str(col).strip().lower()
        if name_col is None and any(k in label for k in ("account", "name", "description")):
            name_col = col
        if balance_col is None and any(
            k in label for k in ("balance", "amount", "debit", "credit", "net")
        ):
            balance_col = col
    if name_col is None:
        name_col = df.columns[0]
    if balance_col is None:
        for col in reversed(df.columns):
            if col != name_col:
                balance_col = col
                break
    return name_col, balance_col


def _generic_columns_confident(df: pd.DataFrame) -> bool:
    """True only if both a name-like and a balance-like column were found by
    keyword, not by falling back to 'first column' / 'last other column'."""
    labels = [
        str(c).strip().lower()
        for c in df.columns
        if not re.match(r"^unnamed:?\s*\d+$", str(c).strip().lower())
    ]
    name_found = any(any(k in label for k in ("account", "name", "description")) for label in labels)
    balance_found = any(
        any(k in label for k in ("balance", "amount", "debit", "credit", "net")) for label in labels
    )
    return name_found and balance_found


def _rows_to_entries(df: pd.DataFrame) -> list[TBEntry]:
    name_col, balance_col = _pick_columns(df)
    entries: list[TBEntry] = []
    for _, row in df.iterrows():
        name = str(row.get(name_col, "")).strip()
        if not name or name.lower() in ("nan", "total", "totals", "grand total"):
            continue
        balance = _to_float(row.get(balance_col))
        if balance is None:
            continue
        entries.append(TBEntry(account_name=name, balance=balance))
    return entries


def parse_csv(raw: bytes) -> list[TBEntry]:
    df = pd.read_csv(io.BytesIO(raw))
    return _rows_to_entries(df)


def parse_excel_generic(raw: bytes) -> list[TBEntry]:
    df = pd.read_excel(io.BytesIO(raw))
    return _rows_to_entries(df)


def parse_pdf(raw: bytes) -> list[TBEntry]:
    entries: list[TBEntry] = []
    with pdfplumber.open(io.BytesIO(raw)) as pdf:
        for page in pdf.pages:
            for table in page.extract_tables() or []:
                if not table or len(table) < 2:
                    continue
                header, *rows = table
                df = pd.DataFrame(rows, columns=header)
                entries.extend(_rows_to_entries(df))
    if entries:
        return entries

    with pdfplumber.open(io.BytesIO(raw)) as pdf:
        for page in pdf.pages:
            text = page.extract_text() or ""
            for line in text.splitlines():
                line = line.strip()
                if not line:
                    continue
                match = re.search(r"(.+?)\s+(\(?-?\$?[\d,]+\.\d{2}\)?)\s*$", line)
                if not match:
                    continue
                name = match.group(1).strip()
                balance = _to_float(match.group(2))
                if name and balance is not None and not name.lower().startswith("total"):
                    entries.append(TBEntry(account_name=name, balance=balance))
    return entries


def parse_pasted_text(text: str) -> list[TBEntry]:
    text = text.strip()
    if not text:
        return []

    sniff_sample = text[:2048]
    delimiter = "\t" if "\t" in sniff_sample else ("," if "," in sniff_sample else None)
    if delimiter:
        reader = csv.reader(io.StringIO(text), delimiter=delimiter)
        rows = list(reader)
        if rows:
            header_like = rows[0]
            looks_like_header = not any(_to_float(cell) is not None for cell in header_like)
            if looks_like_header:
                df = pd.DataFrame(rows[1:], columns=header_like)
            else:
                df = pd.DataFrame(rows)
            entries = _rows_to_entries(df)
            if entries:
                return entries

    entries: list[TBEntry] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        match = re.search(r"(.+?)[\s,]+(\(?-?\$?[\d,]+\.?\d*\)?)\s*$", line)
        if not match:
            continue
        name = match.group(1).strip().strip(",")
        balance = _to_float(match.group(2))
        if name and balance is not None and name.lower() not in ("total", "totals"):
            entries.append(TBEntry(account_name=name, balance=balance))
    return entries


# ---------------------------------------------------------------------------
# Column-mapping fallback: for files that don't match any known shape, an
# AI-suggested mapping (confirmed or corrected by the user) replaces the
# heuristics entirely.
# ---------------------------------------------------------------------------


@dataclass
class ColumnMapping:
    header_row: int  # 1-indexed; 0 means there is no header row
    name_col: int  # 1-indexed
    code_col: int | None = None
    balance_col: int | None = None
    debit_col: int | None = None
    credit_col: int | None = None
    stop_text: str | None = None  # row whose name cell equals this (case-insensitive) ends the data

    @classmethod
    def from_dict(cls, data: dict) -> "ColumnMapping":
        try:
            header_row = int(data["header_row"])
            name_col = int(data["name_col"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(
                "Column mapping is missing a header row or account name column."
            ) from exc
        try:
            return cls(
                header_row=header_row,
                name_col=name_col,
                code_col=int(data["code_col"]) if data.get("code_col") else None,
                balance_col=int(data["balance_col"]) if data.get("balance_col") else None,
                debit_col=int(data["debit_col"]) if data.get("debit_col") else None,
                credit_col=int(data["credit_col"]) if data.get("credit_col") else None,
                stop_text=data.get("stop_text") or None,
            )
        except (TypeError, ValueError) as exc:
            raise ValueError("Column mapping has a non-numeric column index.") from exc


def parse_with_mapping(filename: str, raw: bytes, mapping: ColumnMapping) -> list[TBEntry]:
    grid = _load_grid(filename, raw)

    def cell(row: list, col: int | None):
        if not col:
            return None
        idx = col - 1
        return row[idx] if idx < len(row) else None

    entries: list[TBEntry] = []
    for row in grid[mapping.header_row :]:
        name_val = cell(row, mapping.name_col)
        if not name_val:
            continue
        name = str(name_val).strip()
        if not name:
            continue
        if mapping.stop_text and name.lower() == mapping.stop_text.strip().lower():
            break

        if mapping.balance_col:
            balance = _to_float(cell(row, mapping.balance_col))
        else:
            debit = _to_float(cell(row, mapping.debit_col)) or 0.0
            credit = _to_float(cell(row, mapping.credit_col)) or 0.0
            balance = debit - credit
        if balance is None:
            continue

        code_val = cell(row, mapping.code_col)
        entries.append(
            TBEntry(
                account_name=name,
                balance=balance,
                account_code=str(code_val).strip() if code_val else None,
            )
        )
    return entries


_FORMAT_DETECT_SYSTEM_PROMPT = """You are looking at the raw grid (rows x columns, as a JSON array of \
arrays) of a trial balance export whose column headers could not be recognized automatically. Identify \
which row is the header row, which column holds the account name, which holds either a single signed \
balance or a debit/credit pair, which (optional) column holds an account code, and what exact text (if \
any) marks a "total" row where the data ends.

Respond with ONLY a JSON object of this shape:
{
  "header_row": <1-indexed row number, or 0 if there is no header row>,
  "name_col": <1-indexed column number>,
  "code_col": <1-indexed column number, or null>,
  "balance_col": <1-indexed column number, or null — use this OR debit_col/credit_col, not both>,
  "debit_col": <1-indexed column number, or null>,
  "credit_col": <1-indexed column number, or null>,
  "stop_text": "<exact text marking the end of data, e.g. 'TOTAL', or null>",
  "confidence": 0-100
}
If you cannot confidently identify the account-name and balance columns, set confidence below 50.
"""


def _ai_detect_mapping(filename: str, raw: bytes) -> tuple[ColumnMapping | None, int]:
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return None, 0
    try:
        import anthropic
    except ImportError:
        return None, 0

    try:
        grid = _load_grid(filename, raw)
    except ValueError:
        return None, 0

    sample = grid[:40]
    client = anthropic.Anthropic(api_key=api_key)
    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=500,
        system=_FORMAT_DETECT_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": json.dumps(sample, default=str)}],
    )

    text = "".join(block.text for block in response.content if block.type == "text")
    try:
        parsed = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if not match:
            return None, 0
        parsed = json.loads(match.group(0))

    confidence = int(parsed.get("confidence", 0))
    if parsed.get("header_row") is None or not parsed.get("name_col"):
        return None, confidence

    mapping = ColumnMapping(
        header_row=int(parsed["header_row"]),
        name_col=int(parsed["name_col"]),
        code_col=parsed.get("code_col"),
        balance_col=parsed.get("balance_col"),
        debit_col=parsed.get("debit_col"),
        credit_col=parsed.get("credit_col"),
        stop_text=parsed.get("stop_text"),
    )
    return mapping, confidence


def _inspect_unknown(filename: str, raw: bytes) -> dict:
    grid = _load_grid(filename, raw)
    preview = [[("" if c is None else str(c)) for c in row] for row in grid[:20]]
    mapping, confidence = _ai_detect_mapping(filename, raw)
    return {
        "format": "unknown",
        "grid_preview": preview,
        "suggested_mapping": asdict(mapping) if mapping and confidence >= 50 else None,
        "mapping_confidence": confidence,
    }


# ---------------------------------------------------------------------------
# Format auto-detection entry points
# ---------------------------------------------------------------------------


def inspect_upload(filename: str, raw: bytes) -> dict:
    """Inspect a file before full parsing: report detected format and, for audit
    workpapers, the available balance columns to choose from. Falls back to an
    AI-suggested column mapping (for the user to confirm or fix) when the file
    doesn't match any recognized shape."""
    lower = filename.lower()
    if lower.endswith((".xlsx", ".xls")):
        audit_info = inspect_audit_workpaper(raw)
        if audit_info["is_audit_workpaper"]:
            return {"format": "audit_workpaper", **audit_info}
        if is_client_debit_credit_format(filename, raw):
            return {"format": "client_debit_credit"}
        if _generic_columns_confident(pd.read_excel(io.BytesIO(raw))):
            return {"format": "generic"}
        return _inspect_unknown(filename, raw)
    if lower.endswith(".csv"):
        if is_client_debit_credit_format(filename, raw):
            return {"format": "client_debit_credit"}
        if _generic_columns_confident(pd.read_csv(io.BytesIO(raw))):
            return {"format": "generic"}
        return _inspect_unknown(filename, raw)
    if lower.endswith(".pdf"):
        return {"format": "generic"}
    return {
        "format": "unsupported",
        "error": f"Unsupported file type: {filename}. Please upload a CSV, XLSX, or PDF file.",
    }


def parse_upload(
    filename: str,
    raw: bytes,
    balance_column: str | None = None,
    mapping: dict | None = None,
) -> list[TBEntry]:
    if mapping is not None:
        return parse_with_mapping(filename, raw, ColumnMapping.from_dict(mapping))

    lower = filename.lower()
    if lower.endswith((".xlsx", ".xls")):
        audit_info = inspect_audit_workpaper(raw)
        if audit_info["is_audit_workpaper"]:
            return parse_audit_workpaper(raw, balance_column=balance_column)
        if is_client_debit_credit_format(filename, raw):
            return parse_client_debit_credit(filename, raw)
        return parse_excel_generic(raw)
    if lower.endswith(".csv"):
        if is_client_debit_credit_format(filename, raw):
            return parse_client_debit_credit(filename, raw)
        return parse_csv(raw)
    if lower.endswith(".pdf"):
        return parse_pdf(raw)
    raise ValueError(f"Unsupported file type: {filename}")
