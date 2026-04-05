const statusEl = document.getElementById('status');
const resultSection = document.getElementById('resultSection');
const output = document.getElementById('outputPrompt');
const downloadArea = document.getElementById('downloadArea');
const blogUrlInput = document.getElementById('blogUrl');
const urlErrorEl = document.getElementById('urlError');
const startBtn = document.getElementById('startBtn');
const progressPanel = document.getElementById('progressPanel');
const progressMessage = document.getElementById('progressMessage');
const progressSub = document.getElementById('progressSub');
const progressBarWrap = document.getElementById('progressBarWrap');
const progressBarFill = document.getElementById('progressBarFill');

const BLOCKED_BLOG_IDS = new Set([
    'theme',
    'category',
    'blog_portal',
    'campaign_list',
    'official',
]);

/** Flask 既定ポート。別ポートで python app.py した場合はここを合わせる */
const FLASK_PORT = '5000';

const POLL_MS = 350;

/** Live Server 等でページがリロードされても、同じ job を追いかけられるようにする */
const EXPORT_POLL_STORAGE_KEY = 'cloneMakerExportPoll';
const EXPORT_POLL_MAX_AGE_MS = 6 * 60 * 60 * 1000;

function clearExportPollStorage() {
    try {
        sessionStorage.removeItem(EXPORT_POLL_STORAGE_KEY);
    } catch {
        /* private mode 等 */
    }
}

function saveExportPollStorage(jobId, base) {
    try {
        sessionStorage.setItem(
            EXPORT_POLL_STORAGE_KEY,
            JSON.stringify({
                jobId,
                base,
                savedAt: Date.now(),
            })
        );
    } catch {
        /* ignore */
    }
}

/** 完了後にページがリロードされてもダウンロード欄を復元する */
const EXPORT_DONE_STORAGE_KEY = 'cloneMakerExportDone';
const EXPORT_DONE_MAX_AGE_MS = 48 * 60 * 60 * 1000;

function clearExportDoneStorage() {
    try {
        sessionStorage.removeItem(EXPORT_DONE_STORAGE_KEY);
    } catch {
        /* ignore */
    }
}

function saveExportDoneStorage(jobId, total, message) {
    try {
        sessionStorage.setItem(
            EXPORT_DONE_STORAGE_KEY,
            JSON.stringify({
                jobId,
                total,
                message: message || '',
                savedAt: Date.now(),
            })
        );
    } catch {
        /* ignore */
    }
}

/** 完了画面（ステータス文・テキストエリア・DL リンク）を描画 */
function renderExportSuccessUI(jobId, apiBase, data) {
    const msg = data.message || '完了';
    const total = data.total;
    statusEl.textContent = `✅ ${msg}`;
    output.value = `書き出しが完了しました。\n・CSV: タイトルと URL の一覧（UTF-8 BOM 付き・Excel 向け）\n・TXT: 全記事の本文をつなげたテキスト（UTF-8）\n（記事数: ${total}）\n\n下のボタンから、必要なファイルだけ保存できます。`;
    resultSection.classList.remove('hidden');
    setProgressVisible(false);
    if (downloadArea) {
        downloadArea.innerHTML = '';
        const csvA = document.createElement('a');
        csvA.href = `${apiBase}/api/export/download/${jobId}/csv`;
        csvA.className = 'download-link download-link--csv';
        csvA.setAttribute('download', '');
        csvA.textContent = '📊 CSVをダウンロード';

        const txtA = document.createElement('a');
        txtA.href = `${apiBase}/api/export/download/${jobId}/txt`;
        txtA.className = 'download-link download-link--txt';
        txtA.setAttribute('download', '');
        txtA.textContent = '📄 テキストをダウンロード';

        downloadArea.appendChild(csvA);
        downloadArea.appendChild(txtA);
    }
}

/** ブラウザの開発者ツール用: URL に ?debug=1 または localStorage.clone_maker_debug = "1" */
const CLONE_DEBUG =
    new URLSearchParams(window.location.search).get('debug') === '1' ||
    window.localStorage.getItem('clone_maker_debug') === '1';

function dlog(...args) {
    if (CLONE_DEBUG) {
        console.log('[CloneMaker]', new Date().toISOString(), ...args);
    }
}

/** 接続確認後に確定する API のオリジン（同一オリジン時は ''） */
let effectiveApiBase = '';

/**
 * Live Server / file:// など「ページと API のポートが違う」ときは Flask へ直結する。
 */
function preferredApiBase() {
    if (window.location.protocol === 'file:') {
        return `http://127.0.0.1:${FLASK_PORT}`;
    }
    const port =
        window.location.port ||
        (window.location.protocol === 'https:' ? '443' : '80');
    const samePortAsFlask = port === FLASK_PORT;
    const local =
        window.location.hostname === 'localhost' ||
        window.location.hostname === '127.0.0.1';
    if (local && samePortAsFlask) {
        return '';
    }
    return `http://127.0.0.1:${FLASK_PORT}`;
}

function healthUrlForBase(base) {
    return base ? `${base}/api/health` : '/api/health';
}

/**
 * 127.0.0.1 / localhost のどちらで届くか環境差があるため、生きている方を採用する。
 * @returns {Promise<string|null>} 動いた base（同一オリジンは ''）、だめなら null
 */
async function probeWorkingBase() {
    const primary = preferredApiBase();
    const candidates = [];
    if (primary === '') {
        candidates.push('');
    } else {
        candidates.push(primary);
        const alt = primary.includes('127.0.0.1')
            ? primary.replace('127.0.0.1', 'localhost')
            : primary.replace('localhost', '127.0.0.1');
        if (alt !== primary) {
            candidates.push(alt);
        }
    }
    for (const base of candidates) {
        try {
            const r = await fetch(healthUrlForBase(base), {
                method: 'GET',
                cache: 'no-store',
            });
            if (r.ok) {
                return base;
            }
        } catch {
            /* try next */
        }
    }
    return null;
}

function connectionHelpHtml() {
    const port = FLASK_PORT;
    return (
        '<strong>Python サーバーに接続できません。</strong><br>' +
        '次の手順を試してください。<br><br>' +
        `1. ターミナルを開き、プロジェクトのフォルダへ移動する<br>` +
        `2. <code>pip3 install -r requirements.txt</code>（初回だけ）<br>` +
        `3. <code>python3 app.py</code> を実行し、「Running on http://…」と出ることを確認<br>` +
        `4. ブラウザの<strong>アドレス欄</strong>に <code>http://127.0.0.1:${port}/</code> と入力して開く<br><br>` +
        '<small>※ Cursor の「簡易ブラウザ」や Live Server だけでは、セキュリティの都合で <code>127.0.0.1</code> に届かないことがあります。その場合は Chrome や Safari で開いてください。</small>'
    );
}

async function refreshServerBanner() {
    const el = document.getElementById('serverBanner');
    if (!el) {
        return;
    }
    el.classList.remove('server-banner--ok', 'server-banner--bad');
    el.classList.add('server-banner--checking');
    el.textContent = 'サーバーへの接続を確認しています…';

    const working = await probeWorkingBase();
    effectiveApiBase = working !== null ? working : preferredApiBase();

    el.classList.remove('server-banner--checking');
    if (working !== null) {
        el.classList.add('server-banner--ok');
        el.innerHTML =
            '✅ バックエンドとつながっています。分析を開始できます。';
    } else {
        el.classList.add('server-banner--bad');
        el.innerHTML = connectionHelpHtml();
    }
}

function isValidAmebloTopUrl(raw) {
    const s = raw.trim();
    if (!s) return false;
    let u;
    try {
        u = new URL(s);
    } catch {
        return false;
    }
    let host = u.hostname.toLowerCase();
    if (host.startsWith('www.')) {
        host = host.slice(4);
    }
    if (host !== 'ameblo.jp') return false;
    const parts = u.pathname.split('/').filter(Boolean);
    if (parts.length !== 1) return false;
    const id = parts[0];
    if (BLOCKED_BLOG_IDS.has(id)) return false;
    if (/^entry-\d+\.html$/i.test(id)) return false;
    const il = id.toLowerCase();
    if (il === 'entrylist.html' || il === 'entrylist') return false;
    return true;
}

/** 分析実行中は URL の検証でボタンが有効化されないようにする */
let exportInProgress = false;

function syncUrlValidation() {
    const raw = blogUrlInput.value;
    const s = raw.trim();
    if (!s) {
        urlErrorEl.hidden = true;
        if (!exportInProgress) {
            startBtn.disabled = true;
        }
        return false;
    }
    const ok = isValidAmebloTopUrl(raw);
    urlErrorEl.hidden = ok;
    if (!exportInProgress) {
        startBtn.disabled = !ok;
    }
    return ok;
}

blogUrlInput.addEventListener('input', syncUrlValidation);
blogUrlInput.addEventListener('blur', syncUrlValidation);

function sleep(ms) {
    return new Promise((r) => setTimeout(r, ms));
}

async function parseJsonResponse(res) {
    const text = await res.text();
    try {
        return JSON.parse(text);
    } catch {
        return {
            error:
                text.trim().slice(0, 180) ||
                `サーバーが JSON 以外を返しました (HTTP ${res.status})`,
        };
    }
}

async function fetchStatusWithRetry(base, jobId, attempts = 8) {
    let lastErr;
    for (let a = 0; a < attempts; a++) {
        try {
            const s = await fetch(`${base}/api/export/status/${jobId}`, {
                cache: 'no-store',
            });
            return s;
        } catch (e) {
            lastErr = e;
            await sleep(400 + a * 200);
        }
    }
    throw lastErr;
}

function setProgressVisible(on) {
    progressPanel.classList.toggle('hidden', !on);
    if (!on) {
        progressBarWrap.classList.remove('progress-bar-wrap--busy');
        progressBarFill.style.width = '0%';
        progressMessage.textContent = '';
        progressSub.textContent = '';
    }
}

/**
 * 進捗ポーリング〜完了 UI まで（リロード後の再開でも使う）
 * @returns {'done'|'error'|'aborted'}
 */
async function runExportPollAndFinishUI(jobId, base) {
    dlog('poll start', { jobId, base: base || '(same-origin)' });
    let data = {};
    let pollCount = 0;
    try {
        for (;;) {
            await sleep(POLL_MS);
            pollCount += 1;
            let s;
            try {
                s = await fetchStatusWithRetry(base, jobId);
            } catch (e) {
                dlog('status fetch failed after retries', e);
                statusEl.textContent =
                    '進捗の取得が何度か失敗しました。Wi-Fi や VPN を確認し、python3 app.py が止まっていないか見てください。';
                setProgressVisible(false);
                clearExportPollStorage();
                return 'aborted';
            }
            data = await parseJsonResponse(s);
            if (!s.ok) {
                dlog('status HTTP error', s.status, data);
                statusEl.textContent =
                    data.error ||
                    `状態の取得に失敗しました (HTTP ${s.status})。サーバーを再起動した場合は、もう一度分析を開始してください。`;
                setProgressVisible(false);
                clearExportPollStorage();
                return 'aborted';
            }
            if (data.status == null && data.error) {
                dlog('status JSON has error field', data);
                statusEl.textContent = data.error;
                setProgressVisible(false);
                clearExportPollStorage();
                return 'aborted';
            }
            if (typeof data.status !== 'string') {
                dlog('status missing or not string', data);
                statusEl.textContent =
                    'サーバーから想定外の応答がありました。ページを再読み込みしてからやり直してください。';
                setProgressVisible(false);
                clearExportPollStorage();
                return 'aborted';
            }
            if (CLONE_DEBUG && (pollCount <= 3 || pollCount % 15 === 0)) {
                dlog('poll', pollCount, {
                    status: data.status,
                    phase: data.phase,
                    done: data.done,
                    total: data.total,
                });
            }
            try {
                statusEl.textContent = data.message || '';
                updateProgressUI(data);
            } catch (uiErr) {
                console.error('[CloneMaker] progress UI error', uiErr);
                statusEl.textContent =
                    '画面の更新でエラーが出ました。?debug=1 を付けてコンソールを確認してください。';
                setProgressVisible(false);
                clearExportPollStorage();
                return 'aborted';
            }
            if (data.status === 'done' || data.status === 'error') {
                dlog('poll loop exit', data.status, data);
                break;
            }
        }

        setProgressVisible(false);

        if (data.status === 'error') {
            statusEl.textContent = data.message || 'エラーで終了しました';
            clearExportPollStorage();
            return 'error';
        }

        saveExportDoneStorage(jobId, data.total, data.message);
        clearExportPollStorage();
        renderExportSuccessUI(jobId, base, data);
        return 'done';
    } catch (e) {
        console.error('[CloneMaker] pollExport', e);
        statusEl.textContent =
            '予期しないエラーで止まりました。コンソールと exports/clone_maker.log を確認してください。';
        setProgressVisible(false);
        clearExportPollStorage();
        return 'aborted';
    }
}

function updateProgressUI(data) {
    progressMessage.textContent = data.message || '';
    const phase = data.phase;
    const total = Number(data.total) || 0;
    const done = Number(data.done) || 0;

    if (phase === 'listing' || phase === 'starting') {
        progressBarWrap.classList.add('progress-bar-wrap--busy');
        progressBarFill.style.width = '100%';
        const lp = data.list_pages || 0;
        const uf = data.urls_found ?? 0;
        progressSub.textContent =
            lp > 0
                ? `記事一覧: ${lp} ページまで取得 · 記事 URL ${uf} 件`
                : '記事一覧を準備しています…';
    } else if (phase === 'entries' && total > 0) {
        progressBarWrap.classList.remove('progress-bar-wrap--busy');
        const pct = Math.min(100, Math.round((done / total) * 100));
        progressBarFill.style.width = `${pct}%`;
        const title = data.current_title || '';
        progressSub.textContent = title
            ? `いま読んでいる記事: ${title}`
            : `${done} / ${total} 件`;
    } else if (phase === 'done') {
        progressBarWrap.classList.remove('progress-bar-wrap--busy');
        progressBarFill.style.width = '100%';
        progressSub.textContent = '';
    } else {
        progressSub.textContent = '';
    }
}

startBtn.addEventListener('click', async () => {
    if (exportInProgress) {
        return;
    }
    if (!syncUrlValidation()) {
        return;
    }

    exportInProgress = true;
    blogUrlInput.readOnly = true;
    startBtn.disabled = true;

    try {
        const url = blogUrlInput.value.trim();
        const recheck = await probeWorkingBase();
        if (recheck === null) {
            statusEl.textContent =
                'バックエンドに接続できません。画面上の赤い案内に従って python3 app.py を起動し、http://127.0.0.1:5000/ を開いてください。';
            await refreshServerBanner();
            return;
        }
        effectiveApiBase = recheck;
        const base = effectiveApiBase;

        clearExportPollStorage();
        clearExportDoneStorage();

        resultSection.classList.add('hidden');
        if (downloadArea) downloadArea.innerHTML = '';
        output.value = '';
        statusEl.textContent = '⏳ エクスポートを開始しています…';
        setProgressVisible(true);
        progressBarWrap.classList.add('progress-bar-wrap--busy');
        progressBarFill.style.width = '100%';
        progressMessage.textContent = '接続しています…';
        progressSub.textContent = '';

        let res;
        try {
            res = await fetch(`${base}/api/export/start`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ blog_url: url }),
            });
        } catch (e) {
            statusEl.textContent =
                '通信に失敗しました。python3 app.py が動いているか確認し、Chrome / Safari で http://127.0.0.1:5000/ を開いてください。';
            setProgressVisible(false);
            refreshServerBanner();
            return;
        }

        const startPayload = await parseJsonResponse(res);
        if (!res.ok) {
            statusEl.textContent =
                startPayload.error || `開始に失敗しました (HTTP ${res.status})`;
            setProgressVisible(false);
            return;
        }

        const jobId = startPayload.job_id;
        if (!jobId) {
            statusEl.textContent = 'サーバーから job_id が返りませんでした。';
            setProgressVisible(false);
            return;
        }

        saveExportPollStorage(jobId, base);
        await runExportPollAndFinishUI(jobId, base);
    } finally {
        exportInProgress = false;
        blogUrlInput.readOnly = false;
        syncUrlValidation();
    }
});

document.getElementById('copyBtn').addEventListener('click', () => {
    const text = document.getElementById('outputPrompt');
    text.select();
    document.execCommand('copy');
    alert('コピーしました！');
});

syncUrlValidation();

async function tryResumeExportAfterReload() {
    let raw;
    try {
        raw = sessionStorage.getItem(EXPORT_POLL_STORAGE_KEY);
    } catch {
        return;
    }
    if (!raw) {
        return;
    }
    let parsed;
    try {
        parsed = JSON.parse(raw);
    } catch {
        clearExportPollStorage();
        return;
    }
    const { jobId, base, savedAt } = parsed;
    if (!jobId || typeof base !== 'string') {
        clearExportPollStorage();
        return;
    }
    if (Date.now() - (savedAt || 0) > EXPORT_POLL_MAX_AGE_MS) {
        clearExportPollStorage();
        return;
    }

    const working = await probeWorkingBase();
    if (working === null) {
        return;
    }
    effectiveApiBase = working;
    const api = working;

    let s;
    try {
        s = await fetch(`${api}/api/export/status/${jobId}`, { cache: 'no-store' });
    } catch {
        return;
    }
    const d = await parseJsonResponse(s);
    if (!s.ok) {
        return;
    }
    if (d.status === 'done') {
        saveExportDoneStorage(jobId, d.total, d.message);
        clearExportPollStorage();
        renderExportSuccessUI(jobId, api, d);
        return;
    }
    if (d.status === 'error') {
        clearExportPollStorage();
        return;
    }
    if (d.status !== 'running') {
        return;
    }

    exportInProgress = true;
    blogUrlInput.readOnly = true;
    startBtn.disabled = true;
    setProgressVisible(true);
    progressBarWrap.classList.add('progress-bar-wrap--busy');
    statusEl.textContent =
        'ページが再読み込みされました。サーバー上の取得は続いています。進捗を再開します…';
    try {
        await runExportPollAndFinishUI(jobId, api);
    } finally {
        exportInProgress = false;
        blogUrlInput.readOnly = false;
        syncUrlValidation();
    }
}

async function restoreExportDoneAfterReload() {
    if (!resultSection.classList.contains('hidden')) {
        return;
    }
    let raw;
    try {
        raw = sessionStorage.getItem(EXPORT_DONE_STORAGE_KEY);
    } catch {
        return;
    }
    if (!raw) {
        return;
    }
    let p;
    try {
        p = JSON.parse(raw);
    } catch {
        clearExportDoneStorage();
        return;
    }
    const { jobId, savedAt } = p;
    if (!jobId || Date.now() - (savedAt || 0) > EXPORT_DONE_MAX_AGE_MS) {
        clearExportDoneStorage();
        return;
    }

    const api = await probeWorkingBase();
    if (api === null) {
        return;
    }
    effectiveApiBase = api;

    let s;
    try {
        s = await fetch(`${api}/api/export/status/${jobId}`, { cache: 'no-store' });
    } catch {
        return;
    }
    const d = await parseJsonResponse(s);
    if (!s.ok || d.status !== 'done') {
        clearExportDoneStorage();
        return;
    }

    dlog('restore done UI after reload', jobId);
    renderExportSuccessUI(jobId, api, d);
}

refreshServerBanner().then(async () => {
    await tryResumeExportAfterReload();
    await restoreExportDoneAfterReload();
});
