"""Parallel upload sessions: sparse staging file + offset writes + finalize."""

from __future__ import annotations

import logging
import os
import re
import threading
import time
from dataclasses import dataclass, field

from aird.core.transfer_native import pwrite_fd, write_fd

logger = logging.getLogger(__name__)

_SESSION_RE = re.compile(r"^[a-fA-F0-9-]{8,64}$")
_sessions: dict[str, "_UploadSession"] = {}
_sessions_lock = threading.Lock()


@dataclass
class _UploadSession:
    session_id: str
    temp_path: str
    total: int
    upload_dir: str
    filename: str
    user_root: str
    fd: int
    ticket: str = ""
    written: int = 0
    lock: threading.Lock = field(default_factory=threading.Lock)
    created_at: float = field(default_factory=time.time)


def valid_session_id(session_id: str | None) -> bool:
    return bool(session_id and _SESSION_RE.match(session_id))


def _session_key(user_root: str, session_id: str) -> str:
    return f"{os.path.realpath(user_root)}::{session_id}"


def open_or_get_session(
    *,
    session_id: str,
    user_root: str,
    upload_dir: str,
    filename: str,
    total: int,
    ticket: str | None = None,
) -> _UploadSession:
    if not valid_session_id(session_id):
        raise ValueError("invalid upload session")
    if total <= 0:
        raise ValueError("invalid upload total")
    key = _session_key(user_root, session_id)
    with _sessions_lock:
        sess = _sessions.get(key)
        if sess is not None:
            if sess.total != total or sess.filename != filename:
                raise ValueError("upload session mismatch")
            return sess
        os.makedirs(user_root, exist_ok=True)
        fd, temp_path = _mk_staging(user_root, session_id)
        try:
            os.ftruncate(fd, total)
        except OSError:
            os.close(fd)
            raise
        sess = _UploadSession(
            session_id=session_id,
            temp_path=temp_path,
            total=total,
            upload_dir=upload_dir or "",
            filename=filename,
            user_root=user_root,
            fd=fd,
            ticket=ticket or "",
        )
        _sessions[key] = sess
        return sess


def _mk_staging(user_root: str, session_id: str) -> tuple[int, str]:
    path = os.path.join(user_root, f".aird_up_sess_{session_id}")
    flags = os.O_RDWR | os.O_CREAT
    if hasattr(os, "O_BINARY"):
        flags |= os.O_BINARY
    fd = os.open(path, flags)
    return fd, path


def write_at_offset(sess: _UploadSession, offset: int, data: bytes) -> int:
    if offset < 0 or offset + len(data) > sess.total:
        raise ValueError("chunk out of range")
    with sess.lock:
        n = pwrite_fd(sess.fd, data, offset)
        sess.written += n
        return n


def close_session_fd(sess: _UploadSession) -> None:
    with sess.lock:
        if sess.fd >= 0:
            try:
                os.close(sess.fd)
            except OSError:
                pass
            sess.fd = -1


def pop_session(user_root: str, session_id: str) -> _UploadSession | None:
    key = _session_key(user_root, session_id)
    with _sessions_lock:
        sess = _sessions.pop(key, None)
    return sess


def abandon_session(user_root: str, session_id: str) -> None:
    sess = pop_session(user_root, session_id)
    if not sess:
        return
    close_session_fd(sess)
    try:
        if os.path.exists(sess.temp_path):
            os.remove(sess.temp_path)
    except OSError:
        logger.debug("abandon session unlink failed", exc_info=True)


def cleanup_stale_sessions(max_age_sec: float = 3600.0) -> None:
    now = time.time()
    with _sessions_lock:
        stale = [k for k, s in _sessions.items() if now - s.created_at > max_age_sec]
    for key in stale:
        with _sessions_lock:
            sess = _sessions.pop(key, None)
        if sess:
            close_session_fd(sess)
            try:
                if os.path.exists(sess.temp_path):
                    os.remove(sess.temp_path)
            except OSError:
                pass
