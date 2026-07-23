from __future__ import annotations

import getpass

import bcrypt

if __name__ == "__main__":
    password = getpass.getpass("Admin password: ")
    confirm = getpass.getpass("Confirm password: ")
    if password != confirm:
        raise SystemExit("Passwords do not match")
    if len(password) < 12:
        raise SystemExit("Use at least 12 characters")
    print(bcrypt.hashpw(password.encode(), bcrypt.gensalt(rounds=12)).decode())
