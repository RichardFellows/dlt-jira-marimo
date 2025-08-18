"""A template that is a good start for vibe coding REST API Source. Works best with `dlt ai` command cursor rules"""

import dlt
from dlt.sources.rest_api import (
    RESTAPIConfig,
    rest_api_resources,
)


@dlt.source
def jira_source(pat_token: str = dlt.secrets.value, base_url: str = dlt.secrets.value):
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
        "resources": [
            {
                "name": "application_roles",
                "endpoint": {
                    "path": "/rest/api/2/applicationrole",
                    "method": "GET",
                },
            },
            {
                "name": "avatars",
                "endpoint": {
                    "path": "/rest/api/2/avatar/type/system",
                    "method": "GET",
                },
            },
        ],
    }

    yield from rest_api_resources(config)


def get_data() -> None:
    pipeline = dlt.pipeline(
        pipeline_name='jira_pipeline',
        destination='duckdb',
        dataset_name='jira_data',
        progress="log",
    )

    load_info = pipeline.run(jira_source())
    print(load_info)  # noqa


if __name__ == "__main__":
    get_data()
