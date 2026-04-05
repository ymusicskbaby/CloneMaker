"""
CloneMaker — Flask API
記事一覧 → 各記事を段階的に取得し、CSV（タイトル・URL）と TXT（本文連結）を生成する。
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

ROOT = Path(__file__).resolve().parent
# ログ・ジョブメタ・書き出しはすべて exports/ に置く（プロジェクト直下のファイル更新で Live Server が
# ページをリロードし、ポーリングが途中で止まるのを防ぐ）
EXPORT_DIR = ROOT / "exports"
EXPORT_DIR.mkdir(exist_ok=True)


def _configure_logging() -> logging.Logger:
    lg = logging.getLogger("clone_maker")
    if lg.handlers:
        return lg
    lg.setLevel(logging.DEBUG)
    log_path = EXPORT_DIR / "clone_maker.log"
    file_level = getattr(
        logging,
        (os.environ.get("LOG_LEVEL") or "DEBUG").upper(),
        logging.DEBUG,
    )
    fmt = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s [%(threadName)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    fh = logging.FileHandler(log_path, encoding="utf-8")
    fh.setLevel(file_level)
    fh.setFormatter(fmt)
    ch = logging.StreamHandler()
    ch.setLevel(logging.INFO)
    ch.setFormatter(fmt)
    lg.addHandler(fh)
    lg.addHandler(ch)
    lg.info(
        "ログファイル: %s（exports フォルダ内。Live Server の監視から外すとページがリロードされにくいです）",
        log_path.resolve(),
    )
    return lg


log = _configure_logging()

from ameblo_scraper import export_blog_to_files, parse_blog_id

app = Flask(__name__)
# Live Server 等「別ポートのフロント」からも /api を叩けるようにする
CORS(
    app,
    resources={r"/api/*": {"origins": "*", "allow_headers": ["Content-Type"]}},
)

_jobs: dict[str, dict] = {}
_jobs_lock = threading.Lock()

_JOB_SNAP_KEYS = frozenset(
    {
        "status",
        "phase",
        "message",
        "blog_id",
        "done",
        "total",
        "list_pages",
        "urls_found",
        "current_title",
        "filename_csv",
        "filename_txt",
    }
)


def _job_disk_path(job_id: str) -> Path:
    return EXPORT_DIR / f".job_{job_id}.json"


def _snapshot_job(j: dict) -> dict[str, Any]:
    return {k: j.get(k) for k in _JOB_SNAP_KEYS}


def _persist_job_disk(job_id: str) -> None:
    """メモリ上のジョブを JSON に保存（プロセス再起動・リロード後の復旧用）。"""
    with _jobs_lock:
        j = _jobs.get(job_id)
        if not j:
            return
        snap = _snapshot_job(j)
    try:
        _job_disk_path(job_id).write_text(
            json.dumps(snap, ensure_ascii=False, indent=0),
            encoding="utf-8",
        )
    except OSError as e:
        log.warning("ジョブ状態の保存に失敗 job_id=%s err=%s", job_id[:12], e)


def _load_job_disk(job_id: str) -> Optional[dict[str, Any]]:
    p = _job_disk_path(job_id)
    if not p.is_file():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _resolve_job(job_id: str) -> Optional[dict[str, Any]]:
    with _jobs_lock:
        mem = _jobs.get(job_id)
        if mem:
            return dict(mem)
    disk = _load_job_disk(job_id)
    if not disk:
        return None
    if disk.get("status") == "running":
        log.warning(
            "ジョブはディスク上では running のまま（プロセス外からの参照）job_id=%s",
            job_id[:12],
        )
        return {
            **disk,
            "status": "error",
            "phase": "error",
            "message": (
                "処理が中断されました（サーバーが再起動したか、コードが更新されました）。"
                "もう一度「分析を開始」してください。"
            ),
        }
    return disk


def _run_job(job_id: str, blog_url: str, csv_path: Path, txt_path: Path) -> None:
    entry_persist_every = 15
    log.info(
        "バックグラウンドジョブ開始 job_id=%s url=%s",
        job_id[:12],
        blog_url[:80],
    )

    def on_list_page(
        page_idx: int, count: int, cumulative_urls: int, list_url: str
    ) -> None:
        log.info(
            "記事一覧ページ job_id=%s page=%d +%d件 累計URL=%d",
            job_id[:12],
            page_idx,
            count,
            cumulative_urls,
        )
        with _jobs_lock:
            j = _jobs.get(job_id)
            if j:
                j["phase"] = "listing"
                j["list_pages"] = page_idx
                j["urls_found"] = cumulative_urls
                j["message"] = (
                    f"記事一覧を取得中… {page_idx} ページ目 "
                    f"（このページ {count} 件 / 累計 {cumulative_urls} 件の URL）"
                )
        _persist_job_disk(job_id)

    def on_entry(i: int, total: int, title: str, url: str) -> None:
        with _jobs_lock:
            j = _jobs.get(job_id)
            if j:
                j["phase"] = "entries"
                j["done"] = i
                j["total"] = total
                j["current_title"] = title
                j["message"] = f"{i} 記事目を読み込み中…（全 {total} 件） {title}"
        if i == 1 or i == total or i % entry_persist_every == 0:
            _persist_job_disk(job_id)

    try:
        with _jobs_lock:
            _jobs[job_id]["phase"] = "listing"
            _jobs[job_id]["message"] = "記事 URL を収集中…"
            _jobs[job_id]["urls_found"] = 0
        _persist_job_disk(job_id)

        total, blog_id = export_blog_to_files(
            blog_url,
            str(csv_path),
            str(txt_path),
            list_delay_sec=float(os.environ.get("LIST_DELAY", "0.4")),
            entry_delay_sec=float(os.environ.get("ENTRY_DELAY", "0.5")),
            on_list_page=on_list_page,
            on_entry=on_entry,
        )
        with _jobs_lock:
            j = _jobs.get(job_id)
            if j:
                j["status"] = "done"
                j["phase"] = "done"
                j["blog_id"] = blog_id
                j["total"] = total
                j["done"] = total
                j["urls_found"] = total
                j["current_title"] = ""
                j["message"] = f"完了（全 {total} 件）"
                j["filename_csv"] = csv_path.name
                j["filename_txt"] = txt_path.name
        _persist_job_disk(job_id)
        log.info(
            "ジョブ正常終了 job_id=%s 記事数=%s csv=%s",
            job_id[:12],
            total,
            csv_path.name,
        )
    except Exception as e:
        log.exception("ジョブ失敗 job_id=%s error=%s", job_id[:12], e)
        with _jobs_lock:
            j = _jobs.get(job_id)
            if j:
                j["status"] = "error"
                j["phase"] = "error"
                j["message"] = str(e)
        _persist_job_disk(job_id)
        for p in (csv_path, txt_path):
            if p.exists():
                try:
                    p.unlink()
                except OSError:
                    pass


@app.post("/api/export/start")
def export_start():
    data = request.get_json(silent=True) or {}
    blog_url = (data.get("blog_url") or "").strip()
    if not blog_url:
        log.warning("POST /api/export/start 400 empty blog_url")
        return jsonify({"error": "blog_url が必要です"}), 400
    try:
        blog_id = parse_blog_id(blog_url)
    except ValueError as e:
        log.warning("POST /api/export/start 400 bad url: %s", e)
        return jsonify({"error": str(e)}), 400

    job_id = uuid.uuid4().hex
    short = job_id[:8]
    csv_name = f"{blog_id}_{short}_articles.csv"
    txt_name = f"{blog_id}_{short}_bodies.txt"
    csv_path = EXPORT_DIR / csv_name
    txt_path = EXPORT_DIR / txt_name

    with _jobs_lock:
        _jobs[job_id] = {
            "status": "running",
            "phase": "starting",
            "message": "開始しました",
            "blog_id": blog_id,
            "done": 0,
            "total": 0,
            "list_pages": 0,
            "urls_found": 0,
            "current_title": "",
            "filename_csv": csv_name,
            "filename_txt": txt_name,
        }
    _persist_job_disk(job_id)

    t = threading.Thread(
        target=_run_job,
        args=(job_id, blog_url, csv_path, txt_path),
        daemon=True,
    )
    t.start()
    log.info(
        "POST /api/export/start job_id=%s blog_id=%s thread=%s",
        job_id[:12],
        blog_id,
        t.name,
    )
    return jsonify({"job_id": job_id, "blog_id": blog_id})


@app.get("/api/export/status/<job_id>")
def export_status(job_id: str):
    j = _resolve_job(job_id)
    if not j:
        log.warning("GET /api/export/status 404 job_id=%s", job_id[:12])
        return jsonify({"error": "job が見つかりません"}), 404
    log.debug(
        "GET /api/export/status job_id=%s status=%s phase=%s done=%s total=%s",
        job_id[:12],
        j.get("status"),
        j.get("phase"),
        j.get("done"),
        j.get("total"),
    )
    return jsonify(
        {
            "status": j["status"],
            "phase": j["phase"],
            "message": j["message"],
            "blog_id": j.get("blog_id"),
            "done": j.get("done", 0),
            "total": j.get("total", 0),
            "list_pages": j.get("list_pages", 0),
            "urls_found": j.get("urls_found", 0),
            "current_title": j.get("current_title", ""),
            "filename_csv": j.get("filename_csv"),
            "filename_txt": j.get("filename_txt"),
        }
    )


@app.get("/api/export/download/<job_id>/<fmt>")
def export_download(job_id: str, fmt: str):
    fmt = (fmt or "").lower()
    if fmt not in ("csv", "txt"):
        return jsonify({"error": "csv または txt を指定してください"}), 400

    j = _resolve_job(job_id)
    if not j:
        log.warning("GET /api/export/download 404 job job_id=%s", job_id[:12])
        return jsonify({"error": "job が見つかりません"}), 404
    if j.get("status") != "done":
        log.info(
            "GET /api/export/download 未完了 job_id=%s fmt=%s status=%s",
            job_id[:12],
            fmt,
            j.get("status"),
        )
        return jsonify({"error": "まだ完了していません", "status": j.get("status")}), 400

    key = "filename_csv" if fmt == "csv" else "filename_txt"
    name = j.get(key)
    if not name:
        return jsonify({"error": "ファイル名がありません"}), 500
    path = EXPORT_DIR / name
    if not path.is_file():
        log.error(
            "ダウンロード対象ファイルなし job_id=%s fmt=%s path=%s",
            job_id[:12],
            fmt,
            path,
        )
        return jsonify({"error": "ファイルが見つかりません"}), 404

    log.info("GET /api/export/download OK job_id=%s fmt=%s file=%s", job_id[:12], fmt, name)
    blog_id = j.get("blog_id", "ameblo")
    if fmt == "csv":
        dl = f"{blog_id}_articles.csv"
        mime = "text/csv; charset=utf-8"
    else:
        dl = f"{blog_id}_bodies.txt"
        mime = "text/plain; charset=utf-8"

    return send_file(path, as_attachment=True, download_name=dl, mimetype=mime)


@app.get("/api/health")
def health():
    return jsonify({"ok": True})


@app.get("/")
def serve_index():
    return send_file(ROOT / "index.html")


@app.get("/style.css")
def serve_css():
    return send_file(ROOT / "style.css", mimetype="text/css")


@app.get("/script.js")
def serve_js():
    return send_file(ROOT / "script.js", mimetype="application/javascript")


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5000"))
    host = os.environ.get("HOST", "127.0.0.1")
    # use_reloader=False: 自動リロードでプロセスが替わるとメモリ上のジョブが消え、取得が途中で途切れる
    app.run(
        host=host,
        port=port,
        debug=False,
        threaded=True,
        use_reloader=False,
    )
