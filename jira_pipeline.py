"""A template that is a good start for vibe coding REST API Source. Works best with `dlt ai` command cursor rules"""

import dlt
from dlt.sources.rest_api import (
    RESTAPIConfig,
    rest_api_resources,
)


# Global collections to store extracted objects
_assignees = {}
_reporters = {}  
_components = {}
_issue_labels = []
_changelog_entries = []

def process_issues(record):
    """Process issues and extract related objects"""
    issue_id = record.get('id')
    
    # Extract assignee if exists
    if record.get('fields', {}).get('assignee'):
        assignee = record['fields']['assignee']
        if assignee.get('accountId'):
            _assignees[assignee.get('accountId')] = {
                'account_id': assignee.get('accountId'),
                'key': assignee.get('key'),
                'name': assignee.get('name'),
                'display_name': assignee.get('displayName'),
                'email_address': assignee.get('emailAddress'),
                'avatar_urls': assignee.get('avatarUrls'),
                'active': assignee.get('active', True)
            }
            # Keep just the reference in the main table
            record['fields']['assignee_id'] = assignee.get('accountId')
    
    # Extract reporter if exists
    if record.get('fields', {}).get('reporter'):
        reporter = record['fields']['reporter']
        if reporter.get('accountId'):
            _reporters[reporter.get('accountId')] = {
                'account_id': reporter.get('accountId'),
                'key': reporter.get('key'),
                'name': reporter.get('name'),
                'display_name': reporter.get('displayName'),
                'email_address': reporter.get('emailAddress'),
                'avatar_urls': reporter.get('avatarUrls'),
                'active': reporter.get('active', True)
            }
            # Keep just the reference in the main table
            record['fields']['reporter_id'] = reporter.get('accountId')
    
    # Extract components if exist
    if record.get('fields', {}).get('components'):
        component_ids = []
        for component in record['fields']['components']:
            if component.get('id'):
                _components[component.get('id')] = {
                    'id': component.get('id'),
                    'name': component.get('name'),
                    'description': component.get('description'),
                    'lead': component.get('lead'),
                    'project_id': component.get('projectId')
                }
                component_ids.append(component.get('id'))
        # Keep just the references in the main table
        record['fields']['component_ids'] = component_ids
    
    # Extract labels if exist
    if record.get('fields', {}).get('labels') and issue_id:
        for label in record['fields']['labels']:
            _issue_labels.append({
                'issue_id': issue_id,
                'label': label
            })
    
    # Extract changelog if exists (when expand=changelog is used)
    if record.get('changelog', {}).get('histories') and issue_id:
        for history in record['changelog']['histories']:
            for item in history.get('items', []):
                _changelog_entries.append({
                    'issue_id': issue_id,
                    'history_id': history.get('id'),
                    'created': history.get('created'),
                    'author_account_id': history.get('author', {}).get('accountId'),
                    'author_display_name': history.get('author', {}).get('displayName'),
                    'field': item.get('field'),
                    'field_type': item.get('fieldtype'),
                    'field_id': item.get('fieldId'),
                    'from_value': item.get('from'),
                    'from_string': item.get('fromString'),
                    'to_value': item.get('to'),
                    'to_string': item.get('toString')
                })
    
    return record

@dlt.resource(write_disposition="merge", primary_key="account_id")
def assignees():
    """Extract unique assignees into a separate table"""
    yield from _assignees.values()

@dlt.resource(write_disposition="merge", primary_key="account_id") 
def reporters():
    """Extract unique reporters into a separate table"""
    yield from _reporters.values()

@dlt.resource(write_disposition="merge", primary_key="id")
def components():
    """Extract unique components into a separate table"""
    yield from _components.values()

@dlt.resource(write_disposition="append", primary_key=["issue_id", "label"])
def issue_labels():
    """Extract issue-label relationships into a separate table"""
    yield from _issue_labels

@dlt.resource(write_disposition="append", primary_key=["issue_id", "history_id", "field"])
def changelog():
    """Extract changelog entries into a separate table"""
    yield from _changelog_entries


@dlt.source
def jira_source(
    pat_token: str = dlt.secrets.value, 
    base_url: str = dlt.secrets.value, 
    jql_query: str = dlt.secrets.value,
    fields: str = dlt.secrets.value,
    expand: str = dlt.secrets.value
):
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

    # Get the main issues resource and apply transformations
    for resource in rest_api_resources(config):
        if resource.name == "issues":
            # Apply the processing transformation to extract related objects
            yield resource.add_map(process_issues)
        else:
            yield resource
    
    # Yield the related tables
    yield assignees
    yield reporters  
    yield components
    yield issue_labels
    yield changelog


def get_data() -> None:
    pipeline = dlt.pipeline(
        pipeline_name='jira_pipeline',
        destination='duckdb',
        dataset_name='jira_data',
        progress="alive_progress",
    )

    load_info = pipeline.run(jira_source())
    print(load_info)  # noqa


if __name__ == "__main__":
    get_data()
