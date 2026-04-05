import os
from pathlib import Path
from flask import Flask, send_file, jsonify

# フォルダの場所を「Vercel用」に固定
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

app = app # Vercelがこれを目印に起動します