"""Presentation layer — Scaled-Agile PI / feature dashboard (marimo web app).

This layer ONLY renders. It imports the query layer (`metrics.py`) and contains no SQL
and runs no pipeline — data is produced by the ingestion job (jira_agile_pipeline.py),
which is scheduled separately. Served as a standalone web app with:

    marimo run pi_dashboard.py --host 0.0.0.0 --port 2718

(In `marimo run` / app mode the code cells are hidden and only this UI is shown.)
"""

import marimo

__generated_with = "0.14.17"
app = marimo.App(width="medium")


@app.cell
def _():
    import marimo as mo
    import altair as alt
    import pandas as pd

    import metrics

    return alt, metrics, mo, pd


@app.cell
def _(mo):
    mo.md(
        """
        # 🚀 Scaled-Agile Dashboard — PI & Feature Progress

        Daily view of committed feature work and progress toward Planning Increment (PI)
        goals across your teams. Data is refreshed by the ingestion job; this page only
        reads it.
        """
    )
    return


@app.cell
def _(metrics, mo):
    # --- Selectors (drive every section below reactively) -------------------
    boards_df = metrics.list_boards()
    if boards_df.empty:
        board_options = {}
    else:
        board_options = {
            f"{row['name']} ({row['id']})": int(row["id"])
            for _, row in boards_df.iterrows()
        }

    board_select = mo.ui.multiselect(
        options=board_options,
        value=list(board_options.keys()),  # default: all configured boards
        label="Teams / boards",
    )

    pi_options = metrics.list_pis()
    pi_select = mo.ui.dropdown(
        options=["(all PIs)"] + pi_options,
        value="(all PIs)",
        label="Planning Increment",
    )

    mo.hstack([board_select, pi_select], justify="start", gap=2)
    return board_select, boards_df, pi_select


@app.cell
def _(board_select, pi_select):
    # Resolve selector values into args for the query layer.
    selected_boards = list(board_select.value) if board_select.value else None
    selected_pi = None if pi_select.value in (None, "(all PIs)") else pi_select.value
    return selected_boards, selected_pi


@app.cell
def _(boards_df, mo):
    mo.stop(
        boards_df.empty,
        mo.md(
            """
            ### ⏳ No data yet

            The DuckDB database has no `boards` table. Run the ingestion job first:

            ```
            python jira_agile_pipeline.py
            ```

            (or `docker compose up` — the refresh service loads it on start).
            """
        ),
    )
    return


@app.cell
def _(mo):
    mo.md("## 🎯 PI Goal Progress")
    return


@app.cell
def _(alt, metrics, mo, selected_boards, selected_pi):
    pi_df = metrics.pi_goal_progress(pi=selected_pi, board_ids=selected_boards)

    if pi_df.empty:
        pi_view = mo.md(
            "_No issues match the selected PI. Check the `pi_pattern` / fixVersion "
            "convention in `.dlt/config.toml`._"
        )
    else:
        pi_chart = (
            alt.Chart(pi_df)
            .mark_bar()
            .encode(
                x=alt.X("pct_complete:Q", title="% complete", scale=alt.Scale(domain=[0, 100])),
                y=alt.Y("epic_name:N", title="Feature (epic)", sort="-x"),
                color=alt.Color("pct_complete:Q", scale=alt.Scale(scheme="greens"), legend=None),
                tooltip=["epic_key", "epic_name", "done_points", "total_points", "pct_complete"],
            )
            .properties(height=max(120, 28 * len(pi_df)), width=620, title="Feature completion within PI")
        )
        pi_view = mo.vstack([mo.ui.altair_chart(pi_chart), pi_df])

    pi_view
    return


@app.cell
def _(metrics, mo, selected_boards):
    goals_df = metrics.sprint_goals(board_ids=selected_boards)
    mo.vstack(
        [
            mo.md("**Sprint goals (active & upcoming)**"),
            goals_df[["name", "state", "goal", "end_date"]] if not goals_df.empty
            else mo.md("_No active or future sprints found._"),
        ]
    )
    return


@app.cell
def _(mo):
    mo.md("## 📦 Committed Feature (Epic) Progress")
    return


@app.cell
def _(alt, metrics, mo, pd, selected_boards):
    epic_df = metrics.epic_progress(board_ids=selected_boards)

    if epic_df.empty:
        epic_view = mo.md("_No epics with issues found for the selected teams._")
    else:
        # Long form for a stacked done/remaining bar.
        stacked = pd.melt(
            epic_df,
            id_vars=["epic_key", "epic_name"],
            value_vars=["done_points", "remaining_points"],
            var_name="state",
            value_name="points",
        )
        epic_chart = (
            alt.Chart(stacked)
            .mark_bar()
            .encode(
                x=alt.X("points:Q", title="Story points", stack="zero"),
                y=alt.Y("epic_name:N", title="Feature (epic)", sort="-x"),
                color=alt.Color(
                    "state:N",
                    scale=alt.Scale(
                        domain=["done_points", "remaining_points"],
                        range=["#2e7d32", "#cfd8dc"],
                    ),
                    legend=alt.Legend(title=""),
                ),
                tooltip=["epic_key", "epic_name", "points", "state"],
            )
            .properties(height=max(120, 28 * len(epic_df)), width=620, title="Done vs remaining per feature")
        )
        epic_view = mo.vstack([mo.ui.altair_chart(epic_chart), epic_df])

    epic_view
    return


@app.cell
def _(mo):
    mo.md("## 🏃 Active Sprint Status")
    return


@app.cell
def _(alt, metrics, mo, selected_boards):
    sprint_df = metrics.active_sprint_status(board_ids=selected_boards)
    breakdown_df = metrics.active_sprint_breakdown(board_ids=selected_boards)

    if sprint_df.empty:
        sprint_view = mo.md("_No active sprints for the selected teams._")
    else:
        if breakdown_df.empty:
            chart_part = mo.md("")
        else:
            bd_chart = (
                alt.Chart(breakdown_df)
                .mark_bar()
                .encode(
                    x=alt.X("issues:Q", title="Issues", stack="zero"),
                    y=alt.Y("sprint_name:N", title="Sprint", sort="-x"),
                    color=alt.Color("status_category_key:N", legend=alt.Legend(title="Status")),
                    tooltip=["sprint_name", "status_category_key", "issues", "points"],
                )
                .properties(height=max(100, 30 * breakdown_df["sprint_name"].nunique()), width=620,
                            title="Active sprint composition by status")
            )
            chart_part = mo.ui.altair_chart(bd_chart)

        sprint_view = mo.vstack(
            [
                chart_part,
                sprint_df[
                    [
                        "sprint_name",
                        "goal",
                        "committed_points",
                        "done_points",
                        "total_issues",
                        "done_issues",
                        "end_date",
                    ]
                ],
            ]
        )

    sprint_view
    return


@app.cell
def _(mo):
    mo.md("## 📈 Velocity Trend (per team)")
    return


@app.cell
def _(alt, metrics, mo, selected_boards):
    vel_df = metrics.velocity_trend(board_ids=selected_boards)

    if vel_df.empty:
        vel_view = mo.md("_No closed sprints yet — velocity needs completed sprints._")
    else:
        vel_chart = (
            alt.Chart(vel_df)
            .mark_line(point=True)
            .encode(
                x=alt.X("complete_date:T", title="Sprint completion"),
                y=alt.Y("done_points:Q", title="Completed story points"),
                color=alt.Color("board_id:N", title="Board"),
                tooltip=["sprint_name", "board_id", "done_points", "complete_date"],
            )
            .properties(height=320, width=620, title="Completed points per closed sprint")
        )
        vel_view = mo.ui.altair_chart(vel_chart)

    vel_view
    return


@app.cell
def _(mo):
    # --- Reserved extension point: GitLab MR / pipeline oversight (deferred) ---
    gitlab_enabled = False
    if gitlab_enabled:
        section = mo.md("## 🔧 GitLab — MRs & Pipelines")  # populated in a follow-up
    else:
        section = mo.md(
            """
            ## 🔧 Coding / Infra Oversight — _coming soon_

            Blocked GitLab MRs and failed pipelines will appear here in a follow-up.
            Ingestion will be added as `gitlab_pipeline.py` (GitLab REST API, PAT) feeding
            the same DuckDB, with query functions in `metrics.py` and cells here.
            """
        )
    section
    return


if __name__ == "__main__":
    app.run()
