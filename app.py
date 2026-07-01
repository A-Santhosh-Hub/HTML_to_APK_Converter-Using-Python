"""
HTML to APK Converter — Local Build Console (web UI)
Wraps the existing converter.py pipeline with a local Flask server so the
whole tool can be driven from a browser instead of the terminal.

Run with: python app.py
Then open: http://localhost:5000

This file does NOT change how APKs are built — it calls the same functions
in converter.py (analyze_html, collect_all_assets, build_android_project,
apply_app_icon, compile_apk) that the CLI version uses. It just adds:
  - a web UI for upload + metadata entry
  - background job execution so the browser isn't blocked during a build
  - live progress polling
  - a download link for the finished APK
"""

import os
import re
import sys
import shutil
import threading
import traceback
import uuid
import zipfile
from pathlib import Path
from datetime import datetime

from flask import Flask, request, jsonify, send_file, render_template

# ── Make converter.py importable, and reuse its real build pipeline ────────
sys.path.insert(0, str(Path(__file__).parent.resolve()))
import converter as conv

BASE_DIR = Path(__file__).parent.resolve()
JOBS_ROOT = BASE_DIR / "web_jobs"
JOBS_ROOT.mkdir(parents=True, exist_ok=True)

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 200 * 1024 * 1024  # 200 MB upload ceiling

# In-memory job registry. Fine for a single-user local tool; not for
# multi-user production use.
JOBS = {}
JOBS_LOCK = threading.Lock()

ALLOWED_ICON_EXT = {".png", ".jpg", ".jpeg", ".webp"}


def _job_dir(job_id: str) -> Path:
    return JOBS_ROOT / job_id


def _set_job(job_id: str, **kwargs):
    with JOBS_LOCK:
        JOBS[job_id].update(kwargs)


def _append_log(job_id: str, line: str):
    with JOBS_LOCK:
        JOBS[job_id]["log"].append(line)


def validate_package_id(pkg: str) -> bool:
    return bool(re.match(r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$", pkg))


def run_build_job(job_id: str, project_dir: Path, icon_path: Path, meta: dict):
    """Runs in a background thread. Mirrors converter.py's main(), but
    reports progress into the JOBS registry instead of printing to stdout,
    and uses per-job directories so concurrent builds don't collide."""
    try:
        _set_job(job_id, status="running", stage="Analyzing project", progress=5)
        _append_log(job_id, "Starting build for " + meta["app_name"])

        html_path = project_dir / "index.html"
        if not html_path.exists():
            raise RuntimeError("index.html not found in uploaded project.")

        # Step 1: analyze HTML
        features = conv.analyze_html(html_path)
        _set_job(job_id, stage="Collecting assets (CSS/JS/images/fonts)", progress=20)

        # Step 2: collect assets
        asset_files = conv.collect_all_assets(project_dir, features)
        _append_log(job_id, "Detected %d local asset file(s)." % len(asset_files))

        # Per-job build/output dirs so this job's files don't clash with
        # another job's, or with the CLI tool's own build/ and output/.
        job_build_dir = _job_dir(job_id) / "build" / "android_project"
        job_output_dir = _job_dir(job_id) / "output"

        # Temporarily point the module-level dirs converter.py's functions
        # rely on at this job's own folders, then restore them afterward.
        orig_build_dir = conv.BUILD_DIR
        conv.BUILD_DIR = job_build_dir
        try:
            _set_job(job_id, stage="Generating Android project", progress=35)
            proj_dir = conv.build_android_project(
                features, html_path, asset_files,
                pkg=meta["package_id"],
                app_name=meta["app_name"],
                version_name=meta["version_name"],
                version_code=meta["version_code"],
                orientation=meta["orientation"],
            )

            _set_job(job_id, stage="Applying app icon", progress=50)
            conv.apply_app_icon(proj_dir, icon_path=icon_path)

            _set_job(job_id, stage="Compiling APK with Gradle (this can take a few minutes)", progress=60)
            apk_built = conv.compile_apk(proj_dir, pkg=meta["package_id"], output_dir=job_output_dir)
        finally:
            conv.BUILD_DIR = orig_build_dir

        if apk_built:
            apks = list(job_output_dir.glob("*.apk"))
            if apks:
                _set_job(
                    job_id,
                    status="success",
                    stage="Build complete",
                    progress=100,
                    apk_path=str(apks[0]),
                    apk_name=apks[0].name,
                )
                _append_log(job_id, "APK ready: " + apks[0].name)
                return

        # Gradle build did not produce an APK — most commonly because the
        # Android SDK (ANDROID_HOME) isn't installed on this machine, or
        # because the build itself failed. The full reason is in logs/.
        sdk = conv.find_sdk()
        reason = (
            "Android SDK not found. Set ANDROID_HOME / ANDROID_SDK_ROOT, "
            "or install Android Studio, then try again."
            if not sdk else
            "Gradle build failed. Check the build log file for details."
        )
        _set_job(job_id, status="error", stage="Build failed", progress=100, error=reason)
        _append_log(job_id, "ERROR: " + reason)

    except Exception as e:
        _set_job(job_id, status="error", stage="Build failed", progress=100, error=str(e))
        _append_log(job_id, "ERROR: " + str(e))
        _append_log(job_id, traceback.format_exc())


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/build", methods=["POST"])
def start_build():
    try:
        zip_file = request.files.get("project_zip")
        icon_file = request.files.get("icon")
        app_name = (request.form.get("app_name") or "").strip()
        package_id = (request.form.get("package_id") or "").strip()
        version_name = (request.form.get("version_name") or "1.0.0").strip()
        version_code_raw = (request.form.get("version_code") or "1").strip()
        orientation = (request.form.get("orientation") or "auto").strip()

        if not zip_file:
            return jsonify({"error": "No project ZIP uploaded."}), 400
        if not app_name:
            return jsonify({"error": "App name is required."}), 400
        if not package_id or not validate_package_id(package_id):
            return jsonify({"error": "Package ID must look like com.example.myapp"}), 400
        try:
            version_code = max(1, int(version_code_raw))
        except ValueError:
            version_code = 1

        job_id = uuid.uuid4().hex[:12]
        job_path = _job_dir(job_id)
        upload_dir = job_path / "input_project"
        upload_dir.mkdir(parents=True, exist_ok=True)

        # Save and extract the project zip
        zip_save_path = job_path / "upload.zip"
        zip_file.save(zip_save_path)

        with zipfile.ZipFile(zip_save_path, "r") as zf:
            # Basic zip-slip protection before extracting anything.
            for member in zf.namelist():
                member_path = (upload_dir / member).resolve()
                if not str(member_path).startswith(str(upload_dir.resolve())):
                    return jsonify({"error": "Invalid file path in ZIP archive."}), 400
            zf.extractall(upload_dir)

        # If the zip contained a single wrapping folder, flatten it so
        # index.html ends up directly under input_project/.
        entries = [p for p in upload_dir.iterdir()]
        if len(entries) == 1 and entries[0].is_dir() and not (upload_dir / "index.html").exists():
            inner = entries[0]
            for item in inner.iterdir():
                shutil.move(str(item), str(upload_dir / item.name))
            inner.rmdir()

        if not (upload_dir / "index.html").exists():
            return jsonify({"error": "ZIP must contain an index.html at its root."}), 400

        # Save icon if provided
        icon_path = None
        if icon_file and icon_file.filename:
            ext = Path(icon_file.filename).suffix.lower()
            if ext in ALLOWED_ICON_EXT:
                icon_path = job_path / ("icon" + ext)
                icon_file.save(icon_path)

        meta = {
            "app_name": app_name,
            "package_id": package_id,
            "version_name": version_name,
            "version_code": version_code,
            "orientation": orientation,
        }

        with JOBS_LOCK:
            JOBS[job_id] = {
                "status": "queued",
                "stage": "Queued",
                "progress": 0,
                "log": [],
                "apk_path": None,
                "apk_name": None,
                "error": None,
                "created": datetime.now().isoformat(),
            }

        thread = threading.Thread(
            target=run_build_job,
            args=(job_id, upload_dir, icon_path, meta),
            daemon=True,
        )
        thread.start()

        return jsonify({"job_id": job_id})

    except zipfile.BadZipFile:
        return jsonify({"error": "Uploaded file is not a valid ZIP."}), 400
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/build/<job_id>/status")
def build_status(job_id):
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        if not job:
            return jsonify({"error": "Unknown job id."}), 404
        return jsonify({
            "status": job["status"],
            "stage": job["stage"],
            "progress": job["progress"],
            "log": job["log"][-50:],
            "error": job["error"],
            "apk_name": job["apk_name"],
        })


@app.route("/api/build/<job_id>/download")
def download_apk(job_id):
    with JOBS_LOCK:
        job = JOBS.get(job_id)
    if not job or job.get("status") != "success" or not job.get("apk_path"):
        return jsonify({"error": "APK not available."}), 404
    apk_path = Path(job["apk_path"])
    if not apk_path.exists():
        return jsonify({"error": "APK file missing on disk."}), 404
    return send_file(apk_path, as_attachment=True, download_name=job["apk_name"])


if __name__ == "__main__":
    print()
    print("  HTML to APK Converter — Build Console")
    print("  Open in your browser: http://localhost:5000")
    print()
    app.run(host="127.0.0.1", port=5000, debug=False, threaded=True)
