"""HTTP handlers for GitLab bindings, comments, and owner-only GitLab proxy."""

from __future__ import annotations

import logging

import tornado.web

from aird.constants.input_limits import (
    FILE_COMMENT_MAX_LEN,
    GITLAB_BOARD_ID_MAX,
    GITLAB_HOST_MAX_LEN,
    GITLAB_PROJECT_PATH_MAX_LEN,
    GITLAB_TOKEN_MAX_LEN,
)
from aird.handlers.base_handler import (
    BaseHandler,
    XSRFTokenMixin,
    get_username_string_for_db,
    require_db,
)
from aird.plugins.gitlab import db as gitlab_db
from aird.plugins.gitlab import is_gitlab_enabled
from aird.plugins.gitlab.acl import PathAccess, resolve_access
from aird.plugins.gitlab.board_stats import (
    busyness_for_issues,
    days_in_lane,
    days_since,
    last_comment_at,
)
from aird.plugins.gitlab.client import (
    GitlabError,
    create_issue_note,
    create_mr_note,
    get_board,
    get_current_user,
    get_mr,
    list_boards,
    list_issue_notes,
    list_label_events,
    list_mr_notes,
    list_mrs,
    list_open_issues,
    list_pipelines,
)
from aird.plugins.gitlab import cache as gitlab_cache
from aird.plugins.gitlab import settings as gitlab_settings
from aird.plugins.gitlab.hub import get_comment_hub
from aird.plugins.gitlab.token import (
    delete_user_token,
    load_owner_token,
    save_user_token,
)

logger = logging.getLogger(__name__)
_TOKEN_ONLY = frozenset({"token_user", "admin_token"})
_ERR_ACCESS_DENIED = "Access denied"
_ERR_NO_BINDING = "No GitLab binding for this folder"
_ERR_NO_TOKEN = "Configure a GitLab token for this account"
_ERR_BINDING_OR_TOKEN = "GitLab binding or token missing"
_ERR_COMMENT_NOT_FOUND = "Comment not found"


def _require_gitlab(handler: BaseHandler) -> bool:
    if not is_gitlab_enabled():
        handler.set_status(403)
        handler.write({"error": "GitLab integration is disabled."})
        return False
    from aird.plugins.access import PLUGIN_GITLAB, user_may_use_plugin

    username = get_username_string_for_db(handler)
    if not user_may_use_plugin(PLUGIN_GITLAB, username, handler.db_conn):
        handler.set_status(403)
        handler.write({"error": "GitLab is not assigned to this account."})
        return False
    return True


def _username(handler: BaseHandler) -> str:
    return get_username_string_for_db(handler) or ""


def _access(handler: BaseHandler) -> PathAccess | None:
    share_id = (handler.get_argument("share_id", "") or "").strip() or None
    path = handler.get_argument("path", "") or ""
    try:
        return resolve_access(handler, path=path, share_id=share_id)
    except ValueError as exc:
        handler.set_status(400)
        handler.write({"error": str(exc)})
        return None


def _need_access(handler: BaseHandler, *, write: bool = False, owner_proxy: bool = False):
    access = _access(handler)
    if access is None:
        if handler.get_status() == 200:
            handler.set_status(403)
            handler.write({"error": _ERR_ACCESS_DENIED})
        return None
    if write and not access.can_write:
        handler.set_status(403)
        handler.write({"error": "This folder is read-only for comments and bindings."})
        return None
    if owner_proxy and not access.is_self:
        handler.set_status(403)
        handler.write(
            {
                "error": "GitLab proxy is only available to the folder owner. "
                "Use your own GitLab token in the browser."
            }
        )
        return None
    return access


def _binding_and_token(handler: BaseHandler, access: PathAccess):
    binding = gitlab_db.resolve_binding(
        handler.db_conn, access.owner_username, access.rel_path
    )
    host = (binding or {}).get("gitlab_host") or "https://gitlab.com"
    token = (
        load_owner_token(access.owner_username, host, conn=handler.db_conn)
        if access.is_self
        else None
    )
    return binding, token


def _parse_board_iid(raw) -> tuple[int | None, str | None]:
    if raw in (None, ""):
        return None, None
    try:
        board_iid = int(raw)
    except (TypeError, ValueError):
        return None, "board_iid must be an integer"
    if board_iid < 1 or board_iid > GITLAB_BOARD_ID_MAX:
        return None, "board_iid out of range"
    return board_iid, None


def _binding_fields_from_body(body: dict) -> tuple[dict | None, str | None]:
    host = str(body.get("gitlab_host") or "https://gitlab.com").strip()
    code = str(body.get("code_project") or "").strip()
    issues = str(body.get("issues_project") or code).strip()
    prefix = str(body.get("repo_path_prefix") or "").strip()
    if len(host) > GITLAB_HOST_MAX_LEN or len(code) > GITLAB_PROJECT_PATH_MAX_LEN:
        return None, "GitLab host or project path is too long"
    if not code:
        return None, "code_project is required"
    board_iid, board_err = _parse_board_iid(body.get("board_iid"))
    if board_err:
        return None, board_err
    return {
        "gitlab_host": host,
        "code_project": code,
        "issues_project": issues,
        "board_iid": board_iid,
        "repo_path_prefix": prefix,
    }, None


def _dashboard_empty_payload(admin_cfg: dict) -> dict:
    boards = admin_cfg.get("boards") or []
    return {
        "binding": None,
        "boards": boards,
        "gitlab_user": None,
        "open_issues": [],
        "merge_requests": [],
        "active_users": [],
        "cache": None,
    }


def _dashboard_user_host(binding, boards, admin_cfg: dict) -> str | None:
    return (
        (binding or {}).get("gitlab_host")
        or (boards[0]["gitlab_host"] if boards else None)
        or admin_cfg.get("default_host")
    )


def _dashboard_issues_and_mrs(
    handler: BaseHandler,
    *,
    access: PathAccess,
    host: str,
    token: str,
    issues_project: str,
    code_project: str,
) -> tuple[list[dict], list[dict], dict | None] | None:
    key = gitlab_cache.project_key(host, issues_project)
    cache_meta = gitlab_cache.cache_meta(handler.db_conn, access.owner_username, key)
    open_issues = gitlab_cache.list_cached_issues(
        handler.db_conn, access.owner_username, key
    )
    mrs = _gl_call(handler, list_mrs, host, token, code_project)
    if mrs is None:
        return None
    return open_issues, mrs, cache_meta


def _enrich_board_issue(
    *,
    host: str,
    token: str,
    project: str,
    issue: dict,
    aird_comments: list[dict],
) -> dict:
    iid = issue.get("iid")
    notes = []
    events = []
    if iid:
        notes = list_issue_notes(host, token, project, int(iid)) or []
        events = list_label_events(host, token, project, int(iid)) or []
    labels = [
        (lab.get("name") if isinstance(lab, dict) else str(lab))
        for lab in (issue.get("labels") or [])
    ]
    last_gl = last_comment_at(notes)
    aird_last = next(
        (c.get("created_at") for c in aird_comments if c.get("gitlab_issue_iid") == iid),
        None,
    )
    last = last_gl or aird_last
    return {
        "iid": iid,
        "title": issue.get("title"),
        "web_url": issue.get("web_url"),
        "assignees": issue.get("assignees") or [],
        "labels": labels,
        "weight": issue.get("weight"),
        "time_stats": issue.get("time_stats"),
        "created_at": issue.get("created_at"),
        "last_comment_at": last,
        "days_since_comment": days_since(last),
        "days_in_lane": days_in_lane(
            events, labels, fallback_created_at=issue.get("created_at")
        ),
    }


def _refresh_other_board_caches(
    conn,
    *,
    owner_username: str,
    token: str,
    primary_host: str,
    primary_project: str,
    admin_boards: list[dict],
) -> list:
    extra = []
    for board in admin_boards:
        if board.get("issues_project") == primary_project and board.get("gitlab_host") == primary_host:
            continue
        try:
            extra.append(
                gitlab_cache.refresh_project_cache(
                    conn,
                    owner_username=owner_username,
                    host=board["gitlab_host"],
                    issues_project=board["issues_project"],
                    token=token,
                )
            )
        except Exception as exc:
            logger.warning("GitLab board cache refresh failed: %s", exc)
    return extra


def _gl_call(handler: BaseHandler, fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except GitlabError as exc:
        handler.set_status(exc.status if 400 <= exc.status < 500 else 502)
        handler.write({"error": exc.message})
        return None


class GitlabPageHandler(BaseHandler):
    @tornado.web.authenticated
    @require_db
    def get(self):
        if not _require_gitlab(self):
            return
        path = (self.get_argument("path", "") or "").replace("\\", "/").strip("/")
        if ".." in path.split("/"):
            path = ""
        self.render("gitlab.html", initial_path=path)


class GitlabStatusHandler(BaseHandler):
    @tornado.web.authenticated
    @require_db
    def get(self):
        if not _require_gitlab(self):
            return
        access = _need_access(self)
        if not access:
            return
        binding, token = _binding_and_token(self, access)
        self.write(
            {
                "enabled": True,
                "is_self": access.is_self,
                "can_write": access.can_write,
                "owner_username": access.owner_username,
                "path": access.rel_path,
                "binding": binding,
                "token_configured": bool(token) if access.is_self else False,
            }
        )


class GitlabTokenHandler(BaseHandler, XSRFTokenMixin):
    @tornado.web.authenticated
    @require_db
    def put(self):
        if not _require_gitlab(self):
            return
        self.check_xsrf_cookie()
        access = _need_access(self, owner_proxy=True)
        if not access:
            return
        try:
            body = self.parse_json_body()
        except tornado.web.HTTPError:
            return
        token = str(body.get("token") or "").strip()
        if not token or len(token) > GITLAB_TOKEN_MAX_LEN:
            self.set_status(400)
            self.write({"error": "A GitLab token is required"})
            return
        save_user_token(access.owner_username, token, conn=self.db_conn)
        self.write({"ok": True, "token_configured": True})

    @tornado.web.authenticated
    @require_db
    def delete(self):
        if not _require_gitlab(self):
            return
        self.check_xsrf_cookie()
        access = _need_access(self, owner_proxy=True)
        if not access:
            return
        delete_user_token(access.owner_username, conn=self.db_conn)
        self.write({"ok": True, "token_configured": False})


class GitlabBindingHandler(BaseHandler, XSRFTokenMixin):
    @tornado.web.authenticated
    @require_db
    def get(self):
        if not _require_gitlab(self):
            return
        access = _need_access(self)
        if not access:
            return
        binding = gitlab_db.resolve_binding(
            self.db_conn, access.owner_username, access.rel_path
        )
        self.write({"binding": binding})

    @tornado.web.authenticated
    @require_db
    def put(self):
        if not _require_gitlab(self):
            return
        self.check_xsrf_cookie()
        access = _need_access(self, write=True)
        if not access:
            return
        try:
            body = self.parse_json_body()
        except tornado.web.HTTPError:
            return
        fields, err = _binding_fields_from_body(body)
        if err:
            self.set_status(400)
            self.write({"error": err})
            return
        binding = gitlab_db.upsert_binding(
            self.db_conn,
            owner_username=access.owner_username,
            folder_rel_path=access.rel_path,
            gitlab_host=fields["gitlab_host"],
            code_project=fields["code_project"],
            issues_project=fields["issues_project"],
            board_iid=fields["board_iid"],
            repo_path_prefix=fields["repo_path_prefix"],
            updated_by=_username(self),
        )
        self.write({"binding": binding})

    @tornado.web.authenticated
    @require_db
    def delete(self):
        if not _require_gitlab(self):
            return
        self.check_xsrf_cookie()
        access = _need_access(self, write=True)
        if not access:
            return
        gitlab_db.delete_binding(self.db_conn, access.owner_username, access.rel_path)
        self.write({"ok": True})


class GitlabFolderMetaHandler(BaseHandler):
    @tornado.web.authenticated
    @require_db
    def get(self):
        if not _require_gitlab(self):
            return
        access = _need_access(self)
        if not access:
            return
        names = self.get_arguments("name") or []
        binding = gitlab_db.resolve_binding(
            self.db_conn, access.owner_username, access.rel_path
        )
        counts = gitlab_db.comment_counts(
            self.db_conn, access.owner_username, access.rel_path, names
        )
        self.write(
            {
                "binding": binding,
                "comment_counts": counts,
                "is_self": access.is_self,
                "can_write": access.can_write,
            }
        )


class GitlabPipelinesHandler(BaseHandler):
    @tornado.web.authenticated
    @require_db
    def get(self):
        if not _require_gitlab(self):
            return
        access = _need_access(self, owner_proxy=True)
        if not access:
            return
        binding, token = _binding_and_token(self, access)
        if not binding:
            self.set_status(404)
            self.write({"error": _ERR_NO_BINDING})
            return
        if not token:
            self.set_status(401)
            self.write({"error": _ERR_NO_TOKEN})
            return
        pipes = _gl_call(
            self, list_pipelines, binding["gitlab_host"], token, binding["code_project"]
        )
        if pipes is None:
            return
        self.write({"pipelines": pipes, "latest": pipes[0] if pipes else None})


def _issues_mentioning(handler, access: PathAccess, names: list[str]):
    binding, token = _binding_and_token(handler, access)
    if not binding or not token:
        return binding, token, {}
    project = binding.get("issues_project") or binding.get("code_project")
    key = gitlab_cache.project_key(binding["gitlab_host"], project)
    cached = gitlab_cache.issues_for_names(
        handler.db_conn,
        access.owner_username,
        key,
        names,
        folder_rel=access.rel_path,
        repo_prefix=binding.get("repo_path_prefix") or "",
    )
    return binding, token, cached


class GitlabIssuesForPathHandler(BaseHandler):
    @tornado.web.authenticated
    @require_db
    def get(self):
        if not _require_gitlab(self):
            return
        access = _need_access(self, owner_proxy=True)
        if not access:
            return
        names = self.get_arguments("name") or []
        if not names:
            leaf = access.rel_path.split("/")[-1] if access.rel_path else ""
            if leaf:
                names = [leaf]
        binding, token, mapped = _issues_mentioning(self, access, names)
        if not binding:
            self.set_status(404)
            self.write({"error": _ERR_NO_BINDING})
            return
        if not token:
            self.set_status(401)
            self.write({"error": _ERR_NO_TOKEN})
            return
        self.write({"issues_by_name": mapped, "cache": gitlab_cache.cache_meta(
            self.db_conn,
            access.owner_username,
            gitlab_cache.project_key(binding["gitlab_host"], binding.get("issues_project") or binding.get("code_project")),
        )})


class GitlabCachedIssuesHandler(BaseHandler):
    @tornado.web.authenticated
    @require_db
    def get(self):
        if not _require_gitlab(self):
            return
        access = _need_access(self, owner_proxy=True)
        if not access:
            return
        binding, token = _binding_and_token(self, access)
        if not binding:
            self.set_status(404)
            self.write({"error": _ERR_NO_BINDING})
            return
        if not token:
            self.set_status(401)
            self.write({"error": _ERR_NO_TOKEN})
            return
        project = binding.get("issues_project") or binding.get("code_project")
        key = gitlab_cache.project_key(binding["gitlab_host"], project)
        names = self.get_arguments("name") or []
        if names:
            mapped = gitlab_cache.issues_for_names(
                self.db_conn,
                access.owner_username,
                key,
                names,
                folder_rel=access.rel_path,
                repo_prefix=binding.get("repo_path_prefix") or "",
            )
            self.write(
                {
                    "issues_by_name": mapped,
                    "cache": gitlab_cache.cache_meta(self.db_conn, access.owner_username, key),
                }
            )
            return
        issues = gitlab_cache.list_cached_issues(self.db_conn, access.owner_username, key)
        self.write(
            {
                "issues": issues,
                "cache": gitlab_cache.cache_meta(self.db_conn, access.owner_username, key),
            }
        )


class GitlabRefreshHandler(BaseHandler, XSRFTokenMixin):
    @tornado.web.authenticated
    @require_db
    def post(self):
        if not _require_gitlab(self):
            return
        self.check_xsrf_cookie()
        access = _need_access(self, owner_proxy=True)
        if not access:
            return
        binding, token = _binding_and_token(self, access)
        if not binding:
            self.set_status(404)
            self.write({"error": _ERR_NO_BINDING})
            return
        if not token:
            self.set_status(401)
            self.write({"error": _ERR_NO_TOKEN})
            return
        host = binding["gitlab_host"]
        project = binding.get("issues_project") or binding.get("code_project")
        result = gitlab_cache.refresh_project_cache(
            self.db_conn,
            owner_username=access.owner_username,
            host=host,
            issues_project=project,
            token=token,
        )
        admin_boards = gitlab_settings.get_settings(self.db_conn).get("boards") or []
        extra = _refresh_other_board_caches(
            self.db_conn,
            owner_username=access.owner_username,
            token=token,
            primary_host=host,
            primary_project=project,
            admin_boards=admin_boards,
        )
        self.write({"ok": True, "primary": result, "boards": extra})


def _dashboard_project_pair(binding, boards) -> tuple[str, str, str] | None:
    if binding:
        host = binding["gitlab_host"]
        code = binding.get("code_project")
        return host, binding.get("issues_project") or code, code
    if boards:
        host = boards[0]["gitlab_host"]
        issues_project = boards[0]["issues_project"]
        code = boards[0].get("code_project") or issues_project
        return host, issues_project, code
    return None


class GitlabDashboardHandler(BaseHandler):
    @tornado.web.authenticated
    @require_db
    def get(self):
        if not _require_gitlab(self):
            return
        access = _need_access(self, owner_proxy=True)
        if not access:
            return
        binding, token = _binding_and_token(self, access)
        admin_cfg = gitlab_settings.get_settings(self.db_conn)
        boards = admin_cfg.get("boards") or []
        if not binding and not boards:
            self.write(_dashboard_empty_payload(admin_cfg))
            return
        gitlab_user = None
        open_issues: list[dict] = []
        merge_requests: list[dict] = []
        cache_meta = None
        if token:
            user_host = _dashboard_user_host(binding, boards, admin_cfg)
            gitlab_user = _gl_call(self, get_current_user, user_host, token)
            if gitlab_user is None:
                return
            pair = _dashboard_project_pair(binding, boards)
            if pair:
                host, issues_project, code = pair
                loaded = _dashboard_issues_and_mrs(
                    self,
                    access=access,
                    host=host,
                    token=token,
                    issues_project=issues_project,
                    code_project=code,
                )
                if loaded is None:
                    return
                open_issues, merge_requests, cache_meta = loaded
        self.write(
            {
                "binding": binding,
                "boards": boards,
                "gitlab_user": gitlab_user,
                "open_issues": open_issues[:50],
                "open_issue_count": len(open_issues),
                "merge_requests": merge_requests[:20],
                "open_mr_count": len(merge_requests),
                "active_users": gitlab_cache.active_assignees(open_issues),
                "cache": cache_meta,
            }
        )


class GitlabMrsHandler(BaseHandler):
    @tornado.web.authenticated
    @require_db
    def get(self):
        if not _require_gitlab(self):
            return
        access = _need_access(self, owner_proxy=True)
        if not access:
            return
        binding, token = _binding_and_token(self, access)
        if not binding or not token:
            self.set_status(404 if not binding else 401)
            self.write({"error": _ERR_BINDING_OR_TOKEN})
            return
        data = _gl_call(
            self, list_mrs, binding["gitlab_host"], token, binding["code_project"]
        )
        if data is None:
            return
        self.write({"merge_requests": data})


class GitlabMrItemHandler(BaseHandler):
    @tornado.web.authenticated
    @require_db
    def get(self, iid):
        if not _require_gitlab(self):
            return
        access = _need_access(self, owner_proxy=True)
        if not access:
            return
        binding, token = _binding_and_token(self, access)
        if not binding or not token:
            self.set_status(404 if not binding else 401)
            self.write({"error": _ERR_BINDING_OR_TOKEN})
            return
        try:
            mr_iid = int(iid)
        except (TypeError, ValueError):
            self.set_status(400)
            self.write({"error": "Invalid merge request id"})
            return
        mr = _gl_call(
            self, get_mr, binding["gitlab_host"], token, binding["code_project"], mr_iid
        )
        if mr is None:
            return
        notes = _gl_call(
            self,
            list_mr_notes,
            binding["gitlab_host"],
            token,
            binding["code_project"],
            mr_iid,
        )
        if notes is None:
            return
        self.write({"merge_request": mr, "notes": notes})


class GitlabMrNotesHandler(BaseHandler, XSRFTokenMixin):
    @tornado.web.authenticated
    @require_db
    def post(self, iid):
        if not _require_gitlab(self):
            return
        self.check_xsrf_cookie()
        access = _need_access(self, owner_proxy=True)
        if not access:
            return
        binding, token = _binding_and_token(self, access)
        if not binding or not token:
            self.set_status(404 if not binding else 401)
            self.write({"error": _ERR_BINDING_OR_TOKEN})
            return
        try:
            body = self.parse_json_body()
            mr_iid = int(iid)
        except (tornado.web.HTTPError, TypeError, ValueError):
            self.set_status(400)
            self.write({"error": "Invalid request"})
            return
        text = str(body.get("body") or "").strip()
        if not text or len(text) > FILE_COMMENT_MAX_LEN:
            self.set_status(400)
            self.write({"error": "Note body required"})
            return
        note = _gl_call(
            self,
            create_mr_note,
            binding["gitlab_host"],
            token,
            binding["code_project"],
            mr_iid,
            text,
        )
        if note is None:
            return
        self.write({"note": note})


def _resolve_board(handler, host: str, token: str, project: str, board_id) -> dict | None:
    if board_id:
        return _gl_call(handler, get_board, host, token, project, int(board_id))
    boards = _gl_call(handler, list_boards, host, token, project)
    if boards is None:
        return None
    return boards[0] if boards else {}


class GitlabBoardHandler(BaseHandler):
    @tornado.web.authenticated
    @require_db
    def get(self):
        if not _require_gitlab(self):
            return
        access = _need_access(self, owner_proxy=True)
        if not access:
            return
        binding, token = _binding_and_token(self, access)
        if not binding or not token:
            self.set_status(404 if not binding else 401)
            self.write({"error": _ERR_BINDING_OR_TOKEN})
            return
        host = binding["gitlab_host"]
        project = binding.get("issues_project") or binding["code_project"]
        board = _resolve_board(self, host, token, project, binding.get("board_iid"))
        if board is None:
            return
        issues = _gl_call(self, list_open_issues, host, token, project)
        if issues is None:
            return
        lists = (board or {}).get("lists") or []
        aird_comments = gitlab_db.list_comments(
            self.db_conn, access.owner_username, access.rel_path
        )
        enriched = [
            _enrich_board_issue(
                host=host,
                token=token,
                project=project,
                issue=issue,
                aird_comments=aird_comments,
            )
            for issue in issues
        ]
        self.write(
            {
                "board": board or None,
                "lists": lists,
                "issues": enriched,
                "busyness": busyness_for_issues(issues),
            }
        )


class GitlabCommentsHandler(BaseHandler, XSRFTokenMixin):
    @tornado.web.authenticated
    @require_db
    def get(self):
        if not _require_gitlab(self):
            return
        access = _need_access(self)
        if not access:
            return
        comments = gitlab_db.list_comments(
            self.db_conn, access.owner_username, access.rel_path
        )
        self.write({"comments": comments, "can_write": access.can_write})

    @tornado.web.authenticated
    @require_db
    def post(self):
        if not _require_gitlab(self):
            return
        self.check_xsrf_cookie()
        access = _need_access(self, write=True)
        if not access:
            return
        author = _username(self)
        if not author or author in _TOKEN_ONLY:
            self.set_status(403)
            self.write({"error": "A logged-in user account is required to comment"})
            return
        try:
            body = self.parse_json_body()
        except tornado.web.HTTPError:
            return
        text = str(body.get("body") or "")
        issue_iid = body.get("gitlab_issue_iid")
        try:
            iid = int(issue_iid) if issue_iid not in (None, "") else None
        except (TypeError, ValueError):
            iid = None
        try:
            comment = gitlab_db.insert_comment(
                self.db_conn,
                owner_username=access.owner_username,
                file_rel_path=access.rel_path,
                author_username=author,
                body=text,
                gitlab_issue_iid=iid,
            )
        except ValueError as exc:
            self.set_status(400)
            self.write({"error": str(exc)})
            return
        get_comment_hub().publish(
            access.owner_username,
            access.rel_path,
            {"type": "comment_created", "comment": comment},
        )
        self.write({"comment": comment})


class GitlabCommentItemHandler(BaseHandler, XSRFTokenMixin):
    @tornado.web.authenticated
    @require_db
    def patch(self, comment_id):
        if not _require_gitlab(self):
            return
        self.check_xsrf_cookie()
        comment = gitlab_db.get_comment(self.db_conn, comment_id)
        if not comment:
            self.set_status(404)
            self.write({"error": _ERR_COMMENT_NOT_FOUND})
            return
        access = resolve_access(
            self,
            path=comment["file_rel_path"],
            share_id=(self.get_argument("share_id", "") or "").strip() or None,
        )
        if not access or not access.can_write:
            self.set_status(403)
            self.write({"error": _ERR_ACCESS_DENIED})
            return
        try:
            body = self.parse_json_body()
        except tornado.web.HTTPError:
            return
        author = _username(self)
        set_promote = "gitlab_note_id" in body or "gitlab_issue_iid" in body
        if body.get("body") is not None and comment["author_username"] != author:
            self.set_status(403)
            self.write({"error": "Only the author can edit this comment"})
            return
        note_id = body.get("gitlab_note_id")
        issue_iid = body.get("gitlab_issue_iid")
        try:
            updated = gitlab_db.update_comment(
                self.db_conn,
                comment_id,
                body=body.get("body"),
                gitlab_issue_iid=int(issue_iid) if issue_iid not in (None, "") else None,
                gitlab_note_id=int(note_id) if note_id not in (None, "") else None,
                set_promote=set_promote,
            )
        except ValueError as exc:
            self.set_status(400)
            self.write({"error": str(exc)})
            return
        get_comment_hub().publish(
            access.owner_username,
            access.rel_path,
            {"type": "comment_updated", "comment": updated},
        )
        self.write({"comment": updated})

    @tornado.web.authenticated
    @require_db
    def delete(self, comment_id):
        if not _require_gitlab(self):
            return
        self.check_xsrf_cookie()
        comment = gitlab_db.get_comment(self.db_conn, comment_id)
        if not comment:
            self.set_status(404)
            self.write({"error": _ERR_COMMENT_NOT_FOUND})
            return
        access = resolve_access(
            self,
            path=comment["file_rel_path"],
            share_id=(self.get_argument("share_id", "") or "").strip() or None,
        )
        author = _username(self)
        if not access or not access.can_write:
            self.set_status(403)
            self.write({"error": _ERR_ACCESS_DENIED})
            return
        if comment["author_username"] != author and not access.is_self:
            self.set_status(403)
            self.write({"error": "Only the author or folder owner can delete"})
            return
        gitlab_db.delete_comment(self.db_conn, comment_id)
        get_comment_hub().publish(
            access.owner_username,
            access.rel_path,
            {"type": "comment_deleted", "id": comment_id},
        )
        self.write({"ok": True})


class GitlabPromoteHandler(BaseHandler, XSRFTokenMixin):
    @tornado.web.authenticated
    @require_db
    def post(self, comment_id):
        if not _require_gitlab(self):
            return
        self.check_xsrf_cookie()
        comment = gitlab_db.get_comment(self.db_conn, comment_id)
        if not comment:
            self.set_status(404)
            self.write({"error": _ERR_COMMENT_NOT_FOUND})
            return
        access = resolve_access(
            self,
            path=comment["file_rel_path"],
            share_id=(self.get_argument("share_id", "") or "").strip() or None,
        )
        if not access or not access.is_self:
            self.set_status(403)
            self.write({"error": "Only the folder owner can promote via Aird GitLab token"})
            return
        try:
            body = self.parse_json_body()
            iid = int(body.get("gitlab_issue_iid") or comment.get("gitlab_issue_iid"))
        except (tornado.web.HTTPError, TypeError, ValueError):
            self.set_status(400)
            self.write({"error": "gitlab_issue_iid is required"})
            return
        binding, token = _binding_and_token(self, access)
        if not binding or not token:
            self.set_status(401)
            self.write({"error": _ERR_BINDING_OR_TOKEN})
            return
        project = binding.get("issues_project") or binding.get("code_project")
        note_body = f"**Aird comment** on `{comment['file_rel_path']}` by {comment['author_username']}:\n\n{comment['body']}"
        note = _gl_call(
            self,
            create_issue_note,
            binding["gitlab_host"],
            token,
            project,
            iid,
            note_body,
        )
        if note is None:
            return
        updated = gitlab_db.update_comment(
            self.db_conn,
            comment_id,
            gitlab_issue_iid=iid,
            gitlab_note_id=note.get("id"),
            set_promote=True,
        )
        self.write({"comment": updated, "note": note})
