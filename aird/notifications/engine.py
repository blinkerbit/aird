"""One event id per recipient. Socket immediately. Web Push if that browser
does not confirm within a few hundred milliseconds.

A confirm does not cancel the push. Other signed-in devices still need it.
The confirming page tells its own service worker to drop that id so the same
device does not ring twice. Failed pushes stay in sqlite until they succeed
or the event expires. Call alerts expire quickly. There is no FCM or APNs.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import time
import uuid
from concurrent.futures import ThreadPoolExecutor

logger = logging.getLogger(__name__)

_pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="notify-push")
_MAX_ATTEMPTS = 3


def _db_path():
    import aird.constants as constants_module

    return getattr(constants_module, "DB_PATH", None)


def _connect():
    path = _db_path()
    if not path:
        return None
    conn = sqlite3.connect(path, timeout=5)
    conn.row_factory = sqlite3.Row
    conn.execute(
        """CREATE TABLE IF NOT EXISTS notify_outbox (
            id TEXT PRIMARY KEY,
            username TEXT NOT NULL,
            kind TEXT NOT NULL,
            payload TEXT NOT NULL,
            created_at REAL NOT NULL,
            expire_at REAL NOT NULL,
            push_after REAL NOT NULL,
            pushed_at REAL,
            attempts INTEGER NOT NULL DEFAULT 0,
            push_ttl INTEGER NOT NULL
        )"""
    )
    conn.execute(
        """CREATE INDEX IF NOT EXISTS idx_notify_outbox_due
           ON notify_outbox(push_after)"""
    )
    return conn


def deliver(
    *,
    username: str,
    user_id: int,
    kind: str,
    socket_payload: dict | None,
    push_payload: dict,
    push: bool = True,
    expire_s: float = 86400,
    push_ttl: int = 86400,
    immediate: bool = False,
) -> str:
    """Send *socket_payload* now and start the push in the same turn."""
    notify_id = uuid.uuid4().hex
    if socket_payload is not None:
        socket = dict(socket_payload)
        socket["notify_id"] = notify_id
        try:
            from aird.plugins.chat.service import get_chat_hub

            get_chat_hub().send_to_user(int(user_id), socket)
        except Exception:
            logger.warning("notify socket failed for %s", username, exc_info=True)
    if not push or not username:
        return notify_id
    body = dict(push_payload)
    body["notify_id"] = notify_id
    body["kind"] = kind
    now = time.time()
    delay = 0.0
    conn = _connect()
    if conn is None:
        return notify_id
    try:
        conn.execute(
            """INSERT INTO notify_outbox(
                   id, username, kind, payload, created_at, expire_at,
                   push_after, attempts, push_ttl
               ) VALUES(?,?,?,?,?,?,?,0,?)""",
            (
                notify_id,
                username,
                kind,
                json.dumps(body),
                now,
                now + float(expire_s),
                now + delay,
                int(push_ttl),
            ),
        )
        conn.commit()
    except Exception:
        logger.warning("notify enqueue failed for %s", username, exc_info=True)
        return notify_id
    finally:
        conn.close()
    _kick(delay)
    return notify_id


def push_now(username: str, payload: dict, ttl: int = 30) -> None:
    """Hand a push to the network thread without waiting on the outbox."""
    if not username:
        return
    body = dict(payload)

    def _run() -> None:
        conn = _connect()
        if conn is None:
            return
        try:
            from aird.pwa.push import send_web_push

            send_web_push(conn, username, body, ttl=max(0, int(ttl)))
        except Exception:
            logger.warning("instant web push failed for %s", username, exc_info=True)
        finally:
            conn.close()

    _pool.submit(_run)


def _kick(delay: float) -> None:
    if delay <= 0:
        _async(flush_due)
        return
    try:
        import tornado.ioloop

        loop = tornado.ioloop.IOLoop.current(instance=False)
    except Exception:
        loop = None
    if loop is None:
        return
    loop.call_later(delay, lambda: _async(flush_due))


def _async(fn, *args) -> None:
    _pool.submit(fn, *args)


def flush_due() -> int:
    rows = _claim()
    for row in rows:
        _send_row(row)
    return len(rows)


def _claim() -> list[dict]:
    conn = _connect()
    if conn is None:
        return []
    now = time.time()
    claimed: list[dict] = []
    try:
        conn.execute("BEGIN IMMEDIATE")
        rows = conn.execute(
            """SELECT id, username, kind, payload, attempts, push_ttl
               FROM notify_outbox
               WHERE pushed_at IS NULL AND attempts < ?
                 AND expire_at > ? AND push_after <= ?
               ORDER BY push_after
               LIMIT 20""",
            (_MAX_ATTEMPTS, now, now),
        ).fetchall()
        for row in rows:
            backoff = 1.0 if int(row["attempts"]) == 0 else 3.0
            cur = conn.execute(
                """UPDATE notify_outbox
                   SET attempts = attempts + 1, push_after = ?
                   WHERE id = ? AND pushed_at IS NULL AND attempts = ?""",
                (now + backoff, row["id"], row["attempts"]),
            )
            if cur.rowcount:
                claimed.append({key: row[key] for key in row.keys()})
        conn.execute(
            """DELETE FROM notify_outbox
               WHERE expire_at < ? OR (pushed_at IS NOT NULL AND pushed_at < ?)""",
            (now - 3600, now - 3600),
        )
        conn.commit()
        return claimed
    except Exception:
        logger.warning("notify claim failed", exc_info=True)
        try:
            conn.rollback()
        except Exception:
            pass
        return []
    finally:
        conn.close()


def drop_pending_calls(conversation_id: str, call_id: str = "") -> None:
    """Drop a call alert that has not been pushed yet."""
    conn = _connect()
    if conn is None or not conversation_id:
        return
    now = time.time()
    try:
        rows = conn.execute(
            "SELECT id, payload FROM notify_outbox WHERE kind='call' AND pushed_at IS NULL"
        ).fetchall()
        for row in rows:
            try:
                payload = json.loads(row["payload"])
            except json.JSONDecodeError:
                continue
            if str(payload.get("conversation_id") or "") != conversation_id:
                continue
            pending_id = str(payload.get("call_id") or "")
            if call_id and pending_id not in ("", call_id):
                continue
            conn.execute(
                "UPDATE notify_outbox SET pushed_at=?, expire_at=? WHERE id=? AND pushed_at IS NULL",
                (now, now, row["id"]),
            )
        conn.commit()
    except Exception:
        logger.warning("notify call cancel failed", exc_info=True)
    finally:
        conn.close()


def _send_row(row: dict) -> None:
    conn = _connect()
    if conn is None:
        return
    try:
        from aird.pwa.push import send_web_push

        current = conn.execute(
            "SELECT pushed_at FROM notify_outbox WHERE id=?",
            (row["id"],),
        ).fetchone()
        if current is not None and current["pushed_at"]:
            return
        payload = json.loads(row["payload"])
        sent = send_web_push(
            conn,
            row["username"],
            payload,
            ttl=int(row["push_ttl"] or 30),
        )
        if sent != 0:
            conn.execute(
                "UPDATE notify_outbox SET pushed_at=? WHERE id=?",
                (time.time(), row["id"]),
            )
            conn.commit()
        else:
            logger.warning(
                "web push failed for %s (%s), attempt %s",
                row["username"],
                row["kind"],
                row["attempts"],
            )
    except Exception:
        logger.warning("web push failed for %s", row.get("username"), exc_info=True)
    finally:
        conn.close()


def start_retry_loop() -> None:
    """Retry pushes a dead worker left behind. Safe on every worker."""
    import tornado.ioloop

    def _tick() -> None:
        try:
            _async(flush_due)
        except Exception:
            logger.warning("notify retry failed", exc_info=True)
        tornado.ioloop.IOLoop.current().call_later(2.0, _tick)

    tornado.ioloop.IOLoop.current().call_later(2.0, _tick)
