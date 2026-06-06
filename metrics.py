"""Query layer — read-only metrics over the DuckDB that the ingestion job writes.

This module contains ALL the SQL / rollup logic and NO presentation. Every function
returns a pandas DataFrame, so it can be:
  - imported by the dashboard (pi_dashboard.py),
  - reused by any other front-end later,
  - or run on its own (`python metrics.py`) to sanity-check the numbers without marimo.

It opens DuckDB read-only and never writes, so it is safe to run while the refresh job
owns the file. Data is scoped to the configured boards at ingestion time; the optional
`board_ids` argument here narrows further for the per-team views.

"Done" is defined as `status_category_key == 'done'` (Jira's status category).
"""

import os

import duckdb
import pandas as pd


SCHEMA = "jira_data"


def duckdb_path() -> str:
    return os.environ.get("DUCKDB_PATH", "jira.duckdb")


def connect():
    """Open a read-only connection. Raises FileNotFoundError if not loaded yet."""
    path = duckdb_path()
    if not os.path.exists(path):
        raise FileNotFoundError(
            f"DuckDB file '{path}' not found — run jira_agile_pipeline.py first."
        )
    return duckdb.connect(path, read_only=True)


def _query(sql: str, params: list | None = None) -> pd.DataFrame:
    """Run a query, returning an empty DataFrame if the file/tables don't exist yet."""
    try:
        con = connect()
    except FileNotFoundError:
        return pd.DataFrame()
    try:
        return con.execute(sql, params or []).df()
    except duckdb.CatalogException:
        # Tables not created yet (first run hasn't completed).
        return pd.DataFrame()
    finally:
        con.close()


def _board_filter(board_ids, column: str) -> tuple[str, list]:
    """Build an optional `AND <column> IN (...)` clause."""
    if not board_ids:
        return "", []
    placeholders = ",".join("?" for _ in board_ids)
    return f" AND {column} IN ({placeholders})", [int(b) for b in board_ids]


# ---------------------------------------------------------------------------
# Shared issue view: union sprint + backlog issues, each carrying its board id.
# sprint_issues get their board via sprints.origin_board_id; backlog_issues
# carry board_id directly.
# ---------------------------------------------------------------------------
_ISSUES_CTE = f"""
WITH issues AS (
    SELECT
        si.id                   AS issue_id,
        si.key                  AS issue_key,
        si.summary              AS summary,
        TRY_CAST(si.story_points AS DOUBLE) AS story_points,
        si.status_name          AS status_name,
        si.status_category_key  AS status_category_key,
        si.issue_type           AS issue_type,
        si.epic_key             AS epic_key,
        si.fix_versions         AS fix_versions,
        sp.origin_board_id      AS board_id,
        si.sprint_id            AS sprint_id,
        'sprint'                AS source
    FROM {SCHEMA}.sprint_issues si
    LEFT JOIN {SCHEMA}.sprints sp ON si.sprint_id = sp.id
    UNION ALL
    SELECT
        bi.id, bi.key, bi.summary,
        TRY_CAST(bi.story_points AS DOUBLE),
        bi.status_name, bi.status_category_key, bi.issue_type,
        bi.epic_key, bi.fix_versions,
        bi.board_id, NULL AS sprint_id, 'backlog' AS source
    FROM {SCHEMA}.backlog_issues bi
)
"""


# ---------------------------------------------------------------------------
# Lookups used to populate the dashboard's selectors.
# ---------------------------------------------------------------------------
def list_boards() -> pd.DataFrame:
    """Configured team boards: id, name."""
    return _query(f"SELECT id, name FROM {SCHEMA}.boards ORDER BY name")


def list_pis(pi_pattern: str = "PI-") -> list[str]:
    """Distinct PI labels found in issue fixVersions matching the pattern.

    fix_versions is a comma-joined string; we split and keep tokens containing the
    PI pattern. Used by the `fixversion` PI strategy.
    """
    df = _query(
        _ISSUES_CTE
        + """
        SELECT DISTINCT TRIM(fv.unnest) AS pi
        FROM issues, UNNEST(string_split(issues.fix_versions, ',')) AS fv
        WHERE fv.unnest ILIKE '%' || ? || '%'
        ORDER BY pi
        """,
        [pi_pattern],
    )
    if df.empty:
        return []
    return [p for p in df["pi"].tolist() if p]


# ---------------------------------------------------------------------------
# Committed feature (epic) progress.
# ---------------------------------------------------------------------------
def epic_progress(board_ids=None) -> pd.DataFrame:
    """Per-epic story-point completion across sprint + backlog issues.

    Returns: epic_key, epic_name, total_points, done_points, remaining_points,
             total_issues, done_issues, pct_complete.
    """
    clause, params = _board_filter(board_ids, "issues.board_id")
    sql = (
        _ISSUES_CTE
        + f"""
        SELECT
            issues.epic_key,
            e.name AS epic_name,
            SUM(COALESCE(issues.story_points, 0))                                   AS total_points,
            SUM(CASE WHEN issues.status_category_key = 'done'
                     THEN COALESCE(issues.story_points, 0) ELSE 0 END)              AS done_points,
            COUNT(*)                                                                AS total_issues,
            SUM(CASE WHEN issues.status_category_key = 'done' THEN 1 ELSE 0 END)    AS done_issues
        FROM issues
        LEFT JOIN {SCHEMA}.epics e ON issues.epic_key = e.key
        WHERE issues.epic_key IS NOT NULL{clause}
        GROUP BY issues.epic_key, e.name
        ORDER BY total_points DESC
        """
    )
    df = _query(sql, params)
    if df.empty:
        return df
    df["remaining_points"] = df["total_points"] - df["done_points"]
    df["pct_complete"] = (
        (df["done_points"] / df["total_points"].replace(0, pd.NA) * 100).round(1).fillna(0)
    )
    return df


# ---------------------------------------------------------------------------
# PI goal progress.
# ---------------------------------------------------------------------------
def pi_goal_progress(pi: str | None = None, pi_pattern: str = "PI-", board_ids=None) -> pd.DataFrame:
    """Epic completion restricted to issues whose fixVersions match the PI.

    If `pi` is given, match that exact PI token; otherwise match any token
    containing `pi_pattern`. Same columns as epic_progress().
    """
    clause, params = _board_filter(board_ids, "issues.board_id")
    # Match the exact PI token if given, otherwise any token containing the pattern.
    pi_clause = " AND issues.fix_versions ILIKE '%' || ? || '%'"
    params = params + [pi if pi else pi_pattern]

    sql = (
        _ISSUES_CTE
        + f"""
        SELECT
            issues.epic_key,
            e.name AS epic_name,
            SUM(COALESCE(issues.story_points, 0))                                AS total_points,
            SUM(CASE WHEN issues.status_category_key = 'done'
                     THEN COALESCE(issues.story_points, 0) ELSE 0 END)           AS done_points,
            COUNT(*)                                                             AS total_issues,
            SUM(CASE WHEN issues.status_category_key = 'done' THEN 1 ELSE 0 END) AS done_issues
        FROM issues
        LEFT JOIN {SCHEMA}.epics e ON issues.epic_key = e.key
        WHERE 1 = 1{clause}{pi_clause}
        GROUP BY issues.epic_key, e.name
        ORDER BY total_points DESC
        """
    )
    df = _query(sql, params)
    if df.empty:
        return df
    df["remaining_points"] = df["total_points"] - df["done_points"]
    df["pct_complete"] = (
        (df["done_points"] / df["total_points"].replace(0, pd.NA) * 100).round(1).fillna(0)
    )
    return df


def sprint_goals(states=("active", "future"), board_ids=None) -> pd.DataFrame:
    """Sprint goals for the given states (the narrative side of PI progress)."""
    state_ph = ",".join("?" for _ in states)
    params = list(states)
    clause, bparams = _board_filter(board_ids, "origin_board_id")
    params += bparams
    return _query(
        f"""
        SELECT id, name, state, goal, start_date, end_date, complete_date, origin_board_id
        FROM {SCHEMA}.sprints
        WHERE state IN ({state_ph}){clause}
        ORDER BY end_date
        """,
        params,
    )


# ---------------------------------------------------------------------------
# Active sprint status.
# ---------------------------------------------------------------------------
def active_sprint_status(board_ids=None) -> pd.DataFrame:
    """Per active sprint: committed vs done points and issue counts."""
    clause, params = _board_filter(board_ids, "sp.origin_board_id")
    return _query(
        f"""
        SELECT
            sp.id   AS sprint_id,
            sp.name AS sprint_name,
            sp.goal AS goal,
            sp.origin_board_id AS board_id,
            sp.end_date AS end_date,
            COUNT(si.id) AS total_issues,
            SUM(CASE WHEN si.status_category_key = 'done' THEN 1 ELSE 0 END) AS done_issues,
            SUM(COALESCE(TRY_CAST(si.story_points AS DOUBLE), 0)) AS committed_points,
            SUM(CASE WHEN si.status_category_key = 'done'
                     THEN COALESCE(TRY_CAST(si.story_points AS DOUBLE), 0) ELSE 0 END) AS done_points
        FROM {SCHEMA}.sprints sp
        LEFT JOIN {SCHEMA}.sprint_issues si ON si.sprint_id = sp.id
        WHERE sp.state = 'active'{clause}
        GROUP BY sp.id, sp.name, sp.goal, sp.origin_board_id, sp.end_date
        ORDER BY sp.end_date
        """,
        params,
    )


def active_sprint_breakdown(board_ids=None) -> pd.DataFrame:
    """Issue counts by status category within each active sprint (for stacked bars)."""
    clause, params = _board_filter(board_ids, "sp.origin_board_id")
    return _query(
        f"""
        SELECT
            sp.name AS sprint_name,
            sp.origin_board_id AS board_id,
            COALESCE(si.status_category_key, 'unknown') AS status_category_key,
            COUNT(*) AS issues,
            SUM(COALESCE(TRY_CAST(si.story_points AS DOUBLE), 0)) AS points
        FROM {SCHEMA}.sprints sp
        JOIN {SCHEMA}.sprint_issues si ON si.sprint_id = sp.id
        WHERE sp.state = 'active'{clause}
        GROUP BY sp.name, sp.origin_board_id, status_category_key
        ORDER BY sp.name, status_category_key
        """,
        params,
    )


# ---------------------------------------------------------------------------
# Velocity trend (done points per closed sprint, per board).
# ---------------------------------------------------------------------------
def velocity_trend(board_ids=None) -> pd.DataFrame:
    clause, params = _board_filter(board_ids, "sp.origin_board_id")
    return _query(
        f"""
        SELECT
            sp.id   AS sprint_id,
            sp.name AS sprint_name,
            sp.origin_board_id AS board_id,
            sp.complete_date AS complete_date,
            SUM(CASE WHEN si.status_category_key = 'done'
                     THEN COALESCE(TRY_CAST(si.story_points AS DOUBLE), 0) ELSE 0 END) AS done_points
        FROM {SCHEMA}.sprints sp
        LEFT JOIN {SCHEMA}.sprint_issues si ON si.sprint_id = sp.id
        WHERE sp.state = 'closed'{clause}
        GROUP BY sp.id, sp.name, sp.origin_board_id, sp.complete_date
        ORDER BY sp.complete_date
        """,
        params,
    )


if __name__ == "__main__":
    # Standalone sanity check of the query layer — no marimo, no charts.
    pd.set_option("display.max_columns", None)
    pd.set_option("display.width", 200)

    print("\n=== Boards ===")
    print(list_boards())
    print("\n=== PIs found ===")
    print(list_pis())
    print("\n=== Epic (feature) progress ===")
    print(epic_progress())
    print("\n=== PI goal progress ===")
    print(pi_goal_progress())
    print("\n=== Active sprint status ===")
    print(active_sprint_status())
    print("\n=== Velocity trend ===")
    print(velocity_trend())
