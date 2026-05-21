"""Jira dlt pipeline — extracts issues and related data into DuckDB."""

import dlt
from dlt.sources.rest_api import RESTAPIConfig, rest_api_resources


# Module-level collections populated by process_issues during extraction
_assignees: dict = {}
_reporters: dict = {}
_components: dict = {}
_issue_labels: list = []
_changelog_entries: list = []


def _select_value(v):
    """Return a plain string from a Jira single-select option or raw value."""
    if isinstance(v, dict):
        return v.get("value") or v.get("name") or v.get("displayName")
    return str(v) if v is not None else None


def make_process_issues(
    pi_field_id: str = None,
    team_field_id: str = None,
    sprint_field_id: str = None,
):
    """Return a record-level transformer that extracts SAFe fields and related objects."""

    def process_issues(record):
        issue_id = record.get("id")
        fields = record.get("fields", {})

        # ── Assignee ─────────────────────────────────────────────────────────
        if fields.get("assignee"):
            a = fields["assignee"]
            if a.get("accountId"):
                _assignees[a["accountId"]] = {
                    "account_id": a.get("accountId"),
                    "key": a.get("key"),
                    "name": a.get("name"),
                    "display_name": a.get("displayName"),
                    "email_address": a.get("emailAddress"),
                    "avatar_urls": a.get("avatarUrls"),
                    "active": a.get("active", True),
                }
                fields["assignee_id"] = a["accountId"]

        # ── Reporter ─────────────────────────────────────────────────────────
        if fields.get("reporter"):
            r = fields["reporter"]
            if r.get("accountId"):
                _reporters[r["accountId"]] = {
                    "account_id": r.get("accountId"),
                    "key": r.get("key"),
                    "name": r.get("name"),
                    "display_name": r.get("displayName"),
                    "email_address": r.get("emailAddress"),
                    "avatar_urls": r.get("avatarUrls"),
                    "active": r.get("active", True),
                }
                fields["reporter_id"] = r["accountId"]

        # ── Components ───────────────────────────────────────────────────────
        if fields.get("components"):
            cids = []
            for c in fields["components"]:
                if c.get("id"):
                    _components[c["id"]] = {
                        "id": c.get("id"),
                        "name": c.get("name"),
                        "description": c.get("description"),
                        "lead": c.get("lead"),
                        "project_id": c.get("projectId"),
                    }
                    cids.append(c["id"])
            fields["component_ids"] = cids

        # ── Labels ───────────────────────────────────────────────────────────
        if fields.get("labels") and issue_id:
            for label in fields["labels"]:
                _issue_labels.append({"issue_id": issue_id, "label": label})

        # ── Changelog ────────────────────────────────────────────────────────
        if record.get("changelog", {}).get("histories") and issue_id:
            for history in record["changelog"]["histories"]:
                for item in history.get("items", []):
                    _changelog_entries.append({
                        "issue_id": issue_id,
                        "history_id": history.get("id"),
                        "created": history.get("created"),
                        "author_account_id": history.get("author", {}).get("accountId"),
                        "author_display_name": history.get("author", {}).get("displayName"),
                        "field": item.get("field"),
                        "field_type": item.get("fieldtype"),
                        "field_id": item.get("fieldId"),
                        "from_value": item.get("from"),
                        "from_string": item.get("fromString"),
                        "to_value": item.get("to"),
                        "to_string": item.get("toString"),
                    })

        # ── PI (planning increment) ───────────────────────────────────────────
        if pi_field_id and fields.get(pi_field_id) is not None:
            fields["pi"] = _select_value(fields[pi_field_id])

        # ── Team (GFED team) ─────────────────────────────────────────────────
        if team_field_id and fields.get(team_field_id) is not None:
            fields["team"] = _select_value(fields[team_field_id])

        # ── Sprint metadata ──────────────────────────────────────────────────
        # Sprint field returns an array; we use the most recent active/closed sprint.
        if sprint_field_id and fields.get(sprint_field_id):
            sprints = fields[sprint_field_id]
            if isinstance(sprints, list) and sprints:
                candidates = [
                    s for s in sprints
                    if isinstance(s, dict) and s.get("state") in ("active", "closed")
                ]
                sprint = candidates[-1] if candidates else sprints[-1]
                if isinstance(sprint, dict):
                    fields["sprint_id"] = sprint.get("id")
                    fields["sprint_name"] = sprint.get("name")
                    fields["sprint_state"] = sprint.get("state")
                    fields["sprint_start_date"] = sprint.get("startDate")
                    fields["sprint_end_date"] = sprint.get("endDate")
                    fields["sprint_complete_date"] = sprint.get("completeDate")
                    fields["sprint_board_id"] = sprint.get("boardId")

        return record

    return process_issues


@dlt.resource(write_disposition="merge", primary_key="account_id")
def assignees():
    yield from _assignees.values()


@dlt.resource(write_disposition="merge", primary_key="account_id")
def reporters():
    yield from _reporters.values()


@dlt.resource(write_disposition="merge", primary_key="id")
def components():
    yield from _components.values()


@dlt.resource(write_disposition="append", primary_key=["issue_id", "label"])
def issue_labels():
    yield from _issue_labels


@dlt.resource(write_disposition="append", primary_key=["issue_id", "history_id", "field"])
def changelog():
    yield from _changelog_entries


@dlt.source
def jira_source(
    pat_token: str = dlt.secrets.value,
    base_url: str = dlt.secrets.value,
    jql_query: str = dlt.secrets.value,
    fields: str = dlt.secrets.value,
    expand: str = dlt.secrets.value,
    # SAFe custom field IDs — set in .dlt/config.toml under [sources.jira_source]
    pi_field_id: str = dlt.config.value,
    team_field_id: str = dlt.config.value,
    sprint_field_id: str = dlt.config.value,
):
    config: RESTAPIConfig = {
        "client": {
            "base_url": base_url,
            "auth": {"type": "bearer", "token": pat_token},
            "headers": {"Accept": "application/json"},
        },
        "resource_defaults": {
            "primary_key": "id",
            "write_disposition": "merge",
        },
        "resources": [
            {
                "name": "issues",
                "endpoint": {
                    "path": "/rest/api/2/search",
                    "method": "GET",
                    "params": {
                        "jql": jql_query,
                        "maxResults": 50,
                        "fields": fields,
                        "expand": expand,
                    },
                    "data_selector": "issues",
                    "paginator": {
                        "type": "offset",
                        "offset": 0,
                        "limit": 50,
                        "offset_param": "startAt",
                        "limit_param": "maxResults",
                        "total_path": "total",
                    },
                },
            },
        ],
    }

    process_fn = make_process_issues(pi_field_id, team_field_id, sprint_field_id)

    for resource in rest_api_resources(config):
        if resource.name == "issues":
            yield resource.add_map(process_fn)
        else:
            yield resource

    yield assignees
    yield reporters
    yield components
    yield issue_labels
    yield changelog


def get_data() -> None:
    pipeline = dlt.pipeline(
        pipeline_name="jira_pipeline",
        destination="duckdb",
        dataset_name="jira_data",
        progress="alive_progress",
    )
    load_info = pipeline.run(jira_source())
    print(load_info)


if __name__ == "__main__":
    get_data()
