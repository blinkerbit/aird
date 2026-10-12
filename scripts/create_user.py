#!/usr/bin/env python3
import sqlite3
import sys

from aird.db import init_db
from aird.db.users import create_user, get_user_by_username

DBS = [
    "/opt/aird/data/aird/aird.sqlite3",
    "/home/ubuntu/.local/share/aird/aird.sqlite3",
]
USERNAME = "random"
PASSWORD = "test"

for db_path in DBS:
    try:
        conn = sqlite3.connect(db_path)
    except OSError as exc:
        print(f"skip {db_path}: {exc}")
        continue
    init_db(conn)
    existing = get_user_by_username(conn, USERNAME)
    if existing:
        from aird.db.users import update_user

        update_user(conn, existing["id"], password=PASSWORD, active=True, must_change_password=False)
        print(f"updated: {USERNAME} password in {db_path}")
        continue
    user = create_user(conn, USERNAME, PASSWORD, role="user")
    print(f"created: {user['username']} (id={user['id']}) in {db_path}")

sys.exit(0)
