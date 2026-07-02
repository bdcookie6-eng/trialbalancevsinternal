# Portfolio Website — Build Brief

A handoff spec for building my portfolio site in its own repository. It was written in
the session that built the trial balance tool in this repo, so a fresh Claude Code
session (or anyone else) can build the portfolio without that context.

## Goal

A professional, fast, static portfolio site that showcases the web tools I build for
audit/accounting workflows. Each tool stays deployed separately; the portfolio is a hub
that links out. First entry: the Trial Balance Reconciliation tool in this repo.

## Architecture (decided — don't relitigate)

- **Pure static site**: hand-rolled HTML/CSS + a small JS file that renders the tools
  grid from a data array. No framework, no build step, no backend, no external CDNs or
  fonts (system font stack). Revisit a static-site generator only if the site outgrows
  ~5 pages.
- **Separate repo** (e.g. `portfolio`), deployed as a **Render Static Site** (free
  tier): publish directory = repo root, no build command.
- **Why static/separate**: tool outages can't take down the portfolio; nothing
  sensitive on the marketing site; free, instant hosting; each tool keeps its own repo
  and deploy cadence.
- Custom domain later (portfolio at the apex, tools on subdomains). Build with relative
  links so the domain doesn't matter.

## Structure

```
index.html                 — everything important on one page
tools/trial-balance.html   — case study page for tool #1
static/style.css
static/tools-data.js       — TOOLS array (data, not markup)
static/tools.js            — renders the grid from TOOLS
static/img/                — screenshots (see capture instructions below)
README.md                  — deploy + "how to add a tool" instructions
```

### index.html sections

1. **Header nav**: name at left; Tools / About / Contact anchor links at right.
2. **Hero**: name, one-line positioning, one supporting sentence. Suggested copy
   (edit freely):
   - H1: `Benton Cook`
   - Tagline: `Audit & accounting automation — purpose-built tools that eliminate
     manual tie-outs.`
   - Supporting line: `I'm an auditor who builds software for the engagement work I do
     every day. These tools come from real workflows, not hypotheticals.`
3. **Tools grid** (`id="tools"`): cards rendered from `TOOLS` in `tools-data.js`. Card =
   screenshot, name, 2-sentence problem→outcome description, small tech tags, two
   buttons: **Launch tool** (external) and **Case study** (internal page).
4. **About** (`id="about"`): 3–4 sentences. Auditor + builder combination is the
   differentiator; lead with it.
5. **Contact** (`id="contact"`): email `benton.cook24@gmail.com` (mailto link) and a
   LinkedIn link (**placeholder — I need to fill in the URL**).
6. **Footer**: © year, name. Nothing else.

### Tool #1 data entry

```js
{
  slug: "trial-balance",
  name: "Trial Balance Reconciliation",
  tagline: "Ties a client's internal trial balance to the audited working trial balance and drafts the adjusting journal entry.",
  description: "Matching hundreds of accounts across two systems with different naming conventions is hours of manual tie-out work. This tool parses both files, matches accounts in three passes (exact, fuzzy, AI), and exports the client-approval workbook — AJE with live formulas, TB comparison, and summary — in minutes.",
  tags: ["Python", "FastAPI", "Claude API", "rapidfuzz", "openpyxl"],
  launchUrl: "https://trial-balance-reconciliation.onrender.com/",  // VERIFY: actual Render URL
  caseStudyUrl: "tools/trial-balance.html",
  image: "static/img/tb-upload.png",
}
```

### Case study page (tools/trial-balance.html)

Four short sections with a screenshot each where noted:

1. **The problem** — auditors receive a client TB (QuickBooks debit/credit export) and
   an audit working trial balance with no shared account numbers. Hand-matching account
   names, computing differences, and building the adjusting journal entry takes hours
   per engagement and invites transcription errors.
2. **The solution** — upload (or paste) both files; the tool detects each format,
   auto-fills client name and period from the filenames, lets you pick the audit
   balance column, then matches accounts in three passes: exact/normalized names,
   fuzzy similarity, and a Claude API pass for accounts named completely differently
   (e.g. a deep QuickBooks path mapping to a short audit label). Screenshot:
   `tb-upload.png` and `tb-aje.png`.
3. **The deliverable** — a three-sheet Excel workbook matching the real audit
   deliverable: Adjusting Journal Entry (debit/credit lines under the client's own
   account names, SUM totals, balance check cell), TB Comparison (live `=Report−Client`
   difference formulas, material differences highlighted), and a Summary with totals
   and a plain-English conclusion. What you preview in the app is exactly what
   downloads. Screenshot: `tb-comparison.png`.
4. **Notes on the build** — stateless by design (no client data is ever stored;
   `Cache-Control: no-store` on all API responses), works without an API key (AI pass
   degrades gracefully), 44-test backend suite. Keep this section short.

**Honesty rule**: no invented metrics. "Turns hours of manual tie-out into minutes" is
fine; specific fabricated numbers ("saves 2 hours per engagement") are not, unless I
supply real ones.

## Design system

Match the tool family's accounting aesthetic so everything feels related:

- **Palette**: accent navy `#1F4E78` (the AJE workbook blue; the tool UI uses
  `#2c5f8a` — standardize the portfolio on `#1F4E78`); background `#F7F9FB`; card
  white; text `#1B2430`; muted `#5A6472`; borders `#D7DDE5`.
- **Type**: system stack (`"Segoe UI", system-ui, sans-serif`), generous line-height,
  max content width ~1100px, restrained sizes (H1 ≈ 2.2rem).
- **Cards**: consistent size, 1px border + subtle shadow on hover, screenshots at a
  fixed aspect ratio (`object-fit: cover`), rounded corners ~10px.
- **Restraint**: one accent color, no animations beyond subtle hover states, no
  carousels, no stock imagery. Whitespace does the work. Must look clean on mobile
  (single-column grid) — test at 375px wide.
- **Favicon**: inline SVG data URI — navy rounded square with white "BC" monogram.
- **Open Graph / meta**: `og:title`, `og:description`, `og:image` (use the tool
  screenshot), plus a meta description — so shared links render a clean card.

## Screenshot capture (do this from the trialbalancevsinternal repo)

The screenshots must show **demo data only** (never client data). The example data and
a one-click fill button exist for exactly this purpose:

1. `cd backend && pip install -r requirements.txt && uvicorn main:app --port 8123`
2. Playwright (Chromium), viewport 1280×800, `deviceScaleFactor: 2`.
3. Load `http://127.0.0.1:8123/`, click `#example-btn`, wait for
   `#client_name` to be non-empty.
4. Capture the top of the form (client/audit dropzones + column picker) →
   `tb-upload.png`.
5. Click `#run-btn`, wait for `#results:not([hidden])`.
6. Capture `#aje-preview` (use its bounding box; clip **must not exceed the viewport**
   — scroll the element into view first) → `tb-aje.png`.
7. Capture `#comparison-table` the same way → `tb-comparison.png`.

## Deploy & maintenance

- README in the portfolio repo must cover: Render Static Site setup (connect repo,
  publish directory `.`, no build command) and **adding a tool = one new entry in
  `tools-data.js` + one case study page + screenshots**.
- Adding a new tool must never require touching `index.html`.

## Open items for me (Benton) to supply

- [ ] Confirm the live URL of the trial balance tool (Render dashboard → service URL).
- [ ] LinkedIn URL (and confirm `benton.cook24@gmail.com` as the public contact).
- [ ] Preferred site title if not "Benton Cook — Audit Automation Tools".
- [ ] Custom domain, if/when purchased.
