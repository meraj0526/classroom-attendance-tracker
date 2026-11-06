"""
Teacher / Student portal tests - the testing scenarios from the project brief.

    1  Landing page shows Teacher / Student selection
    2  Teacher login and dashboard
    3  Teacher adds a student
    4  Teacher marks attendance
    5  Student logs in with their credentials
    6  Student views their own attendance
    7  Attendance percentage calculations
    8  Low-attendance warning
    9  Students cannot access teacher routes
    10 Students cannot view other students' records
    11 Logout
    12 Existing attendance records are preserved by the database upgrade
    (13 and 14 - running inside Podman - are covered by podman-verify.ps1)
    Plus: secure first-time accounts (no built-in passwords)

Run from the project folder:
    python -m unittest discover -s tests -v
"""

import contextlib
import io
import os
import re
import shutil
import sqlite3
import tempfile
import unittest

from helpers import (ACCOUNT_ENV_VARS, STUDENT_PASSWORD, TEACHER_PASSWORD, apply_test_accounts_env,
                     csrf_post, login_student, login_teacher, logout, use_temp_data_dir)

TEST_DATA_DIR = use_temp_data_dir("attendance-portal-test-")

import app as attendance_app  # noqa: E402  (import after DATA_DIR is set)
import database as db  # noqa: E402

LCT = "Linux and Container Technologies"
TEST_SUBJECT = "Test Driven Subject"


def setUpModule():
    os.environ["DATA_DIR"] = TEST_DATA_DIR
    db.init_db()


def tearDownModule():
    shutil.rmtree(TEST_DATA_DIR, ignore_errors=True)


class PortalTestCase(unittest.TestCase):
    def setUp(self):
        os.environ["DATA_DIR"] = TEST_DATA_DIR
        attendance_app.app.config["TESTING"] = True
        attendance_app._failed_logins.clear()
        self.client = attendance_app.app.test_client()
        self.conn = db.connect()

    def tearDown(self):
        self.conn.close()

    def overall_pct(self, roll_no):
        rows = db.student_subject_summary(self.conn, roll_no)
        total = sum(r["total"] for r in rows)
        present = sum(r["present"] for r in rows)
        return round(present * 100.0 / total, 1) if total else None


# --------------------------------------------------------------------------- #
class LandingAndTeacherTests(PortalTestCase):
    def test_01_landing_shows_role_selection(self):
        resp = self.client.get("/")
        self.assertEqual(resp.status_code, 200)
        for text in [b"Classroom Attendance Tracker",
                     b"Smart, Simple and Transparent Attendance Management",
                     b"Teacher Portal", b"Continue as Teacher",
                     b"Student Portal", b"Continue as Student"]:
            self.assertIn(text, resp.data)

    def test_02_teacher_login_and_dashboard(self):
        # anonymous -> redirected to the teacher login, remembering the page
        resp = self.client.get("/dashboard")
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/teacher/login?next=/dashboard", resp.headers["Location"])

        resp = login_teacher(self.client, password="wrong-password-1")
        self.assertIn(b"Invalid username or password", resp.data)

        resp = login_teacher(self.client)
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b"Total Students", resp.data)
        self.assertIn(b"Teacher Portal", resp.data)
        for url in ["/students", "/attendance", "/reports", "/reports/export.csv", "/teacher/profile"]:
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 200)

    def test_03_teacher_adds_student_with_hashed_password(self):
        login_teacher(self.client)
        resp = csrf_post(self.client, "/students/add", {
            "roll_no": "CS23T100", "name": "Nila Arora", "subject": TEST_SUBJECT, "section": "T",
            "password": "NilaPass9", "confirm_password": "NilaPass9", "action": "save"},
            follow_redirects=True)
        self.assertIn(b"added successfully", resp.data)
        account = db.get_student_account(self.conn, "CS23T100")
        self.assertIsNotNone(account)
        self.assertNotIn("NilaPass9", account["password_hash"])          # never stored in plain text
        self.assertTrue(account["password_hash"].startswith(("scrypt:", "pbkdf2:")))
        self.assertTrue(db.verify_password(account["password_hash"], "NilaPass9"))

    def test_03b_password_policy_and_mismatch(self):
        login_teacher(self.client)
        resp = csrf_post(self.client, "/students/add", {
            "roll_no": "CS23T101", "name": "Weak Pass", "subject": TEST_SUBJECT, "section": "T",
            "password": "short", "confirm_password": "other"}, follow_redirects=True)
        self.assertIn(b"8-128 characters", resp.data)
        self.assertIn(b"do not match", resp.data)
        self.assertIsNone(db.get_student_account(self.conn, "CS23T101"))

    def test_04_teacher_marks_attendance(self):
        login_teacher(self.client)
        students = db.students_for_class(self.conn, LCT, "A")
        data = {"subject": LCT, "section": "A", "date": "2023-02-01"}
        for s in students:
            data[f"status_{s['id']}"] = "Present"
        data[f"status_{students[0]['id']}"] = "Absent"
        resp = csrf_post(self.client, "/attendance/save", data, follow_redirects=True)
        self.assertIn(b"9 present, 1 absent", resp.data)
        session = db.find_session(self.conn, LCT, "A", "2023-02-01")
        self.assertEqual(db.attendance_map(self.conn, session["id"])[students[0]["id"]], "Absent")

    def test_teacher_resets_student_password(self):
        login_teacher(self.client)
        db.add_student(self.conn, "CS23T102", "Reset Me", TEST_SUBJECT, "T", password="OldPass11")
        resp = csrf_post(self.client, "/students/account/CS23T102",
                         {"password": "NewPass22", "confirm_password": "NewPass22"}, follow_redirects=True)
        self.assertIn(b"has been set", resp.data)
        logout(self.client)
        self.assertIn(b"Welcome, Reset Me", login_student(self.client, "CS23T102", "NewPass22").data)

    def test_teacher_cannot_open_student_portal(self):
        login_teacher(self.client)
        self.assertEqual(self.client.get("/student/dashboard").status_code, 403)


# --------------------------------------------------------------------------- #
class StudentPortalTests(PortalTestCase):
    def test_05_student_login(self):
        resp = login_student(self.client, "CS23A001", "wrong-pass-1")
        self.assertIn(b"Invalid roll number or password", resp.data)
        resp = login_student(self.client, "cs23a001")  # roll number is case-insensitive
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b"Welcome, Aarav Krishnan", resp.data)

    def test_06_student_views_only_own_attendance(self):
        login_student(self.client, "CS23A001")
        resp = self.client.get("/student/dashboard")
        self.assertIn(b"Welcome, Aarav Krishnan", resp.data)
        self.assertIn(b"Subject-wise Attendance", resp.data)
        self.assertIn(b"Database Management Systems", resp.data)
        self.assertNotIn(b"Diya Raman", resp.data)  # classmate in the same section

        history = self.client.get("/student/attendance")
        expected = len(db.student_history(self.conn, "CS23A001"))
        self.assertEqual(history.data.count(b'class="weekday"'), expected)
        self.assertIn(b"text-bg-success", history.data)  # green "Present" badges

        profile = self.client.get("/student/profile")
        for text in [b"Aarav Krishnan", b"CS23A001", b"Registered Subjects", LCT.encode()]:
            self.assertIn(text, profile.data)

    def test_07_percentage_calculations(self):
        sid = db.add_student(self.conn, "CS23T200", "Percent Tester", TEST_SUBJECT, "P", password="Percent123")
        for day, status in [("2022-01-03", "Present"), ("2022-01-04", "Present"),
                            ("2022-01-05", "Absent"), ("2022-01-06", "Absent")]:
            db.save_attendance(self.conn, TEST_SUBJECT, "P", day, {sid: status})

        rows = db.student_subject_summary(self.conn, "CS23T200")
        self.assertEqual((rows[0]["total"], rows[0]["present"], rows[0]["absent"]), (4, 2, 2))
        self.assertEqual(rows[0]["percentage"], 50.0)

        # (2 + x) / (4 + x) >= 0.75  ->  x = 4
        self.assertEqual(db.classes_needed(2, 4), 4)
        self.assertEqual(db.classes_needed(6, 10), 6)
        self.assertEqual(db.classes_needed(9, 10), 0)
        self.assertEqual(db.classes_needed(0, 0), 0)
        self.assertIsNone(db.classes_needed(5, 6, threshold=100))   # unreachable
        self.assertEqual(db.classes_can_miss(9, 10), 2)             # 9 / 12 = 75 %
        self.assertEqual(db.classes_can_miss(3, 4), 0)

        resp = login_student(self.client, "CS23T200", "Percent123")
        self.assertIn(b"50.0%", resp.data)
        self.assertIn(b"Attend the next <strong>4</strong> classes in a row", resp.data)

    def test_08_low_attendance_warning(self):
        rolls = [r["roll_no"] for r in self.conn.execute(
            "SELECT DISTINCT roll_no FROM students WHERE roll_no GLOB 'CS23[AB]0[0-9][0-9]'")]
        low = next(r for r in rolls if self.overall_pct(r) < 75)
        high = next(r for r in rolls if self.overall_pct(r) >= 75)

        resp = login_student(self.client, low)
        self.assertIn(b"below the required 75%", resp.data)
        logout(self.client)

        resp = login_student(self.client, high)
        self.assertNotIn(b"below the required 75%", resp.data)
        self.assertIn(b"Great job!", resp.data)

    def test_09_student_cannot_use_teacher_routes(self):
        login_student(self.client, "CS23A002")
        for url in ["/dashboard", "/students", "/students/add", "/attendance", "/reports",
                    "/reports/export.csv", "/reports/session/1", "/teacher/profile",
                    "/students/account/CS23A001"]:
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 403)

        # Students can never mark or modify attendance
        before = self.conn.execute("SELECT COUNT(*), SUM(status='Present') FROM attendance").fetchone()
        sid = db.students_for_class(self.conn, LCT, "A")[1]["id"]
        resp = csrf_post(self.client, "/attendance/save", {
            "subject": LCT, "section": "A", "date": "2024-01-02", f"status_{sid}": "Present"})
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(csrf_post(self.client, f"/students/{sid}/delete").status_code, 403)
        self.assertEqual(csrf_post(self.client, "/reports/session/1/delete").status_code, 403)
        after = self.conn.execute("SELECT COUNT(*), SUM(status='Present') FROM attendance").fetchone()
        self.assertEqual(tuple(before), tuple(after))

    def test_10_student_cannot_view_other_students(self):
        login_student(self.client, "CS23A001")
        # Query parameters cannot switch the student - the roll number comes from the session only
        resp = self.client.get("/student/dashboard?roll_no=CS23B011")
        self.assertIn(b"Welcome, Aarav Krishnan", resp.data)
        self.assertNotIn(b"Dev Malhotra", resp.data)
        # A subject the student is not enrolled in is ignored
        resp = self.client.get("/student/attendance?subject=Computer+Networks")
        self.assertNotIn(b"<td>Computer Networks</td>", resp.data)
        # Changing the session cookie is impossible without the secret key; tampering just logs out
        with self.client.session_transaction() as sess:
            sess["roll_no"] = "DOES-NOT-EXIST"
        self.assertEqual(self.client.get("/student/dashboard").status_code, 302)

    def test_11_logout(self):
        login_student(self.client, "CS23A001")
        self.assertEqual(self.client.get("/student/dashboard").status_code, 200)
        resp = logout(self.client)
        self.assertIn(b"You have been logged out", resp.data)
        resp = self.client.get("/student/dashboard")
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/student/login", resp.headers["Location"])
        self.assertEqual(self.client.get("/logout").status_code, 405)  # logout is POST-only (CSRF safe)

        login_teacher(self.client)
        logout(self.client)
        self.assertEqual(self.client.get("/dashboard").status_code, 302)

    def test_student_changes_password(self):
        db.add_student(self.conn, "CS23T300", "Change Pass", TEST_SUBJECT, "C", password="FirstPass1")
        login_student(self.client, "CS23T300", "FirstPass1")
        resp = csrf_post(self.client, "/student/profile", {
            "current_password": "FirstPass1", "new_password": "SecondPass2",
            "confirm_password": "SecondPass2"}, follow_redirects=True)
        self.assertIn(b"Your password has been changed", resp.data)
        logout(self.client)
        self.assertIn(b"Invalid roll number", login_student(self.client, "CS23T300", "FirstPass1").data)
        self.assertIn(b"Welcome, Change Pass", login_student(self.client, "CS23T300", "SecondPass2").data)


# --------------------------------------------------------------------------- #
class SecurityTests(PortalTestCase):
    def test_csrf_token_required(self):
        resp = self.client.post("/teacher/login", data={"username": "teacher", "password": TEACHER_PASSWORD})
        self.assertEqual(resp.status_code, 400)

    def test_login_lockout_after_failed_attempts(self):
        for _ in range(attendance_app.MAX_LOGIN_ATTEMPTS):
            login_student(self.client, "CS23A003", "Wrong-pass-9")
        resp = login_student(self.client, "CS23A003", STUDENT_PASSWORD)
        self.assertIn(b"Too many failed attempts", resp.data)

    def test_open_redirect_blocked(self):
        resp = csrf_post(self.client, "/teacher/login",
                         {"username": "teacher", "password": TEACHER_PASSWORD, "next": "//evil.example"})
        self.assertEqual(resp.headers["Location"], "/dashboard")

    def test_session_cookie_flags(self):
        resp = login_teacher(self.client)
        self.assertEqual(resp.headers.get("Cache-Control"), "no-store")
        cookie = self.client.get_cookie("session")
        self.assertTrue(cookie.http_only)
        self.assertEqual(cookie.same_site, "Lax")


# --------------------------------------------------------------------------- #
class MigrationTests(unittest.TestCase):
    """Scenario 12: a database from the original (pre-login) version is upgraded safely."""

    def setUp(self):
        self.legacy_dir = tempfile.mkdtemp(prefix="attendance-legacy-")
        os.environ["DATA_DIR"] = self.legacy_dir
        conn = sqlite3.connect(os.path.join(self.legacy_dir, "attendance.db"))
        conn.executescript(db.SCHEMA)  # exactly the original three tables, user_version 0
        conn.executemany("INSERT INTO students (roll_no, name, subject, section) VALUES (?, ?, ?, ?)", [
            ("OLD001", "Legacy One", "Physics", "A"),
            ("OLD002", "Legacy Two", "Physics", "A"),
            ("OLD001", "Legacy One", "Chemistry", "A"),
        ])
        conn.execute("INSERT INTO sessions (subject, section, session_date) VALUES ('Physics', 'A', '2025-07-01')")
        conn.executemany("INSERT INTO attendance (student_id, session_id, status) VALUES (?, 1, ?)",
                         [(1, "Present"), (2, "Absent")])
        conn.commit()
        conn.close()

    def tearDown(self):
        os.environ["DATA_DIR"] = TEST_DATA_DIR
        shutil.rmtree(self.legacy_dir, ignore_errors=True)

    def counts(self, conn):
        return {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                for t in ("students", "sessions", "attendance")}

    def test_12_existing_records_preserved(self):
        db.init_db()
        db.init_db()  # running again (container restart) changes nothing
        conn = db.connect()
        try:
            self.assertEqual(self.counts(conn), {"students": 3, "sessions": 1, "attendance": 2})
            self.assertEqual(conn.execute("PRAGMA user_version").fetchone()[0], db.SCHEMA_VERSION)
            statuses = [r[0] for r in conn.execute("SELECT status FROM attendance ORDER BY id")]
            self.assertEqual(statuses, ["Present", "Absent"])
            # no sample data was mixed into the real data
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM students WHERE roll_no LIKE 'CS23%'").fetchone()[0], 0)
            # one login per roll number, configured DEFAULT_STUDENT_PASSWORD, hashed
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM student_accounts").fetchone()[0], 2)
            account = db.get_student_account(conn, "OLD001")
            self.assertTrue(db.verify_password(account["password_hash"], STUDENT_PASSWORD))
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM teachers").fetchone()[0], 1)
        finally:
            conn.close()

        client = attendance_app.app.test_client()
        resp = login_student(client, "OLD001")
        self.assertIn(b"Welcome, Legacy One", resp.data)
        self.assertIn(b"Physics", resp.data)
        self.assertIn(b"Chemistry", resp.data)


# --------------------------------------------------------------------------- #
class FirstRunSetupTests(unittest.TestCase):
    """No built-in passwords: unset or placeholder values get a random password printed once."""

    OLD_DEMO_PASSWORDS = ("Teacher@123", "Student@123")

    def setUp(self):
        self.data_dir = tempfile.mkdtemp(prefix="attendance-firstrun-")
        os.environ["DATA_DIR"] = self.data_dir
        for name in ACCOUNT_ENV_VARS:
            os.environ.pop(name, None)
        attendance_app._failed_logins.clear()

    def tearDown(self):
        apply_test_accounts_env()
        os.environ["DATA_DIR"] = TEST_DATA_DIR
        shutil.rmtree(self.data_dir, ignore_errors=True)

    def start_app(self):
        """Run the start-up initialisation and return what it printed."""
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            db.init_db()
        return output.getvalue()

    def test_generated_passwords_when_not_configured(self):
        log = self.start_app()
        self.assertIn("FIRST-TIME SETUP - teacher account created", log)
        teacher_pw = re.search(r"Password : (\S+)", log).group(1)
        student_pw = re.search(r"Initial password : (\S+)", log).group(1)
        self.assertNotEqual(teacher_pw, student_pw)
        self.assertGreaterEqual(len(teacher_pw), 14)

        conn = db.connect()
        try:
            teacher = db.get_teacher_by_username(conn, "teacher")
            student = db.get_student_account(conn, "CS23A001")
            self.assertTrue(db.verify_password(teacher["password_hash"], teacher_pw))
            self.assertTrue(db.verify_password(student["password_hash"], student_pw))
            for old in self.OLD_DEMO_PASSWORDS:
                self.assertFalse(db.verify_password(teacher["password_hash"], old))
                self.assertFalse(db.verify_password(student["password_hash"], old))
        finally:
            conn.close()

        client = attendance_app.app.test_client()
        self.assertIn(b"Total Students", login_teacher(client, password=teacher_pw).data)
        logout(client)
        self.assertIn(b"Welcome, Aarav Krishnan", login_student(client, "CS23A001", student_pw).data)

        # Restarting prints nothing and does not change the accounts
        self.assertEqual(self.start_app(), "")
        self.assertIn(b"Total Students", login_teacher(attendance_app.app.test_client(), password=teacher_pw).data)

    def test_placeholders_and_empty_values_are_never_used(self):
        os.environ["TEACHER_USERNAME"] = "REPLACE_WITH_TEACHER_USERNAME"
        os.environ["TEACHER_PASSWORD"] = "REPLACE_WITH_A_STRONG_TEACHER_PASSWORD"
        os.environ["DEFAULT_STUDENT_PASSWORD"] = ""
        log = self.start_app()
        self.assertIn("Username : teacher", log)
        self.assertIn("Initial password : ", log)
        conn = db.connect()
        try:
            teacher = db.get_teacher_by_username(conn, "teacher")
            self.assertFalse(db.verify_password(teacher["password_hash"], "REPLACE_WITH_A_STRONG_TEACHER_PASSWORD"))
            student = db.get_student_account(conn, "CS23A001")
            self.assertFalse(db.verify_password(student["password_hash"], ""))
        finally:
            conn.close()

    def test_configured_values_are_used_and_never_printed(self):
        os.environ["TEACHER_USERNAME"] = "head.teacher"
        os.environ["TEACHER_PASSWORD"] = "Configured-Pass-9"
        os.environ["TEACHER_NAME"] = "Head Teacher"
        os.environ["DEFAULT_STUDENT_PASSWORD"] = "Configured-Student-9"
        self.assertEqual(self.start_app(), "")
        client = attendance_app.app.test_client()
        resp = login_teacher(client, username="head.teacher", password="Configured-Pass-9")
        self.assertIn(b"Welcome back, Head Teacher", resp.data)
        logout(client)
        self.assertIn(b"Welcome, ", login_student(client, "CS23B011", "Configured-Student-9").data)

    def test_invalid_username_falls_back_to_default(self):
        os.environ["TEACHER_USERNAME"] = "bad name with spaces"
        os.environ["TEACHER_PASSWORD"] = "Valid-Pass-1"
        log = self.start_app()
        self.assertIn("is not a valid username", log)
        resp = login_teacher(attendance_app.app.test_client(), username="teacher", password="Valid-Pass-1")
        self.assertIn(b"Total Students", resp.data)


if __name__ == "__main__":
    unittest.main()
