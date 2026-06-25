"""FastAPI app: trial balance reconciliation (internal vs. audited)."""
from __future__ import annotations

import base64
import io
from pathlib import Path

import pandas as pd
from fastapi import FastAPI, Form, UploadFile
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from matching import MatchReport, reconcile
from parsing import TBEntry, parse_pasted_text, parse_upload

BASE_DIR = Path(__file__).resolve().parent.parent
TEMPLATES_DIR = BASE_DIR / "frontend" / "templates"
STATIC_DIR = BASE_DIR / "frontend" / "static"

app = FastAPI(title="Trial Balance Reconciliation")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/", response_class=HTMLResponse)
async def index() -> HTMLResponse:
    return HTMLResponse((TEMPLATES_DIR / "index.html").read_text())


async def _load_entries(file: UploadFile | None, text: str | None) -> list[TBEntry]:
    if file is not None and file.filename:
        raw = await file.read()
        return parse_upload(file.filename, raw)
    if text:
        return parse_pasted_text(text)
    return []


def _build_workbook(report: MatchReport) -> bytes:
    matched_rows = [
        {
            "Internal Account": m.internal_name,
            "Internal Balance": m.internal_balance,
            "Audited Account": m.audited_name,
            "Audited Balance": m.audited_balance,
            "Difference": m.difference,
            "Balances Agree": m.balances_agree,
            "Match Method": m.method,
            "Confidence": m.confidence,
            "Rationale": m.rationale,
        }
        for m in report.matched
    ]
    missing_audited_rows = [
        {"Internal Account": m.internal_name, "Internal Balance": m.internal_balance}
        for m in report.missing_in_audited
    ]
    missing_internal_rows = [
        {"Audited Account": m.audited_name, "Audited Balance": m.audited_balance}
        for m in report.missing_in_internal
    ]

    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        pd.DataFrame(matched_rows).to_excel(writer, sheet_name="Matched", index=False)
        pd.DataFrame(missing_audited_rows).to_excel(
            writer, sheet_name="Missing in Audited", index=False
        )
        pd.DataFrame(missing_internal_rows).to_excel(
            writer, sheet_name="Missing in Internal", index=False
        )
    return buffer.getvalue()


def _report_to_json(report: MatchReport) -> dict:
    return {
        "matched": [
            {
                "internal_name": m.internal_name,
                "internal_balance": m.internal_balance,
                "audited_name": m.audited_name,
                "audited_balance": m.audited_balance,
                "difference": m.difference,
                "balances_agree": m.balances_agree,
                "method": m.method,
                "confidence": m.confidence,
                "rationale": m.rationale,
            }
            for m in report.matched
        ],
        "missing_in_audited": [
            {"internal_name": m.internal_name, "internal_balance": m.internal_balance}
            for m in report.missing_in_audited
        ],
        "missing_in_internal": [
            {"audited_name": m.audited_name, "audited_balance": m.audited_balance}
            for m in report.missing_in_internal
        ],
        "summary": {
            "matched_count": len(report.matched),
            "missing_in_audited_count": len(report.missing_in_audited),
            "missing_in_internal_count": len(report.missing_in_internal),
            "balance_mismatches": sum(
                1 for m in report.matched if m.balances_agree is False
            ),
        },
    }


@app.post("/api/reconcile")
async def reconcile_endpoint(
    internal_file: UploadFile | None = None,
    audited_file: UploadFile | None = None,
    internal_text: str | None = Form(default=None),
    audited_text: str | None = Form(default=None),
):
    internal_entries = await _load_entries(internal_file, internal_text)
    audited_entries = await _load_entries(audited_file, audited_text)

    if not internal_entries or not audited_entries:
        return {
            "error": "Could not extract any accounts from one or both trial balances. "
            "Check the uploaded file or pasted data."
        }

    report = reconcile(internal_entries, audited_entries)
    result = _report_to_json(report)
    result["workbook_base64"] = base64.b64encode(_build_workbook(report)).decode("ascii")
    return result
