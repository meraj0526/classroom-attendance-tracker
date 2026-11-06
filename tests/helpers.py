"""
Shared helpers for the test modules (not a test module itself).

Every test module gets its own throw-away DATA_DIR, so the real data/attendance.db
is never touched.
"""

import os
import sys
import tempfile

PROJECT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PROJECT_DIR not in sys.path:
    sys.path.insert(0, PROJECT_DIR)

# Test-only accounts for the throw-away test databases. They are set explicitly, so the
# tests behave the same no matter what is loaded in your terminal (e.g. your own .env).
TEACHER_USERNAME = "teacher"
TEACHER_PASSWORD = "Test-Teacher-Pass-1"
STUDENT_PASSWORD = "Test-Student-Pass-1"
ACCOUNT_ENV_VARS = ("TEACHER_USERNAME", "TEACHER_PASSWORD", "TEACHER_NAME", "DEFAULT_STUDENT_PASSWORD")


def apply_test_accounts_env():
    """Set the first-run account variables to the test-only values above."""
    os.environ["TEACHER_USERNAME"] = TEACHER_USERNAME
    os.environ["TEACHER_PASSWORD"] = TEACHER_PASSWORD
    os.environ["TEACHER_NAME"] = "Class Teacher"
    os.environ["DEFAULT_STUDENT_PASSWORD"] = STUDENT_PASSWORD


def use_temp_data_dir(prefix):
    """Point DATA_DIR at a new temporary folder (call before importing app)."""
    path = tempfile.mkdtemp(prefix=prefix)
    os.environ["DATA_DIR"] = path
    os.environ["SEED_SAMPLE_DATA"] = "1"
    apply_test_accounts_env()
    return path


def csrf_post(client, url, data=None, **kwargs):
    """POST with the session's CSRF token, exactly like a browser form would."""
    with client.session_transaction() as sess:
        token = sess.setdefault("_csrf", "test-csrf-token")
    payload = dict(data or {})
    payload["csrf_token"] = token
    return client.post(url, data=payload, **kwargs)


def login_teacher(client, username=TEACHER_USERNAME, password=TEACHER_PASSWORD):
    return csrf_post(client, "/teacher/login",
                     {"username": username, "password": password}, follow_redirects=True)


def login_student(client, roll_no, password=STUDENT_PASSWORD):
    return csrf_post(client, "/student/login",
                     {"roll_no": roll_no, "password": password}, follow_redirects=True)


def logout(client):
    return csrf_post(client, "/logout", follow_redirects=True)
