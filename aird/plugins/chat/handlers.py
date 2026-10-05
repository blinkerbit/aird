"""HTTP handlers for mailbox chat."""

from __future__ import annotations

import asyncio
import json
import logging
import mimetypes
import os
import secrets
import tempfile

import aiofiles
import tornado.web

from aird.constants import CHAT_STORE_FOLDER, CHUNK_SIZE
from aird.constants.input_limits import CHAT_MAX_ATTACHMENTS
from aird.core.image_preview import inline_preview_payload
from aird.core.security import is_within_root
from aird.core.zip_download import ZipDownloadError, build_zip_file, collect_zip_entries
from aird.handlers.base_handler import (
    BaseHandler,
    XSRFTokenMixin,
    get_user_root,
    get_username_string_for_db,
    require_db,
)
from aird.plugins.chat import db as chat_db
from aird.plugins.chat import is_chat_enabled
from aird.plugins.chat.files import (
    attachment_entries,
    attachment_meta,
    attachment_store_root,
    dir_tree_size,
    owner_abs_path,
    pack_attachments,
    save_copy_to_data,
    store_upload,
)
from aird.plugins.chat.notify import (
    dispatch_message,
    dispatch_message_deleted,
    dispatch_message_edited,
    dispatch_pin,
    dispatch_reaction,
    dispatch_chat_request,
    dispatch_e2e_key_ready,
)
from aird.plugins.chat.e2e import (
    get_identity_keys,
    is_e2e_meta,
    delete_stored_conv_keys,
    load_identity_backup,
    metadata_from_e2e_request,
    put_identity_key,
    save_identity_backup,
)
from aird.plugins.chat.sanitize import sanitize_chat_html
from aird.plugins.chat.service import get_chat_hub
from aird.utils.util import is_feature_enabled

logger = logging.getLogger(__name__)

_TOKEN_ONLY = frozenset({"token_user", "admin_token"})
_ERR_USER_NOT_FOUND = "User not found"


def _reject_user_not_found(handler: BaseHandler, *, status: int = 403) -> None:
    handler.set_status(status)
    handler.write({"error": _ERR_USER_NOT_FOUND})


def _att_at(msg: dict, index: int) -> dict | None:
    atts = attachment_entries(msg.get("metadata") or {})
    if index < 0 or index >= len(atts):
        return None
    return atts[index]


def _e2e_from_body(body: dict) -> dict | None:
    if not body.get("e2e"):
        return None
    return metadata_from_e2e_request(body)


def _require_chat(handler: BaseHandler) -> bool:
    if not is_chat_enabled():
        handler.set_status(403)
        handler.write({"error": "Direct messages are disabled."})
        return False
    username = get_username_string_for_db(handler)
    if not username or username in _TOKEN_ONLY:
        handler.set_status(403)
        handler.write({"error": "Chat requires a logged-in user account."})
        return False
    from aird.plugins.access import PLUGIN_CHAT, user_may_use_plugin

    if not user_may_use_plugin(PLUGIN_CHAT, username, handler.db_conn):
        handler.set_status(403)
        handler.write({"error": "Direct messages are not assigned to this account."})
        return False
    return True


def _username(handler: BaseHandler) -> str:
    return get_username_string_for_db(handler) or ""


def _uid(handler: BaseHandler) -> int | None:
    name = _username(handler)
    if not name:
        return None
    return chat_db.resolve_user_id(handler.db_conn, name)


def _chat_peer_usernames(sender: str, conversation_id: str) -> list[str]:
    members = chat_db.members_of(sender, conversation_id)
    return [
        m["username"]
        for m in members
        if m.get("username") and m["username"] != sender
    ]


def _try_create_chat_share(
    handler: BaseHandler,
    *,
    rel_path: str,
    allowed_users: list[str],
    created_by: str | None = None,
    root_dir: str | None = None,
) -> tuple[str | None, str | None]:
    """Static share of a browsable path, restricted to conversation members."""
    rel = (rel_path or "").replace("\\", "/").lstrip("/")
    if not rel or rel.startswith(f"{CHAT_STORE_FOLDER}/") or not allowed_users:
        return None, None
    if not is_feature_enabled("file_share", True):
        return None, None
    actor = get_username_string_for_db(handler)
    creator = created_by or actor
    try:
        if creator == actor:
            decision = handler.check_access("share.create", resource_path=rel)
            if decision is not None and decision.is_deny:
                return None, None
        from aird.handlers.share_handlers import _create_share_record, _resolve_share_paths

        sid = secrets.token_urlsafe(64)
        final_paths, err = _resolve_share_paths(
            [rel], "static", sid, root_dir=root_dir or get_user_root(handler)
        )
        if err or not final_paths:
            return None, None
        success, _token = _create_share_record(
            handler,
            handler.db_conn,
            sid,
            final_paths,
            allowed_users,
            True,
            "static",
            None,
            None,
            None,
            created_by=creator,
        )
        if not success:
            return None, None
        try:
            handler.get_service("audit_service").log(
                handler.db_conn,
                "share_create",
                username=handler.get_display_username(),
                details=f"share_id={sid} chat_attach",
                ip=handler.request.remote_ip,
            )
        except Exception:
            logger.debug("chat share audit failed", exc_info=True)
        return sid, f"/shared/{sid}"
    except Exception:
        logger.exception("chat static share failed for %s", rel)
        return None, None


def _ensure_chat_folder_share(
    handler: BaseHandler,
    *,
    actor: str,
    owner: str,
    rel: str,
    conversation_id: str,
    message_id: str,
    index: int,
    att: dict,
) -> str | None:
    share_url = att.get("share_url")
    if isinstance(share_url, str) and share_url.startswith("/shared/"):
        return share_url
    from aird.core.user_storage import user_data_dir_for_username

    share_id, share_url = _try_create_chat_share(
        handler,
        rel_path=rel,
        allowed_users=_chat_peer_usernames(owner, conversation_id),
        created_by=owner,
        root_dir=user_data_dir_for_username(owner),
    )
    if share_id and share_url:
        chat_db.patch_attachment_share(
            actor=actor,
            conversation_id=conversation_id,
            message_id=message_id,
            index=index,
            share_id=share_id,
            share_url=share_url,
        )
        return share_url
    return None


def _exchange_blocked(handler: BaseHandler, name: str, conversation_id: str) -> bool:
    if chat_db.exchange_open(name, conversation_id):
        return False
    handler.set_status(403)
    handler.write({"error": "Chat request not accepted yet"})
    return True


def _mark_members_online(members: list, online: set) -> None:
    for m in members:
        m["online"] = m["user_id"] in online


def _apply_conversation_online(c: dict, online: set) -> None:
    members = c.get("members") or []
    _mark_members_online(members, online)
    if c.get("kind") == "dm":
        me = c.get("_me")
        c["online"] = any(m["online"] for m in members if m.get("username") != me)
        return
    c["online_count"] = sum(1 for m in members if m.get("online"))


def _decorate_online(convs: list[dict]) -> list[dict]:
    online = get_chat_hub().online_user_ids()
    for c in convs:
        _apply_conversation_online(c, online)
    return convs


def _create_conversation_from_body(
    handler: BaseHandler, name: str, uid: int, body: dict
) -> str | None:
    """Create group or DM; write error response and return None on failure."""
    members = body.get("members")
    if members is not None:
        title = str(body.get("title") or "").strip()
        try:
            return chat_db.create_group_conversation(
                handler.db_conn,
                creator_username=name,
                title=title,
                member_usernames=[str(m) for m in members],
            )
        except ValueError as exc:
            handler.set_status(400)
            handler.write({"error": str(exc)})
            return None
    peer_name = str(body.get("username") or "").strip()
    if not peer_name:
        handler.set_status(400)
        handler.write({"error": "username required"})
        return None
    if peer_name in _TOKEN_ONLY:
        handler.set_status(400)
        handler.write({"error": "Invalid recipient"})
        return None
    peer_id = chat_db.resolve_user_id(handler.db_conn, peer_name)
    if peer_id is None:
        _reject_user_not_found(handler, status=404)
        return None
    if peer_id == uid:
        handler.set_status(400)
        handler.write({"error": "Cannot message yourself"})
        return None
    return chat_db.create_dm_conversation(handler.db_conn, uid, peer_id)


def _quota_check(handler: BaseHandler, username: str, extra: int) -> bool:
    if extra <= 0 or not is_feature_enabled("storage_quotas", False):
        return True
    svc = handler.get_service("quota_service")
    if not svc:
        return True
    quota = svc.get_quota(handler.db_conn, username)
    cap = quota.get("quota_bytes")
    if cap is None:
        return True
    if (quota.get("used_bytes") or 0) + extra > cap:
        handler.set_status(413)
        handler.write({"error": "Storage quota exceeded"})
        return False
    return True


def _quota_bump(handler: BaseHandler, username: str, extra: int) -> None:
    if extra <= 0 or not is_feature_enabled("storage_quotas", False):
        return
    svc = handler.get_service("quota_service")
    if svc:
        svc.update_used_bytes(handler.db_conn, username, extra)


def _parse_chat_attach_e2e(handler: BaseHandler) -> tuple[dict | None, str | None]:
    raw_e2e = (handler.get_argument("e2e", "") or "").strip()
    if not raw_e2e:
        return None, None
    try:
        parsed = json.loads(raw_e2e)
        try:
            mentions = json.loads(handler.get_argument("mentions", "") or "[]")
        except json.JSONDecodeError:
            mentions = []
        return metadata_from_e2e_request({"e2e": parsed, "mentions": mentions}), None
    except (ValueError, json.JSONDecodeError) as exc:
        return None, str(exc)


def _store_uploaded_chat_attachment(
    handler: BaseHandler,
    *,
    name: str,
    meta: dict,
    conversation_id: str,
    tmps: list[str],
) -> dict | None:
    original_name = os.path.basename(meta["filename"] or "file")
    fd, tmp = tempfile.mkstemp(prefix="aird_chat_")
    os.close(fd)
    tmps.append(tmp)
    with open(tmp, "wb") as out:
        out.write(meta["body"])
    size = os.path.getsize(tmp)
    if not _quota_check(handler, name, size):
        return None
    owner_rel, size = store_upload(
        sender_username=name,
        conversation_id=conversation_id,
        source_abs=tmp,
        original_name=original_name,
    )
    _quota_bump(handler, name, size)
    return attachment_meta(
        original_name=original_name,
        owner_username=name,
        owner_rel=owner_rel,
        size_bytes=size,
    )


def _chat_attachment_from_source_path(
    handler: BaseHandler,
    *,
    source_path: str,
    sender_root: str,
    peers: list[str],
    name: str,
) -> tuple[dict | None, tuple[int, str] | None]:
    source_abs = os.path.join(sender_root, source_path.replace("/", os.sep))
    original_name = os.path.basename(source_path.rstrip("/"))
    if (
        not is_within_root(source_abs, sender_root)
        or os.path.islink(source_abs)
        or not (os.path.isfile(source_abs) or os.path.isdir(source_abs))
    ):
        return None, (400, f"Invalid source: {original_name or source_path}")
    is_dir = os.path.isdir(source_abs)
    try:
        size = dir_tree_size(source_abs) if is_dir else os.path.getsize(source_abs)
    except ValueError as exc:
        return None, (413, str(exc))
    share_id, share_url = _try_create_chat_share(
        handler, rel_path=source_path, allowed_users=peers
    )
    return (
        attachment_meta(
            original_name=original_name or os.path.basename(source_path),
            owner_username=name,
            owner_rel=source_path,
            size_bytes=size,
            media_kind="folder" if is_dir else None,
            share_id=share_id,
            share_url=share_url,
        ),
        None,
    )


def _collect_chat_attachments(
    handler: BaseHandler,
    *,
    name: str,
    conversation_id: str,
    sender_root: str,
    peers: list[str],
    tmps: list[str],
) -> list[dict] | None:
    """Build attachment list from uploads/paths; write error and return None on failure."""
    uploads = list(handler.request.files.get("file") or [])
    paths = [
        p.strip().lstrip("/")
        for p in handler.get_arguments("source_path")
        if p and p.strip()
    ]
    total = len(uploads) + len(paths)
    if total < 1:
        handler.set_status(400)
        handler.write({"error": "file or source_path required"})
        return None
    if total > CHAT_MAX_ATTACHMENTS:
        handler.set_status(400)
        handler.write({"error": f"Up to {CHAT_MAX_ATTACHMENTS} files per message"})
        return None
    atts: list[dict] = []
    for meta in uploads:
        att = _store_uploaded_chat_attachment(
            handler,
            name=name,
            meta=meta,
            conversation_id=conversation_id,
            tmps=tmps,
        )
        if att is None:
            return None
        atts.append(att)
    for source_path in paths:
        att, err = _chat_attachment_from_source_path(
            handler,
            source_path=source_path,
            sender_root=sender_root,
            peers=peers,
            name=name,
        )
        if err is not None:
            status, message = err
            handler.set_status(status)
            handler.write({"error": message})
            return None
        atts.append(att)
    return atts


def _save_copy_attachment(
    handler: BaseHandler,
    *,
    name: str,
    message_id: str,
    msg: dict,
    idx: int,
) -> dict | None:
    """Save attachment copy; return response dict or None after writing an error."""
    att = _att_at(msg, idx)
    if not att:
        handler.set_status(404)
        handler.write({"error": "No attachment"})
        return None
    if att.get("owner_username") == name:
        return {"saved_rel": att.get("owner_rel"), "already_owner": True}
    try:
        abs_path = owner_abs_path(att["owner_username"], att["owner_rel"])
    except (ValueError, KeyError):
        handler.set_status(400)
        handler.write({"error": "Invalid attachment"})
        return None
    if not os.path.isfile(abs_path) and not os.path.isdir(abs_path):
        handler.set_status(410)
        handler.write({"error": "File unavailable"})
        return None
    try:
        size = dir_tree_size(abs_path) if os.path.isdir(abs_path) else os.path.getsize(abs_path)
    except ValueError as exc:
        handler.set_status(413)
        handler.write({"error": str(exc)})
        return None
    if not _quota_check(handler, name, size):
        return None
    payload = handler.parse_json_body()
    if not isinstance(payload, dict) or "dest_dir" not in payload:
        handler.set_status(400)
        handler.write({"error": "Choose a folder to save into"})
        return None
    try:
        saved_rel, size = save_copy_to_data(
            recipient_username=name,
            source_abs=abs_path,
            original_name=att.get("original_name") or "file",
            dest_dir=payload.get("dest_dir") or "",
        )
    except (OSError, ValueError) as exc:
        handler.set_status(400)
        handler.write({"error": str(exc)})
        return None
    _quota_bump(handler, name, size)
    chat_db.set_saved_rel(name, message_id, saved_rel, index=idx)
    return {"saved_rel": saved_rel, "size_bytes": size}


def _forward_message_to_targets(
    handler: BaseHandler,
    *,
    name: str,
    uid: int,
    conversation_id: str,
    src: dict,
    targets: list,
) -> list[dict]:
    forwarded = []
    meta = dict(src.get("metadata") or {})
    meta["forwarded_from"] = {
        "username": src.get("sender_username"),
        "source_message_id": src["id"],
        "source_conversation_id": conversation_id,
    }
    for dest in targets[:20]:
        dest_id = str(dest)
        if not chat_db.user_in_conversation(name, dest_id):
            continue
        if not chat_db.exchange_open(name, dest_id):
            continue
        msg = chat_db.insert_message(
            handler.db_conn,
            username=name,
            conversation_id=dest_id,
            msg_type=src.get("msg_type") or "text",
            body=src.get("body") or "",
            metadata=meta if attachment_entries(meta) else {"forwarded_from": meta["forwarded_from"]},
        )
        dispatch_message(msg, sender_id=uid, sender_username=name)
        forwarded.append(msg)
    return forwarded


class ChatPageHandler(BaseHandler):
    @tornado.web.authenticated
    def get(self):
        if not _require_chat(self):
            return
        self.render("chat.html")


class ChatConversationsHandler(BaseHandler, XSRFTokenMixin):
    @tornado.web.authenticated
    @require_db
    def get(self):
        if not _require_chat(self):
            return
        name = _username(self)
        convs = chat_db.list_conversations(self.db_conn, name)
        for c in convs:
            c["_me"] = name
        self.write({"conversations": _decorate_online(convs)})

    @tornado.web.authenticated
    @require_db
    def post(self):
        if not _require_chat(self):
            return
        self.check_xsrf_cookie()
        name = _username(self)
        uid = _uid(self)
        if uid is None:
            _reject_user_not_found(self)
            return
        try:
            body = self.parse_json_body()
        except tornado.web.HTTPError:
            return
        conv_id = _create_conversation_from_body(self, name, uid, body)
        if conv_id is None:
            return
        convos = chat_db.list_conversations(self.db_conn, name)
        match = next((c for c in convos if c["id"] == conv_id), None)
        self.write({"conversation": match or {"id": conv_id}})


class ChatConversationMessagesHandler(BaseHandler, XSRFTokenMixin):
    @tornado.web.authenticated
    @require_db
    def get(self, conversation_id: str):
        if not _require_chat(self):
            return
        name = _username(self)
        if not chat_db.user_in_conversation(name, conversation_id):
            self.set_status(403)
            self.write({"error": "Forbidden"})
            return
        if not chat_db.exchange_open(name, conversation_id):
            self.write({"messages": [], "pins": [], "receipts": []})
            return
        messages = chat_db.list_messages(
            name,
            conversation_id,
            before_id=self.get_argument("before_id", None) or None,
            after_id=self.get_argument("after_id", None) or None,
            limit=int(self.get_argument("limit", "50")),
        )
        pins = chat_db.list_pins(name, conversation_id)
        receipts = chat_db.list_receipts(name, conversation_id)
        self.write({"messages": messages, "pins": pins, "receipts": receipts})

    @tornado.web.authenticated
    @require_db
    def post(self, conversation_id: str):
        if not _require_chat(self):
            return
        self.check_xsrf_cookie()
        name = _username(self)
        uid = _uid(self)
        if uid is None or not chat_db.user_in_conversation(name, conversation_id):
            self.set_status(403)
            self.write({"error": "Forbidden"})
            return
        if _exchange_blocked(self, name, conversation_id):
            return
        try:
            body = self.parse_json_body()
        except tornado.web.HTTPError:
            return
        msg_type = str(body.get("type") or "text")
        if msg_type != "text":
            self.set_status(400)
            self.write({"error": "Use /attach for files"})
            return
        reply_to = str(body.get("reply_to_id") or "") or None
        try:
            e2e_meta = _e2e_from_body(body)
        except ValueError as exc:
            self.set_status(400)
            self.write({"error": str(exc)})
            return
        if e2e_meta:
            msg = chat_db.insert_message(
                self.db_conn,
                username=name,
                conversation_id=conversation_id,
                msg_type="text",
                body="",
                metadata=e2e_meta,
                reply_to_id=reply_to,
            )
        else:
            text = sanitize_chat_html(body.get("body") or "")
            if not text:
                self.set_status(400)
                self.write({"error": "Empty message"})
                return
            msg = chat_db.insert_message(
                self.db_conn,
                username=name,
                conversation_id=conversation_id,
                msg_type="text",
                body=text,
                reply_to_id=reply_to,
            )
        dispatch_message(msg, sender_id=uid, sender_username=name)
        self.write({"message": msg})


class ChatMessageHandler(BaseHandler, XSRFTokenMixin):
    @tornado.web.authenticated
    @require_db
    def patch(self, conversation_id: str, message_id: str):
        if not _require_chat(self):
            return
        self.check_xsrf_cookie()
        name = _username(self)
        if not chat_db.user_in_conversation(name, conversation_id):
            self.set_status(403)
            self.write({"error": "Forbidden"})
            return
        if _exchange_blocked(self, name, conversation_id):
            return
        try:
            body = self.parse_json_body()
        except tornado.web.HTTPError:
            return
        try:
            e2e_meta = _e2e_from_body(body)
        except ValueError as exc:
            self.set_status(400)
            self.write({"error": str(exc)})
            return
        if e2e_meta:
            msg = chat_db.edit_message(
                actor=name,
                conversation_id=conversation_id,
                message_id=message_id,
                body="",
                e2e=e2e_meta["e2e"],
                mentions=e2e_meta.get("mentions"),
            )
        else:
            text = sanitize_chat_html(body.get("body") or "")
            if not text:
                self.set_status(400)
                self.write({"error": "Empty message"})
                return
            current = chat_db.get_message(name, message_id)
            if current is not None:
                from aird.plugins.chat.sanitize import plain_preview

                if plain_preview(current.get("body") or "", 10000) == plain_preview(text, 10000):
                    self.write({"message": current})
                    return
            msg = chat_db.edit_message(
                actor=name, conversation_id=conversation_id, message_id=message_id, body=text
            )
        if msg is None:
            self.set_status(404)
            self.write({"error": "Message not found or not editable"})
            return
        dispatch_message_edited(name, msg)
        self.write({"message": msg})

    @tornado.web.authenticated
    @require_db
    def delete(self, conversation_id: str, message_id: str):
        if not _require_chat(self):
            return
        self.check_xsrf_cookie()
        name = _username(self)
        if not chat_db.user_in_conversation(name, conversation_id):
            self.set_status(403)
            self.write({"error": "Forbidden"})
            return
        deleted = chat_db.delete_message(
            actor=name, conversation_id=conversation_id, message_id=message_id
        )
        if deleted is None:
            self.set_status(404)
            self.write({"error": "Message not found or not deletable"})
            return
        dispatch_message_deleted(name, conversation_id, message_id)
        self.write({"deleted": True, "message_id": message_id})


def _cleanup_tmp_paths(tmps: list[str]) -> None:
    for tmp in tmps:
        try:
            os.unlink(tmp)
        except OSError:
            pass


def _insert_chat_attachment_message(
    handler: BaseHandler,
    *,
    name: str,
    conversation_id: str,
    caption: str,
    reply_to: str | None,
    e2e_meta: dict | None,
    atts: list[dict],
) -> dict:
    packed = pack_attachments(atts)
    if e2e_meta:
        packed.update(e2e_meta)
    kinds = {a.get("media_kind") for a in atts}
    msg_type = "gif" if kinds == {"gif"} else "file"
    return chat_db.insert_message(
        handler.db_conn,
        username=name,
        conversation_id=conversation_id,
        msg_type=msg_type,
        body=caption,
        metadata=packed,
        reply_to_id=reply_to,
    )


class ChatAttachHandler(BaseHandler, XSRFTokenMixin):
    @tornado.web.authenticated
    @require_db
    def post(self, conversation_id: str):
        if not _require_chat(self):
            return
        self.check_xsrf_cookie()
        name = _username(self)
        uid = _uid(self)
        if uid is None or not chat_db.user_in_conversation(name, conversation_id):
            self.set_status(403)
            self.write({"error": "Forbidden"})
            return
        if _exchange_blocked(self, name, conversation_id):
            return
        caption = sanitize_chat_html(self.get_argument("caption", "") or self.get_argument("body", ""))
        reply_to = str(self.get_argument("reply_to_id", "") or "") or None
        e2e_meta, e2e_err = _parse_chat_attach_e2e(self)
        if e2e_err is not None:
            self.set_status(400)
            self.write({"error": e2e_err})
            return
        if e2e_meta:
            caption = ""
        tmps: list[str] = []
        try:
            atts = _collect_chat_attachments(
                self,
                name=name,
                conversation_id=conversation_id,
                sender_root=get_user_root(self),
                peers=_chat_peer_usernames(name, conversation_id),
                tmps=tmps,
            )
            if atts is None:
                return
            msg = _insert_chat_attachment_message(
                self,
                name=name,
                conversation_id=conversation_id,
                caption=caption,
                reply_to=reply_to,
                e2e_meta=e2e_meta,
                atts=atts,
            )
        except (OSError, ValueError) as exc:
            logger.exception("chat attach failed")
            self.set_status(400)
            self.write({"error": str(exc)})
            return
        finally:
            _cleanup_tmp_paths(tmps)
        dispatch_message(msg, sender_id=uid, sender_username=name)
        self.write({"message": msg})


async def _stream_file_chunks(handler: BaseHandler, path: str) -> None:
    async with aiofiles.open(path, "rb") as fh:
        while True:
            chunk = await fh.read(CHUNK_SIZE)
            if not chunk:
                break
            handler.write(chunk)
            await handler.flush()


async def _serve_chat_folder_zip(handler: BaseHandler, owner: str, rel: str) -> None:
    try:
        root = attachment_store_root(owner, rel)
        entries = await asyncio.to_thread(collect_zip_entries, root, [rel])
        zip_path = await asyncio.to_thread(build_zip_file, entries)
    except ZipDownloadError as exc:
        handler.set_status(exc.status)
        handler.write({"error": str(exc)})
        return
    except Exception:
        logger.exception("chat folder zip failed")
        handler.set_status(500)
        handler.write({"error": "Failed to zip folder"})
        return
    filename = f"{os.path.basename(rel.rstrip('/')) or 'folder'}.zip"
    try:
        handler.set_header("Content-Type", "application/zip")
        handler.set_header("Cache-Control", "private, max-age=60")
        handler.set_header("Content-Length", str(os.path.getsize(zip_path)))
        handler.set_header("Content-Disposition", f'attachment; filename="{filename}"')
        await _stream_file_chunks(handler, zip_path)
    finally:
        try:
            os.unlink(zip_path)
        except OSError:
            pass


async def _serve_chat_file_attachment(
    handler: BaseHandler, abs_path: str, att: dict
) -> None:
    filename = att.get("original_name") or os.path.basename(abs_path)
    preview = inline_preview_payload(abs_path)
    if preview:
        data, mime = preview
        handler.set_header("Content-Type", mime)
        handler.set_header("Cache-Control", "private, max-age=60")
        handler.set_header("Content-Length", str(len(data)))
        handler.set_header("Content-Disposition", f'inline; filename="{filename}"')
        handler.write(data)
        return
    mime, _ = mimetypes.guess_type(filename)
    handler.set_header("Content-Type", mime or "application/octet-stream")
    handler.set_header("Cache-Control", "private, max-age=60")
    handler.set_header("Content-Length", str(os.path.getsize(abs_path)))
    handler.set_header("Content-Disposition", f'inline; filename="{filename}"')
    await _stream_file_chunks(handler, abs_path)


class ChatMessageFileHandler(BaseHandler):
    @tornado.web.authenticated
    @require_db
    async def get(self, conversation_id: str, message_id: str):
        if not _require_chat(self):
            return
        name = _username(self)
        if not chat_db.user_in_conversation(name, conversation_id):
            self.set_status(403)
            self.write({"error": "Forbidden"})
            return
        msg = chat_db.get_message(name, message_id)
        if msg is None or msg["conversation_id"] != conversation_id:
            self.set_status(404)
            self.write({"error": "Message not found"})
            return
        try:
            idx = int(self.get_argument("i", "0") or 0)
        except (TypeError, ValueError):
            idx = 0
        att = _att_at(msg, idx)
        if not att:
            self.set_status(404)
            self.write({"error": "No attachment"})
            return
        owner = att.get("owner_username")
        rel = att.get("owner_rel")
        if not owner or not rel:
            self.set_status(404)
            self.write({"error": "No attachment"})
            return
        try:
            abs_path = owner_abs_path(owner, rel)
        except ValueError:
            self.set_status(400)
            self.write({"error": "Invalid attachment"})
            return
        if os.path.isdir(abs_path):
            share_url = _ensure_chat_folder_share(
                self,
                actor=name,
                owner=owner,
                rel=rel,
                conversation_id=conversation_id,
                message_id=message_id,
                index=idx,
                att=att,
            )
            if share_url:
                self.redirect(share_url)
                return
            await _serve_chat_folder_zip(self, owner, rel)
            return
        if not os.path.isfile(abs_path):
            self.set_status(410)
            self.write({"error": "File unavailable"})
            return
        await _serve_chat_file_attachment(self, abs_path, att)


class ChatSaveCopyHandler(BaseHandler, XSRFTokenMixin):
    @tornado.web.authenticated
    @require_db
    def post(self, conversation_id: str, message_id: str):
        if not _require_chat(self):
            return
        self.check_xsrf_cookie()
        name = _username(self)
        if not chat_db.user_in_conversation(name, conversation_id):
            self.set_status(403)
            self.write({"error": "Forbidden"})
            return
        msg = chat_db.get_message(name, message_id)
        if msg is None:
            self.set_status(404)
            self.write({"error": "Message not found"})
            return
        try:
            idx = int(self.get_argument("i", "0") or 0)
        except (TypeError, ValueError):
            idx = 0
        result = _save_copy_attachment(
            self, name=name, message_id=message_id, msg=msg, idx=idx
        )
        if result is not None:
            self.write(result)


class ChatReactionHandler(BaseHandler, XSRFTokenMixin):
    @tornado.web.authenticated
    @require_db
    def post(self, conversation_id: str, message_id: str):
        if not _require_chat(self):
            return
        self.check_xsrf_cookie()
        name = _username(self)
        if not chat_db.user_in_conversation(name, conversation_id):
            self.set_status(403)
            self.write({"error": "Forbidden"})
            return
        try:
            body = self.parse_json_body()
        except tornado.web.HTTPError:
            return
        try:
            reactions = chat_db.toggle_reaction(
                actor=name,
                conversation_id=conversation_id,
                message_id=message_id,
                emoji=str(body.get("emoji") or ""),
            )
        except ValueError as exc:
            self.set_status(400)
            self.write({"error": str(exc)})
            return
        dispatch_reaction(name, conversation_id, message_id, reactions)
        self.write({"reactions": reactions})


class ChatPinHandler(BaseHandler, XSRFTokenMixin):
    @tornado.web.authenticated
    @require_db
    def post(self, conversation_id: str, message_id: str):
        if not _require_chat(self):
            return
        self.check_xsrf_cookie()
        name = _username(self)
        if not chat_db.user_in_conversation(name, conversation_id):
            self.set_status(403)
            self.write({"error": "Forbidden"})
            return
        try:
            pins = chat_db.pin_message(actor=name, conversation_id=conversation_id, message_id=message_id)
        except ValueError as exc:
            self.set_status(400)
            self.write({"error": str(exc)})
            return
        dispatch_pin(name, conversation_id, pins, pinned=True)
        self.write({"pins": pins})

    @tornado.web.authenticated
    @require_db
    def delete(self, conversation_id: str, message_id: str):
        if not _require_chat(self):
            return
        self.check_xsrf_cookie()
        name = _username(self)
        if not chat_db.user_in_conversation(name, conversation_id):
            self.set_status(403)
            self.write({"error": "Forbidden"})
            return
        pins = chat_db.unpin_message(actor=name, conversation_id=conversation_id, message_id=message_id)
        dispatch_pin(name, conversation_id, pins, pinned=False)
        self.write({"pins": pins})


class ChatForwardHandler(BaseHandler, XSRFTokenMixin):
    @tornado.web.authenticated
    @require_db
    def post(self, conversation_id: str, message_id: str):
        if not _require_chat(self):
            return
        self.check_xsrf_cookie()
        name = _username(self)
        uid = _uid(self)
        if uid is None or not chat_db.user_in_conversation(name, conversation_id):
            self.set_status(403)
            self.write({"error": "Forbidden"})
            return
        src = chat_db.get_message(name, message_id)
        if src is None:
            self.set_status(404)
            self.write({"error": "Message not found"})
            return
        if is_e2e_meta(src.get("metadata")) or src.get("e2e"):
            self.set_status(400)
            self.write({"error": "Encrypted messages must be re-encrypted on the client"})
            return
        try:
            body = self.parse_json_body()
        except tornado.web.HTTPError:
            return
        targets = body.get("conversation_ids") or []
        if not isinstance(targets, list) or not targets:
            self.set_status(400)
            self.write({"error": "conversation_ids required"})
            return
        forwarded = _forward_message_to_targets(
            self,
            name=name,
            uid=uid,
            conversation_id=conversation_id,
            src=src,
            targets=targets,
        )
        self.write({"messages": forwarded})


class ChatMembersHandler(BaseHandler, XSRFTokenMixin):
    @tornado.web.authenticated
    @require_db
    def post(self, conversation_id: str):
        if not _require_chat(self):
            return
        self.check_xsrf_cookie()
        name = _username(self)
        if not chat_db.user_in_conversation(name, conversation_id):
            self.set_status(403)
            self.write({"error": "Forbidden"})
            return
        try:
            body = self.parse_json_body()
        except tornado.web.HTTPError:
            return
        try:
            rec = chat_db.add_member(
                self.db_conn,
                actor=name,
                conversation_id=conversation_id,
                username=str(body.get("username") or ""),
            )
        except ValueError as exc:
            self.set_status(400)
            self.write({"error": str(exc)})
            return
        self.write({"member": rec, "conversation": chat_db.get_conversation(name, conversation_id)})

    @tornado.web.authenticated
    @require_db
    def delete(self, conversation_id: str):
        if not _require_chat(self):
            return
        self.check_xsrf_cookie()
        name = _username(self)
        if not chat_db.user_in_conversation(name, conversation_id):
            self.set_status(403)
            self.write({"error": "Forbidden"})
            return
        target = self.get_argument("username", "") or _username(self)
        try:
            chat_db.remove_member(actor=name, conversation_id=conversation_id, username=target)
        except ValueError as exc:
            self.set_status(400)
            self.write({"error": str(exc)})
            return
        self.write({"removed": target})


class ChatMuteHandler(BaseHandler, XSRFTokenMixin):
    @tornado.web.authenticated
    @require_db
    def post(self, conversation_id: str):
        if not _require_chat(self):
            return
        self.check_xsrf_cookie()
        name = _username(self)
        if not chat_db.user_in_conversation(name, conversation_id):
            self.set_status(403)
            self.write({"error": "Forbidden"})
            return
        try:
            body = self.parse_json_body()
        except tornado.web.HTTPError:
            return
        muted = bool(body.get("muted"))
        chat_db.set_muted(name, conversation_id, muted)
        self.write({"muted": muted})


class ChatRequestHandler(BaseHandler, XSRFTokenMixin):
    @tornado.web.authenticated
    @require_db
    def post(self, conversation_id: str):
        if not _require_chat(self):
            return
        self.check_xsrf_cookie()
        name = _username(self)
        if not chat_db.user_in_conversation(name, conversation_id):
            self.set_status(403)
            self.write({"error": "Forbidden"})
            return
        try:
            body = self.parse_json_body()
        except tornado.web.HTTPError:
            return
        action = str(body.get("action") or "")
        conv = chat_db.get_conversation(name, conversation_id) or {}
        user_ids = [m["user_id"] for m in conv.get("members") or []]
        try:
            result = chat_db.respond_to_request(
                actor=name, conversation_id=conversation_id, action=action
            )
        except ValueError as exc:
            self.set_status(400)
            self.write({"error": str(exc)})
            return
        if action == "decline":
            names = (
                [m["username"] for m in conv.get("members") or []]
                if conv.get("kind") == "dm"
                else [name]
            )
            delete_stored_conv_keys(names, conversation_id)
        try:
            dispatch_chat_request(user_ids, conversation_id)
        except Exception:
            logger.debug("chat request dispatch failed", exc_info=True)
        self.write({"ok": True, "conversation": result})


class ChatSearchHandler(BaseHandler):
    @tornado.web.authenticated
    @require_db
    def get(self):
        if not _require_chat(self):
            return
        name = _username(self)
        q = self.get_argument("q", "")
        self.write({"results": chat_db.search_messages(name, q)})


class ChatSharedWithMeHandler(BaseHandler):
    @tornado.web.authenticated
    @require_db
    def get(self):
        if not _require_chat(self):
            return
        name = _username(self)
        senders = chat_db.list_shared_with_me_grouped(self.db_conn, name)
        total = sum(len(s.get("files") or []) for s in senders)
        self.write({"senders": senders, "total_files": total, "items": chat_db.list_shared_with_me(self.db_conn, name)})


class ChatSharedWithMeDeleteHandler(BaseHandler, XSRFTokenMixin):
    @tornado.web.authenticated
    @require_db
    def delete(self, share_id: str):
        if not _require_chat(self):
            return
        self.check_xsrf_cookie()
        name = _username(self)
        hidden = chat_db.hide_shared_item(name, share_id)
        if hidden is None:
            self.set_status(404)
            self.write({"error": "Share not found"})
            return
        self.write({"deleted": True, "id": share_id})


class ChatUnreadHandler(BaseHandler):
    @tornado.web.authenticated
    @require_db
    def get(self):
        if not _require_chat(self):
            return
        name = _username(self)
        self.write(chat_db.unread_summary(self.db_conn, name))


class ChatE2EKeysHandler(BaseHandler, XSRFTokenMixin):
    def compute_etag(self):
        return None

    @tornado.web.authenticated
    @require_db
    def get(self):
        if not _require_chat(self):
            return
        raw = self.get_argument("users", "")
        names = [n.strip() for n in raw.split(",") if n.strip()][:50]
        if not names:
            names = [_username(self)]
        self.set_header("Cache-Control", "no-store, no-cache, must-revalidate")
        self.write({"keys": get_identity_keys(self.db_conn, names)})

    @tornado.web.authenticated
    @require_db
    def put(self):
        if not _require_chat(self):
            return
        self.check_xsrf_cookie()
        try:
            body = self.parse_json_body()
        except tornado.web.HTTPError:
            return
        try:
            jwk = put_identity_key(self.db_conn, _username(self), body.get("public_jwk"))
        except ValueError as exc:
            self.set_status(400)
            self.write({"error": str(exc)})
            return
        self.write({"ok": True, "public_jwk": jwk})


class ChatE2EIdentityHandler(BaseHandler, XSRFTokenMixin):
    def compute_etag(self):
        return None

    @tornado.web.authenticated
    @require_db
    def get(self):
        if not _require_chat(self):
            return
        name = _username(self)
        self.set_header("Cache-Control", "no-store, no-cache, must-revalidate")
        self.write({"backup": load_identity_backup(name)})

    @tornado.web.authenticated
    @require_db
    def put(self):
        if not _require_chat(self):
            return
        self.check_xsrf_cookie()
        name = _username(self)
        try:
            body = self.parse_json_body()
        except tornado.web.HTTPError:
            return
        try:
            backup = save_identity_backup(name, body.get("backup") or body)
            jwk = put_identity_key(self.db_conn, name, backup["publicJwk"])
        except ValueError as exc:
            self.set_status(400)
            self.write({"error": str(exc)})
            return
        self.write({"ok": True, "backup": backup, "public_jwk": jwk})


class ChatConversationE2EHandler(BaseHandler, XSRFTokenMixin):
    def compute_etag(self):
        return None

    @tornado.web.authenticated
    @require_db
    def get(self, conversation_id: str):
        if not _require_chat(self):
            return
        name = _username(self)
        if not chat_db.user_in_conversation(name, conversation_id):
            self.set_status(403)
            self.write({"error": "Forbidden"})
            return
        conv = chat_db.get_conversation(name, conversation_id) or {}
        members = conv.get("members") or []
        if conv.get("request") == "incoming":
            self.set_header("Cache-Control", "no-store, no-cache, must-revalidate")
            self.write({"wraps": {}, "keys": {}})
            return
        accepted = [m["username"] for m in members if int(m.get("accepted", 1))]
        wraps = chat_db.get_e2e_wraps(name, conversation_id)
        self.set_header("Cache-Control", "no-store, no-cache, must-revalidate")
        self.write(
            {
                "wraps": {k: v for k, v in wraps.items() if k in accepted},
                "keys": get_identity_keys(self.db_conn, accepted),
            }
        )

    @tornado.web.authenticated
    @require_db
    def put(self, conversation_id: str):
        if not _require_chat(self):
            return
        self.check_xsrf_cookie()
        name = _username(self)
        if not chat_db.user_in_conversation(name, conversation_id):
            self.set_status(403)
            self.write({"error": "Forbidden"})
            return
        try:
            body = self.parse_json_body()
        except tornado.web.HTTPError:
            return
        wraps_in = body.get("wraps") if isinstance(body.get("wraps"), dict) else {}
        if not wraps_in:
            self.set_status(400)
            self.write({"error": "wraps required"})
            return
        conv = chat_db.get_conversation(name, conversation_id) or {}
        if conv.get("request") == "incoming":
            self.set_status(403)
            self.write({"error": "Chat request not accepted yet"})
            return
        accepted = {
            m["username"]
            for m in (conv.get("members") or [])
            if int(m.get("accepted", 1))
        }
        wraps_in = {k: v for k, v in wraps_in.items() if k in accepted}
        if not wraps_in:
            self.set_status(400)
            self.write({"error": "wraps required"})
            return
        members = [m["username"] for m in (conv.get("members") or [])]
        try:
            wraps = chat_db.put_e2e_wraps(
                actor=name, conversation_id=conversation_id, wraps=wraps_in
            )
            delete_stored_conv_keys(members, conversation_id)
        except ValueError as exc:
            self.set_status(400)
            self.write({"error": str(exc)})
            return
        except OSError:
            logger.warning("e2e wrap write failed", exc_info=True)
            self.set_status(503)
            self.write({"error": "Could not save encryption key"})
            return
        try:
            dispatch_e2e_key_ready(name, conversation_id)
        except Exception:
            logger.debug("e2e key ready dispatch failed", exc_info=True)
        self.write({"wraps": {k: v for k, v in wraps.items() if k in accepted}})
