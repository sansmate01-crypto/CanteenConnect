"""
SmartCanteen — Login, sessions and role-based access.

Roles: student, staff, admin.
  - Anyone can register, but only as a STUDENT.
  - Staff/admin accounts are created with:  python create_user.py
  - One before_request hook enforces access for every route, including the
    ones defined in app.py, so those files don't need to change.

Rules enforced:
  /login, /register, /logout   -> public
  /staff  and  /staff/...      -> staff, admin
  /admin/...                   -> admin
  /order/place                 -> student (and student_id must be YOUR id)
  everything else              -> any logged-in user
"""

import os
import re

from flask import request, session, redirect, render_template, jsonify
from werkzeug.security import generate_password_hash, check_password_hash

from models import User

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
PUBLIC_PATHS = {"/login", "/register", "/logout"}
PAGE_PATHS = {"/", "/staff","/admin"}


def home_for(role):
    if role == "student":
        return "/"
    elif role == "admin":
        return "/admin"
    else:
        return "/staff"


def register_auth(app, get_db):
    # Set a real SECRET_KEY environment variable before any real deployment.
    app.secret_key = os.environ.get("SECRET_KEY", "dev-only-change-me")
    app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax")

    @app.before_request
    def enforce_access():
        path = request.path
        if path in PUBLIC_PATHS or path.startswith("/static/"):
            return None

        is_page = path in PAGE_PATHS
        role = session.get("role")

        if not role:
            if is_page:
                return redirect("/login")
            return jsonify({"error": "Please log in."}), 401

        if path.startswith("/admin"):
            allowed = {"admin"}
        elif path.startswith("/staff"):
            allowed = {"staff", "admin"}
        elif path == "/order/place":
            allowed = {"student"}
        elif path == "/":
            allowed = {"student"}
        else:
            allowed = None  # any logged-in user

        if allowed and role not in allowed:
            if is_page:
                return redirect(home_for(role))
            return jsonify({"error": "You are not allowed to do that."}), 403

        # A student may only place orders as themselves.
        if path == "/order/place" and request.method == "POST":
            data = request.get_json(silent=True, force=True) or {}
            try:
                sent_id = int(data.get("student_id"))
            except (TypeError, ValueError):
                sent_id = None
            if sent_id != session.get("user_id"):
                return jsonify({"error": "You are not allowed to do that."}), 403

        return None

    @app.route("/login", methods=["GET", "POST"])
    def login():
        if request.method == "POST":
            email = request.form.get("email", "").strip().lower()
            password = request.form.get("password", "")
            db = get_db()
            try:
                user = db.query(User).filter_by(email=email).first()
                if user and check_password_hash(user.password_hash, password):
                    session.clear()
                    session["user_id"] = user.user_id
                    session["role"] = user.role
                    session["name"] = user.name
                    return redirect(home_for(user.role))
            finally:
                db.close()
            return render_template("auth.html", mode="login", email=email,
                                   error="Invalid email or password."), 401

        if session.get("role"):
            return redirect(home_for(session["role"]))
        return render_template("auth.html", mode="login")

    @app.route("/register", methods=["GET", "POST"])
    def register():
        if request.method == "POST":
            name = request.form.get("name", "").strip()
            email = request.form.get("email", "").strip().lower()
            password = request.form.get("password", "")

            error = None
            if not name:
                error = "Please enter your name."
            elif not EMAIL_RE.match(email):
                error = "Please enter a valid email."
            elif len(password) < 6:
                error = "Password must be at least 6 characters."

            if not error:
                db = get_db()
                try:
                    if db.query(User).filter_by(email=email).first():
                        error = "An account with this email already exists."
                    else:
                        user = User(
                            name=name,
                            email=email,
                            password_hash=generate_password_hash(password, method="pbkdf2:sha256"),
                            role="student",  # public sign-up is always a student
                        )
                        db.add(user)
                        db.commit()
                        session.clear()
                        session["user_id"] = user.user_id
                        session["role"] = "student"
                        session["name"] = user.name
                        return redirect("/")
                except Exception:
                    db.rollback()
                    error = "Could not create the account. Please try again."
                finally:
                    db.close()

            return render_template("auth.html", mode="register", name=name,
                                   email=email, error=error), 400

        if session.get("role"):
            return redirect(home_for(session["role"]))
        return render_template("auth.html", mode="register")

    @app.route("/logout")
    def logout():
        session.clear()
        return redirect("/login")