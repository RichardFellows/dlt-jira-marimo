import marimo

__generated_with = "0.14.17"
app = marimo.App(width="full")


@app.cell
def _():
    import marimo as mo
    return (mo,)


@app.cell
def _():
    import duckdb
    import pandas as pd
    import altair as alt
    return alt, duckdb, pd


@app.cell
def _():
    # ── CONFIGURATION ────────────────────────────────────────────────────────────
    # DB_PATH: path to the DuckDB file created by jira_pipeline.py.
    # Column names use dlt's double-underscore flattening of nested Jira fields.
    # Run the Field Discovery accordion below to verify column names after the
    # first pipeline run.
    DB_PATH = "jira_pipeline.duckdb"
    SCHEMA = "jira_data"
    PI_COL = "fields__pi"
    TEAM_COL = "fields__team"
    SPRINT_NAME_COL = "fields__sprint_name"
    SPRINT_START_COL = "fields__sprint_start_date"
    SPRINT_END_COL = "fields__sprint_end_date"
    POINTS_COL = "fields__story_points"
    ISSUETYPE_COL = "fields__issuetype__name"
    STORY_TYPES = ("Story", "Bug")
    return (
        DB_PATH, SCHEMA, PI_COL, TEAM_COL, SPRINT_NAME_COL,
        SPRINT_START_COL, SPRINT_END_COL, POINTS_COL,
        ISSUETYPE_COL, STORY_TYPES,
    )


@app.cell
def _(mo):
    mo.md("# PI Burn-up & Team Velocity Tracker")
    return


@app.cell
def _(DB_PATH, SCHEMA, duckdb, mo):
    try:
        _conn = duckdb.connect(DB_PATH, read_only=True)
        _cols = _conn.execute(f"DESCRIBE {SCHEMA}.issues").df()
        _conn.close()
        _discovery = mo.accordion({
            "Field discovery — expand to verify column names match the config above":
                mo.ui.table(_cols)
        })
    except Exception as _e:
        _discovery = mo.callout(
            mo.md(
                f"**Cannot connect to `{DB_PATH}`:** {_e}\n\n"
                "Run `python jira_pipeline.py` to load Jira data first, "
                "then confirm `DB_PATH` in the config cell above."
            ),
            kind="danger",
        )
    _discovery
    return


@app.cell
def _(DB_PATH, SCHEMA, PI_COL, duckdb, mo):
    try:
        _conn = duckdb.connect(DB_PATH, read_only=True)
        _df = _conn.execute(
            f"SELECT DISTINCT {PI_COL} AS pi "
            f"FROM {SCHEMA}.issues WHERE {PI_COL} IS NOT NULL ORDER BY {PI_COL}"
        ).df()
        _conn.close()
        _opts = _df["pi"].tolist()
    except Exception:
        _opts = []

    pi_selector = mo.ui.dropdown(
        options=_opts,
        label="Program Increment",
        value=_opts[-1] if _opts else None,
    )
    mo.vstack([mo.md("## Filters"), pi_selector])
    return (pi_selector,)


@app.cell
def _(DB_PATH, SCHEMA, PI_COL, TEAM_COL, ISSUETYPE_COL, STORY_TYPES, pi_selector, duckdb, mo):
    if pi_selector.value:
        _types_sql = ", ".join(f"'{t}'" for t in STORY_TYPES)
        _pi_safe = pi_selector.value.replace("'", "''")
        try:
            _conn = duckdb.connect(DB_PATH, read_only=True)
            _df = _conn.execute(f"""
                SELECT DISTINCT {TEAM_COL} AS team
                FROM {SCHEMA}.issues
                WHERE {PI_COL} = '{_pi_safe}'
                  AND {ISSUETYPE_COL} IN ({_types_sql})
                  AND {TEAM_COL} IS NOT NULL
                ORDER BY {TEAM_COL}
            """).df()
            _conn.close()
            _opts = _df["team"].tolist()
        except Exception:
            _opts = []
    else:
        _opts = []

    team_selector = mo.ui.multiselect(
        options=_opts,
        value=_opts,
        label="Teams",
    )
    team_selector
    return (team_selector,)


@app.cell
def _(
    DB_PATH, SCHEMA, PI_COL, TEAM_COL, SPRINT_NAME_COL, SPRINT_START_COL,
    SPRINT_END_COL, POINTS_COL, ISSUETYPE_COL, STORY_TYPES,
    pi_selector, team_selector, duckdb, pd,
):
    if not pi_selector.value or not team_selector.value:
        stories_df = pd.DataFrame()
        sprints_df = pd.DataFrame()
    else:
        _types_sql = ", ".join(f"'{t}'" for t in STORY_TYPES)
        _teams_sql = ", ".join(
            f"'{t.replace(chr(39), chr(39)*2)}'" for t in team_selector.value
        )
        _pi_safe = pi_selector.value.replace("'", "''")

        _conn = duckdb.connect(DB_PATH, read_only=True)
        stories_df = _conn.execute(f"""
            WITH done AS (
                SELECT issue_id, MIN(created::TIMESTAMP) AS done_at
                FROM {SCHEMA}.changelog
                WHERE field = 'status' AND to_string = 'Done'
                GROUP BY issue_id
            )
            SELECT
                i.id,
                i.{TEAM_COL}                                AS team,
                i.{SPRINT_NAME_COL}                         AS sprint_name,
                TRY_CAST(i.{SPRINT_START_COL} AS TIMESTAMP) AS sprint_start,
                TRY_CAST(i.{SPRINT_END_COL}   AS TIMESTAMP) AS sprint_end,
                COALESCE(i.{POINTS_COL}, 0)::DOUBLE         AS points,
                d.done_at
            FROM {SCHEMA}.issues i
            LEFT JOIN done d ON d.issue_id = i.id
            WHERE i.{PI_COL} = '{_pi_safe}'
              AND i.{ISSUETYPE_COL} IN ({_types_sql})
              AND i.{TEAM_COL} IN ({_teams_sql})
        """).df()
        _conn.close()

        sprints_df = (
            stories_df[
                stories_df["sprint_name"].notna()
                & stories_df["sprint_end"].notna()
            ]
            .groupby("sprint_name", as_index=False)
            .agg(
                sprint_start=("sprint_start", "min"),
                sprint_end=("sprint_end", "max"),
            )
            .sort_values("sprint_end")
            .reset_index(drop=True)
        )

    return stories_df, sprints_df


@app.cell
def _(stories_df, sprints_df, pd):
    if stories_df.empty or sprints_df.empty:
        burnup_df = pd.DataFrame()
    else:
        _total = float(stories_df["points"].sum())
        _n = len(sprints_df)
        _rows = []
        for _idx, _sprint in sprints_df.iterrows():
            _end = _sprint["sprint_end"]
            _done = float(
                stories_df[
                    stories_df["done_at"].notna() & (stories_df["done_at"] <= _end)
                ]["points"].sum()
            )
            _rows.append({
                "sprint": _sprint["sprint_name"],
                "sprint_end": _end,
                "Scope": _total,
                "Completed": _done,
                "Ideal": (_idx + 1) / _n * _total,
            })
        burnup_df = pd.DataFrame(_rows)
    return (burnup_df,)


@app.cell
def _(stories_df, sprints_df, team_selector, pd):
    if stories_df.empty or sprints_df.empty:
        velocity_df = pd.DataFrame()
    else:
        _rows = []
        for _idx, _sprint in sprints_df.iterrows():
            _s_start = _sprint["sprint_start"]
            _s_end = _sprint["sprint_end"]
            for _team in team_selector.value:
                _ts = stories_df[stories_df["team"] == _team]
                if pd.notna(_s_start):
                    _mask = (
                        _ts["done_at"].notna()
                        & (_ts["done_at"] >= _s_start)
                        & (_ts["done_at"] <= _s_end)
                    )
                else:
                    # fall back: match by sprint name if start date is missing
                    _mask = (
                        _ts["done_at"].notna()
                        & (_ts["done_at"] <= _s_end)
                        & (_ts["sprint_name"] == _sprint["sprint_name"])
                    )
                _rows.append({
                    "sprint": _sprint["sprint_name"],
                    "sprint_end": _s_end,
                    "sprint_idx": int(_idx),
                    "team": _team,
                    "velocity": float(_ts[_mask]["points"].sum()),
                })
        velocity_df = pd.DataFrame(_rows)
    return (velocity_df,)


@app.cell
def _(burnup_df, pi_selector, alt, mo):
    if burnup_df.empty:
        _burnup_out = mo.callout(
            mo.md("No data for the selected PI and teams."), kind="warn"
        )
    else:
        _order = burnup_df["sprint"].tolist()
        _melt = burnup_df.melt(
            id_vars=["sprint", "sprint_end"],
            value_vars=["Scope", "Completed", "Ideal"],
            var_name="Series",
            value_name="Points",
        )
        _burnup_out = (
            alt.Chart(_melt)
            .mark_line(point=True, strokeWidth=2)
            .encode(
                x=alt.X("sprint:N", sort=_order, title="Sprint"),
                y=alt.Y("Points:Q", title="Story Points"),
                color=alt.Color(
                    "Series:N",
                    scale=alt.Scale(
                        domain=["Scope", "Completed", "Ideal"],
                        range=["#ff7f0e", "#2ca02c", "#aec7e8"],
                    ),
                    legend=alt.Legend(title="Series"),
                ),
                strokeDash=alt.StrokeDash(
                    "Series:N",
                    scale=alt.Scale(
                        domain=["Scope", "Completed", "Ideal"],
                        range=[[6, 3], [0, 0], [4, 4]],
                    ),
                ),
                tooltip=[
                    "sprint:N",
                    "Series:N",
                    alt.Tooltip("Points:Q", format=".0f"),
                ],
            )
            .properties(
                title=f"PI Burn-up — {pi_selector.value}",
                width=750,
                height=360,
            )
        )
    _burnup_out
    return


@app.cell
def _(velocity_df, alt, mo):
    if velocity_df.empty:
        _vel_out = mo.callout(
            mo.md("No velocity data for the selected PI and teams."), kind="warn"
        )
    else:
        _order = (
            velocity_df.sort_values("sprint_idx")["sprint"].unique().tolist()
        )
        _bars = (
            alt.Chart(velocity_df)
            .mark_bar(opacity=0.75)
            .encode(
                x=alt.X("sprint:N", sort=_order, title="Sprint"),
                y=alt.Y("velocity:Q", title="Story Points"),
                color=alt.Color("team:N", title="Team"),
                xOffset="team:N",
                tooltip=[
                    "sprint:N",
                    "team:N",
                    alt.Tooltip("velocity:Q", format=".0f", title="Points"),
                ],
            )
        )
        # 3-sprint rolling average per team overlaid as a dashed trend line
        _trend = (
            alt.Chart(velocity_df)
            .mark_line(strokeDash=[5, 3], strokeWidth=2, point=False)
            .transform_window(
                rolling_avg="mean(velocity)",
                frame=[-2, 0],
                groupby=["team"],
                sort=[{"field": "sprint_idx", "order": "ascending"}],
            )
            .encode(
                x=alt.X("sprint:N", sort=_order),
                y=alt.Y("rolling_avg:Q"),
                color=alt.Color("team:N"),
                tooltip=[
                    "sprint:N",
                    "team:N",
                    alt.Tooltip("rolling_avg:Q", format=".1f", title="3-sprint avg"),
                ],
            )
        )
        _vel_out = (_bars + _trend).properties(
            title="Team Velocity by Sprint  ·  bars = actual  ·  dashed = 3-sprint rolling avg",
            width=750,
            height=360,
        )
    _vel_out
    return


@app.cell
def _(burnup_df, velocity_df, pi_selector, mo, pd):
    if burnup_df.empty:
        mo.callout(mo.md("Select a PI to see summary stats."), kind="info")
    else:
        _total = burnup_df["Scope"].iloc[0]
        _done = burnup_df["Completed"].iloc[-1]
        _pct = _done / _total * 100 if _total else 0

        _avg_vel = (
            velocity_df.groupby("team", as_index=False)["velocity"]
            .mean()
            .rename(columns={"velocity": "Avg pts / sprint"})
            .round(1)
        ) if not velocity_df.empty else pd.DataFrame()

        mo.vstack([
            mo.md(f"## {pi_selector.value} — Summary"),
            mo.hstack([
                mo.stat(value=f"{int(_total)} pts", label="Total Scope"),
                mo.stat(value=f"{int(_done)} pts", label="Completed"),
                mo.stat(value=f"{_pct:.0f}%", label="Progress"),
            ]),
            mo.md("### Average velocity per team"),
            mo.ui.table(_avg_vel) if not _avg_vel.empty else mo.md("—"),
        ])
    return


if __name__ == "__main__":
    app.run()
