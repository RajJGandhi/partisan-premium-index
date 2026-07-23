from __future__ import annotations

import argparse
import getpass

import bcrypt
from sqlalchemy import select

from app.db.database import get_session, init_db
from app.db.models import AdminUser

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--username", default="admin")
    args = parser.parse_args()
    password = getpass.getpass("Password (12+ characters): ")
    if len(password) < 12:
        raise SystemExit("Password is too short")
    init_db()
    with get_session() as session:
        user = session.scalar(select(AdminUser).where(AdminUser.username == args.username))
        if not user:
            user = AdminUser(username=args.username, password_hash="")
            session.add(user)
        user.password_hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt(rounds=12)).decode()
        user.active = True
    print(f"Admin user {args.username!r} saved.")
