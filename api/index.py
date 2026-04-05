"""
CloneMaker — Flask API (Vercel Optimized)
"""

from __future__ import annotations

import json
import logging
import os
import threading
import uuid
from pathlib import Path
from typing import Any, Optional

from flask import Flask, jsonify, request, send_file
from flask_cors import CORS

# Vercel環境でパスを正しく認識させるための設定
ROOT = Path(__file__).resolve().parent.parent # apiフォルダの1つ上を指す
EXPORT_DIR = Path("/tmp") # Vercelで書き込みが許可されている唯一の場所

def _configure_logging() -> logging.Logger:
    lg = logging.getLogger("clone_maker")
    if lg.handlers: return lg
    lg.setLevel(logging.DEBUG)
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
    ch = logging.StreamHandler()
    ch.setFormatter(fmt)
    lg.addHandler(ch)
    return lg

log = _configure_logging()

# スクレイパーの読み込み
from ameblo_scraper import export_blog_to_files, parse_blog_id

app = Flask(__name__)
CORS(app)

_jobs: dict[str, dict] = {}
_jobs_lock = threading.Lock()

def _job_disk_path(job_id: str) -> Path:
    return EXPORT_DIR / f".job_{job_id}.json"

def _persist_job_disk(job_id: str) -> None:
    with _jobs_lock:
        j = _jobs.get(job_id)
        if not j: return
        snap = {k: j.get(k) for k in ["status", "phase", "message", "blog_id", "done", "total", "filename_csv", "filename_txt"]}
    try:
        _job_disk_path(job_id).write_text(json.dumps(snap, ensure_ascii=False), encoding="utf-8")
    except: pass

def _run_job(job_id: str, blog_url: str, csv_path: Path, txt_path: Path) -> None:
    try:
        total, blog_id = export_blog_to_files(
            blog_url, str(csv_path), str(txt_path),
            list_delay_sec=0.4, entry_delay_sec=0.5
        )
        with _jobs_lock:
            if job_id in _jobs:
                _jobs[job_id].update({"status": "done", "phase": "done", "message": f"完了（全 {total} 件）", "filename_csv": csv_path.name, "filename_txt": txt_path.name})
        _persist_job_disk(job_id)
    except Exception as e:
        with _jobs_lock:
            if job_id in _jobs: _jobs[job_id].update({"status": "error", "message": str(e)})

@app.post("/api/export/start")
def export_start():
    data = request.get_json(silent=True) or {}
    blog_url = (data.get("blog_url") or "").strip()
    blog_id = parse_blog_id(blog_url)
    job_id = uuid.uuid4().hex
    csv_name, txt_name = f"{blog_id}_{job_id[:8]}.csv", f"{blog_id}_{job_id[:8]}.txt"
    
    with _jobs_lock:
        _jobs[job_id] = {"status": "running", "message": "開始しました", "blog_id": blog_id, "filename_csv": csv_name, "filename_txt": txt_name}
    
    threading.Thread(target=_run_job, args=(job_id, blog_url, EXPORT_DIR/csv_name, EXPORT_DIR/txt_name), daemon=True).start()
    return jsonify({"job_id": job_id, "blog_id": blog_id})

@app.get("/api/export/status/<job_id>")
def export_status(job_id: str):
    j = _jobs.get(job_id)
    if not j: return jsonify({"error": "not found"}), 404
    return jsonify(j)

@app.get("/api/export/download/<job_id>/<fmt>")
def export_download(job_id: str, fmt: str):
    j = _jobs.get(job_id)
    name = j.get("filename_csv") if fmt == "csv" else j.get("filename_txt")
    return send_file(EXPORT_DIR / name, as_attachment=True)

# --- ここから下が「見た目」を正しく表示させるための修正 ---

@app.get("/")
def serve_index():
    return send_file(os.path.join(ROOT, "index.html"))

@app.get("/style.css")
def serve_css():
    return send_file(os.path.join(ROOT, "style.css"), mimetype="text/css")

@app.get("/script.js")
def serve_js():
    return send_file(os.path.join(ROOT, "script.js"), mimetype="application/javascript")

# Vercel用の公開設定
app = app

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)