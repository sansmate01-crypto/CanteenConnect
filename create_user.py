"""
Create a user from the command line (this is how you make staff/admin accounts).

Usage:
    python create_user.py "Ravi Kumar" ravi@college.edu staff
    python create_user.py "Canteen Admin" admin@college.edu admin

The password is asked for interactively so it doesn't end up in your shell history.
Make sure MySQL is running first.
"""

import sys
import getpass

from werkzeug.security import generate_password_hash

from app import get_db, engine
from models import Base, User

ROLES = ("student", "staff", "admin")


def main():
    if len(sys.argv) != 4 or sys.argv[3] not in ROLES:
        print('Usage: python create_user.py "Full Name" email role')
        print("role is one of:", ", ".join(ROLES))
        sys.exit(1)

    name, email, role = sys.argv[1], sys.argv[2].strip().lower(), sys.argv[3]

    def ask_password(prompt):
        # getpass (hidden input) fails in some Windows terminals (notably
        # some VS Code integrated terminal setups), raising KeyboardInterrupt
        # instead of actually working. Fall back to a visible prompt so the
        # script still runs -- just be aware the password will be visible
        # on screen in that case.
        try:
            return getpass.getpass(prompt)
        except (KeyboardInterrupt, Exception):
            print("(Hidden input isn't working in this terminal -- "
                  "your typing will be visible below.)")
            return input(prompt)

    password = ask_password("Password (min 6 chars): ")
    if len(password) < 6:
        print("Password too short.")
        sys.exit(1)
    if ask_password("Repeat password: ") != password:
        print("Passwords do not match.")
        sys.exit(1)

    Base.metadata.create_all(engine)
    db = get_db()
    try:
        if db.query(User).filter_by(email=email).first():
            print("A user with that email already exists.")
            sys.exit(1)
        db.add(User(
            name=name,
            email=email,
            password_hash=generate_password_hash(password, method="pbkdf2:sha256"),
            role=role,
        ))
        db.commit()
        print(f"Created {role} account for {email}")
    finally:
        db.close()


if __name__ == "__main__":
    main()