"""
app.py - Flask web application for the Classroom Attendance Tracker.

One website, two portals:
    /                    landing page (choose Teacher or Student)
    /teacher/login       teacher portal  -> dashboard, students, attendance, reports
    /student/login       student portal  -> own dashboard, attendance history, profile

Run locally   : python app.py                      (http://127.0.0.1:5000)
Run in Podman : gunicorn is started by the Containerfile CMD on 0.0.0.0:5000
"""

import csv
import io
import os
import re
import secrets
import sqlite3
import threading
import time
from datetime import date, datetime, timedelta
from functools import wraps

from flask import (Flask, Response, abort, flash, g, jsonify, redirect,
                   render_template, request, session, url_for)

import database as db

ROLL_NO_PATTERN = re.compile(r"^[A-Za-z0-9\-/]{2,20}$")
USERNAME_PATTERN = re.compile(r"^[A-Za-z0-9_.\-]{3,40}$")
DEFAULT_THRESHOLD = 75.0
MAX_LOGIN_ATTEMPTS = 5          # failed logins allowed ...
LOCKOUT_SECONDS = 300           # ... within this window before the login is blocked


def load_secret_key():
    """
    SECRET_KEY signs the session cookie. Use the environment variable if given, otherwise
    generate a random key once and keep it in DATA_DIR (the Podman volume), so logins
    survive container restarts and no key is hard-coded in the source.
    """
    if os.environ.get("SECRET_KEY"):
        return os.environ["SECRET_KEY"]
    data_dir = db.get_data_dir()
    os.makedirs(data_dir, exist_ok=True)
    key_path = os.path.join(data_dir, "secret_key")
    try:
        with open(key_path, encoding="utf-8") as fh:
            key = fh.read().strip()
        if key:
            return key
    except FileNotFoundError:
        pass
    key = secrets.token_hex(32)
    fd = os.open(key_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(key)
    return key


app = Flask(__name__)
app.config.update(
    SECRET_KEY=load_secret_key(),
    SESSION_COOKIE_HTTPONLY=True,       # JavaScript cannot read the session cookie
    SESSION_COOKIE_SAMESITE="Lax",      # cookie is not sent on cross-site POSTs
    SESSION_COOKIE_SECURE=os.environ.get("SESSION_COOKIE_SECURE", "0") == "1",  # set 1 behind HTTPS
    PERMANENT_SESSION_LIFETIME=timedelta(hours=2),
    CSRF_ENABLED=True,
)


# --------------------------------------------------------------------------- #
# Database connection per request                                             #
# --------------------------------------------------------------------------- #
def get_conn():
    if "conn" not in g:
        g.conn = db.connect()
    return g.conn


@app.teardown_appcontext
def close_conn(exc):
    conn = g.pop("conn", None)
    if conn is not None:
        conn.close()


# --------------------------------------------------------------------------- #
# Authentication: who is logged in?                                           #
# --------------------------------------------------------------------------- #
@app.before_request
def load_current_user():
    """Load the logged-in teacher or student into g.user (or clear a stale session)."""
    g.role, g.user = None, None
    if request.endpoint in ("static", "health"):
        return
    role = session.get("role")
    if role == "teacher":
        teacher = db.get_teacher(get_conn(), session.get("teacher_id"))
        if teacher:
            g.role = role
            g.user = {"id": teacher["id"], "name": teacher["full_name"],
                      "username": teacher["username"], "last_login": teacher["last_login"]}
    elif role == "student":
        profile = db.student_profile(get_conn(), session.get("roll_no"))
        if profile and profile["has_account"]:
            g.role, g.user = role, profile
    if role and g.user is None:
        session.clear()  # account was deleted or the session is invalid


def csrf_token():
    """Per-session random token that every POST form must send back."""
    if "_csrf" not in session:
        session["_csrf"] = secrets.token_urlsafe(32)
    return session["_csrf"]


app.jinja_env.globals["csrf_token"] = csrf_token


@app.before_request
def csrf_protect():
    if request.method != "POST" or not app.config.get("CSRF_ENABLED", True):
        return
    expected = session.get("_csrf")
    sent = request.form.get("csrf_token", "")
    if not expected or not secrets.compare_digest(expected, sent):
        abort(400)


@app.after_request
def security_headers(response):
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "same-origin")
    if g.get("role"):
        # Pages with personal data must not be shown again from the browser cache after logout
        response.headers["Cache-Control"] = "no-store"
    return response


def role_required(role):
    """Decorator: allow only a logged-in user of `role`; others are sent to login or get 403."""
    def decorator(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            if g.get("role") is None:
                flash("Please log in to continue.", "warning")
                target = request.full_path.rstrip("?") if request.method == "GET" else None
                return redirect(url_for(f"{role}_login", next=target))
            if g.role != role:
                abort(403)
            return view(*args, **kwargs)
        return wrapped
    return decorator


teacher_required = role_required("teacher")
student_required = role_required("student")


def start_session(role, **values):
    """Start a fresh session for one role (logging in always ends any previous session)."""
    session.clear()
    session.permanent = True
    session["role"] = role
    session.update(values)


def safe_next(target, fallback):
    """Only allow redirects to local paths (prevents open-redirect attacks)."""
    if target and target.startswith("/") and not target.startswith("//") and "\\" not in target:
        return target
    return fallback


# Simple in-memory brute-force protection (gunicorn runs a single worker process)
_failed_logins = {}
_failed_logins_lock = threading.Lock()


def _login_key(role, identifier):
    return f"{role}|{request.remote_addr}|{identifier.lower()}"


def login_blocked(key):
    now = time.time()
    with _failed_logins_lock:
        recent = [t for t in _failed_logins.get(key, []) if now - t < LOCKOUT_SECONDS]
        _failed_logins[key] = recent
        return len(recent) >= MAX_LOGIN_ATTEMPTS


def record_failed_login(key):
    with _failed_logins_lock:
        _failed_logins.setdefault(key, []).append(time.time())


def clear_failed_logins(key):
    with _failed_logins_lock:
        _failed_logins.pop(key, None)


@app.context_processor
def inject_globals():
    return {
        "today": date.today().isoformat(),
        "current_year": date.today().year,
        "current_role": g.get("role"),
        "current_user": g.get("user"),
    }


# --------------------------------------------------------------------------- #
# Input helpers                                                               #
# --------------------------------------------------------------------------- #
def clean(value, max_len=100):
    """Strip and collapse whitespace from form/query input."""
    return " ".join((value or "").split())[:max_len]


def parse_date(value):
    """Return a date from YYYY-MM-DD, or None if invalid."""
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return None


def parse_threshold(value):
    """User-defined percentage threshold, clamped to 0..100 (defaults to 75)."""
    try:
        threshold = float(value)
    except (TypeError, ValueError):
        return DEFAULT_THRESHOLD
    return max(0.0, min(100.0, threshold))


def password_problems(password, confirm=None):
    """Password policy: 8-128 characters with at least one letter and one digit."""
    problems = []
    if len(password) < 8 or len(password) > 128:
        problems.append("Password must be 8-128 characters long.")
    if not re.search(r"[A-Za-z]", password) or not re.search(r"\d", password):
        problems.append("Password must contain at least one letter and one digit.")
    if confirm is not None and password != confirm:
        problems.append("The two passwords do not match.")
    return problems


# --------------------------------------------------------------------------- #
# Landing page, login and logout                                              #
# --------------------------------------------------------------------------- #
@app.route("/")
def landing():
    return render_template("landing.html")


@app.route("/teacher/login", methods=["GET", "POST"])
def teacher_login():
    target = safe_next(request.values.get("next"), url_for("dashboard"))
    if g.role == "teacher":
        return redirect(target)

    username = ""
    if request.method == "POST":
        username = clean(request.form.get("username"), 40)
        password = request.form.get("password", "")
        key = _login_key("teacher", username)
        if not USERNAME_PATTERN.match(username) or not password or len(password) > 128:
            flash("Please enter a valid username and password.", "danger")
        elif login_blocked(key):
            flash(f"Too many failed attempts. Try again in {LOCKOUT_SECONDS // 60} minutes.", "danger")
        else:
            conn = get_conn()
            teacher = db.get_teacher_by_username(conn, username)
            if db.verify_password(teacher["password_hash"] if teacher else None, password):
                clear_failed_logins(key)
                start_session("teacher", teacher_id=teacher["id"])
                db.touch_teacher_login(conn, teacher["id"])
                flash(f"Welcome back, {teacher['full_name']}!", "success")
                return redirect(target)
            record_failed_login(key)
            flash("Invalid username or password.", "danger")

    return render_template("login.html", role="teacher", identifier=username,
                           next_url=request.values.get("next", ""))


@app.route("/student/login", methods=["GET", "POST"])
def student_login():
    target = safe_next(request.values.get("next"), url_for("student_dashboard"))
    if g.role == "student":
        return redirect(target)

    roll_no = ""
    if request.method == "POST":
        roll_no = clean(request.form.get("roll_no"), 20).upper()
        password = request.form.get("password", "")
        key = _login_key("student", roll_no)
        if not ROLL_NO_PATTERN.match(roll_no) or not password or len(password) > 128:
            flash("Please enter a valid roll number and password.", "danger")
        elif login_blocked(key):
            flash(f"Too many failed attempts. Try again in {LOCKOUT_SECONDS // 60} minutes.", "danger")
        else:
            conn = get_conn()
            account = db.get_student_account(conn, roll_no)
            valid = db.verify_password(account["password_hash"] if account else None, password)
            if valid and db.student_profile(conn, roll_no):
                clear_failed_logins(key)
                start_session("student", roll_no=roll_no)
                db.touch_student_login(conn, roll_no)
                return redirect(target)
            record_failed_login(key)
            flash("Invalid roll number or password.", "danger")

    return render_template("login.html", role="student", identifier=roll_no,
                           next_url=request.values.get("next", ""))


@app.route("/logout", methods=["POST"])
def logout():
    session.clear()
    flash("You have been logged out.", "info")
    return redirect(url_for("landing"))


# --------------------------------------------------------------------------- #
# TEACHER PORTAL                                                              #
# --------------------------------------------------------------------------- #
@app.route("/dashboard")
@teacher_required
def dashboard():
    conn = get_conn()
    stats = db.summary(conn)
    all_rows = db.student_stats(conn)
    low = db.below_threshold(all_rows, DEFAULT_THRESHOLD)
    chart_data = {
        "classes": db.class_breakdown(conn),
        "trend": db.daily_trend(conn, days=10),
        "present": stats["total_present"],
        "absent": stats["total_absent"],
    }
    return render_template(
        "dashboard.html",
        stats=stats,
        below_count=len(low),
        low_students=low[:8],
        recent_sessions=db.session_history(conn, limit=5),
        chart_data=chart_data,
        threshold=DEFAULT_THRESHOLD,
    )


@app.route("/teacher/profile", methods=["GET", "POST"])
@teacher_required
def teacher_profile():
    conn = get_conn()
    if request.method == "POST":
        current = request.form.get("current_password", "")
        new = request.form.get("new_password", "")
        confirm = request.form.get("confirm_password", "")
        teacher = db.get_teacher(conn, g.user["id"])
        errors = []
        if not db.verify_password(teacher["password_hash"], current):
            errors.append("Your current password is incorrect.")
        errors.extend(password_problems(new, confirm))
        if not errors and new == current:
            errors.append("The new password must be different from the current one.")
        if errors:
            for error in errors:
                flash(error, "danger")
        else:
            db.set_teacher_password(conn, teacher["id"], new)
            flash("Your password has been changed.", "success")
            return redirect(url_for("teacher_profile"))
    return render_template("teacher_profile.html", stats=db.summary(conn))


@app.route("/students")
@teacher_required
def students():
    conn = get_conn()
    q = clean(request.args.get("q"), 60)
    subject = clean(request.args.get("subject"))
    section = clean(request.args.get("section"), 10)
    rows = db.student_stats(conn, subject or None, section or None, q or None)
    return render_template(
        "students.html",
        students=rows,
        q=q,
        subject=subject,
        section=section,
        subjects=db.get_subjects(conn),
        sections=db.get_sections(conn),
        threshold=DEFAULT_THRESHOLD,
    )


@app.route("/students/add", methods=["GET", "POST"])
@teacher_required
def add_student():
    conn = get_conn()
    form = {"roll_no": "", "name": "", "subject": "", "section": ""}

    if request.method == "POST":
        form = {
            "roll_no": clean(request.form.get("roll_no"), 20).upper(),
            "name": clean(request.form.get("name"), 80),
            "subject": clean(request.form.get("subject"), 80),
            "section": clean(request.form.get("section"), 10).upper(),
        }
        password = request.form.get("password", "")
        confirm = request.form.get("confirm_password", "")
        errors = []
        if not ROLL_NO_PATTERN.match(form["roll_no"]):
            errors.append("Roll number must be 2-20 characters (letters, digits, - or /).")
        if len(form["name"]) < 2:
            errors.append("Please enter the student's full name.")
        if not form["subject"]:
            errors.append("Subject is required.")
        if not form["section"]:
            errors.append("Section is required.")

        # A new roll number needs a login password; an existing one keeps its account.
        has_account = bool(form["roll_no"]) and db.get_student_account(conn, form["roll_no"]) is not None
        new_password = None
        if not has_account:
            if not password:
                errors.append("Set an initial password so the student can log in to the Student Portal.")
            else:
                errors.extend(password_problems(password, confirm))
                new_password = password

        if not errors:
            try:
                db.add_student(conn, **form, password=new_password)
            except sqlite3.IntegrityError:
                errors.append(
                    f"Roll number {form['roll_no']} is already enrolled in {form['subject']}."
                )
            else:
                flash(f"Student {form['name']} ({form['roll_no']}) added successfully.", "success")
                if has_account:
                    flash(f"{form['roll_no']} already has a Student Portal login - "
                          "their existing password was kept.", "info")
                if request.form.get("action") == "save_add_another":
                    return redirect(url_for("add_student"))
                return redirect(url_for("students", subject=form["subject"], section=form["section"]))

        for error in errors:
            flash(error, "danger")

    return render_template(
        "add_student.html",
        form=form,
        subjects=db.get_subjects(conn),
        sections=db.get_sections(conn),
    )


@app.route("/students/<int:student_id>/delete", methods=["POST"])
@teacher_required
def delete_student(student_id):
    conn = get_conn()
    student = db.get_student(conn, student_id)
    if student is None:
        abort(404)
    db.delete_student(conn, student_id)
    flash(f"Deleted {student['name']} ({student['roll_no']}) and their attendance records.", "info")
    referrer = request.referrer or ""
    if referrer.startswith(request.host_url):
        return redirect(referrer)
    return redirect(url_for("students"))


@app.route("/students/account/<roll_no>", methods=["GET", "POST"])
@teacher_required
def student_account(roll_no):
    """Teacher view of one student's login + enrolments, with password reset."""
    roll_no = roll_no.upper()
    if not ROLL_NO_PATTERN.match(roll_no):
        abort(404)
    conn = get_conn()
    profile = db.student_profile(conn, roll_no)
    if profile is None:
        abort(404)
    if request.method == "POST":
        password = request.form.get("password", "")
        errors = password_problems(password, request.form.get("confirm_password", ""))
        if errors:
            for error in errors:
                flash(error, "danger")
        else:
            db.set_student_password(conn, roll_no, password)
            flash(f"Student Portal password for {roll_no} has been set. "
                  "Share it with the student privately.", "success")
            return redirect(url_for("student_account", roll_no=roll_no))
    return render_template("student_account.html", profile=profile,
                           subjects=db.student_subject_summary(conn, roll_no),
                           threshold=DEFAULT_THRESHOLD)


@app.route("/attendance")
@teacher_required
def attendance():
    conn = get_conn()
    subject = clean(request.args.get("subject"))
    section = clean(request.args.get("section"), 10)
    date_str = clean(request.args.get("date"), 10) or date.today().isoformat()

    class_students, existing, session_row = [], {}, None
    loaded = bool(subject and section and request.args.get("date"))

    if loaded:
        session_date = parse_date(date_str)
        if session_date is None:
            flash("Please choose a valid date.", "danger")
            loaded = False
        elif session_date > date.today():
            flash("Attendance cannot be marked for a future date.", "warning")
            loaded = False
        else:
            class_students = db.students_for_class(conn, subject, section)
            if not class_students:
                flash(f"No students found for {subject} - Section {section}.", "warning")
            session_row = db.find_session(conn, subject, section, date_str)
            if session_row:
                existing = db.attendance_map(conn, session_row["id"])

    return render_template(
        "attendance.html",
        subject=subject,
        section=section,
        date_str=date_str,
        loaded=loaded,
        students=class_students,
        existing=existing,
        session=session_row,
        subjects=db.get_subjects(conn),
        class_map=db.get_class_map(conn),
    )


@app.route("/attendance/save", methods=["POST"])
@teacher_required
def save_attendance():
    conn = get_conn()
    subject = clean(request.form.get("subject"))
    section = clean(request.form.get("section"), 10)
    date_str = clean(request.form.get("date"), 10)
    back = url_for("attendance", subject=subject, section=section, date=date_str)

    session_date = parse_date(date_str)
    if not subject or not section or session_date is None:
        flash("Subject, section and a valid date are required.", "danger")
        return redirect(url_for("attendance"))
    if session_date > date.today():
        flash("Attendance cannot be marked for a future date.", "warning")
        return redirect(url_for("attendance", subject=subject, section=section))

    class_students = db.students_for_class(conn, subject, section)
    if not class_students:
        flash("There are no students in this class to mark.", "warning")
        return redirect(back)

    statuses, missing = {}, []
    for student in class_students:
        status = request.form.get(f"status_{student['id']}")
        if status in db.VALID_STATUSES:
            statuses[student["id"]] = status
        else:
            missing.append(student["roll_no"])
    if missing:
        flash("Please mark every student. Missing: " + ", ".join(missing), "danger")
        return redirect(back)

    result = db.save_attendance(conn, subject, section, date_str, statuses)
    present = sum(1 for s in statuses.values() if s == db.STATUS_PRESENT)
    if result["session_existed"]:
        flash(
            f"Session on {date_str} already existed - no duplicates created. "
            f"{result['updated']} record(s) updated, {result['inserted']} added, "
            f"{result['unchanged']} unchanged.",
            "info",
        )
    else:
        flash(
            f"Attendance saved for {subject} - Section {section} on {date_str}: "
            f"{present} present, {len(statuses) - present} absent.",
            "success",
        )
    return redirect(back)


@app.route("/reports")
@teacher_required
def reports():
    conn = get_conn()
    subject = clean(request.args.get("subject"))
    section = clean(request.args.get("section"), 10)
    threshold = parse_threshold(request.args.get("threshold", DEFAULT_THRESHOLD))

    rows = db.student_stats(conn, subject or None, section or None)
    low = db.below_threshold(rows, threshold)
    history = db.session_history(conn, subject or None, section or None)
    stats = db.summary(conn, subject or None, section or None)

    chart_data = {
        "labels": [f"{r['roll_no']}" for r in rows if r["percentage"] is not None],
        "names": [r["name"] for r in rows if r["percentage"] is not None],
        "values": [r["percentage"] for r in rows if r["percentage"] is not None],
        "threshold": threshold,
    }
    return render_template(
        "reports.html",
        rows=rows,
        low=low,
        history=history,
        stats=stats,
        subject=subject,
        section=section,
        threshold=threshold,
        subjects=db.get_subjects(conn),
        sections=db.get_sections(conn),
        chart_data=chart_data,
    )


@app.route("/reports/export.csv")
@teacher_required
def export_report():
    conn = get_conn()
    subject = clean(request.args.get("subject"))
    section = clean(request.args.get("section"), 10)
    threshold = parse_threshold(request.args.get("threshold", DEFAULT_THRESHOLD))
    rows = db.student_stats(conn, subject or None, section or None)

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["Roll No", "Name", "Subject", "Section", "Sessions",
                     "Present", "Absent", "Attendance %", f"Below {threshold:g}%"])
    for r in rows:
        pct = r["percentage"]
        writer.writerow([
            r["roll_no"], r["name"], r["subject"], r["section"], r["total"],
            r["present"], r["absent"], "" if pct is None else pct,
            "" if pct is None else ("YES" if pct < threshold else "NO"),
        ])
    filename = f"attendance_report_{date.today().isoformat()}.csv"
    return Response(
        buffer.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


@app.route("/reports/session/<int:session_id>")
@teacher_required
def session_view(session_id):
    conn = get_conn()
    session_row = db.get_session(conn, session_id)
    if session_row is None:
        abort(404)
    rows = db.session_detail(conn, session_id)
    present = sum(1 for r in rows if r["status"] == db.STATUS_PRESENT)
    absent = sum(1 for r in rows if r["status"] == db.STATUS_ABSENT)
    return render_template("session_detail.html", session=session_row, rows=rows,
                           present=present, absent=absent)


@app.route("/reports/session/<int:session_id>/delete", methods=["POST"])
@teacher_required
def session_delete(session_id):
    conn = get_conn()
    session_row = db.get_session(conn, session_id)
    if session_row is None:
        abort(404)
    db.delete_session(conn, session_id)
    flash(
        f"Deleted session {session_row['subject']} - Section {session_row['section']} "
        f"on {session_row['session_date']}.",
        "info",
    )
    return redirect(url_for("reports", subject=session_row["subject"], section=session_row["section"]))


# --------------------------------------------------------------------------- #
# STUDENT PORTAL - read-only, always limited to the logged-in roll number      #
# --------------------------------------------------------------------------- #
def _student_summary(conn, roll_no):
    """Subject rows + overall totals + 75 % guidance for the logged-in student."""
    subjects = db.student_subject_summary(conn, roll_no)
    for s in subjects:
        s["needed"] = db.classes_needed(s["present"], s["total"], DEFAULT_THRESHOLD)
        s["can_miss"] = db.classes_can_miss(s["present"], s["total"], DEFAULT_THRESHOLD)
        s["low"] = s["percentage"] is not None and s["percentage"] < DEFAULT_THRESHOLD
    total = sum(s["total"] for s in subjects)
    present = sum(s["present"] for s in subjects)
    overall = {
        "total": total,
        "present": present,
        "absent": total - present,
        "percentage": round(present * 100.0 / total, 1) if total else None,
        "needed": db.classes_needed(present, total, DEFAULT_THRESHOLD),
        "can_miss": db.classes_can_miss(present, total, DEFAULT_THRESHOLD),
    }
    overall["low"] = overall["percentage"] is not None and overall["percentage"] < DEFAULT_THRESHOLD
    return subjects, overall


@app.route("/student/dashboard")
@student_required
def student_dashboard():
    conn = get_conn()
    roll_no = g.user["roll_no"]  # never taken from the URL or form
    subjects, overall = _student_summary(conn, roll_no)
    chart_data = {
        "labels": [s["subject"] for s in subjects],
        "values": [s["percentage"] or 0 for s in subjects],
        "present": overall["present"],
        "absent": overall["absent"],
        "threshold": DEFAULT_THRESHOLD,
    }
    return render_template(
        "student/dashboard.html",
        subjects=subjects,
        overall=overall,
        low_subjects=[s for s in subjects if s["low"]],
        recent=db.student_history(conn, roll_no, limit=8),
        chart_data=chart_data,
        threshold=DEFAULT_THRESHOLD,
    )


@app.route("/student/attendance")
@student_required
def student_attendance():
    conn = get_conn()
    roll_no = g.user["roll_no"]
    my_subjects = [e["subject"] for e in g.user["subjects"]]
    subject = clean(request.args.get("subject"))
    if subject not in my_subjects:
        subject = ""  # ignore anything that is not one of the student's own subjects
    history = db.student_history(conn, roll_no, subject or None)
    present = sum(1 for h in history if h["status"] == db.STATUS_PRESENT)
    return render_template(
        "student/attendance.html",
        history=history,
        subject=subject,
        my_subjects=my_subjects,
        present=present,
        absent=len(history) - present,
    )


@app.route("/student/profile", methods=["GET", "POST"])
@student_required
def student_profile():
    conn = get_conn()
    roll_no = g.user["roll_no"]
    if request.method == "POST":
        current = request.form.get("current_password", "")
        new = request.form.get("new_password", "")
        confirm = request.form.get("confirm_password", "")
        account = db.get_student_account(conn, roll_no)
        errors = []
        if not db.verify_password(account["password_hash"], current):
            errors.append("Your current password is incorrect.")
        errors.extend(password_problems(new, confirm))
        if not errors and new == current:
            errors.append("The new password must be different from the current one.")
        if errors:
            for error in errors:
                flash(error, "danger")
        else:
            db.set_student_password(conn, roll_no, new)
            flash("Your password has been changed.", "success")
            return redirect(url_for("student_profile"))
    subjects, overall = _student_summary(conn, roll_no)
    return render_template("student/profile.html", subjects=subjects, overall=overall,
                           threshold=DEFAULT_THRESHOLD)


# --------------------------------------------------------------------------- #
# Health check (used by `podman run --health-cmd ...`)                        #
# --------------------------------------------------------------------------- #
@app.route("/health")
def health():
    try:
        get_conn().execute("SELECT 1").fetchone()
        return jsonify(status="ok", database=db.get_db_path())
    except sqlite3.Error as exc:
        return jsonify(status="error", detail=str(exc)), 500


# --------------------------------------------------------------------------- #
# Error pages                                                                 #
# --------------------------------------------------------------------------- #
def render_error(code, title, message):
    layout = {"teacher": "base.html", "student": "student/base.html"}.get(g.get("role"), "auth_base.html")
    return render_template("error.html", layout=layout, code=code, title=title, message=message), code


@app.errorhandler(400)
def bad_request(error):
    return render_error(400, "Request could not be processed",
                        "The form has expired or is invalid. Go back, reload the page and try again.")


@app.errorhandler(403)
def forbidden(error):
    if g.get("role") == "student":
        message = "This page is only available in the Teacher Portal. Students can view their own attendance only."
    else:
        message = "You do not have permission to open this page."
    return render_error(403, "Access denied", message)


@app.errorhandler(404)
def not_found(error):
    return render_error(404, "Page not found", "The page you are looking for does not exist.")


@app.errorhandler(500)
def server_error(error):
    return render_error(500, "Server error", "Something went wrong on the server. Check the logs.")


# Create tables, run migrations and add the default teacher when the module is imported,
# so it works both with `python app.py` and with gunicorn inside the container.
db.init_db()

if __name__ == "__main__":
    app.run(
        host=os.environ.get("HOST", "127.0.0.1"),
        port=int(os.environ.get("PORT", "5000")),
        debug=os.environ.get("FLASK_DEBUG", "0") == "1",  # opt in with FLASK_DEBUG=1
    )
