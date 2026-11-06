/* =====================================================================
   Classroom Attendance Tracker - front-end behaviour
   - confirmation dialogs for delete buttons
   - Bootstrap form validation
   - subject -> section dropdown filtering
   - "All Present / All Absent" buttons and live counters
   - Chart.js charts for the dashboard and reports pages
   ===================================================================== */
(function () {
  "use strict";

  const BLUE = "#2563eb";
  const BLUE_LIGHT = "rgba(37, 99, 235, 0.15)";
  const GREEN = "#16a34a";
  const RED = "#dc2626";

  function readJson(id) {
    const el = document.getElementById(id);
    if (!el) return null;
    try {
      return JSON.parse(el.textContent);
    } catch (err) {
      console.error("Could not parse JSON from #" + id, err);
      return null;
    }
  }

  /* ---------- Confirm before destructive actions ---------- */
  function initConfirmForms() {
    document.querySelectorAll("form[data-confirm]").forEach(function (form) {
      form.addEventListener("submit", function (event) {
        if (!window.confirm(form.dataset.confirm)) {
          event.preventDefault();
        }
      });
    });
  }

  /* ---------- Bootstrap client-side validation ---------- */
  function initValidation() {
    document.querySelectorAll("form.needs-validation").forEach(function (form) {
      form.addEventListener("submit", function (event) {
        if (!form.checkValidity()) {
          event.preventDefault();
          event.stopPropagation();
        }
        form.classList.add("was-validated");
      });
    });
  }

  /* ---------- Auto-hide success/info alerts ---------- */
  function initAlerts() {
    document.querySelectorAll(".alert-success, .alert-info").forEach(function (alertEl) {
      if (!alertEl.classList.contains("alert-dismissible")) return;
      setTimeout(function () {
        if (window.bootstrap && document.body.contains(alertEl)) {
          bootstrap.Alert.getOrCreateInstance(alertEl).close();
        }
      }, 6000);
    });
  }

  /* ---------- Attendance page: section list follows chosen subject ---------- */
  function initClassPicker() {
    const classMap = readJson("class-map");
    const subjectSel = document.querySelector("#class-picker #subject");
    const sectionSel = document.querySelector("#class-picker #section");
    if (!classMap || !subjectSel || !sectionSel) return;

    function fillSections() {
      const keep = sectionSel.value || sectionSel.dataset.selected || "";
      const sections = classMap[subjectSel.value] || [];
      sectionSel.innerHTML = '<option value="">Select</option>';
      sections.forEach(function (sec) {
        const opt = document.createElement("option");
        opt.value = sec;
        opt.textContent = sec;
        if (sec === keep) opt.selected = true;
        sectionSel.appendChild(opt);
      });
      if (sections.length === 1) sectionSel.value = sections[0];
    }

    subjectSel.addEventListener("change", function () {
      sectionSel.value = "";
      sectionSel.dataset.selected = "";
      fillSections();
    });
    fillSections();
  }

  /* ---------- Attendance page: mark all + live counters ---------- */
  function initAttendanceForm() {
    const form = document.getElementById("attendance-form");
    if (!form) return;
    const presentBadge = document.getElementById("present-count");
    const absentBadge = document.getElementById("absent-count");

    function updateCounts() {
      const present = form.querySelectorAll('input.status-input[value="Present"]:checked').length;
      const absent = form.querySelectorAll('input.status-input[value="Absent"]:checked').length;
      presentBadge.textContent = present + " present";
      absentBadge.textContent = absent + " absent";
    }

    form.querySelectorAll("[data-mark-all]").forEach(function (btn) {
      btn.addEventListener("click", function () {
        const value = btn.dataset.markAll;
        form.querySelectorAll('input.status-input[value="' + value + '"]').forEach(function (radio) {
          radio.checked = true;
        });
        updateCounts();
      });
    });

    form.addEventListener("change", updateCounts);
    updateCounts();
  }

  /* ---------- Charts ---------- */
  function initCharts() {
    if (typeof Chart === "undefined") {
      // CDN not reachable (offline). Pages still work without charts.
      document.querySelectorAll(".chart-box").forEach(function (box) {
        box.innerHTML = '<p class="empty-state">Charts need an internet connection to load Chart.js.</p>';
      });
      return;
    }
    Chart.defaults.font.family = getComputedStyle(document.body).fontFamily;
    Chart.defaults.color = "#4b5b73";
    Chart.defaults.maintainAspectRatio = false;

    const percentAxis = {
      beginAtZero: true,
      max: 100,
      ticks: { callback: function (v) { return v + "%"; } },
      grid: { color: "#eef2f8" },
    };

    const dash = readJson("dashboard-data");
    if (dash) {
      const classCanvas = document.getElementById("classChart");
      if (classCanvas && dash.classes.length) {
        new Chart(classCanvas, {
          type: "bar",
          data: {
            labels: dash.classes.map(function (c) { return c.label; }),
            datasets: [{
              label: "Attendance %",
              data: dash.classes.map(function (c) { return c.percentage; }),
              backgroundColor: dash.classes.map(function (c) { return c.percentage < 75 ? RED : BLUE; }),
              borderRadius: 6,
              maxBarThickness: 48,
            }],
          },
          options: {
            plugins: { legend: { display: false } },
            scales: { y: percentAxis, x: { grid: { display: false } } },
          },
        });
      }

      const ratioCanvas = document.getElementById("ratioChart");
      if (ratioCanvas && (dash.present + dash.absent) > 0) {
        new Chart(ratioCanvas, {
          type: "doughnut",
          data: {
            labels: ["Present", "Absent"],
            datasets: [{ data: [dash.present, dash.absent], backgroundColor: [BLUE, "#cbd5e1"], borderWidth: 0 }],
          },
          options: { cutout: "68%", plugins: { legend: { position: "bottom" } } },
        });
      }

      const trendCanvas = document.getElementById("trendChart");
      if (trendCanvas && dash.trend.length) {
        new Chart(trendCanvas, {
          type: "line",
          data: {
            labels: dash.trend.map(function (t) { return t.day; }),
            datasets: [{
              label: "Attendance %",
              data: dash.trend.map(function (t) { return t.percentage; }),
              borderColor: BLUE,
              backgroundColor: BLUE_LIGHT,
              fill: true,
              tension: 0.35,
              pointRadius: 4,
              pointBackgroundColor: BLUE,
            }],
          },
          options: {
            plugins: { legend: { display: false } },
            scales: { y: percentAxis, x: { grid: { display: false } } },
          },
        });
      }
    }

    const report = readJson("report-data");
    const studentCanvas = document.getElementById("studentChart");
    if (report && studentCanvas && report.values.length) {
      new Chart(studentCanvas, {
        type: "bar",
        data: {
          labels: report.labels,
          datasets: [
            {
              type: "bar",
              label: "Attendance %",
              data: report.values,
              backgroundColor: report.values.map(function (v) { return v < report.threshold ? RED : GREEN; }),
              borderRadius: 4,
              maxBarThickness: 28,
            },
            {
              type: "line",
              label: "Threshold (" + report.threshold + "%)",
              data: report.values.map(function () { return report.threshold; }),
              borderColor: BLUE,
              borderDash: [6, 4],
              borderWidth: 2,
              pointRadius: 0,
              fill: false,
            },
          ],
        },
        options: {
          plugins: {
            legend: { position: "bottom" },
            tooltip: {
              callbacks: {
                title: function (items) {
                  const i = items[0].dataIndex;
                  return report.labels[i] + " - " + report.names[i];
                },
              },
            },
          },
          scales: { y: percentAxis, x: { grid: { display: false }, ticks: { autoSkip: true, maxRotation: 60 } } },
        },
      });
    }

    // Student portal: subject-wise bar chart + present/absent doughnut
    const student = readJson("student-data");
    if (student) {
      const subjectCanvas = document.getElementById("studentSubjectChart");
      if (subjectCanvas && student.labels.length) {
        new Chart(subjectCanvas, {
          type: "bar",
          data: {
            labels: student.labels,
            datasets: [
              {
                type: "bar",
                label: "Attendance %",
                data: student.values,
                backgroundColor: student.values.map(function (v) { return v < student.threshold ? RED : BLUE; }),
                borderRadius: 6,
                maxBarThickness: 56,
              },
              {
                type: "line",
                label: "Required (" + student.threshold + "%)",
                data: student.values.map(function () { return student.threshold; }),
                borderColor: "#f59e0b",
                borderDash: [6, 4],
                borderWidth: 2,
                pointRadius: 0,
                fill: false,
              },
            ],
          },
          options: {
            plugins: { legend: { position: "bottom" } },
            scales: { y: percentAxis, x: { grid: { display: false } } },
          },
        });
      }

      const studentRatio = document.getElementById("studentRatioChart");
      if (studentRatio && (student.present + student.absent) > 0) {
        new Chart(studentRatio, {
          type: "doughnut",
          data: {
            labels: ["Present", "Absent"],
            datasets: [{ data: [student.present, student.absent], backgroundColor: [GREEN, RED], borderWidth: 0 }],
          },
          options: { cutout: "68%", plugins: { legend: { position: "bottom" } } },
        });
      }
    }
  }

  /* ---------- Show / hide password buttons ---------- */
  function initPasswordToggles() {
    document.querySelectorAll("[data-toggle-password]").forEach(function (btn) {
      const input = document.querySelector(btn.dataset.togglePassword);
      if (!input) return;
      btn.addEventListener("click", function () {
        const show = input.type === "password";
        input.type = show ? "text" : "password";
        btn.setAttribute("aria-label", show ? "Hide password" : "Show password");
        const icon = btn.querySelector("i");
        if (icon) icon.className = show ? "bi bi-eye-slash" : "bi bi-eye";
      });
    });
  }

  /* ---------- Student history: weekday names from ISO dates ---------- */
  function initWeekdays() {
    const names = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"];
    document.querySelectorAll(".weekday[data-date]").forEach(function (cell) {
      const parts = cell.dataset.date.split("-").map(Number);
      if (parts.length !== 3) return;
      const d = new Date(parts[0], parts[1] - 1, parts[2]);
      cell.textContent = isNaN(d) ? "" : names[d.getDay()];
    });
  }

  document.addEventListener("DOMContentLoaded", function () {
    initConfirmForms();
    initValidation();
    initAlerts();
    initClassPicker();
    initAttendanceForm();
    initPasswordToggles();
    initWeekdays();
    initCharts();
  });
})();
