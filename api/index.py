import os
from pathlib import Path
from flask import Flask, send_file, jsonify

# ファイルの場所を特定する設定
ROOT = Path(__file__).resolve().parent.parent
app = Flask(__name__)

@app.route('/')
def index():
    return send_file(os.path.join(ROOT, 'index.html'))

@app.route('/style.css')
def serve_css():
    return send_file(os.path.join(ROOT, 'style.css'), mimetype="text/css")

@app.route('/script.js')
def serve_js():
    return send_file(os.path.join(ROOT, 'script.js'), mimetype="application/javascript")

app = app # Vercel用