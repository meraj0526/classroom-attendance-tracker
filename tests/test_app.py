"""
Teacher-portal feature tests for the Classroom Attendance Tracker
(the original features: students, attendance, reports, persistence).

Run from the project folder:
    python -m unittest discover -s tests -v

Each test module uses a throw-away database in a temporary DATA_DIR,
so your real data/attendance.db is never touched.
"""

import os
import shutil
import sqlite3
import unittest
from datetime import date, timedelta

from helpers import csrf_post, login_teacher, use_temp_data_dir

TEST_DATA_DIR = use_temp_data_dir("attendance-test-")

import app as attendance_app  # noqa: E402  (import after DATA_DIR is set)
import database as db  # noqa: E402

LCT = "Linux and Container Technologies"


def setUpModule():
    os.environ["DATA_DIR"] = TEST_DATA_DIR
    db.init_db()


def tearDownModule():
    shutil.rmtree(TEST_DATA_DIR, ignore_errors=True)


class AttendanceAppTests(unittest.TestCase):
    def setUp(self):
        os.environ["DATA_DIR"] = TEST_DATA_DIR
        attendance_app.app.config["TESTING"] = True
        attendance_app._failed_logins.clear()
        self.client = attendance_app.app.test_client()
        login_teacher(self.client)
        self.conn = db.connect()

    def tearDown(self):
        self.conn.close()

    # ---------- pages ----------
    def test_all_pages_load(self):
        for url in ["/", "/dashboard", "/students", "/students/add", "/attendance",
                    f"/attendance?subject={LCT}&section=A&date={date.today()}",
                    "/reports", f"/reports?subject={LCT}&section=B&threshold=80",
                    "/reports/session/1", "/students/account/CS23A001", "/teacher/profile", "/health"]:
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 200)

    def test_404_page(self):
        self.assertEqual(self.client.get("/does-not-exist").status_code, 404)
        self.assertEqual(self.client.get("/reports/session/999999").status_code, 404)

    def test_database_in_data_dir(self):
        self.assertTrue(os.path.exists(os.path.join(TEST_DATA_DIR, "attendance.db")))
        tables = {r[0] for r in self.conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertTrue({"students", "sessions", "attendance", "teachers", "student_accounts"} <= tables)

    def test_sample_data_seeded(self):
        # 20 fictional students, each enrolled in 2 subjects (other tests may add a few more)
        sample = self.conn.execute(
            "SELECT COUNT(DISTINCT roll_no), COUNT(*) FROM students WHERE roll_no GLOB 'CS23[AB]0[0-9][0-9]'"
        ).fetchone()
        self.assertEqual((sample[0], sample[1]), (20, 40))
        stats = db.summary(self.conn)
        self.assertGreater(stats["total_sessions"], 0)
        self.assertIsNotNone(stats["overall_percentage"])

    # ---------- students ----------
    def test_add_and_search_student(self):
        resp = csrf_post(self.client, "/students/add", {
            "roll_no": "cs23z900", "name": "Test Learner", "subject": "Operating Systems",
            "section": "z", "password": "Learner123", "confirm_password": "Learner123",
            "action": "save"}, follow_redirects=True)
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b"added successfully", resp.data)
        resp = self.client.get("/students?q=CS23Z900")
        self.assertIn(b"Test Learner", resp.data)

    def test_duplicate_student_rejected(self):
        data = {"roll_no": "CS23Y901", "name": "Dup Person", "subject": "Compilers", "section": "Y",
                "password": "Dupper123", "confirm_password": "Dupper123"}
        csrf_post(self.client, "/students/add", data)
        resp = csrf_post(self.client, "/students/add", data, follow_redirects=True)
        self.assertIn(b"already enrolled", resp.data)
        count = self.conn.execute(
            "SELECT COUNT(*) FROM students WHERE roll_no='CS23Y901' AND subject='Compilers'").fetchone()[0]
        self.assertEqual(count, 1)

    def test_invalid_student_rejected(self):
        resp = csrf_post(self.client, "/students/add", {
            "roll_no": "!", "name": "", "subject": "", "section": ""}, follow_redirects=True)
        self.assertIn(b"Roll number must be", resp.data)

    def test_new_student_requires_password(self):
        resp = csrf_post(self.client, "/students/add", {
            "roll_no": "CS23X950", "name": "No Password", "subject": "Compilers", "section": "X"},
            follow_redirects=True)
        self.assertIn(b"Set an initial password", resp.data)
        self.assertIsNone(db.get_student_account(self.conn, "CS23X950"))

    def test_existing_student_keeps_password_in_new_subject(self):
        db.add_student(self.conn, "CS23W960", "Two Subjects", "Compilers", "W", password="FirstPass1")
        resp = csrf_post(self.client, "/students/add", {
            "roll_no": "CS23W960", "name": "Two Subjects", "subject": "Operating Systems", "section": "W"},
            follow_redirects=True)
        self.assertIn(b"existing password was kept", resp.data)
        account = db.get_student_account(self.conn, "CS23W960")
        self.assertTrue(db.verify_password(account["password_hash"], "FirstPass1"))
        self.assertEqual(len(db.student_subject_summary(self.conn, "CS23W960")), 2)

    def test_delete_student_cascades(self):
        sid = db.add_student(self.conn, "CS23X902", "Temp Student", LCT, "A", password="TempPass1")
        db.save_attendance(self.conn, LCT, "A", "2020-01-06", {sid: "Present"})
        csrf_post(self.client, f"/students/{sid}/delete")
        self.assertIsNone(db.get_student(self.conn, sid))
        left = self.conn.execute("SELECT COUNT(*) FROM attendance WHERE student_id=?", (sid,)).fetchone()[0]
        self.assertEqual(left, 0)
        # last enrolment gone -> the login is removed by the database trigger
        self.assertIsNone(db.get_student_account(self.conn, "CS23X902"))

    # ---------- attendance ----------
    def _form_for(self, subject, section, day, status="Present"):
        data = {"subject": subject, "section": section, "date": day}
        for s in db.students_for_class(self.conn, subject, section):
            data[f"status_{s['id']}"] = status
        return data

    def test_save_attendance_and_no_duplicates(self):
        day = "2021-03-15"
        resp = csrf_post(self.client, "/attendance/save", self._form_for(LCT, "B", day),
                         follow_redirects=True)
        self.assertIn(b"Attendance saved", resp.data)

        resp = csrf_post(self.client, "/attendance/save", self._form_for(LCT, "B", day, "Absent"),
                         follow_redirects=True)
        self.assertIn(b"no duplicates created", resp.data)

        sessions = self.conn.execute(
            "SELECT COUNT(*) FROM sessions WHERE subject=? AND section='B' AND session_date=?",
            (LCT, day)).fetchone()[0]
        self.assertEqual(sessions, 1)
        session = db.find_session(self.conn, LCT, "B", day)
        rows = self.conn.execute(
            "SELECT status, COUNT(*) FROM attendance WHERE session_id=? GROUP BY status",
            (session["id"],)).fetchall()
        self.assertEqual([(r[0], r[1]) for r in rows], [("Absent", 10)])

    def test_database_blocks_duplicate_rows(self):
        """Even a raw INSERT cannot create a second row for the same student + session."""
        existing = self.conn.execute("SELECT student_id, session_id FROM attendance LIMIT 1").fetchone()
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute("INSERT INTO attendance (student_id, session_id, status) VALUES (?, ?, 'Present')",
                              (existing["student_id"], existing["session_id"]))

    def test_future_date_rejected(self):
        future = (date.today() + timedelta(days=3)).isoformat()
        resp = csrf_post(self.client, "/attendance/save", self._form_for(LCT, "A", future),
                         follow_redirects=True)
        self.assertIn(b"future date", resp.data)
        self.assertIsNone(db.find_session(self.conn, LCT, "A", future))

    def test_missing_status_rejected(self):
        data = self._form_for(LCT, "A", "2021-04-01")
        first_key = next(k for k in data if k.startswith("status_"))
        del data[first_key]
        resp = csrf_post(self.client, "/attendance/save", data, follow_redirects=True)
        self.assertIn(b"Please mark every student", resp.data)

    # ---------- reports ----------
    def test_threshold_filter(self):
        rows = db.student_stats(self.conn)
        low_50 = db.below_threshold(rows, 50)
        low_100 = db.below_threshold(rows, 100)
        self.assertLessEqual(len(low_50), len(low_100))
        self.assertTrue(all(r["percentage"] < 50 for r in low_50))

    def test_bad_threshold_falls_back(self):
        self.assertEqual(self.client.get("/reports?threshold=abc").status_code, 200)
        self.assertEqual(attendance_app.parse_threshold("250"), 100.0)
        self.assertEqual(attendance_app.parse_threshold("-5"), 0.0)

    def test_csv_export(self):
        resp = self.client.get(f"/reports/export.csv?subject={LCT}&section=A")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.mimetype, "text/csv")
        self.assertTrue(resp.data.startswith(b"Roll No,Name"))

    # ---------- persistence ----------
    def test_data_survives_restart(self):
        """Simulates a container restart: re-running init_db must not wipe or re-seed data."""
        before = db.summary(self.conn)
        db.init_db()
        fresh = db.connect()
        try:
            after = db.summary(fresh)
        finally:
            fresh.close()
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
