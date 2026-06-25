"""FastAPI app: trial balance reconciliation (client records vs. audit working TB)."""
from __future__ import annotations

import base64
from pathlib import Path

from fastapi import FastAPI, Form, UploadFile
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from excel_export import build_workbook
from matching import ComparisonReport, build_comparison
from parsing import TBEntry, inspect_upload, parse_pasted_text, parse_upload

BASE_DIR = Path(__file__).resolve().parent.parent
TEMPLATES_DIR = BASE_DIR / "frontend" / "templates"
STATIC_DIR = BASE_DIR / "frontend" / "static"

app = FastAPI(title="Trial Balance Reconciliation")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/", response_class=HTMLResponse)
async def index() -> HTMLResponse:
    return HTMLResponse((TEMPLATES_DIR / "index.html").read_text())


@app.post("/api/inspect-audit")
async def inspect_audit_endpoint(audit_file: UploadFile):
    raw = await audit_file.read()
    return inspect_upload(audit_file.filename, raw)


async def _load_entries(
    file: UploadFile | None, text: str | None, balance_column: str | None = None
) -> list[TBEntry]:
    if file is not None and file.filename:
        raw = await file.read()
        return parse_upload(file.filename, raw, balance_column=balance_column)
    if text:
        return parse_pasted_text(text)
    return []


def _report_to_json(report: ComparisonReport) -> dict:
    return {
        "compared_column": report.compared_column,
        "summary": {
            "accounts_compared": report.accounts_compared,
            "accounts_tied": report.accounts_tied,
            "material_count": report.material_count,
            "only_in_client_count": report.only_in_client_count,
            "only_in_audit_count": report.only_in_audit_count,
            "net_difference": report.net_difference,
        },
        "conclusion": report.conclusion,
        "notes": report.notes,
        "rows": [
            {
                "account_code": r.account_code,
                "account_name": r.account_name,
                "client_balance": r.client_balance,
                "audit_balance": r.audit_balance,
                "difference": r.difference,
                "method": r.method,
                "note": r.note,
                "is_material": r.is_material,
            }
            for r in report.rows
        ],
    }


@app.post("/api/reconcile")
async def reconcile_endpoint(
    client_file: UploadFile | None = None,
    audit_file: UploadFile | None = None,
    client_text: str | None = Form(default=None),
    audit_text: str | None = Form(default=None),
    balance_column: str | None = Form(default=None),
    client_name: str = Form(default="Client"),
    period_label: str = Form(default=""),
):
    client_entries = await _load_entries(client_file, client_text)
    audit_entries = await _load_entries(audit_file, audit_text, balance_column=balance_column)

    if not client_entries or not audit_entries:
        return {
            "error": "Could not extract any accounts from one or both trial balances. "
            "Check the uploaded file or pasted data."
        }

    compared_column = balance_column or (audit_file.filename if audit_file else "Audited Balance")
    report = build_comparison(client_entries, audit_entries, compared_column)

    result = _report_to_json(report)
    workbook_bytes = build_workbook(report, client_name, period_label)
    result["workbook_base64"] = base64.b64encode(workbook_bytes).decode("ascii")
    return result
