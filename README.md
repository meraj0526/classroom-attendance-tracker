# Classroom Attendance Tracker

**Capstone project – 21CSE216P Linux and Container Technologies**

*Smart, Simple and Transparent Attendance Management*

A web application with separate **Teacher** and **Student** portals for recording and viewing classroom
attendance, built with Python Flask and SQLite and packaged as a custom **Podman** image that runs
with a dedicated container network, a persistent volume and port mapping.

---

## Contents

1. [Project title and description](#1-project-title-and-description)
2. [Problem statement and objectives](#2-problem-statement-and-objectives)
3. [Technologies used](#3-technologies-used)
4. [Application features](#4-application-features)
5. [Project folder structure](#5-project-folder-structure)
6. [Installation and setup](#6-installation-and-setup)
7. [Running the application locally](#7-running-the-application-locally)
8. [Custom Containerfile explained](#8-custom-containerfile-explained)
9. [Building the custom Podman image](#9-building-the-custom-podman-image)
10. [Creating and using a Podman container](#10-creating-and-using-a-podman-container)
11. [Podman networking](#11-podman-networking)
12. [Persistent storage](#12-persistent-storage)
13. [Port mapping and localhost access](#13-port-mapping-and-localhost-access)
14. [Container lifecycle management](#14-container-lifecycle-management)
15. [Image management: build, tag, push, pull](#15-image-management-build-tag-push-pull)
16. [Container troubleshooting](#16-container-troubleshooting)
17. [Testing](#17-testing)
18. [Data backup and restoration](#18-data-backup-and-restoration)
19. [GitHub repository](#19-github-repository)
20. [Screenshots and evidence checklist](#20-screenshots-and-evidence-checklist)
21. [Conclusion](#21-conclusion)

All commands are for **Windows PowerShell** (the VS Code terminal) and must be run from the project
folder `classroom-attendance-tracker` unless stated otherwise.

---

## 1. Project title and description

**Classroom Attendance Tracker** is a small but complete web application for colleges:

* **Teachers** manage students, mark daily attendance per subject and section, review reports,
  find students below a chosen attendance percentage and export reports as CSV.
* **Students** log in with their roll number and see only their own attendance – overall and
  subject-wise percentages, date-wise history, charts and low-attendance alerts.

The application is containerised with a custom `Containerfile`, built into a custom Podman image and
run as a container that stores its SQLite database on a persistent Podman volume.

### Team

| Name | Register number | Role |
|---|---|---|
| `<member 1>` | `<register number>` | `<role>` |
| `<member 2>` | `<register number>` | `<role>` |
| `<member 3>` | `<register number>` | `<role>` |

*(Maximum 3 members as per the capstone guidelines.)*

---

## 2. Problem statement and objectives

### Problem statement

Attendance is often recorded on paper or in scattered spreadsheets. Teachers spend time totalling
percentages by hand, students cannot easily check whether they meet the 75 % requirement, and the
data is hard to back up or move between machines. Running such an application usually also depends
on the exact Python version and libraries installed on each computer.

### Objectives

1. Build a small, working web application for recording and viewing classroom attendance.
2. Provide separate, secure portals for teachers and students, with hashed passwords and
   role-based access.
3. Calculate attendance percentages automatically and warn students below 75 %, including how many
   classes they must attend to recover.
4. Package the application in a **custom Containerfile** and **Podman image** so it runs the same
   way on any machine.
5. Demonstrate **Podman networking**, **persistent storage**, **port mapping** and full
   **container lifecycle management and troubleshooting**.
6. Publish the source code on GitHub and the image on a public container registry.

---

## 3. Technologies used

| Layer | Technology | Version / detail |
|---|---|---|
| Backend | Python, Flask | Python 3.12 in the image (3.10+ locally), Flask 3.0.3 |
| Password hashing | Werkzeug | 3.1.9 – salted **scrypt** hashes |
| Production server | Gunicorn | 23.0.0 (inside the container) |
| Database | SQLite | File `attendance.db` in `/app/data` |
| Frontend | HTML5, CSS3, Bootstrap, Bootstrap Icons, JavaScript | Bootstrap 5.3.3, Icons 1.11.3 (loaded from a CDN) |
| Charts | Chart.js | 4.4.1 (loaded from a CDN) |
| Containers | Podman, Containerfile | Podman 5.x, base image `docker.io/library/python:3.12-slim` |
| Container host on Windows | WSL 2 | Required by `podman machine` |
| Version control / registry | Git, GitHub, Docker Hub | – |
| Editor | Visual Studio Code | Python extension |

---

## 4. Application features

### Landing page (`/`)
Title, subtitle and two cards – **Teacher Portal** and **Student Portal** – each with a
*Continue as …* button.

### Teacher Portal

| Module | Features |
|---|---|
| Dashboard | Total students, total sessions, overall attendance %, students below 75 %, class-wise bar chart, present/absent doughnut chart, daily trend, recent sessions |
| Students | Add (with an initial Student Portal password), list, search, filter by subject and section, delete, manage a student's login / reset password |
| Mark Attendance | Choose subject → section → date, mark Present/Absent, *All Present / All Absent*, live counters; saving the same session again updates it – duplicates are impossible |
| Reports | Per-student percentages, subject/section filters, user-defined threshold, below-threshold list, attendance history, session details, edit/delete a session, **CSV export** |
| Profile | Teacher details, change password |

### Student Portal (read-only)

| Section | Features |
|---|---|
| Dashboard | "Welcome, *name*", overall % ring, cards for overall %, classes conducted, attended, absent |
| Subject-wise table | Subject · Total Classes · Present · Absent · Attendance % |
| Alerts | Warning below 75 % with the number of consecutive classes needed to reach 75 % |
| Charts | Subject-wise bar chart, present-vs-absent doughnut chart |
| Attendance history | Every date with a green **Present** or red **Absent** badge, filter by own subjects |
| Profile | Name, roll number, section, registered subjects, change password |

The "classes needed" value solves (P + x) / (T + x) ≥ 0.75, giving x = 3T − 4P,
where P is classes attended and T is classes conducted.

### Security features

| Feature | Implementation |
|---|---|
| Password storage | Salted scrypt hashes (Werkzeug); plain passwords are never stored |
| Separate sessions | The signed session cookie records the role; logging in always starts a new session |
| Route protection | Teacher pages are teacher-only (students get **403**); student pages use the roll number from the session only, never from the URL |
| Read-only students | Students have no routes that create or change attendance |
| CSRF protection | Every form carries a per-session token |
| Brute-force protection | 5 failed logins for the same account from the same IP → blocked for 5 minutes (kept in memory; resets when the app restarts) |
| Input validation | Server-side checks for roll numbers, usernames, dates, thresholds and password policy (8–128 characters, letters and digits) |
| Cookies and headers | `HttpOnly`, `SameSite=Lax`, optional `Secure`, `no-store` caching for logged-in pages, `X-Frame-Options: DENY`, `X-Content-Type-Options: nosniff` |
| Secrets | Configured through environment variables (`.env`), never hard-coded in the README; the session key is generated per installation when not supplied |

### Routes

| Method | URL | Access |
|---|---|---|
| GET | `/` | public – landing page |
| GET, POST | `/teacher/login`, `/student/login` | public |
| POST | `/logout` | logged in |
| GET | `/health` | public – health check (JSON) |
| GET | `/dashboard`, `/students`, `/attendance`, `/reports`, `/reports/export.csv`, `/reports/session/<id>` | teacher |
| GET, POST | `/students/add`, `/students/account/<roll_no>`, `/teacher/profile` | teacher |
| POST | `/attendance/save`, `/students/<id>/delete`, `/reports/session/<id>/delete` | teacher |
| GET | `/student/dashboard`, `/student/attendance` | student |
| GET, POST | `/student/profile` | student |

---

## 5. Project folder structure

```
classroom-attendance-tracker/
├── app.py                 # Flask routes, logins, role protection, CSRF, student portal
├── database.py            # SQLite schema, migrations, password hashing, all queries
├── healthcheck.py         # Used by `podman run --health-cmd`
├── requirements.txt       # Flask, Werkzeug, Gunicorn (pinned versions)
├── Containerfile          # Custom image definition
├── .containerignore       # Files kept out of the image build
├── .gitignore             # Files kept out of Git (data, secrets, caches)
├── .env.example           # Configuration template with placeholders only
├── podman-verify.ps1      # Automated Podman verification script (PowerShell)
├── README.md
├── .vscode/
│   └── launch.json        # VS Code "Run and Debug" configurations
├── data/
│   └── .gitkeep           # attendance.db and secret_key are created here at run time (never committed)
├── static/
│   ├── css/style.css
│   └── js/main.js
├── templates/
│   ├── auth_base.html, landing.html, login.html          # public pages
│   ├── base.html, dashboard.html, students.html,         # teacher portal
│   │   add_student.html, attendance.html, reports.html,
│   │   session_detail.html, student_account.html, teacher_profile.html
│   ├── error.html                                        # 400 / 403 / 404 / 500
│   └── student/                                          # student portal
│       ├── base.html, dashboard.html, attendance.html, profile.html
└── tests/
    ├── helpers.py
    ├── test_app.py        # teacher features, attendance, reports, persistence
    └── test_portals.py    # both portals, security, database upgrade
```

### Database tables

| Table | Purpose | Key constraints |
|---|---|---|
| `students` | One row per student per subject | `UNIQUE(roll_no, subject)` |
| `sessions` | One class session | `UNIQUE(subject, section, session_date)` |
| `attendance` | Present/Absent per student per session | `UNIQUE(student_id, session_id)`, foreign keys with `ON DELETE CASCADE` |
| `teachers` | Teacher logins | `UNIQUE(username)`, scrypt `password_hash` |
| `student_accounts` | One login per roll number | `UNIQUE(roll_no)`, scrypt `password_hash` |

The schema version is stored in `PRAGMA user_version`. Upgrades only create new tables and never
delete existing records.

---

## 6. Installation and setup

### 6.1 Prerequisites

| Tool | How to install | Check |
|---|---|---|
| Python 3.10 or newer | <https://www.python.org/downloads/> – tick **"Add python.exe to PATH"** | `python --version` |
| Visual Studio Code | <https://code.visualstudio.com/> + the **Python** extension (Microsoft) | – |
| Git | <https://git-scm.com/download/win> | `git --version` |
| WSL 2 (for Podman) | Administrator PowerShell: `wsl --install --no-distribution`, then restart | `wsl --status` |
| Podman | `winget install RedHat.Podman` or Podman Desktop (<https://podman-desktop.io/>) | `podman --version` |

### 6.2 Get the project

Clone it (replace the placeholders) or open your existing folder:

```powershell
git clone https://github.com/<github-username>/<repository-name>.git
cd <repository-name>
```

In VS Code: **File → Open Folder…** → select the project folder → **Terminal → New Terminal**.

### 6.3 Create a virtual environment and install dependencies

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

If activation is blocked ("running scripts is disabled"), allow it for this terminal only:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
```

Then select the interpreter: **Ctrl+Shift+P → Python: Select Interpreter → .venv**.
(Gunicorn is skipped on Windows; it is only used inside the container.)

### 6.4 Secure first-time configuration (`.env`)

No real passwords or secrets are stored in this repository. Each installation sets its own:

```powershell
Copy-Item .env.example .env
python -c "import secrets; print(secrets.token_hex(32))"
notepad .env
```

In `.env`, paste the generated value into `SECRET_KEY` and replace every `REPLACE_WITH_...`
placeholder with your own values:

| Variable | Meaning |
|---|---|
| `SECRET_KEY` | Signs the session cookie. If omitted, a key is generated and stored in the data folder |
| `TEACHER_USERNAME`, `TEACHER_PASSWORD`, `TEACHER_NAME` | The first teacher account – created **only when the database has no teacher yet** |
| `DEFAULT_STUDENT_PASSWORD` | Initial password of the fictional sample students (and of existing students when an old database is upgraded) |
| `SEED_SAMPLE_DATA` | `1` loads 20 fictional students into a new database, `0` starts empty |
| `SESSION_COOKIE_SECURE` | `1` only when served over HTTPS |

Rules: one `KEY=value` per line, **no quotes**, no spaces around `=`. `.env` is ignored by Git and
by the image build.

**How accounts are created on a fresh installation**

1. On the first start with an empty data folder or a new volume, the app creates the tables.
2. If `SEED_SAMPLE_DATA=1`, it loads 20 fictional students (roll numbers `CS23A001`–`CS23A010` and
   `CS23B011`–`CS23B020`) whose Student Portal password is your `DEFAULT_STUDENT_PASSWORD`.
3. It creates one teacher from `TEACHER_USERNAME` / `TEACHER_PASSWORD`.
4. Log in as the teacher and change the password in **Profile** if needed. Give each student their
   own password with **Students → key icon**.

**No built-in passwords.** The source code contains no default passwords. If `TEACHER_PASSWORD` or
`DEFAULT_STUDENT_PASSWORD` is missing, empty or still a `REPLACE_WITH_...` placeholder, the app
generates a **random password** when it creates the accounts and prints it **once**:

```
========================================================================
FIRST-TIME SETUP - teacher account created
  Username : teacher
  Password : <random password>
  (generated because TEACHER_PASSWORD is not set)
Log in to the Teacher Portal and change it under Profile -> Change Password.
This message is shown only once.
========================================================================
```

* Locally, the message appears in the terminal running `python app.py`.
* In a container, read it with `podman logs attendance-app`.

The message is shown only when the accounts are created. Configured passwords are never printed.
A database that already has accounts keeps them – environment variables do not change existing
passwords. Use **Profile → Change Password** and **Students → key icon** for that.

---

## 7. Running the application locally

### 7.1 Load your `.env` settings into the terminal (optional, first run only)

Environment variables only matter when the database is created for the first time. To use your
`.env` locally, load it into the current PowerShell window:

```powershell
Get-Content .env | Where-Object { $_ -match '^[A-Z_]+=' } | ForEach-Object {
    $name, $value = $_ -split '=', 2
    Set-Item -Path "env:$name" -Value $value
}
```

The values stay only in this terminal window.

### 7.2 Start the application

```powershell
python app.py
```

Open <http://127.0.0.1:5000>, choose **Teacher** or **Student** and log in.
The database is created at `data\attendance.db`. Stop the server with **Ctrl+C**.

`python app.py` starts Flask's development server with **debug mode off**, listening only on
`127.0.0.1`. To turn on debug mode (automatic reload and detailed error pages) while developing:

```powershell
$env:FLASK_DEBUG = "1"
python app.py
```

Never enable debug mode on a server that other people can reach. You can also press **F5** in
VS Code and choose **"Flask: Attendance Tracker"**.

### 7.3 Start again with a fresh local database

Your local data lives in `data\attendance.db`. To start from scratch, **back it up first**
(section 18), stop the app, move the file out of the project folder and start the app again –
a new database is created.

---

## 8. Custom Containerfile explained

```dockerfile
FROM docker.io/library/python:3.12-slim
LABEL org.opencontainers.image.title="Classroom Attendance Tracker" ...
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 DATA_DIR=/app/data PORT=5000
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app.py database.py healthcheck.py ./
COPY templates/ templates/
COPY static/ static/
RUN mkdir -p /app/data
VOLUME ["/app/data"]
EXPOSE 5000
CMD ["gunicorn", "--bind", "0.0.0.0:5000", "--workers", "1", "--threads", "4",
     "--access-logfile", "-", "--error-logfile", "-", "app:app"]
```

| Instruction | Purpose |
|---|---|
| `FROM docker.io/library/python:3.12-slim` | Small official Python base image. The fully qualified name avoids Podman's short-name prompt |
| `LABEL` | Image metadata (title, description, version 2.0) shown by `podman image inspect` |
| `ENV PYTHONDONTWRITEBYTECODE=1` | No `.pyc` files in the container |
| `ENV PYTHONUNBUFFERED=1` | Logs appear immediately in `podman logs` |
| `ENV DATA_DIR=/app/data` | Tells `database.py` where to store `attendance.db` and `secret_key` |
| `ENV PORT=5000` | Port used by `healthcheck.py` |
| `WORKDIR /app` | All following instructions run in `/app` |
| `COPY requirements.txt` + `RUN pip install` | Dependencies are installed in their own layer, so code changes rebuild quickly |
| `COPY app.py database.py healthcheck.py templates/ static/` | Only the application files are copied – no tests, data, secrets or `.env` (see `.containerignore`) |
| `RUN mkdir -p /app/data` + `VOLUME ["/app/data"]` | The data directory that is mounted from a Podman volume for persistence |
| `EXPOSE 5000` | Documents the port the application listens on |
| `CMD gunicorn ... --workers 1 --threads 4` | Production WSGI server on all interfaces, port 5000. One worker suits SQLite (a single writer) |

The health check is supplied when the container is run (`--health-cmd`), because Podman's default
OCI image format does not store a `HEALTHCHECK` instruction.

---

## 9. Building the custom Podman image

First time only – start the Podman machine (a small Linux VM that runs the containers):

```powershell
podman machine init
podman machine start
podman info
```

Build the image:

```powershell
podman build -t attendance-tracker:2.0 -f Containerfile .
podman images
podman image inspect attendance-tracker:2.0 --format "{{.Labels}}"
podman history attendance-tracker:2.0
```

---

## 10. Creating and using a Podman container

### 10.1 Quick start (network + volume + container)

```powershell
podman network create attendance-net
podman volume create attendance-data
podman run -d --name attendance-app --network attendance-net -p 5000:5000 -v attendance-data:/app/data --env-file .env --restart unless-stopped --health-cmd "python /app/healthcheck.py" --health-interval 30s attendance-tracker:2.0
```

Open <http://localhost:5000> and log in with the teacher account from your `.env`. If you did not
set a password, read the generated one with `podman logs attendance-app` (section 6.4).

| Option | Meaning |
|---|---|
| `-d` | Run in the background |
| `--name attendance-app` | Fixed container name (also its DNS name on the network) |
| `--network attendance-net` | Attach to the user-defined network (section 11) |
| `-p 5000:5000` | Map host port 5000 to container port 5000 (section 13) |
| `-v attendance-data:/app/data` | Persistent named volume for the database (section 12) |
| `--env-file .env` | Your private configuration (section 6.4) |
| `--restart unless-stopped` | Restart the container if it exits unexpectedly |
| `--health-cmd`, `--health-interval` | Periodic health check using `healthcheck.py` |

### 10.2 Create and start as two separate steps

```powershell
podman create --name attendance-app --network attendance-net -p 5000:5000 -v attendance-data:/app/data --env-file .env --health-cmd "python /app/healthcheck.py" --health-interval 30s attendance-tracker:2.0
podman start attendance-app
podman ps
```

(`podman run` = `podman create` + `podman start`. Remove an existing `attendance-app` first if the
name is already in use – section 14.)

---

## 11. Podman networking

The application runs on a **user-defined bridge network**. Containers on the same network reach
each other by **container name** through Podman's built-in DNS.

```powershell
podman network create attendance-net
podman network ls
podman network inspect attendance-net
podman inspect attendance-app --format "{{json .NetworkSettings.Networks}}"
```

Container-to-container test – a temporary container on the same network calls the app by name:

```powershell
podman run --rm --network attendance-net docker.io/curlimages/curl:8.10.1 -s http://attendance-app:5000/health
```

Expected output: `{"database":"/app/data/attendance.db","status":"ok"}`

The same request from a container **not** on the network fails to resolve `attendance-app`, which
shows the network isolation:

```powershell
podman run --rm docker.io/curlimages/curl:8.10.1 -s --max-time 5 http://attendance-app:5000/health
```

---

## 12. Persistent storage

### 12.1 Named volume (recommended)

The SQLite database lives in `/app/data` inside the container, which is mounted from the named
volume `attendance-data`. The volume exists independently of any container.

```powershell
podman volume create attendance-data
podman volume ls
podman volume inspect attendance-data
podman exec attendance-app ls -l /app/data
```

`attendance.db` (all records) and `secret_key` (session key) are stored in the volume.

### 12.2 Persistence demonstration

1. Log in as teacher, add a test student with a portal password, and mark attendance for the class.
2. Delete the container – the volume remains:

   ```powershell
   podman rm -f attendance-app
   podman ps -a
   podman volume ls
   ```

3. Create a **new** container with the **same volume**:

   ```powershell
   podman run -d --name attendance-app --network attendance-net -p 5000:5000 -v attendance-data:/app/data --env-file .env --restart unless-stopped --health-cmd "python /app/healthcheck.py" --health-interval 30s attendance-tracker:2.0
   ```

4. Log in again – the test student, their login and the attendance are still there.

### 12.3 Bind mount (alternative)

A bind mount stores the data in a normal Windows folder instead of a Podman-managed volume:

```powershell
New-Item -ItemType Directory -Force C:\attendance-data
podman run -d --name attendance-bind -p 5001:5000 -v C:\attendance-data:/app/data --env-file .env attendance-tracker:2.0
```

Open <http://localhost:5001>. The files appear in `C:\attendance-data`. The named volume is
recommended for SQLite because Windows folders shared into the Podman machine are slower and file
locking is less reliable. Remove the example with `podman rm -f attendance-bind`.

---

## 13. Port mapping and localhost access

`-p <host port>:<container port>` publishes the container's port 5000 on the Windows host.

```powershell
podman port attendance-app
Invoke-RestMethod http://localhost:5000/health
```

* Application: <http://localhost:5000>
* Health check: <http://localhost:5000/health>

If port 5000 is busy on the host, publish another host port – the container port stays 5000:

```powershell
podman run -d --name attendance-app --network attendance-net -p 8080:5000 -v attendance-data:/app/data --env-file .env attendance-tracker:2.0
```

Then open <http://localhost:8080>.

---

## 14. Container lifecycle management

| Action | Command |
|---|---|
| Create | `podman create --name attendance-app ... attendance-tracker:2.0` (full command in 10.2) |
| Start | `podman start attendance-app` |
| List running / all | `podman ps` / `podman ps -a` |
| Stop | `podman stop attendance-app` |
| Restart | `podman restart attendance-app` |
| Inspect (all details) | `podman inspect attendance-app` |
| Status only | `podman inspect attendance-app --format "{{.State.Status}}"` |
| Health status | `podman inspect attendance-app --format "{{.State.Health.Status}}"` |
| Run health check now | `podman healthcheck run attendance-app` |
| Mounts | `podman inspect attendance-app --format "{{json .Mounts}}"` |
| View logs | `podman logs attendance-app` |
| Follow logs | `podman logs -f attendance-app` (Ctrl+C to stop following) |
| Last 50 log lines | `podman logs --tail 50 attendance-app` |
| Resource usage | `podman stats --no-stream attendance-app` |
| Processes | `podman top attendance-app` |
| Shell inside | `podman exec -it attendance-app /bin/bash` (`exit` to leave) |
| Remove (stopped) | `podman rm attendance-app` |
| Remove (running) | `podman rm -f attendance-app` |

Removing a container never removes the `attendance-data` volume. Only
`podman volume rm attendance-data` deletes the data – **back up first** (section 18).

---

## 15. Image management: build, tag, push, pull

Replace `<dockerhub-username>` with your Docker Hub username in every command.

### 15.1 Build and list

```powershell
podman build -t attendance-tracker:2.0 -f Containerfile .
podman images
```

### 15.2 Create a Docker Hub account and access token

1. Sign up at <https://hub.docker.com/>.
2. Create an access token: **Account settings → Personal access tokens → Generate new token**
   (permission **Read & Write**). Copy it – Docker Hub shows it only once.
   Use this token instead of your account password when logging in.

### 15.3 Log in securely

```powershell
podman login docker.io
```

Enter your username, and paste the **access token** at the `Password:` prompt. The token is not
shown on screen and is not saved in your PowerShell history. Never put a password or token directly
in a command, script or file in this repository.

### 15.4 Tag

```powershell
podman tag attendance-tracker:2.0 docker.io/<dockerhub-username>/attendance-tracker:2.0
podman tag attendance-tracker:2.0 docker.io/<dockerhub-username>/attendance-tracker:latest
podman images
```

### 15.5 Push

```powershell
podman push docker.io/<dockerhub-username>/attendance-tracker:2.0
podman push docker.io/<dockerhub-username>/attendance-tracker:latest
```

Docker Hub creates the repository on the first push. Open
`https://hub.docker.com/r/<dockerhub-username>/attendance-tracker` and confirm under
**Settings → Visibility** that it is **Public**.

The image contains only application code. `.containerignore` keeps `data/`, databases, `.env` and
`secret_key` out of the build, so no records or secrets are published.

### 15.6 Pull and run the published image

```powershell
podman logout docker.io
podman rmi docker.io/<dockerhub-username>/attendance-tracker:2.0 docker.io/<dockerhub-username>/attendance-tracker:latest
podman pull docker.io/<dockerhub-username>/attendance-tracker:2.0
podman run -d --name attendance-hub-test -p 5050:5000 -v attendance-hub-test:/app/data --env-file .env docker.io/<dockerhub-username>/attendance-tracker:2.0
```

Open <http://localhost:5050>. Logging out first proves that the public image can be pulled without
credentials. `podman rmi` here removes only the registry tags; layers that are still used by your
local image may show *"already exists"* during the pull. Pull on a second computer for a full
download.

Clean up the test container and its volume:

```powershell
podman rm -f attendance-hub-test
podman volume rm attendance-hub-test
```

### 15.7 Other image commands

```powershell
podman image inspect attendance-tracker:2.0
podman history attendance-tracker:2.0
podman rmi attendance-tracker:2.0
podman image prune
```

---

## 16. Container troubleshooting

### 16.1 Diagnostic commands

```powershell
podman ps -a
podman logs --tail 50 attendance-app
podman inspect attendance-app --format "{{.State.Status}} {{.State.ExitCode}} {{.State.Error}}"
podman healthcheck run attendance-app
podman port attendance-app
podman exec -it attendance-app ls -l /app/data
podman machine list
```

### 16.2 Common problems

| Symptom | Cause | Fix |
|---|---|---|
| `The Windows Subsystem for Linux is not installed` | WSL 2 missing | Administrator PowerShell: `wsl --install --no-distribution`, restart |
| `Cannot connect to Podman` / `unable to connect to Podman socket` | Podman machine stopped | `podman machine start` (first time: `podman machine init`) |
| `podman` is not recognized | Terminal opened before installing Podman | Open a new terminal or restart VS Code |
| `short-name ... did not resolve` | Unqualified image name | Use full names such as `docker.io/library/python:3.12-slim` (already used in the Containerfile) |
| `no such file or directory: Containerfile` | Wrong folder | `cd` into the project folder |
| `the container name "attendance-app" is already in use` | Old container exists | `podman rm -f attendance-app` (the volume is kept) |
| `address already in use` / port 5000 busy | Another program uses port 5000 | Use `-p 8080:5000` and open <http://localhost:8080> |
| `network attendance-net not found` / `no such volume` | Not created yet | `podman network create attendance-net`, `podman volume create attendance-data` |
| `open .env: no such file or directory` | `.env` missing | `Copy-Item .env.example .env` and fill it in (section 6.4) |
| Teacher login fails after editing `.env` | The volume already has a teacher; `.env` is used only on the first start | Log in with the existing account, or use a new volume |
| Teacher password unknown on a new installation | No `TEACHER_PASSWORD` was set, so a random one was generated | `podman logs attendance-app` (or the `python app.py` terminal) shows the **FIRST-TIME SETUP** message |
| Data missing after re-creating the container | Started without `-v attendance-data:/app/data` | Always use the same volume (check `podman inspect attendance-app --format "{{json .Mounts}}"`) |
| Health stays `starting` | Checks run every 30 seconds | Wait, or run `podman healthcheck run attendance-app` |
| Container exits immediately | Python error at start-up | `podman logs attendance-app` |
| Page without styling or charts | No internet (Bootstrap and Chart.js come from a CDN) | Connect to the internet |
| `unauthorized` / `denied` on push | Not logged in or wrong username in the tag | `podman login docker.io`; tag as `docker.io/<dockerhub-username>/...` |
| `manifest unknown` on pull | That tag was never pushed | Push the tag first, or pull `:latest` |
| **"Request could not be processed" (400)** | Form opened before a restart (CSRF token expired) | Reload the page and submit again |
| **"Too many failed attempts"** | 5 wrong passwords in 5 minutes | Wait 5 minutes or restart the container |
| **"Access denied" (403)** | A student opened a teacher page | Expected behaviour – use the correct portal |
| `sqlite3.OperationalError: database is locked` | Another program has the database open | Close it (e.g. DB Browser for SQLite) |

---

## 17. Testing

### 17.1 Automated application tests

The tests create their own temporary databases and set their own test-only accounts, so they never
touch `data\` and work even if your `.env` is loaded in the terminal:

```powershell
python -m unittest discover -s tests -v
```

Expected result: `Ran 42 tests ... OK`.

| Scenario | Test |
|---|---|
| Landing page shows Teacher / Student selection | `test_01_landing_shows_role_selection` |
| Teacher login and dashboard | `test_02_teacher_login_and_dashboard` |
| Teacher adds a student (hashed password, policy) | `test_03_...`, `test_03b_...` |
| Teacher marks attendance | `test_04_teacher_marks_attendance` |
| Student login | `test_05_student_login` |
| Student sees only own attendance | `test_06_student_views_only_own_attendance` |
| Percentage and "classes needed" calculations | `test_07_percentage_calculations` |
| Low-attendance warning | `test_08_low_attendance_warning` |
| Students blocked from teacher routes | `test_09_student_cannot_use_teacher_routes` |
| Students cannot see other students | `test_10_student_cannot_view_other_students` |
| Logout | `test_11_logout` |
| Existing records preserved on upgrade | `test_12_existing_records_preserved` |
| CSRF, lockout, open redirect, cookie flags | `SecurityTests` |
| No built-in passwords: random first-time passwords, placeholders rejected | `FirstRunSetupTests` |
| Duplicate prevention, CSV export, validation, restart | `test_app.py` |

### 17.2 Automated container verification

`podman-verify.ps1` builds the image, runs a container and checks the landing page, both logins,
the 403 protection, the volume mount, container-to-container DNS and data persistence after deleting
and re-creating the container. By default it uses its **own test names** (container `audit-app`,
volume `audit-data`, network `audit-net`, port 5050), so your real container and volume are never
touched:

```powershell
powershell -ExecutionPolicy Bypass -File .\podman-verify.ps1
```

Each run creates a new test volume with freshly generated test passwords, which it prints at the
start. Every check prints PASS or FAIL. Remove the test resources afterwards (and before running it
again):

```powershell
podman rm -f audit-app
podman volume rm audit-data
podman network rm audit-net
```

### 17.3 Manual test checklist

- [ ] Landing page shows both portals
- [ ] Teacher login, dashboard numbers and charts
- [ ] Add a student with a portal password
- [ ] Mark attendance and save the same date again (updates, no duplicates)
- [ ] Reports: threshold filter and CSV download
- [ ] Student login shows only that student's data and the 75 % warning when below
- [ ] Student opening `/dashboard` gets "Access denied"
- [ ] Logout returns to the landing page
- [ ] Persistence demonstration (section 12.2)

---

## 18. Data backup and restoration

Store backups **outside** the project folder (`*.db` files are ignored by Git anyway).

### 18.1 Back up the container database (no downtime)

SQLite's backup API copies a consistent snapshot while the application keeps running:

```powershell
New-Item -ItemType Directory -Force "$HOME\attendance-backups"
podman exec attendance-app python -c "import sqlite3; s=sqlite3.connect('/app/data/attendance.db'); d=sqlite3.connect('/app/data/backup.db'); s.backup(d); d.close(); s.close()"
podman cp attendance-app:/app/data/backup.db "$HOME\attendance-backups\attendance-$(Get-Date -Format yyyyMMdd-HHmm).db"
podman exec attendance-app rm /app/data/backup.db
Get-ChildItem "$HOME\attendance-backups"
```

### 18.2 Restore into the container

Replace the file name with the backup you want to restore:

```powershell
podman cp "$HOME\attendance-backups\attendance-20260101-0900.db" attendance-app:/app/data/restore.db
podman exec attendance-app python -c "import sqlite3; s=sqlite3.connect('/app/data/restore.db'); d=sqlite3.connect('/app/data/attendance.db'); s.backup(d); d.close(); s.close()"
podman exec attendance-app rm /app/data/restore.db
podman restart attendance-app
```

### 18.3 Local (non-container) database

Stop `python app.py` first, then copy the file:

```powershell
New-Item -ItemType Directory -Force "$HOME\attendance-backups"
Copy-Item .\data\attendance.db "$HOME\attendance-backups\attendance-local-$(Get-Date -Format yyyyMMdd-HHmm).db"
```

To restore, stop the app and copy a backup back to `data\attendance.db`.

`secret_key` only signs login cookies; if it is lost, a new one is generated and users simply log in
again. Backups contain student data and password hashes – keep them private and never commit them.

---

## 19. GitHub repository

**Repository:** `https://github.com/<github-username>/<repository-name>`
**Container image:** `docker.io/<dockerhub-username>/attendance-tracker:2.0`
*(Replace the placeholders after creating them.)*

### 19.1 What is uploaded and what is not

| Uploaded | Never uploaded (ignored by `.gitignore`) |
|---|---|
| `app.py`, `database.py`, `healthcheck.py`, `requirements.txt` | `data/attendance.db`, `data/secret_key` (only `data/.gitkeep` is kept) |
| `Containerfile`, `.containerignore`, `.gitignore`, `.env.example` | `.env` and any `.env.*` except `.env.example` |
| `templates/`, `static/`, `tests/`, `podman-verify.ps1` | `*.db`, `*.sqlite*`, SQLite journals, `backups/`, `*.bak` |
| `README.md`, `.vscode/launch.json`, `data/.gitkeep` | `.venv/`, `venv/`, `__pycache__/`, `*.pyc`, `*.tar`, `*.log` |

### 19.2 Create the repository and upload the code

1. Go to <https://github.com/new>, enter a repository name, choose **Public**, and do **not** add a
   README, `.gitignore` or licence (the project already has them). Click **Create repository**.
2. Set your Git identity once on this computer:

   ```powershell
   git config --global user.name "<your name>"
   git config --global user.email "<your email>"
   ```

3. Initialise Git **inside the project folder** and check what will be uploaded:

   ```powershell
   git init
   git branch -M main
   git add .
   git status
   git check-ignore -v data/attendance.db data/secret_key .env
   ```

   `git status` must not list any `.db`, `secret_key` or `.env` file. `git check-ignore` prints the
   `.gitignore` rule that excludes each of them (a file that does not exist yet is still shown).

4. Commit and push:

   ```powershell
   git commit -m "Classroom Attendance Tracker - Flask, SQLite and Podman"
   git remote add origin https://github.com/<github-username>/<repository-name>.git
   git push -u origin main
   ```

   On the first push, Git Credential Manager opens a browser window to sign in to GitHub.

5. On GitHub, add the Docker Hub image link to the repository's **About** box.

### 19.3 Verify from a fresh clone

```powershell
cd $HOME
git clone https://github.com/<github-username>/<repository-name>.git attendance-clone-test
cd attendance-clone-test
podman build -t attendance-tracker:clone-test -f Containerfile .
```

If the clone builds, the repository is complete.

### 19.4 If a secret or database is committed by mistake

Remove it with `git rm --cached <file>`, commit and push, then treat it as leaked: change the
affected passwords and, for `secret_key`, delete it from the data folder or volume and restart the
app (a new key is generated and all users log in again).

---

## 20. Screenshots and evidence checklist

| No. | Evidence | How to produce it |
|---|---|---|
| 1 | Landing page with both portals | Browser: <http://localhost:5000> |
| 2 | Teacher dashboard with charts | Teacher login |
| 3 | Marking attendance and the success message | Mark Attendance page |
| 4 | Reports with threshold filter and CSV download | Reports page |
| 5 | Student dashboard with 75 % warning | Student login |
| 6 | "Access denied" when a student opens `/dashboard` | Student login, then visit `/dashboard` |
| 7 | Containerfile | VS Code |
| 8 | Image build output | `podman build -t attendance-tracker:2.0 -f Containerfile .` |
| 9 | Image list | `podman images` |
| 10 | Network created and inspected | `podman network ls`, `podman network inspect attendance-net` |
| 11 | Volume created and inspected | `podman volume ls`, `podman volume inspect attendance-data` |
| 12 | Running container with port mapping | `podman ps` (shows `0.0.0.0:5000->5000/tcp`), `podman port attendance-app` |
| 13 | Container logs | `podman logs attendance-app` |
| 14 | Inspect output: state, health, mounts | Commands from section 14 |
| 15 | Stop / start / restart | Section 14 commands followed by `podman ps -a` |
| 16 | Container-to-container DNS | `curl` test from section 11 |
| 17 | Persistence: delete → re-create → data still there | Section 12.2 |
| 18 | Registry login, tag and push | Section 15.3–15.5 output |
| 19 | Public Docker Hub page | Browser |
| 20 | Pull and run the published image | Section 15.6 |
| 21 | Automated tests passing | `python -m unittest discover -s tests -v` |
| 22 | Troubleshooting example | e.g. port conflict fixed with `-p 8080:5000` |
| 23 | GitHub repository page | Browser |

---

## 21. Conclusion

The Classroom Attendance Tracker shows a complete path from a working web application to a
containerised deployment. The Flask and SQLite application provides separate, secure teacher and
student portals with hashed passwords, role-based access and automatic attendance calculations.
A custom Containerfile packages it into a Podman image that runs on a user-defined network, keeps
its data on a persistent volume, is reachable through port mapping, and is managed and diagnosed
with standard Podman lifecycle commands. Sensitive configuration is kept out of the source code and
the image through environment variables, `.gitignore` and `.containerignore`, so the project can be
shared publicly on GitHub and a container registry.
