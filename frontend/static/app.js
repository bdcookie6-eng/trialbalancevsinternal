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
    }
  });
});

const form = document.getElementById("reconcile-form");
const statusEl = document.getElementById("status");
const resultsEl = document.getElementById("results");
const runBtn = document.getElementById("run-btn");

function money(value) {
  if (value === null || value === undefined) return "";
  return value.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

function fillTable(tbodyId, rows, renderRow) {
  const tbody = document.querySelector(`#${tbodyId} tbody`);
  tbody.innerHTML = "";
  rows.forEach((row) => tbody.appendChild(renderRow(row)));
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

    const summary = data.summary;
    document.getElementById("summary-cards").innerHTML = `
      <div class="summary-card ok"><div class="value">${summary.matched_count}</div><div>Matched Accounts</div></div>
      <div class="summary-card ${summary.missing_in_audited_count ? "warn" : ""}">
        <div class="value">${summary.missing_in_audited_count}</div><div>Missing in Audited TB</div>
      </div>
      <div class="summary-card ${summary.missing_in_internal_count ? "warn" : ""}">
        <div class="value">${summary.missing_in_internal_count}</div><div>Missing in Internal TB</div>
      </div>
      <div class="summary-card ${summary.balance_mismatches ? "warn" : "ok"}">
        <div class="value">${summary.balance_mismatches}</div><div>Balance Mismatches</div>
      </div>
    `;

    fillTable("matched-table", data.matched, (m) => {
      const tr = document.createElement("tr");
      if (m.balances_agree === false) tr.classList.add("mismatch");
      if (m.method === "ai") tr.classList.add("ai-match");
      tr.innerHTML = `
        <td>${m.internal_name ?? ""}</td>
        <td>${money(m.internal_balance)}</td>
        <td>${m.audited_name ?? ""}</td>
        <td>${money(m.audited_balance)}</td>
        <td>${money(m.difference)}</td>
        <td>${m.method}</td>
        <td>${m.confidence ?? ""}</td>
      `;
      if (m.rationale) tr.title = m.rationale;
      return tr;
    });

    fillTable("missing-audited-table", data.missing_in_audited, (m) => {
      const tr = document.createElement("tr");
      tr.classList.add("mismatch");
      tr.innerHTML = `<td>${m.internal_name ?? ""}</td><td>${money(m.internal_balance)}</td>`;
      return tr;
    });

    fillTable("missing-internal-table", data.missing_in_internal, (m) => {
      const tr = document.createElement("tr");
      tr.classList.add("mismatch");
      tr.innerHTML = `<td>${m.audited_name ?? ""}</td><td>${money(m.audited_balance)}</td>`;
      return tr;
    });

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
