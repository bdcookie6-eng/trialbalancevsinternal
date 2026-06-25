document.querySelectorAll(".mode-btn").forEach((btn) => {
  btn.addEventListener("click", () => {
    const target = btn.dataset.target;
    const mode = btn.dataset.mode;
    document
      .querySelectorAll(`.mode-btn[data-target="${target}"]`)
      .forEach((b) => b.classList.toggle("active", b === btn));

    const fileInput = document.getElementById(`${target}_file`);
    const textInput = document.getElementById(`${target}_text`);
    if (mode === "file") {
      fileInput.hidden = false;
      textInput.hidden = true;
    } else {
      fileInput.hidden = true;
      textInput.hidden = false;
      if (target === "audit") {
        document.getElementById("column-picker").hidden = true;
      }
    }
  });
});

const form = document.getElementById("reconcile-form");
const statusEl = document.getElementById("status");
const resultsEl = document.getElementById("results");
const runBtn = document.getElementById("run-btn");
const auditFileInput = document.getElementById("audit_file");
const columnPicker = document.getElementById("column-picker");
const balanceColumnSelect = document.getElementById("balance_column");

function money(value) {
  if (value === null || value === undefined) return "";
  return value.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

auditFileInput.addEventListener("change", async () => {
  const file = auditFileInput.files[0];
  if (!file) {
    columnPicker.hidden = true;
    return;
  }
  const formData = new FormData();
  formData.append("audit_file", file);
  try {
    const response = await fetch("/api/inspect-audit", { method: "POST", body: formData });
    const info = await response.json();
    if (info.is_audit_workpaper && info.columns && info.columns.length) {
      balanceColumnSelect.innerHTML = info.columns
        .map((c) => `<option value="${c}" ${c === info.default_column ? "selected" : ""}>${c}</option>`)
        .join("");
      columnPicker.hidden = false;
    } else {
      columnPicker.hidden = true;
    }
  } catch (err) {
    columnPicker.hidden = true;
  }
});

function renderSummaryPreview(data) {
  document.getElementById("preview-title").textContent = document.getElementById("client_name").value;
  document.getElementById("preview-subtitle").textContent =
    "Trial Balance Comparison — Client Records vs. Audit Working Trial Balance";
  document.getElementById("preview-period").textContent = document.getElementById("period_label").value;
  document.getElementById("preview-column").textContent =
    `Compared column: Audit Working Trial Balance "${data.compared_column}"`;
  document.getElementById("audit-col-header").textContent = `Per ${data.compared_column}`;

  const s = data.summary;
  const metricRows = [
    ["Accounts compared (present in both trial balances)", s.accounts_compared, null],
    ["Accounts that tie (difference under $1)", s.accounts_tied, null],
    ["Accounts with a material difference (≥ $1)", s.material_count, s.material_count === 0],
    ["Accounts with a balance only in client records", s.only_in_client_count, s.only_in_client_count === 0],
    ["Accounts with a balance only on audit working TB", s.only_in_audit_count, s.only_in_audit_count === 0],
    ["Net difference across all accounts", s.net_difference, Math.abs(s.net_difference) < 1],
  ];

  const tbody = document.querySelector("#summary-cards");
  tbody.innerHTML = "";
  metricRows.forEach(([label, value, isGood]) => {
    const tr = document.createElement("tr");
    if (isGood !== null) tr.classList.add(isGood ? "row-good" : "row-bad");
    tr.innerHTML = `<td>${label}</td><td>${money(value)}</td>`;
    tbody.appendChild(tr);
  });

  document.getElementById("preview-conclusion").textContent = data.conclusion;

  const notesList = document.getElementById("preview-notes");
  notesList.innerHTML = "";
  data.notes.forEach((note) => {
    const li = document.createElement("li");
    li.textContent = note.replace(/^\d+\.\s*/, "");
    notesList.appendChild(li);
  });
}

function renderComparisonPreview(data) {
  const tbody = document.querySelector("#comparison-table tbody");
  tbody.innerHTML = "";
  let totalClient = 0;
  let totalAudit = 0;
  data.rows.forEach((r) => {
    const tr = document.createElement("tr");
    if (r.is_material) tr.classList.add("mismatch");
    if (r.method === "ai") tr.classList.add("ai-match");
    tr.innerHTML = `
      <td>${r.account_code ?? ""}</td>
      <td>${r.account_name}</td>
      <td>${money(r.client_balance)}</td>
      <td>${money(r.audit_balance)}</td>
      <td>${money(r.difference)}</td>
      <td>${r.note ?? ""}</td>
    `;
    tbody.appendChild(tr);
    totalClient += r.client_balance || 0;
    totalAudit += r.audit_balance || 0;
  });

  const totalRow = document.getElementById("comparison-total");
  totalRow.innerHTML = `
    <td></td>
    <td><strong>TOTAL</strong></td>
    <td><strong>${money(totalClient)}</strong></td>
    <td><strong>${money(totalAudit)}</strong></td>
    <td><strong>${money(totalClient - totalAudit)}</strong></td>
    <td></td>
  `;
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  statusEl.textContent = "Running reconciliation…";
  statusEl.className = "";
  resultsEl.hidden = true;
  runBtn.disabled = true;

  try {
    const formData = new FormData(form);
    const response = await fetch("/api/reconcile", { method: "POST", body: formData });
    const data = await response.json();

    if (data.error) {
      statusEl.textContent = data.error;
      statusEl.className = "error";
      return;
    }

    statusEl.textContent = "";

    renderSummaryPreview(data);
    renderComparisonPreview(data);

    const downloadLink = document.getElementById("download-link");
    const blob = await (await fetch(`data:application/vnd.openxmlformats-officedocument.spreadsheetml.sheet;base64,${data.workbook_base64}`)).blob();
    downloadLink.href = URL.createObjectURL(blob);

    resultsEl.hidden = false;
  } catch (err) {
    statusEl.textContent = `Unexpected error: ${err.message}`;
    statusEl.className = "error";
  } finally {
    runBtn.disabled = false;
  }
});
