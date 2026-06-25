"""Parse trial balance input (file upload or pasted text) into normalized records."""
from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass

import pandas as pd
import pdfplumber


@dataclass
class TBEntry:
    account_name: str
    balance: float


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


def _pick_columns(df: pd.DataFrame) -> tuple[str, str]:
    """Guess which columns hold account names vs balances."""
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
        # Fall back to the right-most numeric-looking column.
        for col in reversed(df.columns):
            if col != name_col:
                balance_col = col
                break
    return name_col, balance_col


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


def parse_excel(raw: bytes) -> list[TBEntry]:
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

    # Fallback: parse raw text lines like "Cash and Equivalents   12,345.67"
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
    """Parse pasted tabular text: tab/comma-separated or 'Name   Amount' per line."""
    text = text.strip()
    if not text:
        return []

    # Try CSV/TSV parsing first.
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

    # Fall back to free-form "Account Name   Amount" lines.
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


def parse_upload(filename: str, raw: bytes) -> list[TBEntry]:
    lower = filename.lower()
    if lower.endswith(".csv"):
        return parse_csv(raw)
    if lower.endswith((".xlsx", ".xls")):
        return parse_excel(raw)
    if lower.endswith(".pdf"):
        return parse_pdf(raw)
    raise ValueError(f"Unsupported file type: {filename}")
