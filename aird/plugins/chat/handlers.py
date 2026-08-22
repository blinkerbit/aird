"""HTTP handlers for direct messages."""

from __future__ import annotations

import json
import logging
import os
import tempfile

import tornado.web

from aird.core.security import is_within_root
from aird.handlers.base_handler import (
    BaseHandler,
    XSRFTokenMixin,
    get_user_root,
    get_username_string_for_db,
    require_db,
)
from aird.plugins.chat import db as chat_db
from aird.plugins.chat import is_chat_enabled
from aird.plugins.chat.files import copy_to_recipient_share, recipient_root_for_username
from aird.plugins.chat.notify import dispatch_message, dispatch_message_deleted
from aird.plugins.chat.sanitize import sanitize_chat_html

logger = logging.getLogger(__name__)

_TOKEN_ONLY = frozenset({"token_user", "admin_token"})


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
    return True


def _current_user_id(handler: BaseHandler) -> int | None:
    username = get_username_string_for_db(handler)
    if not username:
        return None
    return chat_db.resolve_user_id(handler.db_conn, username)


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
        uid = _current_user_id(self)
        if uid is None:
            self.set_status(403)
            self.write({"error": "User not found"})
            return
        self.write({"conversations": chat_db.list_conversations(self.db_conn, uid)})

    @tornado.web.authenticated
    @require_db
    def post(self):
        if not _require_chat(self):
            return
        self.check_xsrf_cookie()
        uid = _current_user_id(self)
        if uid is None:
            self.set_status(403)
            self.write({"error": "User not found"})
            return
        try:
            body = self.parse_json_body()
        except tornado.web.HTTPError:
            return
        peer_name = str(body.get("username") or "").strip()
        if not peer_name:
            self.set_status(400)
            self.write({"error": "username required"})
            return
        if peer_name in _TOKEN_ONLY:
            self.set_status(400)
            self.write({"error": "Invalid recipient"})
            return
        peer_id = chat_db.resolve_user_id(self.db_conn, peer_name)
        if peer_id is None:
            self.set_status(404)
            self.write({"error": "User not found"})
            return
        if peer_id == uid:
            self.set_status(400)
            self.write({"error": "Cannot message yourself"})
            return
        conv_id = chat_db.create_dm_conversation(self.db_conn, uid, peer_id)
        convos = chat_db.list_conversations(self.db_conn, uid)
        match = next((c for c in convos if c["id"] == conv_id), None)
        self.write({"conversation": match or {"id": conv_id, "peer_username": peer_name}})


class ChatConversationMessagesHandler(BaseHandler, XSRFTokenMixin):
    @tornado.web.authenticated
    @require_db
    def get(self, conversation_id: str):
        if not _require_chat(self):
            return
        uid = _current_user_id(self)
        conv_id = int(conversation_id)
        if uid is None or not chat_db.user_in_conversation(self.db_conn, conv_id, uid):
            self.set_status(403)
            self.write({"error": "Forbidden"})
            return
        before_id = self.get_argument("before_id", None)
        after_id = self.get_argument("after_id", None)
        limit = int(self.get_argument("limit", "50"))
        messages = chat_db.list_messages(
            self.db_conn,
            conv_id,
            before_id=int(before_id) if before_id else None,
            after_id=int(after_id) if after_id else None,
            limit=limit,
        )
        self.write({"messages": messages})

    @tornado.web.authenticated
    @require_db
    def post(self, conversation_id: str):
        if not _require_chat(self):
            return
        self.check_xsrf_cookie()
        uid = _current_user_id(self)
        conv_id = int(conversation_id)
        if uid is None or not chat_db.user_in_conversation(self.db_conn, conv_id, uid):
            self.set_status(403)
            self.write({"error": "Forbidden"})
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
        text = sanitize_chat_html(body.get("body") or "")
        if not text:
            self.set_status(400)
            self.write({"error": "Empty message"})
            return
        msg = chat_db.insert_message(
            self.db_conn,
            conversation_id=conv_id,
            sender_id=uid,
            msg_type="text",
            body=text,
        )
        dispatch_message(self.db_conn, msg, sender_id=uid)
        self.write({"message": msg})


class ChatMessageDeleteHandler(BaseHandler, XSRFTokenMixin):
    @tornado.web.authenticated
    @require_db
    def delete(self, conversation_id: str, message_id: str):
        if not _require_chat(self):
            return
        self.check_xsrf_cookie()
        uid = _current_user_id(self)
        conv_id = int(conversation_id)
        msg_id = int(message_id)
        if uid is None or not chat_db.user_in_conversation(self.db_conn, conv_id, uid):
            self.set_status(403)
            self.write({"error": "Forbidden"})
            return
        deleted = chat_db.delete_message(self.db_conn, msg_id, uid)
        if deleted is None:
            self.set_status(404)
            self.write({"error": "Message not found or not deletable"})
            return
        if int(deleted["conversation_id"]) != conv_id:
            self.set_status(400)
            self.write({"error": "Invalid conversation"})
            return
        dispatch_message_deleted(self.db_conn, conv_id, msg_id)
        self.write({"deleted": True, "message_id": msg_id})


class ChatAttachHandler(BaseHandler, XSRFTokenMixin):
    @tornado.web.authenticated
    @require_db
    def post(self, conversation_id: str):
        if not _require_chat(self):
            return
        self.check_xsrf_cookie()
        uid = _current_user_id(self)
        conv_id = int(conversation_id)
        if uid is None or not chat_db.user_in_conversation(self.db_conn, conv_id, uid):
            self.set_status(403)
            self.write({"error": "Forbidden"})
            return
        sender_name = get_username_string_for_db(self)
        recipient_id = chat_db.other_member_id(self.db_conn, conv_id, uid)
        if recipient_id is None or not sender_name:
            self.set_status(400)
            self.write({"error": "Invalid conversation"})
            return
        recipient_row = self.db_conn.execute(
            "SELECT username FROM users WHERE id = ?",
            (recipient_id,),
        ).fetchone()
        if not recipient_row:
            self.set_status(400)
            self.write({"error": "Recipient not found"})
            return
        recipient_username = recipient_row[0]
        recipient_root = recipient_root_for_username(recipient_username)
        sender_root = get_user_root(self)
        caption = sanitize_chat_html(self.get_argument("caption", ""))

        source_abs = None
        original_name = None
        upload = self.request.files.get("file") if self.request.files else None
        if upload:
            meta = upload[0]
            original_name = os.path.basename(meta["filename"])
            fd, tmp = tempfile.mkstemp(prefix="aird_chat_")
            os.close(fd)
            try:
                with open(tmp, "wb") as out:
                    out.write(meta["body"])
                source_abs = tmp
            except Exception:
                os.unlink(tmp)
                raise
        else:
            source_path = self.get_argument("source_path", "").strip().lstrip("/")
            if not source_path:
                self.set_status(400)
                self.write({"error": "file or source_path required"})
                return
            source_abs = os.path.join(sender_root, source_path.replace("/", os.sep))
            original_name = os.path.basename(source_path)
            if not is_within_root(source_abs, sender_root) or not os.path.isfile(source_abs):
                self.set_status(400)
                self.write({"error": "Invalid source file"})
                return

        try:
            rel_path, size = copy_to_recipient_share(
                recipient_root=recipient_root,
                sender_username=sender_name,
                source_abs=source_abs,
                original_name=original_name,
            )
        except (OSError, ValueError) as exc:
            logger.exception("chat file copy failed")
            self.set_status(400)
            self.write({"error": str(exc)})
            return
        finally:
            if upload and source_abs and source_abs.startswith(tempfile.gettempdir()):
                try:
                    os.unlink(source_abs)
                except OSError:
                    pass

        ext = os.path.splitext(original_name or "")[1].lower()
        msg_type = "gif" if ext == ".gif" else "file"
        msg = chat_db.insert_message(
            self.db_conn,
            conversation_id=conv_id,
            sender_id=uid,
            msg_type=msg_type,
            body=caption,
            attachment_path=rel_path,
            metadata={"original_name": original_name},
        )
        chat_db.insert_file_share(
            self.db_conn,
            conversation_id=conv_id,
            message_id=msg["id"],
            sender_id=uid,
            recipient_id=recipient_id,
            relative_path=rel_path,
            original_name=original_name or os.path.basename(rel_path),
            size_bytes=size,
        )
        dispatch_message(self.db_conn, msg, sender_id=uid)
        self.write({"message": msg, "relative_path": rel_path})


class ChatSharedWithMeHandler(BaseHandler):
    @tornado.web.authenticated
    @require_db
    def get(self):
        if not _require_chat(self):
            return
        uid = _current_user_id(self)
        if uid is None:
            self.set_status(403)
            self.write({"error": "Forbidden"})
            return
        senders = chat_db.list_shared_with_me_grouped(self.db_conn, uid)
        total = sum(len(s.get("files") or []) for s in senders)
        self.write({"senders": senders, "total_files": total, "items": chat_db.list_shared_with_me(self.db_conn, uid)})


class ChatSharedWithMeDeleteHandler(BaseHandler, XSRFTokenMixin):
    @tornado.web.authenticated
    @require_db
    def delete(self, share_id: str):
        if not _require_chat(self):
            return
        self.check_xsrf_cookie()
        uid = _current_user_id(self)
        if uid is None:
            self.set_status(403)
            self.write({"error": "Forbidden"})
            return
        from aird.plugins.chat.files import delete_recipient_share_file

        share = chat_db.delete_file_share(self.db_conn, int(share_id), uid)
        if share is None:
            self.set_status(404)
            self.write({"error": "Share not found"})
            return
        recipient_root = get_user_root(self)
        try:
            delete_recipient_share_file(recipient_root, share["relative_path"])
        except (OSError, ValueError) as exc:
            logger.warning("chat share file delete failed: %s", exc)
        self.write({"deleted": True, "id": int(share_id)})


class ChatUnreadHandler(BaseHandler):
    @tornado.web.authenticated
    @require_db
    def get(self):
        if not _require_chat(self):
            return
        uid = _current_user_id(self)
        if uid is None:
            self.set_status(403)
            self.write({"error": "Forbidden"})
            return
        self.write(chat_db.unread_summary(self.db_conn, uid))
