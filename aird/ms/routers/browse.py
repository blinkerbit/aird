"""Browse UI routes (directories, search, p2p page)."""

from __future__ import annotations

import os
from pathlib import Path

from starlette.requests import Request
from starlette.responses import RedirectResponse, Response
from starlette.routing import Route

import aird.constants as constants_module
from aird.core.file_operations import get_tags_for_path
from aird.core.security import is_within_root, join_path
from aird.db.resource_tags import list_resource_tags
from aird.db.tag_colors import get_tag_colors_map
from aird.handlers.p2p_handlers import room_manager
from aird.ms.ctx import current_user, user_root_for
from aird.ms.render import render_html
from aird.ms.routers._helpers import (
    db_conn,
    get_service,
    is_admin_user,
    require_feature,
    require_user,
    resolve_request_rel,
    username_for_db,
)
from aird.utils.tag_display import tag_chip_inline_style
from aird.utils.util import (
    augment_with_shared_status,
    browser_media_kind,
    get_current_feature_flags,
    get_file_icon,
    get_files_in_directory,
    get_file_size_safe,
    is_feature_enabled,
)


def _norm_rel_path(path: str) -> str:
    return path.strip().strip("/")


async def root(request: Request) -> Response:
    user = current_user(request)
    if user:
        return RedirectResponse("/files/", status_code=302)
    return RedirectResponse("/login", status_code=302)


async def files_redirect(request: Request) -> Response:
    user = require_user(request)
    if isinstance(user, Response):
        return user
    return RedirectResponse("/files/", status_code=302)


async def files_browse(request: Request) -> Response:
    user = require_user(request)
    if isinstance(user, Response):
        return user

    path = _norm_rel_path(request.path_params.get("path", ""))
    user_root = user_root_for(user)
    abspath, confine = resolve_request_rel(request, user, path)

    if not abspath or not confine or not is_within_root(abspath, confine):
        return Response(
            "Access denied: You don't have permission to perform this action",
            status_code=403,
        )

    if os.path.isdir(abspath):
        return _render_directory(request, user, path, abspath, user_root)

    if os.path.isfile(abspath):
        if request.query_params.get("download"):
            # Caddy routes ?download=1 to the transfer service; bounce to same URL.
            return RedirectResponse(str(request.url), status_code=302)
        return _render_file_view(request, path, abspath)

    return Response(
        "File not found: The requested file may have been moved or deleted",
        status_code=404,
    )


def _render_directory(
    request: Request,
    user: dict,
    path: str,
    abspath: str,
    user_root: str,
) -> Response:
    files = get_files_in_directory(abspath)
    conn = db_conn(request)
    if not path:
        from aird.core.browse_paths import mounts_for_username, overlay_mount_entries

        overlay_mount_entries(
            files,
            mounts_for_username(conn, username_for_db(request, user) or ""),
            path,
        )
    if conn:
        augment_with_shared_status(
            files,
            path,
            get_service(request, "share_service").list_shares(conn),
            db_conn=conn,
            root_dir=user_root,
            viewer_username=username_for_db(request, user),
            viewer_is_admin=is_admin_user(user),
        )

    parent_path = os.path.dirname(path) if path else None
    flags = get_current_feature_flags()
    user_favorites: set[str] = set()
    if conn and flags.get("favorites"):
        uname = username_for_db(request, user)
        if uname:
            user_favorites = set(
                get_service(request, "favorites_service").get_favorites(conn, uname)
            )

    tag_rules = list_resource_tags(conn) if conn else []
    tag_colors = get_tag_colors_map(conn) if conn else {}
    file_tags_map: dict[str, list[str]] = {}
    for f in files:
        rel = join_path(path, f["name"]) if path else f["name"]
        file_tags_map[f["name"]] = get_tags_for_path(tag_rules, rel)

    config_service = get_service(request, "config_service")
    runtime_config = (
        config_service.get_runtime_config(db_conn(request))
        if config_service
        else constants_module.get_effective_transfer_strategy()
    )

    return render_html(
        request,
        "browse.html",
        current_path=path,
        parent_path=parent_path,
        files=files,
        join_path=join_path,
        get_file_icon=get_file_icon,
        features=flags,
        max_file_size=constants_module.MAX_FILE_SIZE,
        large_file_threshold=constants_module.LARGE_FILE_THRESHOLD_BYTES,
        transfer_strategy=runtime_config,
        user_favorites=user_favorites,
        file_tags_map=file_tags_map,
        tag_colors=tag_colors,
        tag_chip_style=tag_chip_inline_style,
    )


def _render_file_view(request: Request, path: str, abspath: str) -> Response:
    filename = os.path.basename(abspath)
    rel_path = path.replace("\\", "/").strip("/")
    file_size = get_file_size_safe(abspath)
    base = request.url.path

    media_kind = browser_media_kind(filename)
    if media_kind:
        return render_html(
            request,
            "media_view.html",
            filename=filename,
            path=rel_path,
            media_kind=media_kind,
            media_src=f"{base}?mode=raw",
            download_href=f"{base}?download=1",
            file_size=file_size,
        )

    return render_html(
        request,
        "file.html",
        filename=filename,
        path=rel_path,
        lines=[],
        default_file_view_line_limit=constants_module.DEFAULT_FILE_VIEW_LINE_LIMIT,
        open_editor=request.query_params.get("open_editor", "False"),
        full_file_content="",
        file_size=file_size,
    )


async def search_page(request: Request) -> Response:
    user = require_user(request)
    if isinstance(user, Response):
        return user

    disabled = require_feature(
        "super_search",
        True,
        "Feature disabled: Super Search is currently disabled by administrator",
    )
    if disabled:
        return disabled

    current_path = _norm_rel_path(request.query_params.get("path", ""))
    return render_html(request, "super_search.html", current_path=current_path)


async def p2p_page(request: Request) -> Response:
    disabled = require_feature(
        "p2p_transfer",
        True,
        "Feature disabled: P2P Transfer is currently disabled by administrator",
    )
    if disabled:
        return disabled

    room_id = request.query_params.get("room")
    user = current_user(request)

    if user and not is_feature_enabled("abac_engine", False):
        pass  # ABAC stub — full PDP wiring stays on Tornado for now

    template_path = Path(__file__).resolve().parent.parent.parent / "templates" / "p2p_transfer.html"
    if not template_path.is_file():
        return Response("P2P template not found", status_code=404)

    if not user:
        room_join_error = None
        if room_id:
            room = room_manager.get_room(room_id)
            if not room:
                room_join_error = "not_found"
            elif not room.allow_anonymous:
                room_join_error = "requires_login"
        return render_html(
            request,
            "p2p_transfer.html",
            room_id=room_id,
            current_user=None,
            is_anonymous=True,
            room_join_error=room_join_error,
        )

    return render_html(
        request,
        "p2p_transfer.html",
        room_id=room_id,
        current_user=user,
        is_anonymous=False,
        room_join_error=None,
    )


routes = [
    Route("/", root, methods=["GET"]),
    Route("/files", files_redirect, methods=["GET"]),
    Route("/files/", files_browse, methods=["GET"], name="files_root"),
    Route("/files/{path:path}", files_browse, methods=["GET"]),
    Route("/search", search_page, methods=["GET"]),
    Route("/p2p", p2p_page, methods=["GET"]),
]
