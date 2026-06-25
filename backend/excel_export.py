"""Build the Summary + Comparison workbook for a trial balance reconciliation."""
from __future__ import annotations

import io
import textwrap

from openpyxl import Workbook
from openpyxl.formatting.rule import FormulaRule
from openpyxl.styles import Font, PatternFill
from openpyxl.worksheet.worksheet import Worksheet

from matching import ComparisonReport, MATERIAL_THRESHOLD

NAVY = "FF1F3864"
WHITE = "FFFFFFFF"
GREEN = "FFC6EFCE"
RED = "FFFFC7CE"
YELLOW = "FFFFFF00"
NUMBER_FORMAT = r"#,##0;\(#,##0\);\-"

_HEADER_FONT = Font(bold=True, size=11, color=WHITE)
_HEADER_FILL = PatternFill(fill_type="solid", fgColor=NAVY)
_TITLE_FONT = Font(bold=True, size=14)
_SUBTITLE_FONT = Font(bold=True, size=11)
_BOLD_FONT = Font(bold=True)


def _header_row(ws: Worksheet, row: int, headers: list[str]) -> None:
    for col, text in enumerate(headers, start=1):
        cell = ws.cell(row=row, column=col, value=text)
        cell.font = _HEADER_FONT
        cell.fill = _HEADER_FILL


def _build_summary_sheet(ws: Worksheet, report: ComparisonReport, client_name: str, period_label: str) -> None:
    ws.column_dimensions["A"].width = 92
    ws.column_dimensions["B"].width = 18

    ws["A1"] = client_name
    ws["A1"].font = _TITLE_FONT
    ws["A2"] = "Trial Balance Comparison — Client Records vs. Audit Working Trial Balance"
    ws["A2"].font = _SUBTITLE_FONT
    ws["A3"] = period_label
    ws["A4"] = f'Compared column: Audit Working Trial Balance "{report.compared_column}"'

    _header_row(ws, 6, ["Result", "Count / Amount"])

    metric_rows = [
        ("Accounts compared (present in both trial balances)", report.accounts_compared, None),
        ("Accounts that tie (difference under $1)", report.accounts_tied, None),
        ("Accounts with a material difference (≥ $1)", report.material_count, report.material_count == 0),
        ("Accounts with a balance only in client records", report.only_in_client_count, report.only_in_client_count == 0),
        ("Accounts with a balance only on audit working TB", report.only_in_audit_count, report.only_in_audit_count == 0),
        ("Net difference across all accounts", report.net_difference, abs(report.net_difference) < MATERIAL_THRESHOLD),
    ]

    row = 7
    for label, value, is_good in metric_rows:
        ws.cell(row=row, column=1, value=label)
        value_cell = ws.cell(row=row, column=2, value=value)
        value_cell.number_format = NUMBER_FORMAT
        if is_good is not None:
            value_cell.fill = PatternFill(fill_type="solid", fgColor=GREEN if is_good else RED)
        row += 1

    row += 1
    ws.cell(row=row, column=1, value="Conclusion").font = _SUBTITLE_FONT
    row += 1
    for line in textwrap.wrap(report.conclusion, width=110):
        ws.cell(row=row, column=1, value=line)
        row += 1

    row += 1
    ws.cell(row=row, column=1, value="Notes").font = _SUBTITLE_FONT
    row += 1
    for note in report.notes:
        ws.cell(row=row, column=1, value=note)
        row += 1


def _build_comparison_sheet(ws: Worksheet, report: ComparisonReport, client_name: str, period_label: str) -> None:
    ws.column_dimensions["A"].width = 12
    ws.column_dimensions["B"].width = 58
    ws.column_dimensions["C"].width = 18
    ws.column_dimensions["D"].width = 22
    ws.column_dimensions["E"].width = 14
    ws.column_dimensions["F"].width = 40

    ws["A1"] = f"{client_name} — Trial Balance Comparison as of {period_label}"
    ws["A1"].font = _TITLE_FONT
    ws["A2"] = f"Per Client Records vs. Per Audit Working Trial Balance ({report.compared_column})"

    header_row = 4
    _header_row(
        ws,
        header_row,
        [
            "Account Code",
            "Account / Description",
            "Per Client Records",
            f"Per {report.compared_column}",
            "Difference",
            "Notes",
        ],
    )

    row = header_row + 1
    total_client = 0.0
    total_audit = 0.0
    for r in report.rows:
        ws.cell(row=row, column=1, value=r.account_code)
        ws.cell(row=row, column=2, value=r.account_name)
        c_cell = ws.cell(row=row, column=3, value=r.client_balance)
        a_cell = ws.cell(row=row, column=4, value=r.audit_balance)
        d_cell = ws.cell(row=row, column=5, value=r.difference)
        ws.cell(row=row, column=6, value=r.note)
        for cell in (c_cell, a_cell, d_cell):
            cell.number_format = NUMBER_FORMAT
        total_client += r.client_balance or 0.0
        total_audit += r.audit_balance or 0.0
        row += 1

    last_data_row = row - 1
    if last_data_row >= header_row + 1:
        ws.conditional_formatting.add(
            f"E{header_row + 1}:E{last_data_row}",
            FormulaRule(
                formula=[f"ABS(E{header_row + 1})>={MATERIAL_THRESHOLD}"],
                fill=PatternFill(fill_type="solid", fgColor=YELLOW),
            ),
        )

    total_cell = ws.cell(row=row, column=2, value="TOTAL")
    total_cell.font = _BOLD_FONT
    for col, value in (
        (3, round(total_client, 2)),
        (4, round(total_audit, 2)),
        (5, round(total_client - total_audit, 2)),
    ):
        cell = ws.cell(row=row, column=col, value=value)
        cell.number_format = NUMBER_FORMAT
        cell.font = _BOLD_FONT


def build_workbook(report: ComparisonReport, client_name: str, period_label: str) -> bytes:
    wb = Workbook()
    summary_ws = wb.active
    summary_ws.title = "Summary"
    _build_summary_sheet(summary_ws, report, client_name, period_label)

    comparison_ws = wb.create_sheet("Comparison")
    _build_comparison_sheet(comparison_ws, report, client_name, period_label)

    buffer = io.BytesIO()
    wb.save(buffer)
    return buffer.getvalue()
