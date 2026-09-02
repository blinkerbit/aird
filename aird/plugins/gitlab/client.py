"""GitLab REST client (owner/self only; uses requests)."""

from __future__ import annotations

from typing import Any
from urllib.parse import quote

import requests

DEFAULT_HOST = "https://gitlab.com"
TIMEOUT = 25


class GitlabError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


def _api_root(host: str) -> str:
    base = (host or DEFAULT_HOST).strip() or DEFAULT_HOST
    if not base.startswith("http"):
        base = "https://" + base
    return base.rstrip("/") + "/api/v4"


def project_id(project: str) -> str:
    return quote((project or "").strip().lstrip("/"), safe="")


def gitlab_request(
    host: str,
    token: str,
    method: str,
    path: str,
    *,
    params: dict | None = None,
    json_body: dict | None = None,
) -> Any:
    if not token:
        raise GitlabError(401, "GitLab token is not configured")
    url = _api_root(host) + "/" + path.lstrip("/")
    headers = {"PRIVATE-TOKEN": token, "Accept": "application/json"}
    try:
        response = requests.request(
            method.upper(),
            url,
            headers=headers,
            params=params,
            json=json_body,
            timeout=TIMEOUT,
        )
    except requests.RequestException as exc:
        raise GitlabError(502, f"GitLab request failed: {exc}") from exc
    if response.status_code >= 400:
        detail = response.text[:300] if response.text else response.reason
        raise GitlabError(response.status_code, detail or "GitLab error")
    if response.status_code == 204 or not response.content:
        return None
    try:
        return response.json()
    except ValueError:
        return None


def list_pipelines(host: str, token: str, project: str, per_page: int = 5) -> list:
    data = gitlab_request(
        host,
        token,
        "GET",
        f"projects/{project_id(project)}/pipelines",
        params={"per_page": per_page, "order_by": "id", "sort": "desc"},
    )
    return data if isinstance(data, list) else []


def list_open_issues(host: str, token: str, project: str, per_page: int = 100) -> list:
    data = gitlab_request(
        host,
        token,
        "GET",
        f"projects/{project_id(project)}/issues",
        params={"state": "opened", "per_page": per_page, "with_labels_details": "true"},
    )
    return data if isinstance(data, list) else []


def list_issue_notes(host: str, token: str, project: str, iid: int) -> list:
    data = gitlab_request(
        host,
        token,
        "GET",
        f"projects/{project_id(project)}/issues/{iid}/notes",
        params={"per_page": 50, "sort": "desc", "order_by": "created_at"},
    )
    return data if isinstance(data, list) else []


def create_issue_note(host: str, token: str, project: str, iid: int, body: str) -> dict:
    data = gitlab_request(
        host,
        token,
        "POST",
        f"projects/{project_id(project)}/issues/{iid}/notes",
        json_body={"body": body},
    )
    return data if isinstance(data, dict) else {}


def list_mrs(host: str, token: str, project: str, per_page: int = 50) -> list:
    data = gitlab_request(
        host,
        token,
        "GET",
        f"projects/{project_id(project)}/merge_requests",
        params={"state": "opened", "per_page": per_page},
    )
    return data if isinstance(data, list) else []


def get_mr(host: str, token: str, project: str, iid: int) -> dict:
    data = gitlab_request(
        host,
        token,
        "GET",
        f"projects/{project_id(project)}/merge_requests/{iid}",
    )
    return data if isinstance(data, dict) else {}


def list_mr_notes(host: str, token: str, project: str, iid: int) -> list:
    data = gitlab_request(
        host,
        token,
        "GET",
        f"projects/{project_id(project)}/merge_requests/{iid}/notes",
        params={"per_page": 100, "sort": "asc"},
    )
    return data if isinstance(data, list) else []


def create_mr_note(host: str, token: str, project: str, iid: int, body: str) -> dict:
    data = gitlab_request(
        host,
        token,
        "POST",
        f"projects/{project_id(project)}/merge_requests/{iid}/notes",
        json_body={"body": body},
    )
    return data if isinstance(data, dict) else {}


def list_boards(host: str, token: str, project: str) -> list:
    data = gitlab_request(
        host,
        token,
        "GET",
        f"projects/{project_id(project)}/boards",
    )
    return data if isinstance(data, list) else []


def get_board(host: str, token: str, project: str, board_id: int) -> dict:
    data = gitlab_request(
        host,
        token,
        "GET",
        f"projects/{project_id(project)}/boards/{board_id}",
    )
    return data if isinstance(data, dict) else {}


def get_current_user(host: str, token: str) -> dict:
    data = gitlab_request(host, token, "GET", "user")
    return data if isinstance(data, dict) else {}


def get_issue(host: str, token: str, project: str, iid: int) -> dict | None:
    data = gitlab_request(
        host,
        token,
        "GET",
        f"projects/{project_id(project)}/issues/{iid}",
    )
    return data if isinstance(data, dict) else None


def list_issue_links(host: str, token: str, project: str, iid: int) -> list:
    data = gitlab_request(
        host,
        token,
        "GET",
        f"projects/{project_id(project)}/issues/{iid}/links",
    )
    return data if isinstance(data, list) else []


def list_label_events(host: str, token: str, project: str, iid: int) -> list:
    data = gitlab_request(
        host,
        token,
        "GET",
        f"projects/{project_id(project)}/issues/{iid}/resource_label_events",
        params={"per_page": 100},
    )
    return data if isinstance(data, list) else []
