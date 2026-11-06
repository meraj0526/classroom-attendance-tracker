"""
database.py - SQLite data layer for the Classroom Attendance Tracker.

All SQL lives in this module so that app.py only deals with HTTP concerns.

Where is the database stored?
    * Inside the container : /app/data/attendance.db  (DATA_DIR=/app/data, set in the Containerfile)
    * Local development    : <project>/data/attendance.db
The /app/data directory is mounted from a Podman named volume, so the data
survives container deletion and re-creation.

Schema versions (stored in SQLite's PRAGMA user_version):
    0/1 : students, sessions, attendance                       (original release)
    2   : + teachers, student_accounts (hashed passwords)      (teacher/student portals)
Upgrading only ever CREATEs new tables - existing rows are never touched or deleted.

First-time accounts:
    The first teacher and the initial student password come from environment variables
    (TEACHER_USERNAME, TEACHER_PASSWORD, TEACHER_NAME, DEFAULT_STUDENT_PASSWORD - see
    .env.example). There are no built-in passwords: if a password is missing, empty or still
    a REPLACE_WITH... placeholder, a random one is generated and printed ONCE to the log
    (the terminal, or `podman logs <container>`).
"""

import math
import os
import random
import re
import secrets
import sqlite3
from datetime import date, timedelta
from fractions import Fraction

from werkzeug.security import check_password_hash, generate_password_hash

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

STATUS_PRESENT = "Present"
STATUS_ABSENT = "Absent"
VALID_STATUSES = (STATUS_PRESENT, STATUS_ABSENT)

SCHEMA_VERSION = 2

# First-time account settings (passwords are never hard-coded - see the module docstring)
DEFAULT_TEACHER_USERNAME = "teacher"
DEFAULT_TEACHER_NAME = "Class Teacher"
PLACEHOLDER_PREFIX = "REPLACE_WITH"
USERNAME_PATTERN = re.compile(r"^[A-Za-z0-9_.\-]{3,40}$")  # same rule as the login form
_PASSWORD_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789"  # no 0/O, 1/l/I

SCHEMA = """
CREATE TABLE IF NOT EXISTS students (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    roll_no     TEXT    NOT NULL,
    name        TEXT    NOT NULL,
    subject     TEXT    NOT NULL,
    section     TEXT    NOT NULL,
    created_at  TEXT    NOT NULL DEFAULT (datetime('now', 'localtime')),
    -- the same roll number may be enrolled in several subjects, but only once per subject
    UNIQUE (roll_no, subject)
);

CREATE TABLE IF NOT EXISTS sessions (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    subject       TEXT NOT NULL,
    section       TEXT NOT NULL,
    session_date  TEXT NOT NULL,              -- ISO format YYYY-MM-DD
    created_at    TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    -- one class session per subject + section + date
    UNIQUE (subject, section, session_date)
);

CREATE TABLE IF NOT EXISTS attendance (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    student_id  INTEGER NOT NULL REFERENCES students(id) ON DELETE CASCADE,
    session_id  INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    status      TEXT    NOT NULL CHECK (status IN ('Present', 'Absent')),
    marked_at   TEXT    NOT NULL DEFAULT (datetime('now', 'localtime')),
    -- prevents duplicate attendance entries for the same student and session
    UNIQUE (student_id, session_id)
);

CREATE INDEX IF NOT EXISTS idx_students_class    ON students (subject, section);
CREATE INDEX IF NOT EXISTS idx_sessions_class    ON sessions (subject, section, session_date);
CREATE INDEX IF NOT EXISTS idx_attendance_session ON attendance (session_id);
CREATE INDEX IF NOT EXISTS idx_attendance_student ON attendance (student_id);
"""

# Added in schema version 2. Only CREATE ... IF NOT EXISTS, so it is safe to run on every start.
AUTH_SCHEMA = """
CREATE TABLE IF NOT EXISTS teachers (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    username      TEXT NOT NULL UNIQUE COLLATE NOCASE,
    full_name     TEXT NOT NULL,
    password_hash TEXT NOT NULL,               -- Werkzeug scrypt hash, never the plain password
    created_at    TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    last_login    TEXT
);

-- One login per student (roll number). A roll number can have several rows in
-- `students` (one per subject enrolment); all of them belong to this account.
CREATE TABLE IF NOT EXISTS student_accounts (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    roll_no       TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,               -- Werkzeug scrypt hash, never the plain password
    created_at    TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    last_login    TEXT
);

CREATE INDEX IF NOT EXISTS idx_students_roll ON students (roll_no);

-- Keep accounts and enrolments consistent: when a student's last enrolment is
-- deleted, their login is removed as well.
CREATE TRIGGER IF NOT EXISTS trg_remove_orphan_student_account
AFTER DELETE ON students
WHEN NOT EXISTS (SELECT 1 FROM students WHERE roll_no = OLD.roll_no)
BEGIN
    DELETE FROM student_accounts WHERE roll_no = OLD.roll_no;
END;
"""

# --------------------------------------------------------------------------- #
# Fictional sample data (no real people)                                      #
# --------------------------------------------------------------------------- #
SAMPLE_NAMES = [
    "Aarav Krishnan", "Diya Raman", "Kabir Sethi", "Meera Pillai", "Rohan Iyer",
    "Sanya Kapoor", "Vihaan Rao", "Ananya Bose", "Arjun Nair", "Ishita Varma",
    "Dev Malhotra", "Kavya Reddy", "Nikhil Joshi", "Priya Subramanian", "Rahul Menon",
    "Tara D'Souza", "Aditya Kulkarni", "Neha Chandran", "Siddharth Das", "Riya Mathew",
]

SAMPLE_CLASSES = [
    # (subject, section, slice of SAMPLE_NAMES)
    ("Linux and Container Technologies", "A", slice(0, 10)),
    ("Linux and Container Technologies", "B", slice(10, 20)),
    ("Database Management Systems", "A", slice(0, 10)),
    ("Computer Networks", "B", slice(10, 20)),
]

SAMPLE_SESSIONS_PER_CLASS = 10


# --------------------------------------------------------------------------- #
# Connection helpers                                                          #
# --------------------------------------------------------------------------- #
def get_data_dir():
    """Directory that holds the SQLite file. Read at call time so tests can override it."""
    return os.environ.get("DATA_DIR", os.path.join(BASE_DIR, "data"))


def get_db_path():
    data_dir = get_data_dir()
    os.makedirs(data_dir, exist_ok=True)
    return os.path.join(data_dir, "attendance.db")


def connect():
    """Open a new connection with dict-like rows and foreign keys enforced."""
    conn = sqlite3.connect(get_db_path(), timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(seed=None):
    """
    Create tables, run migrations and create the default teacher. Safe to call on every start.

    Fictional sample data is only loaded into a brand-new, empty database, so existing
    records are never overwritten.
    """
    if seed is None:
        seed = os.environ.get("SEED_SAMPLE_DATA", "1") == "1"
    conn = connect()
    try:
        is_new = conn.execute("PRAGMA user_version").fetchone()[0] == 0
        conn.executescript(SCHEMA)
        if seed and is_new:
            student_count = conn.execute("SELECT COUNT(*) FROM students").fetchone()[0]
            if student_count == 0:
                _seed_sample_data(conn)
        _migrate(conn)
        _ensure_default_teacher(conn)
        conn.commit()
    finally:
        conn.close()


def _migrate(conn):
    """Upgrade an older database in place. Only adds tables - never deletes data."""
    conn.executescript(AUTH_SCHEMA)  # idempotent CREATE ... IF NOT EXISTS
    version = conn.execute("PRAGMA user_version").fetchone()[0]

    if version < 2:
        # Give every existing student a login so that data created before the student
        # portal existed (and the fictional sample students) is immediately usable.
        missing = conn.execute(
            """
            SELECT DISTINCT s.roll_no FROM students s
            LEFT JOIN student_accounts sa ON sa.roll_no = s.roll_no
            WHERE sa.id IS NULL
            """
        ).fetchall()
        if missing:
            student_password = _env_setting("DEFAULT_STUDENT_PASSWORD")
            if student_password is None:
                student_password = generate_password()
                _announce([
                    f"FIRST-TIME SETUP - Student Portal logins created for {len(missing)} students",
                    f"  Initial password : {student_password}",
                    "  (generated because DEFAULT_STUDENT_PASSWORD is not set)",
                    "Teachers can give each student their own password: Students -> key icon.",
                    "This message is shown only once.",
                ])
            for row in missing:
                conn.execute(
                    "INSERT INTO student_accounts (roll_no, password_hash) VALUES (?, ?)",
                    (row["roll_no"], hash_password(student_password)),
                )

    if version < SCHEMA_VERSION:
        conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION:d}")


def _ensure_default_teacher(conn):
    """Create the first teacher account if no teacher exists yet (see the module docstring)."""
    if conn.execute("SELECT COUNT(*) FROM teachers").fetchone()[0]:
        return

    username = _env_setting("TEACHER_USERNAME") or DEFAULT_TEACHER_USERNAME
    if not USERNAME_PATTERN.match(username):
        _announce([
            f"WARNING: TEACHER_USERNAME '{username}' is not a valid username",
            "(3-40 letters, digits, dot, underscore or hyphen).",
            f"Using '{DEFAULT_TEACHER_USERNAME}' instead.",
        ])
        username = DEFAULT_TEACHER_USERNAME
    full_name = _env_setting("TEACHER_NAME") or DEFAULT_TEACHER_NAME

    password = _env_setting("TEACHER_PASSWORD")
    if password is None:
        password = generate_password()
        _announce([
            "FIRST-TIME SETUP - teacher account created",
            f"  Username : {username}",
            f"  Password : {password}",
            "  (generated because TEACHER_PASSWORD is not set)",
            "Log in to the Teacher Portal and change it under Profile -> Change Password.",
            "This message is shown only once.",
        ])

    conn.execute(
        "INSERT INTO teachers (username, full_name, password_hash) VALUES (?, ?, ?)",
        (username, full_name, hash_password(password)),
    )


def _env_setting(name):
    """Value of an environment variable, or None if it is missing, empty or still a placeholder."""
    value = os.environ.get(name, "").strip()
    if not value or value.upper().startswith(PLACEHOLDER_PREFIX):
        return None
    return value


def generate_password(length=14):
    """Cryptographically random password with letters and digits (meets the app's policy)."""
    while True:
        password = "".join(secrets.choice(_PASSWORD_ALPHABET) for _ in range(length))
        if any(c.isdigit() for c in password) and any(c.isalpha() for c in password):
            return password


def _announce(lines):
    """Print a highlighted one-time message to stdout (terminal or `podman logs`)."""
    border = "=" * 72
    print("\n".join([border, *lines, border]), flush=True)


def _seed_sample_data(conn):
    """Insert fictional students and ~10 past sessions per class with realistic attendance."""
    rng = random.Random(216)  # fixed seed -> same demo data every time

    # Each fictional student gets a personal attendance tendency; a few fall below 75 %.
    tendency = {name: rng.choice([0.6, 0.7, 0.82, 0.86, 0.9, 0.92, 0.95, 0.98])
                for name in SAMPLE_NAMES}

    # Past weekdays (most recent last), ending yesterday
    session_dates = []
    day = date.today() - timedelta(days=1)
    while len(session_dates) < SAMPLE_SESSIONS_PER_CLASS:
        if day.weekday() < 5:
            session_dates.append(day.isoformat())
        day -= timedelta(days=1)
    session_dates.reverse()

    for subject, section, names_slice in SAMPLE_CLASSES:
        student_ids = []
        for offset, name in enumerate(SAMPLE_NAMES[names_slice], start=names_slice.start + 1):
            roll_no = f"CS23{section}{offset:03d}"
            cur = conn.execute(
                "INSERT INTO students (roll_no, name, subject, section) VALUES (?, ?, ?, ?)",
                (roll_no, name, subject, section),
            )
            student_ids.append((cur.lastrowid, name))

        for session_date in session_dates:
            cur = conn.execute(
                "INSERT INTO sessions (subject, section, session_date) VALUES (?, ?, ?)",
                (subject, section, session_date),
            )
            session_id = cur.lastrowid
            for student_id, name in student_ids:
                status = STATUS_PRESENT if rng.random() < tendency[name] else STATUS_ABSENT
                conn.execute(
                    "INSERT INTO attendance (student_id, session_id, status) VALUES (?, ?, ?)",
                    (student_id, session_id, status),
                )


# --------------------------------------------------------------------------- #
# Small helpers                                                               #
# --------------------------------------------------------------------------- #
def _pct(part, whole):
    return round(part * 100.0 / whole, 1) if whole else None


def _class_filter(subject=None, section=None, alias="s"):
    """Build a WHERE fragment + params for optional subject/section filters."""
    clauses, params = [], []
    if subject:
        clauses.append(f"{alias}.subject = ?")
        params.append(subject)
    if section:
        clauses.append(f"{alias}.section = ?")
        params.append(section)
    return clauses, params


# --------------------------------------------------------------------------- #
# Passwords                                                                   #
# --------------------------------------------------------------------------- #
_DUMMY_HASH = None


def hash_password(password):
    """Salted scrypt hash (Werkzeug default), e.g. 'scrypt:32768:8:1$salt$hash'."""
    return generate_password_hash(password)


def verify_password(stored_hash, password):
    """
    Check a password against a stored hash.

    When the account does not exist (stored_hash is None) a dummy hash is still checked,
    so the response time does not reveal whether a username / roll number exists.
    """
    global _DUMMY_HASH
    if not stored_hash:
        if _DUMMY_HASH is None:
            _DUMMY_HASH = generate_password_hash("not-a-real-account")
        check_password_hash(_DUMMY_HASH, password)
        return False
    return check_password_hash(stored_hash, password)


# --------------------------------------------------------------------------- #
# Teacher accounts                                                            #
# --------------------------------------------------------------------------- #
def get_teacher(conn, teacher_id):
    return conn.execute("SELECT * FROM teachers WHERE id = ?", (teacher_id,)).fetchone()


def get_teacher_by_username(conn, username):
    return conn.execute("SELECT * FROM teachers WHERE username = ?", (username,)).fetchone()


def set_teacher_password(conn, teacher_id, password):
    conn.execute("UPDATE teachers SET password_hash = ? WHERE id = ?",
                 (hash_password(password), teacher_id))
    conn.commit()


def touch_teacher_login(conn, teacher_id):
    conn.execute("UPDATE teachers SET last_login = datetime('now', 'localtime') WHERE id = ?",
                 (teacher_id,))
    conn.commit()


# --------------------------------------------------------------------------- #
# Student accounts                                                            #
# --------------------------------------------------------------------------- #
def get_student_account(conn, roll_no):
    return conn.execute("SELECT * FROM student_accounts WHERE roll_no = ?", (roll_no,)).fetchone()


def set_student_password(conn, roll_no, password):
    """Set (or create) the login for a roll number."""
    conn.execute(
        """
        INSERT INTO student_accounts (roll_no, password_hash) VALUES (?, ?)
        ON CONFLICT (roll_no) DO UPDATE SET password_hash = excluded.password_hash
        """,
        (roll_no, hash_password(password)),
    )
    conn.commit()


def touch_student_login(conn, roll_no):
    conn.execute("UPDATE student_accounts SET last_login = datetime('now', 'localtime') WHERE roll_no = ?",
                 (roll_no,))
    conn.commit()


# --------------------------------------------------------------------------- #
# Students                                                                    #
# --------------------------------------------------------------------------- #
def add_student(conn, roll_no, name, subject, section, password=None):
    """
    Insert a student enrolment (and, if a password is given, their login) in one transaction.
    Raises sqlite3.IntegrityError on duplicate roll_no + subject.
    """
    try:
        cur = conn.execute(
            "INSERT INTO students (roll_no, name, subject, section) VALUES (?, ?, ?, ?)",
            (roll_no, name, subject, section),
        )
        if password:
            conn.execute(
                "INSERT INTO student_accounts (roll_no, password_hash) VALUES (?, ?)",
                (roll_no, hash_password(password)),
            )
        conn.commit()
    except sqlite3.Error:
        conn.rollback()
        raise
    return cur.lastrowid


def get_student(conn, student_id):
    return conn.execute("SELECT * FROM students WHERE id = ?", (student_id,)).fetchone()


def delete_student(conn, student_id):
    """Delete a student; their attendance rows are removed by ON DELETE CASCADE."""
    cur = conn.execute("DELETE FROM students WHERE id = ?", (student_id,))
    conn.commit()
    return cur.rowcount


def student_stats(conn, subject=None, section=None, search=None):
    """
    One row per student enrolment with attendance totals and percentage.
    Used by the Students page, the Reports page and the CSV export.
    """
    clauses, params = _class_filter(subject, section)
    if search:
        clauses.append("(s.roll_no LIKE ? OR s.name LIKE ?)")
        like = f"%{search}%"
        params.extend([like, like])
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""

    rows = conn.execute(
        f"""
        SELECT s.id, s.roll_no, s.name, s.subject, s.section, s.created_at,
               COUNT(a.id) AS total,
               COALESCE(SUM(CASE WHEN a.status = 'Present' THEN 1 ELSE 0 END), 0) AS present
        FROM students s
        LEFT JOIN attendance a ON a.student_id = s.id
        {where}
        GROUP BY s.id
        ORDER BY s.subject, s.section, s.roll_no
        """,
        params,
    ).fetchall()

    result = []
    for r in rows:
        item = dict(r)
        item["absent"] = item["total"] - item["present"]
        item["percentage"] = _pct(item["present"], item["total"])
        result.append(item)
    return result


def students_for_class(conn, subject, section):
    return conn.execute(
        "SELECT * FROM students WHERE subject = ? AND section = ? ORDER BY roll_no",
        (subject, section),
    ).fetchall()


def get_subjects(conn):
    rows = conn.execute("SELECT DISTINCT subject FROM students ORDER BY subject").fetchall()
    return [r["subject"] for r in rows]


def get_sections(conn):
    rows = conn.execute("SELECT DISTINCT section FROM students ORDER BY section").fetchall()
    return [r["section"] for r in rows]


def get_class_map(conn):
    """{subject: [sections...]} - used to filter the section dropdown in the browser."""
    class_map = {}
    for r in conn.execute(
        "SELECT DISTINCT subject, section FROM students ORDER BY subject, section"
    ):
        class_map.setdefault(r["subject"], []).append(r["section"])
    return class_map


# --------------------------------------------------------------------------- #
# Student portal (always filtered by the logged-in student's roll number)      #
# --------------------------------------------------------------------------- #
def student_profile(conn, roll_no):
    """Name, sections and registered subjects for one roll number (None if not enrolled)."""
    rows = conn.execute(
        "SELECT * FROM students WHERE roll_no = ? ORDER BY id", (roll_no,)
    ).fetchall()
    if not rows:
        return None
    account = get_student_account(conn, roll_no)
    return {
        "roll_no": roll_no,
        "name": rows[0]["name"],
        "sections": sorted({r["section"] for r in rows}),
        "subjects": sorted(({"subject": r["subject"], "section": r["section"]} for r in rows),
                           key=lambda e: e["subject"]),
        "enrolled_since": min(r["created_at"] for r in rows),
        "last_login": account["last_login"] if account else None,
        "has_account": account is not None,
    }


def student_subject_summary(conn, roll_no):
    """Subject | Total classes | Present | Absent | % for one roll number."""
    rows = conn.execute(
        """
        SELECT s.id, s.subject, s.section,
               COUNT(a.id) AS total,
               COALESCE(SUM(CASE WHEN a.status = 'Present' THEN 1 ELSE 0 END), 0) AS present
        FROM students s
        LEFT JOIN attendance a ON a.student_id = s.id
        WHERE s.roll_no = ?
        GROUP BY s.id
        ORDER BY s.subject
        """,
        (roll_no,),
    ).fetchall()
    result = []
    for r in rows:
        item = dict(r)
        item["absent"] = item["total"] - item["present"]
        item["percentage"] = _pct(item["present"], item["total"])
        result.append(item)
    return result


def student_history(conn, roll_no, subject=None, limit=None):
    """Dated Present/Absent records for one roll number, newest first."""
    sql = """
        SELECT se.session_date, se.subject, se.section, a.status, a.marked_at
        FROM attendance a
        JOIN students s  ON s.id  = a.student_id
        JOIN sessions se ON se.id = a.session_id
        WHERE s.roll_no = ?
    """
    params = [roll_no]
    if subject:
        sql += " AND s.subject = ?"
        params.append(subject)
    sql += " ORDER BY se.session_date DESC, se.subject"
    if limit:
        sql += " LIMIT ?"
        params.append(int(limit))
    return conn.execute(sql, params).fetchall()


def classes_needed(present, total, threshold=75):
    """
    Consecutive classes a student must attend to reach `threshold` %.

    Solves (present + x) / (total + x) >= t  ->  x >= (t*total - present) / (1 - t)
    Returns 0 if already at/above the threshold and None if it can never be reached
    (threshold of 100 % with at least one absence).
    """
    t = Fraction(str(threshold)) / 100
    if total == 0 or Fraction(present, total) >= t:
        return 0
    if t >= 1:
        return None
    return math.ceil((t * total - present) / (1 - t))


def classes_can_miss(present, total, threshold=75):
    """How many future classes can be missed while staying at/above `threshold` %."""
    t = Fraction(str(threshold)) / 100
    if total == 0 or t == 0 or Fraction(present, total) < t:
        return 0
    return math.floor((present - t * total) / t)


# --------------------------------------------------------------------------- #
# Sessions & attendance                                                       #
# --------------------------------------------------------------------------- #
def find_session(conn, subject, section, session_date):
    return conn.execute(
        "SELECT * FROM sessions WHERE subject = ? AND section = ? AND session_date = ?",
        (subject, section, session_date),
    ).fetchone()


def get_session(conn, session_id):
    return conn.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()


def attendance_map(conn, session_id):
    """{student_id: 'Present' | 'Absent'} for one session."""
    rows = conn.execute(
        "SELECT student_id, status FROM attendance WHERE session_id = ?", (session_id,)
    ).fetchall()
    return {r["student_id"]: r["status"] for r in rows}


def save_attendance(conn, subject, section, session_date, statuses):
    """
    Save attendance for one class session.

    statuses: {student_id: 'Present' | 'Absent'}

    Duplicate prevention works on two levels:
      1. sessions has UNIQUE(subject, section, session_date) -> the existing session is reused.
      2. attendance has UNIQUE(student_id, session_id) -> an UPSERT updates the existing
         row instead of inserting a second one.

    Returns a dict describing what happened.
    """
    session = find_session(conn, subject, section, session_date)
    session_existed = session is not None
    if session_existed:
        session_id = session["id"]
    else:
        cur = conn.execute(
            "INSERT INTO sessions (subject, section, session_date) VALUES (?, ?, ?)",
            (subject, section, session_date),
        )
        session_id = cur.lastrowid

    existing = attendance_map(conn, session_id)
    inserted = updated = unchanged = 0
    for student_id, status in statuses.items():
        if status not in VALID_STATUSES:
            raise ValueError(f"Invalid status: {status}")
        previous = existing.get(student_id)
        if previous is None:
            inserted += 1
        elif previous != status:
            updated += 1
        else:
            unchanged += 1
            continue
        conn.execute(
            """
            INSERT INTO attendance (student_id, session_id, status) VALUES (?, ?, ?)
            ON CONFLICT (student_id, session_id)
            DO UPDATE SET status = excluded.status,
                          marked_at = datetime('now', 'localtime')
            """,
            (student_id, session_id, status),
        )
    conn.commit()
    return {
        "session_id": session_id,
        "session_existed": session_existed,
        "inserted": inserted,
        "updated": updated,
        "unchanged": unchanged,
    }


def delete_session(conn, session_id):
    cur = conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
    conn.commit()
    return cur.rowcount


def session_history(conn, subject=None, section=None, limit=None):
    """List of sessions with present/absent counts, newest first."""
    clauses, params = _class_filter(subject, section, alias="se")
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    sql = f"""
        SELECT se.id, se.subject, se.section, se.session_date,
               COUNT(a.id) AS total,
               COALESCE(SUM(CASE WHEN a.status = 'Present' THEN 1 ELSE 0 END), 0) AS present
        FROM sessions se
        LEFT JOIN attendance a ON a.session_id = se.id
        {where}
        GROUP BY se.id
        ORDER BY se.session_date DESC, se.subject, se.section
    """
    if limit:
        sql += " LIMIT ?"
        params.append(int(limit))
    result = []
    for r in conn.execute(sql, params).fetchall():
        item = dict(r)
        item["absent"] = item["total"] - item["present"]
        item["percentage"] = _pct(item["present"], item["total"])
        result.append(item)
    return result


def session_detail(conn, session_id):
    """Every student of the session's class with their status (or None if not marked)."""
    return conn.execute(
        """
        SELECT st.id, st.roll_no, st.name, a.status, a.marked_at
        FROM sessions se
        JOIN students st ON st.subject = se.subject AND st.section = se.section
        LEFT JOIN attendance a ON a.student_id = st.id AND a.session_id = se.id
        WHERE se.id = ?
        ORDER BY st.roll_no
        """,
        (session_id,),
    ).fetchall()


# --------------------------------------------------------------------------- #
# Dashboard / report aggregates                                               #
# --------------------------------------------------------------------------- #
def summary(conn, subject=None, section=None):
    """Headline numbers, optionally limited to one subject/section."""
    s_clauses, s_params = _class_filter(subject, section, alias="s")
    s_where = ("WHERE " + " AND ".join(s_clauses)) if s_clauses else ""
    se_clauses, se_params = _class_filter(subject, section, alias="se")
    se_where = ("WHERE " + " AND ".join(se_clauses)) if se_clauses else ""

    students = conn.execute(
        f"SELECT COUNT(DISTINCT s.roll_no) AS uniq, COUNT(*) AS enrolments FROM students s {s_where}",
        s_params,
    ).fetchone()
    sessions = conn.execute(
        f"SELECT COUNT(*) FROM sessions se {se_where}", se_params
    ).fetchone()[0]
    marks = conn.execute(
        f"""
        SELECT COUNT(a.id) AS total,
               COALESCE(SUM(CASE WHEN a.status = 'Present' THEN 1 ELSE 0 END), 0) AS present
        FROM attendance a JOIN students s ON s.id = a.student_id
        {s_where}
        """,
        s_params,
    ).fetchone()

    return {
        "total_students": students["uniq"],
        "total_enrolments": students["enrolments"],
        "total_sessions": sessions,
        "total_marks": marks["total"],
        "total_present": marks["present"],
        "total_absent": marks["total"] - marks["present"],
        "overall_percentage": _pct(marks["present"], marks["total"]),
    }


def below_threshold(stats_rows, threshold):
    """Students with at least one marked session whose percentage is below threshold."""
    low = [r for r in stats_rows if r["percentage"] is not None and r["percentage"] < threshold]
    return sorted(low, key=lambda r: r["percentage"])


def class_breakdown(conn):
    """Attendance % per subject + section (for the dashboard bar chart)."""
    rows = conn.execute(
        """
        SELECT s.subject, s.section,
               COUNT(a.id) AS total,
               COALESCE(SUM(CASE WHEN a.status = 'Present' THEN 1 ELSE 0 END), 0) AS present
        FROM students s
        LEFT JOIN attendance a ON a.student_id = s.id
        GROUP BY s.subject, s.section
        ORDER BY s.subject, s.section
        """
    ).fetchall()
    return [
        {"label": f"{r['subject']} ({r['section']})", "subject": r["subject"],
         "section": r["section"], "percentage": _pct(r["present"], r["total"]) or 0}
        for r in rows
    ]


def daily_trend(conn, days=10):
    """Overall attendance % for the most recent `days` dates that have sessions."""
    rows = conn.execute(
        """
        SELECT se.session_date AS day,
               COUNT(a.id) AS total,
               COALESCE(SUM(CASE WHEN a.status = 'Present' THEN 1 ELSE 0 END), 0) AS present
        FROM sessions se
        JOIN attendance a ON a.session_id = se.id
        GROUP BY se.session_date
        ORDER BY se.session_date DESC
        LIMIT ?
        """,
        (days,),
    ).fetchall()
    rows = list(reversed(rows))
    return [{"day": r["day"], "percentage": _pct(r["present"], r["total"]) or 0} for r in rows]
