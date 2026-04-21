import os
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Dict, List, Optional

from flask import Flask, abort, redirect, render_template_string, request, send_from_directory, url_for

from insta_downloader import (
    DownloadResult,
    build_loader,
    collect_reel_links_for_username,
    collect_reel_links_playwright,
    download_many,
)


app = Flask(__name__)


@dataclass
class Job:
    id: str
    created_at: float
    kind: str  # download | collect
    status: str  # queued | running | done | error
    output_folder: str
    cookie_file: Optional[str]
    urls: List[str]
    sleep_min: int
    sleep_max: int
    collect_username: Optional[str] = None
    collect_limit: Optional[int] = None
    collect_method: Optional[str] = None
    collect_headless: bool = True
    progress_log: List[str] = field(default_factory=list)
    cancel_requested: bool = False
    results: List[dict] = field(default_factory=list)
    collected_links: List[str] = field(default_factory=list)
    collected_file: Optional[str] = None
    error: Optional[str] = None


_jobs: Dict[str, Job] = {}
_jobs_lock = threading.Lock()


PAGE = """
<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>InstaDownloader</title>
    <style>
      :root {
        --bg: #0b1020;
        --panel: rgba(255,255,255,0.06);
        --panel2: rgba(255,255,255,0.10);
        --txt: rgba(255,255,255,0.92);
        --muted: rgba(255,255,255,0.70);
        --accent: #7c3aed;
        --good: #22c55e;
        --bad: #ef4444;
        --border: rgba(255,255,255,0.14);
      }
      html, body { height: 100%; }
      body {
        margin: 0;
        color: var(--txt);
        font-family: ui-sans-serif, system-ui, -apple-system, Segoe UI, Roboto, Arial, "Noto Sans", "Liberation Sans";
        background:
          radial-gradient(1200px 800px at 10% 10%, rgba(124,58,237,0.25), transparent 50%),
          radial-gradient(900px 700px at 90% 20%, rgba(34,197,94,0.12), transparent 55%),
          radial-gradient(800px 600px at 50% 90%, rgba(59,130,246,0.12), transparent 55%),
          var(--bg);
      }
      .wrap { max-width: 980px; margin: 0 auto; padding: 28px 18px 60px; }
      .top {
        display: flex; align-items: center; justify-content: space-between; gap: 12px;
        margin-bottom: 18px;
      }
      .brand { font-weight: 800; letter-spacing: 0.2px; font-size: 18px; }
      .pill {
        border: 1px solid var(--border);
        background: rgba(255,255,255,0.05);
        padding: 8px 10px;
        border-radius: 999px;
        color: var(--muted);
        font-size: 12px;
      }
      .grid { display: grid; grid-template-columns: 1.2fr 0.8fr; gap: 14px; }
      @media (max-width: 920px) { .grid { grid-template-columns: 1fr; } }
      .card {
        background: var(--panel);
        border: 1px solid var(--border);
        border-radius: 16px;
        padding: 16px;
        backdrop-filter: blur(10px);
      }
      label { display: block; font-size: 12px; color: var(--muted); margin: 0 0 6px; }
      textarea, input {
        width: 100%;
        box-sizing: border-box;
        border-radius: 12px;
        border: 1px solid var(--border);
        background: rgba(0,0,0,0.25);
        color: var(--txt);
        padding: 10px 12px;
        outline: none;
      }
      textarea { min-height: 210px; resize: vertical; }
      input { height: 40px; }
      .row { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }
      .row3 { display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 10px; }
      .btns { display: flex; gap: 10px; margin-top: 12px; }
      @media (max-width: 560px) {
        .row { grid-template-columns: 1fr; }
        .row3 { grid-template-columns: 1fr; }
        .btns { flex-wrap: wrap; }
        textarea { min-height: 170px; }
      }
      button {
        cursor: pointer;
        border-radius: 12px;
        border: 1px solid rgba(124,58,237,0.45);
        background: linear-gradient(135deg, rgba(124,58,237,0.95), rgba(59,130,246,0.85));
        color: white;
        font-weight: 700;
        padding: 10px 14px;
      }
      a { color: rgba(167,139,250,0.95); text-decoration: none; }
      a:hover { text-decoration: underline; }
      .muted { color: var(--muted); font-size: 12px; line-height: 1.4; }
      .status { display: inline-flex; align-items: center; gap: 8px; font-size: 12px; color: var(--muted); }
      .dot { width: 8px; height: 8px; border-radius: 999px; background: rgba(255,255,255,0.45); }
      .dot.good { background: var(--good); }
      .dot.bad { background: var(--bad); }
      .dot.run { background: #f59e0b; }
      .list { margin-top: 10px; display: grid; gap: 8px; }
      .item { padding: 10px 12px; border-radius: 12px; background: rgba(255,255,255,0.05); border: 1px solid rgba(255,255,255,0.12); }
      code { font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, "Liberation Mono", monospace; font-size: 12px; }
      .hr { height: 1px; background: rgba(255,255,255,0.10); margin: 14px 0; }
      .check { display: inline-flex; align-items: center; gap: 8px; color: var(--muted); font-size: 12px; margin-top: 8px; }
      .check input { width: auto; height: auto; }
    </style>
  </head>
  <body>
    <div class="wrap">
      <div class="top">
        <div class="brand">InstaDownloader</div>
        <div class="pill">Local web UI • Runs on your PC</div>
      </div>
      <div class="grid">
        <div class="card">
          <form method="post" action="{{ url_for('start_download_job') }}">
            <label>Instagram URLs (one per line)</label>
            <textarea name="urls" placeholder="https://www.instagram.com/reel/XXXXXXXXXXX/">{{ default_urls }}</textarea>
            <div class="row" style="margin-top: 10px;">
              <div>
                <label>Output folder</label>
                <input name="output_folder" value="{{ output_folder }}" />
              </div>
              <div>
                <label>Cookie file (optional)</label>
                <input name="cookie_file" value="{{ cookie_file }}" />
              </div>
            </div>
            <div class="row3" style="margin-top: 10px;">
              <div>
                <label>Sleep min (sec)</label>
                <input name="sleep_min" value="{{ sleep_min }}" />
              </div>
              <div>
                <label>Sleep max (sec)</label>
                <input name="sleep_max" value="{{ sleep_max }}" />
              </div>
              <div>
                <label>Tip</label>
                <div class="muted" style="margin-top: 10px;">Use small delays to reduce rate-limits.</div>
              </div>
            </div>
            <div class="btns">
              <button type="submit">Start download</button>
              <a class="muted" href="{{ url_for('list_jobs') }}" style="margin-top: 10px;">View jobs</a>
            </div>
            <div class="muted" style="margin-top: 10px;">
              Files are saved to <code>{{ output_folder }}</code> and downloadable from the results list.
            </div>
          </form>

          <div class="hr"></div>

          <form method="post" action="{{ url_for('start_collect_job') }}">
            <label>Collect reel links from username</label>
            <div class="row" style="margin-top: 6px;">
              <div>
                <label>Username</label>
                <input name="username" placeholder="e.g. glitch_mindset" />
              </div>
              <div>
                <label>Max links</label>
                <input name="limit" value="500" />
              </div>
            </div>
            <div class="row" style="margin-top: 10px;">
              <div>
                <label>Output folder</label>
                <input name="output_folder" value="{{ output_folder }}" />
              </div>
              <div>
                <label>Cookie file (optional)</label>
                <input name="cookie_file" value="{{ cookie_file }}" />
              </div>
            </div>
            <label class="check">
              <input type="checkbox" name="show_browser" />
              Show browser window (Playwright debug)
            </label>
            <div class="btns">
              <button type="submit">Collect links</button>
              <span class="muted" style="margin-top: 10px;">Creates a downloadable `.txt` result.</span>
            </div>
          </form>
        </div>
        <div class="card">
          <div class="muted">
            <div style="font-weight: 700; color: var(--txt); margin-bottom: 6px;">Quick help</div>
            <div>- If downloads fail, your <code>cookie.json</code> may be expired or missing.</div>
            <div>- Instagram rate-limits aggressively; retries may require waiting.</div>
            <div>- This UI runs locally; don’t expose it to the internet.</div>
            <div>- Link collection works best with cookies for private/limited profiles.</div>
          </div>
          {% if latest_job %}
          <div style="height: 12px;"></div>
          <div class="item">
            <div class="status">
              <span class="dot {% if latest_job.status == 'done' %}good{% elif latest_job.status == 'error' %}bad{% elif latest_job.status == 'running' %}run{% endif %}"></span>
              Latest job: <a href="{{ url_for('job_detail', job_id=latest_job.id) }}"><code>{{ latest_job.id }}</code></a>
            </div>
            <div class="muted" style="margin-top: 6px;">
              {{ latest_job.kind }} • {{ latest_job.status }}
              {% if latest_job.kind == 'download' %}
                • {{ latest_job.urls|length }} URL(s)
              {% else %}
                • {{ latest_job.collected_links|length }} link(s)
              {% endif %}
            </div>
          </div>
          {% endif %}
        </div>
      </div>
    </div>
  </body>
</html>
"""


JOBS_PAGE = """
<!doctype html>
<html><head><meta charset="utf-8"/><meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>Jobs • InstaDownloader</title>
<style>
  body{margin:0;font-family:system-ui,Segoe UI,Roboto,Arial;background:#0b1020;color:rgba(255,255,255,.92)}
  .wrap{max-width:980px;margin:0 auto;padding:24px 18px}
  a{color:rgba(167,139,250,.95);text-decoration:none} a:hover{text-decoration:underline}
  .card{border:1px solid rgba(255,255,255,.14);background:rgba(255,255,255,.06);border-radius:14px;padding:14px;margin-top:12px}
  code{font-family:ui-monospace,Menlo,Consolas,monospace;font-size:12px}
  .muted{color:rgba(255,255,255,.70);font-size:12px}
</style></head>
<body><div class="wrap">
  <div style="display:flex;justify-content:space-between;align-items:center;gap:12px">
    <div style="font-weight:800">Jobs</div>
    <a href="{{ url_for('index') }}" class="muted">← back</a>
  </div>
  {% for j in jobs %}
    <div class="card">
      <div><a href="{{ url_for('job_detail', job_id=j.id) }}"><code>{{ j.id }}</code></a></div>
      <div class="muted">
        {{ j.kind }} • {{ j.status }}
        {% if j.kind == 'download' %}
          • {{ j.urls|length }} URL(s)
        {% else %}
          • {{ j.collected_links|length }} link(s)
        {% endif %}
        • output: <code>{{ j.output_folder }}</code>
      </div>
    </div>
  {% else %}
    <div class="card"><div class="muted">No jobs yet.</div></div>
  {% endfor %}
</div></body></html>
"""


JOB_PAGE = """
<!doctype html>
<html><head><meta charset="utf-8"/><meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>Job {{ job.id }} • InstaDownloader</title>
<style>
  body{margin:0;font-family:system-ui,Segoe UI,Roboto,Arial;background:#0b1020;color:rgba(255,255,255,.92)}
  .wrap{max-width:980px;margin:0 auto;padding:24px 18px}
  a{color:rgba(167,139,250,.95);text-decoration:none} a:hover{text-decoration:underline}
  .card{border:1px solid rgba(255,255,255,.14);background:rgba(255,255,255,.06);border-radius:14px;padding:14px;margin-top:12px}
  code{font-family:ui-monospace,Menlo,Consolas,monospace;font-size:12px}
  .muted{color:rgba(255,255,255,.70);font-size:12px;line-height:1.4}
  .ok{color:#22c55e} .bad{color:#ef4444}
  .row{display:flex;justify-content:space-between;gap:12px;align-items:center}
  .pill{border:1px solid rgba(255,255,255,.14);border-radius:999px;padding:7px 10px;background:rgba(255,255,255,.05);font-size:12px}
  .btn{cursor:pointer;border-radius:12px;border:1px solid rgba(124,58,237,0.45);background:linear-gradient(135deg, rgba(124,58,237,0.95), rgba(59,130,246,0.85));color:white;font-weight:700;padding:10px 14px}
  .linkline{overflow-wrap:anywhere;word-break:break-word}
  textarea{width:100%;min-height:240px;border-radius:12px;border:1px solid rgba(255,255,255,.14);background:rgba(0,0,0,.25);color:rgba(255,255,255,.92);padding:10px 12px;box-sizing:border-box}
  @media (max-width: 560px){ .row{flex-direction:column;align-items:flex-start} textarea{min-height:200px} }
</style></head>
<body><div class="wrap">
  <div class="row">
    <div>
      <div style="font-weight:800">Job <code>{{ job.id }}</code></div>
      <div class="muted">Status: <span class="pill">{{ job.status }}</span></div>
    </div>
    <div style="display:flex;gap:10px;align-items:center;flex-wrap:wrap;justify-content:flex-end">
      {% if job.status in ('queued','running') %}
        <form method="post" action="{{ url_for('stop_job', job_id=job.id) }}" style="margin:0">
          <button class="btn" type="submit">Stop</button>
        </form>
      {% endif %}
      <a href="{{ url_for('list_jobs') }}" class="muted">← jobs</a>
    </div>
  </div>

  {% if job.status in ('queued','running') %}
    <div class="card">
      <div class="muted">This page auto-refreshes while the job is running.</div>
    </div>
    <script>setTimeout(() => location.reload(), 2500);</script>
  {% endif %}

  {% if job.error %}
    <div class="card"><div class="bad">Error:</div><div class="muted">{{ job.error }}</div></div>
  {% endif %}

  <div class="card">
    <div class="muted">Output folder: <code>{{ job.output_folder }}</code></div>
    {% if job.kind == 'download' %}
      <div class="muted">URLs: {{ job.urls|length }}</div>
      <div class="muted">Sleep: {{ job.sleep_min }}–{{ job.sleep_max }} sec</div>
    {% else %}
      <div class="muted">Collected links: {{ job.collected_links|length }}</div>
      {% if job.collect_method %}
        <div class="muted">Method: <code>{{ job.collect_method }}</code></div>
      {% endif %}
      {% if job.progress_log %}
        <div class="muted" style="margin-top:10px">
          <div style="font-weight:800;margin-bottom:6px;color:rgba(255,255,255,.92)">Progress</div>
          <textarea readonly>{{ job.progress_log|join('\\n') }}</textarea>
        </div>
      {% endif %}
    {% endif %}
  </div>

  <div class="card">
    <div style="font-weight:800;margin-bottom:8px">Results</div>
    {% if job.kind == 'collect' %}
      {% if job.collected_file %}
        <div class="muted">Download: <a href="{{ url_for('download_file', job_id=job.id, path=job.collected_file) }}"><code>{{ job.collected_file }}</code></a></div>
      {% endif %}
      {% if job.collected_links %}
        <div class="muted" style="margin-top:10px">
          <textarea id="links">{{ job.collected_links|join('\\n') }}</textarea>
        </div>
        <div style="margin-top:10px">
          <button class="btn" onclick="navigator.clipboard.writeText(document.getElementById('links').value)">Copy to clipboard</button>
        </div>
      {% else %}
        <div class="muted">No links yet.</div>
      {% endif %}
    {% else %}
    {% for r in job.results %}
      <div style="padding:10px 0;border-top:1px solid rgba(255,255,255,.10)">
        <div class="muted linkline"><code>{{ r.shortcode or "?" }}</code> • {{ r.url }}</div>
        {% if r.ok %}
          <div class="ok">OK</div>
          {% for f in r.files %}
            <div class="muted">- <a href="{{ url_for('download_file', job_id=job.id, path=f) }}"><code>{{ f }}</code></a></div>
          {% endfor %}
        {% else %}
          <div class="bad">Failed</div>
          <div class="muted linkline">{{ r.error }}</div>
        {% endif %}
      </div>
    {% else %}
      <div class="muted">No results yet.</div>
    {% endfor %}
    {% endif %}
  </div>
</div></body></html>
"""


def _safe_int(value: str, default: int) -> int:
    try:
        return int(value)
    except Exception:
        return default


def _start_worker(job: Job) -> None:
    def run():
        try:
            job.status = "running"
            # For collection, fail fast on GraphQL 403 loops so we can fall back.
            if job.kind == "collect":
                L = build_loader(
                    output_folder=job.output_folder,
                    cookie_file=(job.cookie_file or None),
                    max_connection_attempts=1,
                    request_timeout=30,
                )
            else:
                L = build_loader(
                    output_folder=job.output_folder,
                    cookie_file=(job.cookie_file or None),
                    max_connection_attempts=3,
                    request_timeout=60,
                )
            if job.kind == "collect":
                links, method = collect_reel_links_for_username(
                    L,
                    username=(job.collect_username or (job.urls[0] if job.urls else "")),
                    limit=max(1, int(job.collect_limit or 500)),
                )
                # If GraphQL is blocked and HTML fallback returns too few links,
                # try a browser automation approach (closest to DevTools script).
                if method != "instaloader_graphql" and len(links) < 20 and job.cookie_file:
                    try:
                        if not os.path.exists(job.cookie_file):
                            raise RuntimeError(f"Cookie file not found: {job.cookie_file}")
                        job.progress_log.append("Starting Playwright scroll collector...")

                        def on_progress(evt: dict) -> None:
                            job.progress_log.append(
                                f"scroll {evt.get('scroll')}/{evt.get('max_scrolls')} • links={evt.get('links')} • stuck={evt.get('stuck')} • {evt.get('url')}"
                            )
                            # keep log bounded
                            if len(job.progress_log) > 200:
                                job.progress_log[:] = job.progress_log[-200:]

                        links = collect_reel_links_playwright(
                            username=(job.collect_username or (job.urls[0] if job.urls else "")),
                            cookie_file=job.cookie_file,
                            limit=max(1, int(job.collect_limit or 500)),
                            headless=job.collect_headless,
                            on_progress=on_progress,
                            should_stop=lambda: job.cancel_requested,
                        )
                        method = "playwright_scroll"
                    except Exception as e:
                        # Keep earlier results/method but surface the fallback error.
                        msg = str(e)
                        if "Playwright is not installed" in msg or "No module named" in msg:
                            msg = (
                                "Playwright fallback failed because Playwright is not installed in the SAME Python environment "
                                "that is running `webapp.py`.\n\n"
                                "Fix:\n"
                                "  1) Activate your venv (if using one)\n"
                                "  2) pip install -r requirements.txt\n"
                                "  3) python -m playwright install chromium\n"
                            )
                        job.error = (job.error + "\n\n" if job.error else "") + msg
                job.collected_links = links
                job.collect_method = method

                exports_dir = os.path.join(job.output_folder, "exports")
                os.makedirs(exports_dir, exist_ok=True)
                out_path = os.path.join(exports_dir, f"{job.id}_reels.txt")
                with open(out_path, "w", encoding="utf-8") as f:
                    f.write("\n".join(links))
                job.collected_file = out_path
            else:
                results = download_many(
                    L,
                    urls=job.urls,
                    output_folder=job.output_folder,
                    sleep_between_s=(job.sleep_min, job.sleep_max),
                    should_stop=lambda: job.cancel_requested,
                )
                job.results = [asdict(r) if isinstance(r, DownloadResult) else r for r in results]
            # If collection produced nothing, treat it as error (more honest UX).
            if job.kind == "collect" and not job.collected_links:
                job.status = "error"
                if not job.error:
                    job.error = (
                        "No reel links were collected. Instagram may be blocking access to the profile listing endpoints. "
                        "Try Playwright mode (install Playwright + Chromium) and retry."
                    )
            else:
                job.status = "done"
            if job.cancel_requested and job.status == "done":
                job.status = "cancelled"
        except Exception as e:
            job.error = str(e)
            job.status = "error"

    t = threading.Thread(target=run, daemon=True)
    t.start()


@app.get("/")
def index():
    with _jobs_lock:
        latest_job = next(reversed(_jobs.values()), None) if _jobs else None
    return render_template_string(
        PAGE,
        default_urls="",
        output_folder="reels",
        cookie_file="cookie.json",
        sleep_min=0,
        sleep_max=0,
        latest_job=latest_job,
    )


@app.post("/start-download")
def start_download_job():
    urls_raw = request.form.get("urls", "")
    urls = [line.strip() for line in urls_raw.splitlines() if line.strip()]
    if not urls:
        return redirect(url_for("index"))

    output_folder = (request.form.get("output_folder") or "reels").strip()
    cookie_file = (request.form.get("cookie_file") or "").strip() or None
    sleep_min = _safe_int(request.form.get("sleep_min", "0"), 0)
    sleep_max = _safe_int(request.form.get("sleep_max", "0"), 0)
    sleep_min = max(0, sleep_min)
    sleep_max = max(sleep_min, sleep_max)

    job = Job(
        id=uuid.uuid4().hex[:12],
        created_at=time.time(),
        kind="download",
        status="queued",
        output_folder=output_folder,
        cookie_file=cookie_file,
        urls=urls,
        sleep_min=sleep_min,
        sleep_max=sleep_max,
    )
    with _jobs_lock:
        _jobs[job.id] = job

    _start_worker(job)
    return redirect(url_for("job_detail", job_id=job.id))


@app.post("/start-collect")
def start_collect_job():
    username = (request.form.get("username") or "").strip()
    if not username:
        return redirect(url_for("index"))

    output_folder = (request.form.get("output_folder") or "reels").strip()
    cookie_file = (request.form.get("cookie_file") or "").strip() or None
    limit = _safe_int(request.form.get("limit", "500"), 500)
    limit = max(1, min(5000, limit))
    show_browser = bool(request.form.get("show_browser"))

    # Reuse job fields:
    # - urls[0] stores username
    # - sleep_max stores limit
    job = Job(
        id=uuid.uuid4().hex[:12],
        created_at=time.time(),
        kind="collect",
        status="queued",
        output_folder=output_folder,
        cookie_file=cookie_file,
        urls=[username],
        sleep_min=0,
        sleep_max=limit,
        collect_username=username,
        collect_limit=limit,
        collect_headless=(not show_browser),
    )
    with _jobs_lock:
        _jobs[job.id] = job

    _start_worker(job)
    return redirect(url_for("job_detail", job_id=job.id))

@app.get("/jobs")
def list_jobs():
    with _jobs_lock:
        jobs = list(_jobs.values())[::-1]
    return render_template_string(JOBS_PAGE, jobs=jobs)


@app.get("/jobs/<job_id>")
def job_detail(job_id: str):
    with _jobs_lock:
        job = _jobs.get(job_id)
    if not job:
        abort(404)
    return render_template_string(JOB_PAGE, job=job)


@app.post("/jobs/<job_id>/stop")
def stop_job(job_id: str):
    with _jobs_lock:
        job = _jobs.get(job_id)
        if job:
            job.cancel_requested = True
    return redirect(url_for("job_detail", job_id=job_id))


@app.get("/jobs/<job_id>/files")
def download_file(job_id: str):
    with _jobs_lock:
        job = _jobs.get(job_id)
    if not job:
        abort(404)

    rel = (request.args.get("path") or "").strip()
    if not rel:
        abort(400)

    abs_output = os.path.abspath(job.output_folder)
    abs_file = os.path.abspath(rel)
    if not abs_file.startswith(abs_output + os.sep):
        abort(403)

    directory = os.path.dirname(abs_file)
    filename = os.path.basename(abs_file)
    return send_from_directory(directory, filename, as_attachment=True)


if __name__ == "__main__":
    # Bind to localhost only by default
    app.run(host="127.0.0.1", port=int(os.environ.get("PORT", "5000")), debug=True)

