"""GitLab plugin: folder bindings, file comments, owner-side GitLab proxy."""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def gitlab_library_available() -> bool:
    """GitLab core uses requests (already required). Always available."""
    return True


def is_gitlab_enabled() -> bool:
    from aird.utils.util import is_feature_enabled

    return gitlab_library_available() and is_feature_enabled("gitlab_integration", False)


def register_gitlab(routes: list) -> None:
    if not gitlab_library_available():
        return
    from aird.plugins.gitlab.handlers import (
        GitlabBindingHandler,
        GitlabBoardHandler,
        GitlabCachedIssuesHandler,
        GitlabCommentItemHandler,
        GitlabCommentsHandler,
        GitlabDashboardHandler,
        GitlabFolderMetaHandler,
        GitlabIssuesForPathHandler,
        GitlabMrItemHandler,
        GitlabMrNotesHandler,
        GitlabMrsHandler,
        GitlabPageHandler,
        GitlabPipelinesHandler,
        GitlabPromoteHandler,
        GitlabRefreshHandler,
        GitlabStatusHandler,
        GitlabTokenHandler,
    )
    from aird.plugins.gitlab.ws import GitlabCommentsWebSocketHandler

    cid = r"([^/]+)"
    routes.extend(
        [
            (r"/gitlab", GitlabPageHandler),
            (r"/api/gitlab/status", GitlabStatusHandler),
            (r"/api/gitlab/token", GitlabTokenHandler),
            (r"/api/gitlab/bindings", GitlabBindingHandler),
            (r"/api/gitlab/folder-meta", GitlabFolderMetaHandler),
            (r"/api/gitlab/pipelines", GitlabPipelinesHandler),
            (r"/api/gitlab/issues-for-path", GitlabIssuesForPathHandler),
            (r"/api/gitlab/cached-issues", GitlabCachedIssuesHandler),
            (r"/api/gitlab/refresh", GitlabRefreshHandler),
            (r"/api/gitlab/dashboard", GitlabDashboardHandler),
            (r"/api/gitlab/mrs", GitlabMrsHandler),
            (rf"/api/gitlab/mrs/{cid}/notes", GitlabMrNotesHandler),
            (rf"/api/gitlab/mrs/{cid}", GitlabMrItemHandler),
            (r"/api/gitlab/board", GitlabBoardHandler),
            (r"/api/gitlab/comments", GitlabCommentsHandler),
            (rf"/api/gitlab/comments/{cid}/promote", GitlabPromoteHandler),
            (rf"/api/gitlab/comments/{cid}", GitlabCommentItemHandler),
            (r"/ws/file-comments", GitlabCommentsWebSocketHandler),
        ]
    )
