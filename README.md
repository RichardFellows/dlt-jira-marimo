# Scaled-Agile Dashboard (Jira)

A daily dashboard for **committed feature work** and **Planning Increment (PI) goal
progress** across your scrum teams, built on the existing dlt → DuckDB Jira setup.

The work is split into **three independent layers** so refresh, querying, and display
can be run/scheduled separately:

| Layer | File | Role |
|-------|------|------|
| **Ingestion** | `jira_agile_pipeline.py` | dlt → DuckDB. The only writer. Scheduled. |
| **Query** | `metrics.py` | Read-only DataFrame functions (all SQL/rollups). Runnable standalone. |
| **Presentation** | `pi_dashboard.py` | marimo web app. Imports `metrics.py`, only renders. |

The original issue-search pipeline (`jira_pipeline.py`, `jira-analysis.py`) is untouched.

## What it shows

- **PI goal progress** — feature (epic) completion within the selected PI + sprint goals.
- **Committed feature progress** — done vs remaining story points per epic.
- **Active sprint status** — committed vs done points, composition by status.
- **Velocity trend** — completed points per closed sprint, one series per team.
- A reserved **GitLab oversight** section (blocked MRs / failed pipelines) for a follow-up.

The Jira model assumed: **Software boards + sprints** (`/rest/agile/1.0/...`) and
**Epics-as-features**.

## Configure

1. **Secrets** — `cp .dlt/secrets.toml.example .dlt/secrets.toml` and set `pat_token`
   and `base_url`. (Gitignored.)
2. **Config** — edit `[sources.jira_agile]` in `.dlt/config.toml`:
   - `board_ids` — your two teams' scrum boards. Find them with:
     ```
     curl -H "Authorization: Bearer $PAT" "$BASE/rest/agile/1.0/board?maxResults=50"
     ```
   - `story_point_field` / `sprint_field` / `epic_link_field` — custom-field ids for
     **your** instance (they vary). Find them with:
     ```
     curl -H "Authorization: Bearer $PAT" "$BASE/rest/api/2/field" | grep -i story
     ```
   - `pi_pattern` — the fixVersion prefix your teams use for a PI (default `PI-`).

## Run locally (Python)

```bash
pip install -r requirements.txt
python jira_agile_pipeline.py     # ingest -> jira.duckdb
python metrics.py                 # sanity-check the numbers (no UI)
marimo run pi_dashboard.py        # open the dashboard
```

## Run with Docker (recommended)

```bash
cp .env.example .env              # set JIRA_PAT_TOKEN / JIRA_BASE_URL
docker compose up --build
```

- `refresh` ingests on start, then re-runs every 24h.
- `dashboard` serves the web app at <http://localhost:2718> (reads DuckDB read-only).
- Both share the `jira-data` volume (`/data/jira.duckdb`).

## Verify the load

After ingestion, confirm the tables/columns:

```python
import dlt
ds = dlt.pipeline("jira_agile_pipeline", destination="duckdb", dataset_name="jira_data").dataset()
print(ds.schema.data_tables())   # boards, sprints, epics, sprint_issues, backlog_issues
```

## Notes / things to verify against your API

dlt config must not be guessed. If a view is empty, check:

- `data_selector` (`values` for board/sprint/epic, `issues` for sprint/backlog issues).
- The custom-field ids actually carry values on issues.
- `status.statusCategory.key` is returned (it is when `status` is in `issue_fields`).
- Epic link is the configured custom field **or** `parent.key` (the pipeline tries both).
- Sprint/epic endpoints may not return `total` — the pipeline handles this by stopping
  on an empty page (`total_path=None`).
