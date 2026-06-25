const form = document.getElementById("reconcile-form");
const statusEl = document.getElementById("status");
const resultsEl = document.getElementById("results");
const runBtn = document.getElementById("run-btn");
const auditFileInput = document.getElementById("audit_file");
const clientFileInput = document.getElementById("client_file");
const columnPicker = document.getElementById("column-picker");
const balanceColumnSelect = document.getElementById("balance_column");

function money(value) {
  if (value === null || value === undefined) return "";
  return value.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

function buildMappingPanel(target) {
  const panel = document.getElementById(`${target}-mapping-panel`);
  const headerRowSelect = panel.querySelector(".map-header-row");
  const nameColSelect = panel.querySelector(".map-name-col");
  const codeColSelect = panel.querySelector(".map-code-col");
  const balanceColSelect = panel.querySelector(".map-balance-col");
  const debitColSelect = panel.querySelector(".map-debit-col");
  const creditColSelect = panel.querySelector(".map-credit-col");
  const stopTextInput = panel.querySelector(".map-stop-text");
  const hiddenField = panel.querySelector(".mapping-json-field");
  const aiFlag = panel.querySelector(".ai-flag");
  const previewTable = panel.querySelector(".grid-preview");
  const balanceModeBtns = panel.querySelectorAll(".balance-mode-btn");
  const singleFields = panel.querySelectorAll(".balance-mode-field.single");
  const debitCreditFields = panel.querySelectorAll(".balance-mode-field.debit-credit");
  let balanceMode = "single";

  function colOptions(n, includeBlank) {
    let html = includeBlank ? `<option value="">—</option>` : "";
    for (let i = 1; i <= n; i++) html += `<option value="${i}">Column ${i}</option>`;
    return html;
  }

  function setBalanceMode(mode) {
    balanceMode = mode;
    balanceModeBtns.forEach((b) => b.classList.toggle("active", b.dataset.balanceMode === mode));
    singleFields.forEach((el) => (el.hidden = mode !== "single"));
    debitCreditFields.forEach((el) => (el.hidden = mode !== "debit-credit"));
  }

  function updateHidden() {
    const mapping = {
      header_row: Number(headerRowSelect.value),
      name_col: Number(nameColSelect.value),
      code_col: codeColSelect.value ? Number(codeColSelect.value) : null,
      stop_text: stopTextInput.value.trim() || null,
    };
    if (balanceMode === "single") {
      mapping.balance_col = balanceColSelect.value ? Number(balanceColSelect.value) : null;
    } else {
      mapping.debit_col = debitColSelect.value ? Number(debitColSelect.value) : null;
      mapping.credit_col = creditColSelect.value ? Number(creditColSelect.value) : null;
    }
    hiddenField.value = JSON.stringify(mapping);
  }

  balanceModeBtns.forEach((btn) => {
    btn.addEventListener("click", () => {
      setBalanceMode(btn.dataset.balanceMode);
      updateHidden();
    });
  });
  [headerRowSelect, nameColSelect, codeColSelect, balanceColSelect, debitColSelect, creditColSelect].forEach(
    (el) => el.addEventListener("change", updateHidden)
  );
  stopTextInput.addEventListener("input", updateHidden);

  return {
    show(info) {
      const rows = info.grid_preview || [];
      const numCols = rows.reduce((max, r) => Math.max(max, r.length), 0);

      previewTable.querySelector("thead").innerHTML =
        "<tr>" + Array.from({ length: numCols }, (_, i) => `<th>Col ${i + 1}</th>`).join("") + "</tr>";
      previewTable.querySelector("tbody").innerHTML = rows
        .slice(0, 10)
        .map((r) => "<tr>" + r.map((c) => `<td>${c ?? ""}</td>`).join("") + "</tr>")
        .join("");

      headerRowSelect.innerHTML =
        `<option value="0">No header row</option>` +
        rows.slice(0, 20).map((_, i) => `<option value="${i + 1}">Row ${i + 1}</option>`).join("");
      nameColSelect.innerHTML = colOptions(numCols, false);
      codeColSelect.innerHTML = colOptions(numCols, true);
      balanceColSelect.innerHTML = colOptions(numCols, false);
      debitColSelect.innerHTML = colOptions(numCols, false);
      creditColSelect.innerHTML = colOptions(numCols, false);

      const suggestion = info.suggested_mapping;
      aiFlag.hidden = !suggestion;
      setBalanceMode("single");
      if (suggestion) {
        headerRowSelect.value = String(suggestion.header_row ?? 0);
        nameColSelect.value = String(suggestion.name_col ?? 1);
        codeColSelect.value = suggestion.code_col ? String(suggestion.code_col) : "";
        stopTextInput.value = suggestion.stop_text || "";
        if (suggestion.balance_col) {
          balanceColSelect.value = String(suggestion.balance_col);
        } else if (suggestion.debit_col || suggestion.credit_col) {
          debitColSelect.value = suggestion.debit_col ? String(suggestion.debit_col) : "";
          creditColSelect.value = suggestion.credit_col ? String(suggestion.credit_col) : "";
          setBalanceMode("debit-credit");
        }
      }

      panel.hidden = false;
      updateHidden();
    },
    hide() {
      panel.hidden = true;
      hiddenField.value = "";
    },
  };
}

const mappingPanels = {
  client: buildMappingPanel("client"),
  audit: buildMappingPanel("audit"),
};

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
      if (target === "audit") columnPicker.hidden = true;
      mappingPanels[target].hide();
    }
  });
});

async function handleFileInspect(target, file) {
  if (!file) {
    if (target === "audit") columnPicker.hidden = true;
    mappingPanels[target].hide();
    return;
  }
  const formData = new FormData();
  formData.append("file", file);
  try {
    const response = await fetch("/api/inspect", { method: "POST", body: formData });
    const info = await response.json();

    if (info.format === "error" || info.format === "unsupported") {
      if (target === "audit") columnPicker.hidden = true;
      mappingPanels[target].hide();
      statusEl.textContent = info.error || "Could not read the uploaded file.";
      statusEl.className = "error";
      return;
    }

    if (target === "audit" && info.format === "audit_workpaper" && info.columns && info.columns.length) {
      balanceColumnSelect.innerHTML = info.columns
        .map((c) => `<option value="${c}" ${c === info.default_column ? "selected" : ""}>${c}</option>`)
        .join("");
      columnPicker.hidden = false;
    } else if (target === "audit") {
      columnPicker.hidden = true;
    }

    if (info.format === "unknown") {
      mappingPanels[target].show(info);
    } else {
      mappingPanels[target].hide();
    }
  } catch (err) {
    if (target === "audit") columnPicker.hidden = true;
    mappingPanels[target].hide();
    statusEl.textContent = `Could not inspect the uploaded file: ${err.message}`;
    statusEl.className = "error";
  }
}

clientFileInput.addEventListener("change", () => handleFileInspect("client", clientFileInput.files[0]));
auditFileInput.addEventListener("change", () => handleFileInspect("audit", auditFileInput.files[0]));

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
