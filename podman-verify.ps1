<#
.SYNOPSIS
  Builds the Classroom Attendance Tracker image and verifies it inside Podman
  (testing scenarios 13 and 14 of the project brief).

  Checks:
    - the image builds from the Containerfile
    - the container starts and /health is OK on port 5000
    - landing page, teacher login and student login work inside the container
    - a student is blocked (403) from teacher pages
    - the database lives on the named volume at /app/data
    - container-to-container networking on the user-defined network
    - data survives deleting and re-creating the container

.USAGE
  From the project folder (where the Containerfile is):
      powershell -ExecutionPolicy Bypass -File .\podman-verify.ps1

  By default it uses its own test resources (container audit-app, volume audit-data,
  network audit-net, port 5050), so your real attendance-app container and attendance-data
  volume are never touched. Each run creates a NEW test volume with freshly generated test
  passwords (printed at the start). Remove a previous test volume first:
      podman rm -f audit-app; podman volume rm audit-data

  The script adds a temporary student (CS23Z999) to prove persistence and deletes it at the end.
#>
param(
    [string]$Image = "attendance-tracker:2.0",
    [string]$Name = "audit-app",
    [string]$Volume = "audit-data",
    [string]$Network = "audit-net",
    [int]$Port = 5050,
    [string]$TeacherUser = "teacher",
    [string]$TeacherPassword = "",
    [string]$StudentRoll = "CS23A001",
    [string]$StudentPassword = ""
)

$ErrorActionPreference = "Continue"
$Base = "http://localhost:$Port"
$MarkerRoll = "CS23Z999"
$script:Failures = 0

# Random test-only password (letters + digits) for the throw-away test volume
function New-TestPassword { return "Tv" + [guid]::NewGuid().ToString("N").Substring(0, 14) + "9" }

$GeneratedPasswords = (-not $TeacherPassword) -or (-not $StudentPassword)
if (-not $TeacherPassword) { $TeacherPassword = New-TestPassword }
if (-not $StudentPassword) { $StudentPassword = New-TestPassword }
$MarkerPassword = New-TestPassword

function Step([string]$Message) { Write-Host ""; Write-Host "==> $Message" -ForegroundColor Cyan }
function Pass([string]$Message) { Write-Host "  PASS  $Message" -ForegroundColor Green }
function Fail([string]$Message) { Write-Host "  FAIL  $Message" -ForegroundColor Red; $script:Failures++ }
function Stop-Script([string]$Message) { Write-Host "ERROR: $Message" -ForegroundColor Red; exit 1 }
function Check([bool]$Condition, [string]$Message) { if ($Condition) { Pass $Message } else { Fail $Message } }

function Wait-Healthy {
    for ($i = 0; $i -lt 40; $i++) {
        try {
            $r = Invoke-RestMethod -Uri "$Base/health" -TimeoutSec 3
            if ($r.status -eq "ok") { return $true }
        } catch { }
        Start-Sleep -Seconds 2
    }
    return $false
}

function Start-App {
    # The account variables only take effect when the volume is new (first start)
    podman run -d --name $Name --network $Network -p "${Port}:5000" -v "${Volume}:/app/data" -e "TEACHER_USERNAME=$TeacherUser" -e "TEACHER_PASSWORD=$TeacherPassword" -e "DEFAULT_STUDENT_PASSWORD=$StudentPassword" -e "SEED_SAMPLE_DATA=1" --restart unless-stopped --health-cmd "python /app/healthcheck.py" --health-interval 30s $Image | Out-Null
    if ($LASTEXITCODE -ne 0) { Stop-Script "podman run failed. Is port $Port already in use?" }
    if (-not (Wait-Healthy)) {
        podman logs --tail 30 $Name
        Stop-Script "The app did not become healthy on $Base."
    }
}

function Invoke-Python([string]$Code) {
    $out = podman exec $Name python -c $Code
    if ($LASTEXITCODE -ne 0) { return $null }
    return (($out | Select-Object -Last 1) -as [string]).Trim()
}

function Get-Counts {
    $code = "import sqlite3; c=sqlite3.connect('/app/data/attendance.db'); print(','.join(str(c.execute('SELECT COUNT(*) FROM ' + t).fetchone()[0]) for t in ('students', 'sessions', 'attendance', 'student_accounts')))"
    return Invoke-Python $code
}

function Get-MarkerCount {
    $code = "import sqlite3; c=sqlite3.connect('/app/data/attendance.db'); print(c.execute('SELECT COUNT(*) FROM students WHERE roll_no=?', ('$MarkerRoll',)).fetchone()[0])"
    return [int](Invoke-Python $code)
}

function Get-CsrfToken([string]$Html) {
    return [regex]::Match($Html, 'name="csrf_token" value="([^"]+)"').Groups[1].Value
}

# Log in through the real HTML form (GET the page for its CSRF token, then POST)
function New-WebLogin([string]$Path, [hashtable]$Fields) {
    $page = Invoke-WebRequest -Uri "$Base$Path" -SessionVariable web -UseBasicParsing
    $Fields["csrf_token"] = Get-CsrfToken $page.Content
    $resp = Invoke-WebRequest -Uri "$Base$Path" -Method Post -Body $Fields -WebSession $web -UseBasicParsing
    return @{ Session = $web; Content = $resp.Content }
}

function Get-StatusCode([string]$Path, $WebSession) {
    try {
        return [int](Invoke-WebRequest -Uri "$Base$Path" -WebSession $WebSession -UseBasicParsing).StatusCode
    } catch {
        if ($_.Exception.Response) { return [int]$_.Exception.Response.StatusCode }
        return -1
    }
}

# --------------------------------------------------------------------------- #
Step "Checking Podman"
podman info --format "{{.Host.Os}}" | Out-Null
if ($LASTEXITCODE -ne 0) { Stop-Script "Podman is not reachable. Run 'podman machine start' (Windows/macOS) and try again." }
Pass "Podman is running"
if (-not (Test-Path ".\Containerfile")) { Stop-Script "Run this script from the project folder (Containerfile not found)." }

Step "Building image $Image"
podman build -t $Image -f Containerfile .
if ($LASTEXITCODE -ne 0) { Stop-Script "Image build failed." }
Pass "Image built"

Step "Preparing volume '$Volume' and network '$Network'"
podman volume exists $Volume
if ($LASTEXITCODE -eq 0 -and $GeneratedPasswords) {
    Stop-Script ("Volume '$Volume' already exists, so its accounts were created by an earlier run. " +
                 "Remove the test resources first:  podman rm -f $Name; podman volume rm $Volume  " +
                 "(or pass -TeacherPassword and -StudentPassword for that volume).")
}
if ($LASTEXITCODE -ne 0) { podman volume create $Volume | Out-Null }
if ($GeneratedPasswords) {
    Write-Host "  Test accounts for this run (test volume only):"
    Write-Host "    Teacher : $TeacherUser / $TeacherPassword"
    Write-Host "    Student : $StudentRoll / $StudentPassword"
}
podman network exists $Network
if ($LASTEXITCODE -ne 0) { podman network create $Network | Out-Null }
podman container exists $Name
if ($LASTEXITCODE -eq 0) { podman rm -f $Name | Out-Null }
Pass "Volume and network ready"

Step "Starting container $Name on port $Port"
Start-App
Pass "Container is running and /health returns ok"

$mounts = podman inspect $Name --format "{{range .Mounts}}{{.Name}}:{{.Destination}} {{end}}"
Check ($mounts -match [regex]::Escape("${Volume}:/app/data")) "Volume $Volume is mounted at /app/data"

Step "Scenario 13: application works inside Podman"
$landing = Invoke-WebRequest -Uri "$Base/" -UseBasicParsing
Check ($landing.Content -match "Smart, Simple and Transparent Attendance Management") "Landing page with Teacher / Student selection"

$teacher = New-WebLogin "/teacher/login" @{ username = $TeacherUser; password = $TeacherPassword }
Check ($teacher.Content -match "Total Students") "Teacher login opens the dashboard"

if ((Get-MarkerCount) -eq 0) {
    $addPage = Invoke-WebRequest -Uri "$Base/students/add" -WebSession $teacher.Session -UseBasicParsing
    $body = @{
        csrf_token = Get-CsrfToken $addPage.Content
        roll_no = $MarkerRoll; name = "Persistence Check"
        subject = "Linux and Container Technologies"; section = "Z"
        password = $MarkerPassword; confirm_password = $MarkerPassword; action = "save"
    }
    Invoke-WebRequest -Uri "$Base/students/add" -Method Post -Body $body -WebSession $teacher.Session -UseBasicParsing | Out-Null
}
Check ((Get-MarkerCount) -eq 1) "Teacher added test student $MarkerRoll"

$student = New-WebLogin "/student/login" @{ roll_no = $StudentRoll; password = $StudentPassword }
Check ($student.Content -match "Welcome, ") "Student $StudentRoll can log in and sees their dashboard"
Check ((Get-StatusCode "/reports" $student.Session) -eq 403) "Student is blocked from teacher pages (403)"

$dns = podman run --rm --network $Network docker.io/curlimages/curl:8.10.1 -s "http://${Name}:5000/health"
Check ($dns -match '"status":"ok"') "Another container reaches the app by name on network $Network"

Step "Scenario 14: data survives container deletion and re-creation"
$before = Get-Counts
Write-Host "  Rows before (students, sessions, attendance, logins): $before"
podman rm -f $Name | Out-Null
podman container exists $Name
Check ($LASTEXITCODE -ne 0) "Container deleted"
podman volume exists $Volume
Check ($LASTEXITCODE -eq 0) "Volume $Volume still exists"

Start-App
$after = Get-Counts
Write-Host "  Rows after  (students, sessions, attendance, logins): $after"
Check ($before -and $before -eq $after) "All rows are still there after re-creating the container"
Check ((Get-MarkerCount) -eq 1) "Test student $MarkerRoll survived"
$marker = New-WebLogin "/student/login" @{ roll_no = $MarkerRoll; password = $MarkerPassword }
Check ($marker.Content -match "Welcome, Persistence Check") "Test student can still log in with the same password"

Step "Cleaning up the test student"
Invoke-Python "import sqlite3; c=sqlite3.connect('/app/data/attendance.db'); c.execute('PRAGMA foreign_keys = ON'); c.execute('DELETE FROM students WHERE roll_no=?', ('$MarkerRoll',)); c.commit(); print('ok')" | Out-Null
Check ((Get-MarkerCount) -eq 0) "Test student removed (your own data is untouched)"

Write-Host ""
if ($script:Failures -eq 0) {
    Write-Host "All checks passed. The app is running at $Base" -ForegroundColor Green
    exit 0
}
Write-Host "$($script:Failures) check(s) failed. See the messages above and 'podman logs $Name'." -ForegroundColor Red
exit 1
