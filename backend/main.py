"""FastAPI app: trial balance reconciliation (client records vs. audit working TB).

Nothing here is persisted: uploads are read into memory, processed, and discarded
once the response is sent. No database, no disk writes, no server-side logging of
account data. The no-store middleware below also stops browsers/proxies from
caching responses that contain client balances.
"""
from __future__ import annotations

import asyncio
import base64
import json
from pathlib import Path

from fastapi import FastAPI, Form, UploadFile
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from excel_export import build_workbook
from matching import ComparisonReport, ComparisonRow, build_comparison
from parsing import TBEntry, inspect_upload, parse_pasted_text, parse_upload

BASE_DIR = Path(__file__).resolve().parent.parent
TEMPLATES_DIR = BASE_DIR / "frontend" / "templates"
STATIC_DIR = BASE_DIR / "frontend" / "static"

app = FastAPI(title="Trial Balance Reconciliation")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.middleware("http")
async def no_store_for_api(request, call_next):
    """Never let a response carrying account data be cached anywhere."""
    response = await call_next(request)
    if request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"
    return response


@app.get("/", response_class=HTMLResponse)
async def index() -> HTMLResponse:
    return HTMLResponse((TEMPLATES_DIR / "index.html").read_text())


@app.post("/api/inspect")
async def inspect_endpoint(file: UploadFile):
    raw = await file.read()
    if not raw:
        return {"format": "error", "error": "The uploaded file is empty."}
    try:
        return inspect_upload(file.filename, raw)
    except Exception as exc:
        return {"format": "error", "error": str(exc)}


class ReconcileInputError(Exception):
    pass


async def _load_entries(
    file: UploadFile | None,
    text: str | None,
    balance_column: str | None = None,
    mapping_json: str | None = None,
) -> list[TBEntry]:
    try:
        mapping = json.loads(mapping_json) if mapping_json else None
    except json.JSONDecodeError as exc:
        raise ReconcileInputError("Invalid column mapping submitted.") from exc

    if file is not None and file.filename:
        raw = await file.read()
        if not raw:
            raise ReconcileInputError(f"The uploaded file '{file.filename}' is empty.")
        try:
            return parse_upload(file.filename, raw, balance_column=balance_column, mapping=mapping)
        except ValueError as exc:
            raise ReconcileInputError(str(exc)) from exc
        except Exception as exc:
            raise ReconcileInputError(f"Could not read '{file.filename}': {exc}") from exc
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
    client_mapping: str | None = Form(default=None),
    audit_mapping: str | None = Form(default=None),
    client_name: str = Form(default="Client"),
    period_label: str = Form(default=""),
):
    try:
        client_entries, audit_entries = await asyncio.gather(
            _load_entries(client_file, client_text, mapping_json=client_mapping),
            _load_entries(audit_file, audit_text, balance_column=balance_column, mapping_json=audit_mapping),
        )
    except ReconcileInputError as exc:
        return {"error": str(exc)}

    if not client_entries or not audit_entries:
        return {
            "error": "Could not extract any accounts from one or both trial balances. "
            "Check the uploaded file or pasted data."
        }

    compared_column = balance_column or (audit_file.filename if audit_file else "Audited Balance")
    try:
        report = build_comparison(client_entries, audit_entries, compared_column)
        result = _report_to_json(report)
        workbook_bytes = build_workbook(report, client_name, period_label)
    except Exception as exc:
        return {"error": f"Reconciliation failed: {exc}"}

    result["workbook_base64"] = base64.b64encode(workbook_bytes).decode("ascii")
    return result


class ExportRow(BaseModel):
    account_code: str | None = None
    account_name: str
    client_balance: float | None = None
    audit_balance: float | None = None
    method: str
    note: str | None = None
    confirmed: bool = False


class ExportSummary(BaseModel):
    accounts_compared: int
    accounts_tied: int
    material_count: int
    only_in_client_count: int
    only_in_audit_count: int
    net_difference: float


class ExportRequest(BaseModel):
    compared_column: str
    client_name: str
    period_label: str
    summary: ExportSummary
    conclusion: str
    notes: list[str]
    rows: list[ExportRow]


@app.post("/api/export")
async def export_endpoint(payload: ExportRequest):
    """Regenerate the workbook with reviewer confirmations/notes baked in.

    Takes the same rows the client originally got from /api/reconcile, plus
    whatever `confirmed`/`note` edits the reviewer made in the browser, and
    rebuilds the Excel file so the sign-off is reflected in the download.
    """
    report = ComparisonReport(
        rows=[
            ComparisonRow(
                account_code=r.account_code,
                account_name=r.account_name,
                client_balance=r.client_balance,
                audit_balance=r.audit_balance,
                method=r.method,
                note=r.note,
                confirmed=r.confirmed,
            )
            for r in payload.rows
        ],
        compared_column=payload.compared_column,
        accounts_compared=payload.summary.accounts_compared,
        accounts_tied=payload.summary.accounts_tied,
        material_count=payload.summary.material_count,
        only_in_client_count=payload.summary.only_in_client_count,
        only_in_audit_count=payload.summary.only_in_audit_count,
        net_difference=payload.summary.net_difference,
        conclusion=payload.conclusion,
        notes=payload.notes,
    )
    try:
        workbook_bytes = build_workbook(report, payload.client_name, payload.period_label)
    except Exception as exc:
        return {"error": f"Could not build workbook: {exc}"}

    return {"workbook_base64": base64.b64encode(workbook_bytes).decode("ascii")}
