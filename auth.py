# -*- coding: utf-8 -*-
"""
auth.py — התחברות מנהלים לדשבורד
-----------------------------------
רק מנהלים מורשים יכולים להיכנס ל-/admin ולנתוני המנהל
(/api/run, /api/history, /api/export.csv).

איפה נשמרים שמות המשתמש והסיסמאות:
  לא בקוד! (הקוד עולה ל-GitHub, וכל מי שרואה אותו היה רואה גם את הסיסמאות.)
  - בענן (Render): משתנה סביבה בשם ADMIN_USERS.
  - במחשב: קובץ admin_users.txt ליד app.py (לא להעלות אותו ל-GitHub).

הפורמט: שם:סיסמה, וכמה מנהלים מופרדים ב-; או בשורות נפרדות. לדוגמה:
  נועה כהן:123456789;שירה לוי:987654321

בהתחברות לא משנה אם יש רווחים כפולים או אותיות גדולות/קטנות באנגלית.
"""

import hmac
import os
import secrets
import time
from datetime import timedelta
from functools import wraps
from urllib.parse import urlparse

from flask import jsonify, redirect, render_template, request, session, url_for

_HERE = os.path.dirname(os.path.abspath(__file__))
_LOCAL_FILE = os.path.join(_HERE, "admin_users.txt")

# הגנה מניחוש סיסמאות: אחרי 5 ניסיונות כושלים מאותה כתובת — חסימה ל-15 דקות
MAX_FAILURES = 5
LOCK_SECONDS = 15 * 60
_failures = {}  # ip → [זמני ניסיונות כושלים]


def _norm(name):
    """'  נועה   כהן ' → 'נועה כהן' (ובאנגלית גם בלי הבדל בין גדולות לקטנות)."""
    return " ".join((name or "").split()).casefold()


def load_admins():
    """{שם מנורמל: (שם לתצוגה, סיסמה)}"""
    raw = os.environ.get("ADMIN_USERS", "").strip()
    if not raw and os.path.exists(_LOCAL_FILE):
        with open(_LOCAL_FILE, encoding="utf-8-sig") as f:
            raw = f.read()
    admins = {}
    for part in raw.replace("\r", "\n").replace("\n", ";").split(";"):
        if ":" not in part:
            continue
        name, password = part.rsplit(":", 1)
        name, password = " ".join(name.split()), password.strip()
        if name and password:
            admins[_norm(name)] = (name, password)
    return admins


def check_login(username, password):
    """מחזיר את שם המנהל אם הפרטים נכונים, אחרת None."""
    entry = load_admins().get(_norm(username))
    # compare_digest משווה בזמן קבוע — כך אי אפשר לנחש סיסמה לפי זמן התגובה
    if entry and hmac.compare_digest(entry[1].encode(), (password or "").strip().encode()):
        return entry[0]
    return None


def _client_ip():
    # ב-Render הבקשה עוברת דרך שרת ביניים, והכתובת האמיתית נמצאת בכותרת הזו
    fwd = request.headers.get("X-Forwarded-For", "")
    return fwd.split(",")[0].strip() if fwd else (request.remote_addr or "?")


def _locked(ip):
    now = time.time()
    recent = [t for t in _failures.get(ip, []) if now - t < LOCK_SECONDS]
    _failures[ip] = recent
    return len(recent) >= MAX_FAILURES


def _safe_next(target):
    """מאפשר לחזור אחרי ההתחברות רק לדף באתר עצמו (לא לאתר חיצוני)."""
    if not target:
        return None
    p = urlparse(target)
    if p.scheme or p.netloc or not target.startswith("/") or target.startswith("//"):
        return None
    return target


def is_admin():
    name = session.get("admin")
    return bool(name) and _norm(name) in load_admins()


def admin_required(view):
    """
    decorator: מגן על דף או API.
    דף → מעביר למסך ההתחברות. API → מחזיר שגיאה 401.
    """
    @wraps(view)
    def wrapper(*args, **kwargs):
        if is_admin():
            return view(*args, **kwargs)
        if request.path.startswith("/api/"):
            return jsonify({"error": "נדרשת התחברות מנהל"}), 401
        return redirect(url_for("admin_login", next=request.full_path.rstrip("?")))
    return wrapper


def init_app(app):
    # מפתח שחותם על עוגיית ההתחברות. ב-Render מגדירים SECRET_KEY (כפתור Generate).
    # בלי מפתח קבוע — נוצר אחד אקראי, וכל הפעלה מחדש של השרת מנתקת את המנהלים.
    app.secret_key = os.environ.get("SECRET_KEY") or secrets.token_hex(32)
    app.config.update(
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=bool(os.environ.get("RENDER")),  # https בענן
        PERMANENT_SESSION_LIFETIME=timedelta(hours=8),
    )

    @app.route("/admin/login", methods=["GET", "POST"])
    def admin_login():
        next_url = _safe_next(request.values.get("next")) or url_for("admin_page")
        if is_admin():
            return redirect(next_url)

        error = None
        configured = bool(load_admins())
        username = ""
        if request.method == "POST":
            ip = _client_ip()
            username = request.form.get("username", "")
            if not configured:
                error = "עדיין לא הוגדרו מנהלים (ADMIN_USERS)."
            elif _locked(ip):
                error = "יותר מדי ניסיונות. נסו שוב בעוד 15 דקות."
            else:
                name = check_login(username, request.form.get("password", ""))
                if name:
                    _failures.pop(ip, None)
                    session.clear()
                    session.permanent = True
                    session["admin"] = name
                    return redirect(next_url)
                _failures.setdefault(ip, []).append(time.time())
                error = "שם משתמש או סיסמה שגויים."
        return render_template("login.html", error=error, next=next_url,
                               username=username, configured=configured), (401 if error else 200)

    @app.route("/admin/logout", methods=["GET", "POST"])
    def admin_logout():
        session.clear()
        return redirect(url_for("admin_login"))
