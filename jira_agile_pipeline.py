"""Ingestion layer — Jira Agile (Software boards) → DuckDB.

This script ONLY loads data. It knows nothing about querying or presentation, so it
can be scheduled independently of the dashboard (see docker-compose.yml).

It extracts, for a configurable set of scrum boards (your teams):
  - boards          (the teams' scrum boards)
  - sprints         (active / closed / future, incl. sprint goal)
  - epics           (committed "features" — Epics-as-features)
  - sprint_issues   (issues in each sprint: story points, status, epic link)
  - backlog_issues  (not-yet-committed work, same shape as sprint_issues)

All Jira Agile list endpoints share the same offset/limit contract
(`startAt` / `maxResults`, response carries `total` / `isLast`), so they all use the
`offset` paginator that is already proven in `jira_pipeline.py`. `isLast` is not a dlt
paginator argument, so where `total` is unreliable we set `total_path=None` and rely on
the offset paginator's `stop_after_empty_page=True` default.

Structure mirrors the in-repo example `jira_pipeline.py` (Bearer/PAT auth, an
`add_map` to flatten nested fields).
"""

import os

import dlt
from dlt.sources.rest_api import RESTAPIConfig, rest_api_resources


# ---------------------------------------------------------------------------
# DuckDB destination path — shared with the query layer (metrics.py).
# Override with DUCKDB_PATH (the Docker services mount /data/jira.duckdb).
# ---------------------------------------------------------------------------
def duckdb_path() -> str:
    return os.environ.get("DUCKDB_PATH", "jira.duckdb")


# ---------------------------------------------------------------------------
# Record transforms — flatten the nested Jira `fields` object into stable,
# snake_case columns the query layer can rely on. Custom-field ids are
# config-driven (they differ per Jira instance), never hardcoded.
# ---------------------------------------------------------------------------
def _parent_id(record, candidate_keys):
    """Read the originating parent id that `include_from_parent` attaches.

    dlt prefixes included parent fields; the exact prefix can vary, so we scan a
    few tolerant candidates rather than guess a single name.
    """
    for key in candidate_keys:
        if record.get(key) is not None:
            return record[key]
    return None


def make_flatten_issue(story_point_field: str, epic_link_field: str, parent: str):
    """Build an add_map function that flattens an Agile issue record.

    `parent` is "sprint" for sprint_issues (attaches sprint_id) or "board" for
    backlog_issues (attaches board_id).
    """

    def _flatten(record):
        fields = record.get("fields") or {}

        record["summary"] = fields.get("summary")
        record["story_points"] = fields.get(story_point_field)

        status = fields.get("status") or {}
        record["status_name"] = status.get("name")
        record["status_category_key"] = (status.get("statusCategory") or {}).get("key")

        record["issue_type"] = (fields.get("issuetype") or {}).get("name")

        assignee = fields.get("assignee") or {}
        record["assignee_display_name"] = assignee.get("displayName")
        record["assignee_account_id"] = assignee.get("accountId") or assignee.get("name")

        record["resolution_date"] = fields.get("resolutiondate")

        # Epic link: classic instances use a custom field, team-managed use parent.
        epic_key = fields.get(epic_link_field)
        if not epic_key:
            epic_key = (fields.get("parent") or {}).get("key")
        record["epic_key"] = epic_key

        # fixVersions -> comma-joined string so PI matching stays a simple LIKE
        # in the query layer (avoids a child join table).
        fix_versions = fields.get("fixVersions") or []
        record["fix_versions"] = ",".join(
            fv.get("name") for fv in fix_versions if isinstance(fv, dict) and fv.get("name")
        )

        # Attribute the row to the parent we queried it from.
        if parent == "sprint":
            record["sprint_id"] = _parent_id(record, ["_sprints_id", "sprints_id", "_sprint_id"])
        else:
            record["board_id"] = _parent_id(record, ["_boards_id", "boards_id", "_board_id"])

        # Drop the bulky nested object now that we've extracted what we need.
        record.pop("fields", None)
        return record

    return _flatten


def make_flatten_epic(parent: str = "board"):
    """Attach the originating board id to each epic for per-team scoping."""

    def _flatten(record):
        record["board_id"] = _parent_id(record, ["_boards_id", "boards_id", "_board_id"])
        return record

    return _flatten


def _keep_boards(board_ids):
    """Filter the (small) board list down to the configured team boards."""
    wanted = {int(b) for b in board_ids} if board_ids else None

    def _filter(record):
        return wanted is None or int(record.get("id")) in wanted

    return _filter


# ---------------------------------------------------------------------------
# Source
# ---------------------------------------------------------------------------
@dlt.source(name="jira_agile")
def jira_agile_source(
    pat_token: str = dlt.secrets.value,
    base_url: str = dlt.secrets.value,
    board_ids: list = dlt.config.value,
    story_point_field: str = dlt.config.value,
    sprint_field: str = dlt.config.value,
    epic_link_field: str = dlt.config.value,
    issue_fields: str = dlt.config.value,
):
    # Fields requested for issue endpoints: the base set plus the configurable
    # custom fields (story points / sprint / epic link).
    fields_param = ",".join(
        f for f in [issue_fields, story_point_field, sprint_field, epic_link_field] if f
    )

    offset_paginator = {
        "type": "offset",
        "offset": 0,
        "limit": 50,
        "offset_param": "startAt",
        "limit_param": "maxResults",
        "total_path": "total",
    }
    # Sprints/epics endpoints do not return a reliable `total`; stop on empty page.
    offset_paginator_no_total = {**offset_paginator, "total_path": None}

    config: RESTAPIConfig = {
        "client": {
            "base_url": base_url,
            "auth": {
                "type": "bearer",
                "token": pat_token,
            },
            "headers": {
                "Accept": "application/json",
            },
        },
        "resource_defaults": {
            "primary_key": "id",
            "write_disposition": "merge",
        },
        "resources": [
            {
                "name": "boards",
                "endpoint": {
                    "path": "/rest/agile/1.0/board",
                    "method": "GET",
                    "data_selector": "values",
                    "params": {"maxResults": 50},
                    "paginator": offset_paginator,
                },
                # Small dimension; full refresh each run.
                "write_disposition": "replace",
            },
            {
                "name": "sprints",
                "endpoint": {
                    "path": "/rest/agile/1.0/board/{board_id}/sprint",
                    "method": "GET",
                    "data_selector": "values",
                    "params": {
                        "board_id": {
                            "type": "resolve",
                            "resource": "boards",
                            "field": "id",
                        },
                        "state": "active,closed,future",
                        "maxResults": 50,
                    },
                    "paginator": offset_paginator_no_total,
                },
            },
            {
                "name": "epics",
                "endpoint": {
                    "path": "/rest/agile/1.0/board/{board_id}/epic",
                    "method": "GET",
                    "data_selector": "values",
                    "params": {
                        "board_id": {
                            "type": "resolve",
                            "resource": "boards",
                            "field": "id",
                        },
                        "maxResults": 50,
                    },
                    "paginator": offset_paginator_no_total,
                },
                "include_from_parent": ["id"],
            },
            {
                "name": "sprint_issues",
                "endpoint": {
                    "path": "/rest/agile/1.0/sprint/{sprint_id}/issue",
                    "method": "GET",
                    "data_selector": "issues",
                    "params": {
                        "sprint_id": {
                            "type": "resolve",
                            "resource": "sprints",
                            "field": "id",
                        },
                        "fields": fields_param,
                        "maxResults": 50,
                    },
                    "paginator": offset_paginator,
                },
                "include_from_parent": ["id"],
            },
            {
                "name": "backlog_issues",
                "endpoint": {
                    "path": "/rest/agile/1.0/board/{board_id}/backlog",
                    "method": "GET",
                    "data_selector": "issues",
                    "params": {
                        "board_id": {
                            "type": "resolve",
                            "resource": "boards",
                            "field": "id",
                        },
                        "fields": fields_param,
                        "maxResults": 50,
                    },
                    "paginator": offset_paginator,
                },
                "include_from_parent": ["id"],
            },
        ],
    }

    flatten_sprint_issue = make_flatten_issue(story_point_field, epic_link_field, "sprint")
    flatten_backlog_issue = make_flatten_issue(story_point_field, epic_link_field, "board")
    flatten_epic = make_flatten_epic("board")
    keep_boards = _keep_boards(board_ids)

    for resource in rest_api_resources(config):
        if resource.name == "boards":
            # Scope every dependent resource to just the configured team boards.
            yield resource.add_filter(keep_boards)
        elif resource.name == "sprint_issues":
            yield resource.add_map(flatten_sprint_issue)
        elif resource.name == "backlog_issues":
            yield resource.add_map(flatten_backlog_issue)
        elif resource.name == "epics":
            yield resource.add_map(flatten_epic)
        else:
            yield resource


def get_data() -> None:
    # dev_mode resets schema/state between iterations — handy while developing,
    # but the scheduled refresh needs persistence so merges accumulate, so it
    # defaults off. Set JIRA_AGILE_DEV_MODE=1 locally to reset.
    dev_mode = os.environ.get("JIRA_AGILE_DEV_MODE", "").lower() in ("1", "true", "yes")

    pipeline = dlt.pipeline(
        pipeline_name="jira_agile_pipeline",
        destination=dlt.destinations.duckdb(duckdb_path()),
        dataset_name="jira_data",
        dev_mode=dev_mode,
        progress="log",
    )

    load_info = pipeline.run(jira_agile_source())
    print(load_info)  # noqa


if __name__ == "__main__":
    get_data()
