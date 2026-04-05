import os
import json
import logging
import threading
import uuid
from pathlib import Path
from flask import Flask, jsonify, request, send_file
from flask_cors import CORS

# Vercel環境で「1つ上の階層」にあるhtmlファイルを探すための設定
ROOT = Path(__file__).resolve().parent.parent 
EXPORT_DIR = Path("/tmp") # 書き込み許可のある一時フォルダ

app = Flask(__name__)
CORS(app)

# --- ここが「見た目」を復活させるための最重要ポイント ---

@app.route('/')
def serve_index():
    # apiフォルダの外（1つ上）にある index.html を送る
    return send_file(os.path.join(ROOT, 'index.html'))

@app.route('/style.css')
def serve_css():
    return send_file(os.path.join(ROOT, 'style.css'), mimetype="text/css")

@app.route('/script.js')
def serve_js():
    return send_file(os.path.join(ROOT, 'script.js'), mimetype="application/javascript")

# --- 以下はシステムの中身（スクレイパー） ---
from ameblo_scraper import export_blog_to_files, parse_blog_id
_jobs = {}

@app.post("/api/export/start")
def export_start():
    data = request.get_json(silent=True) or {}
    blog_url = (data.get("blog_url") or "").strip()
    blog_id = parse_blog_id(blog_url)
    job_id = uuid.uuid4().hex
    csv_name, txt_name = f"{blog_id}_{job_id[:8]}.csv", f"{blog_id}_{job_id[:8]}.txt"
    _jobs[job_id] = {"status": "running", "message": "開始しました"}
    threading.Thread(target=lambda: export_blog_to_files(blog_url, str(EXPORT_DIR/csv_name), str(EXPORT_DIR/txt_name))).start()
    return jsonify({"job_id": job_id})

@app.get("/api/export/status/<job_id>")
def export_status(job_id: str):
    return jsonify(_jobs.get(job_id, {"status": "error"}))

# Vercel用の公開設定（これがないと500エラーになります）
app = app