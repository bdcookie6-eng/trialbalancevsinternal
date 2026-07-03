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

from excel_export import build_workbook
from matching import ComparisonReport, build_comparison
from parsing import TBEntry, inspect_upload, parse_pasted_text, parse_upload

BASE_DIR = Path(__file__).resolve().parent.parent
TEMPLATES_DIR = BASE_DIR / "frontend" / "templates"
STATIC_DIR = BASE_DIR / "frontend" / "static"
EXAMPLES_DIR = BASE_DIR / "examples"

_EXAMPLE_FILES = {
    "client": "Harborview Consulting LLC TB Detail 12-31-2025.csv",
    "audit": "Harborview Consulting LLC Working Trial Balance 12-31-2025.xlsx",
}

app = FastAPI(title="Trial Balance Reconciliation")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.middleware("http")
async def cache_control(request, call_next):
    """API responses carry account data — never cache them anywhere. The page and
    static assets must revalidate on every load, or browsers keep serving a stale
    app.js against fresh HTML after a deploy."""
    response = await call_next(request)
    if request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"
    else:
        response.headers["Cache-Control"] = "no-cache"
    return response


@app.get("/", response_class=HTMLResponse)
async def index() -> HTMLResponse:
    return HTMLResponse((TEMPLATES_DIR / "index.html").read_text())


@app.get("/api/example-data")
async def example_data():
    """The demo files from examples/, base64-encoded so the UI can load them
    into the normal file-upload flow with one click."""
    result = {}
    for side, filename in _EXAMPLE_FILES.items():
        path = EXAMPLES_DIR / filename
        if not path.exists():
            return {"error": "Example data files are not available on this server."}
        result[side] = {
            "filename": filename,
            "base64": base64.b64encode(path.read_bytes()).decode("ascii"),
        }
    return result


@app.post("/api/inspect")
async def inspect_endpoint(file: UploadFile):
    raw = await file.read()
    if not raw:
        return {"format": "error", "error": "The uploaded file is empty."}
    try:
        # Parsing (openpyxl/pdfplumber) is CPU-bound; keep it off the event loop.
        return await asyncio.to_thread(inspect_upload, file.filename, raw)
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
            return await asyncio.to_thread(
                parse_upload, file.filename, raw, balance_column=balance_column, mapping=mapping
            )
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
            "total_client": report.total_client,
            "total_audit": report.total_audit,
            "net_difference": report.net_difference,
        },
        "conclusion": report.conclusion,
        "notes": report.notes,
        "aje": {
            "rows": [
                {"account_name": l.account_name, "debit": l.debit, "credit": l.credit}
                for l in report.aje_rows
            ],
            "total_debit": round(sum(l.debit or 0.0 for l in report.aje_rows), 2),
            "total_credit": round(sum(l.credit or 0.0 for l in report.aje_rows), 2),
        },
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
        # build_comparison can block for a while (fuzzy matching + the AI pass
        # makes synchronous HTTP calls). Run it in a worker thread so the event
        # loop keeps serving other requests instead of freezing the whole app.
        report = await asyncio.to_thread(build_comparison, client_entries, audit_entries, compared_column)
        result = _report_to_json(report)
        workbook_bytes = await asyncio.to_thread(build_workbook, report, client_name, period_label)
    except Exception as exc:
        return {"error": f"Reconciliation failed: {exc}"}

    result["workbook_base64"] = base64.b64encode(workbook_bytes).decode("ascii")
    return result
