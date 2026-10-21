#!/usr/bin/env python3
"""
Conference Journal Maker - the easy way.

Starts a small web page on YOUR computer (nothing is uploaded anywhere), opens it in
your browser, and lets you paste a General Conference link and click a button.

Normally you start this by double-clicking the "Start Journal Maker" file for your
computer, which also does the one-time setup for you.
"""
import argparse
import contextlib
import json
import os
import re
import secrets
import subprocess
import sys
import threading
import traceback
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote, unquote, urlparse

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import build_journal as bj          # noqa: E402
import fetch_conference as fc       # noqa: E402

TOKEN = secrets.token_urlsafe(16)   # stops other websites from poking this local app
PORT = 8765
OUT_DIR = Path.home()               # replaced in main()

STATE = {"state": "idle", "percent": 0, "message": "", "log": [], "pdf": None,
         "pages": 0, "failed": 0, "error": ""}
LOCK = threading.Lock()
SERVER = None


def default_output_dir() -> Path:
    docs = Path.home() / "Documents"
    return (docs if docs.exists() else Path.home()) / "Conference Journals"


# ------------------------------------------------------------------ job handling
def set_state(**kw):
    with LOCK:
        STATE.update(kw)


def add_log(line: str):
    with LOCK:
        STATE["log"].append(line)
        STATE["log"] = STATE["log"][-400:]


class LogWriter:
    """Receives the print() output of the downloader/builder and turns it into progress."""

    def __init__(self):
        self.buf = ""

    def write(self, text):
        self.buf += text
        while "\n" in self.buf:
            line, self.buf = self.buf.split("\n", 1)
            self.handle(line.rstrip())
        return len(text)

    def flush(self):
        pass

    def handle(self, line):
        if not line:
            return
        add_log(line)
        m = re.match(r"\[(\d+)/(\d+)\]\s*(.*)", line)
        if m:
            i, total, rest = int(m.group(1)), int(m.group(2)), m.group(3)
            if rest.startswith("FAILED"):
                with LOCK:
                    STATE["failed"] += 1
            set_state(percent=min(85, int(2 + 83 * i / total)),
                      message=f"Downloading talk {i} of {total}\u2026")
            return
        m = re.search(r"Found .*?(\d+) talks", line)
        if m:
            n = int(m.group(1))
            mins = max(1, round(n * 1.6 / 60))
            set_state(percent=4, message=f"Found {n} talks \u2014 downloading will take about "
                                         f"{mins} minute{'s' if mins != 1 else ''}\u2026")


def normalize_url(url: str) -> str:
    url = url.strip()
    if url and not re.match(r"^https?://", url, re.I):
        url = "https://" + url
    return url


_PEEK_CACHE = {}


def peek_collection(kind: str, slug: str):
    """Quickly look up a speaker/topic page for its real name and number of talks."""
    key = (kind, slug)
    if key not in _PEEK_CACHE:
        folder = "speakers" if kind == "speaker" else "topics"
        page_url = f"https://www.churchofjesuschrist.org/study/general-conference/{folder}/{slug}?lang=eng"
        try:
            heading, talks = fc.list_collection(fc.get_html(page_url, retries=1, timeout=8), page_url)
            _PEEK_CACHE[key] = (heading, len(talks)) if talks else None
        except Exception:
            return None            # offline or slow: fall back to a guess from the link
    return _PEEK_CACHE[key]


def describe_url(url: str) -> dict:
    """Used for the live 'Recognized' check under the link box."""
    url = normalize_url(url)
    host = (urlparse(url).netloc or "").lower()
    if not host.endswith("churchofjesuschrist.org"):
        return {"ok": False, "message": "Please paste a link from churchofjesuschrist.org"}
    try:
        kind, slug = fc.classify_url(url)
    except ValueError:
        return {"ok": False, "message": "That link isn\u2019t a conference, speaker or topic page. "
                                        "See \u201cHow do I find a link?\u201d below."}
    if kind == "conference":
        _, y, m = fc.conference_base(url)
        return {"ok": True, "kind": kind,
                "message": f"{fc.MONTHS.get(m, m)} {y} General Conference \u2014 all sessions and talks"}
    nice, count = slug.replace("-", " ").title(), ""
    found = peek_collection(kind, slug)
    if found:
        nice, n = found[0] or nice, found[1]
        count = f" ({n} talk{'s' if n != 1 else ''})"
    if kind == "speaker":
        return {"ok": True, "kind": kind, "message": f"Every conference talk by {nice}{count}"}
    return {"ok": True, "kind": kind, "message": f"Every conference talk on \u201c{nice}\u201d{count}"}


def run_job(url: str, owner: str, oldest_first: bool):
    fc.STOP.clear()
    set_state(state="running", percent=2, message="Reading the page\u2026", log=[], pdf=None,
              pages=0, failed=0, error="")
    writer = LogWriter()
    try:
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        with contextlib.redirect_stdout(writer):
            kind, slug = fc.classify_url(url)
            if kind == "conference":
                _, y, m = fc.conference_base(url)
                folder_name = f"conference_{y}_{m}"
            else:
                folder_name = f"{kind}_{slug}"
            talks_dir = OUT_DIR / "downloaded talks" / folder_name
            folder = fc.download_conference(url, talks_dir, owner, oldest_first)

            fc._check_stop()
            set_state(percent=88, message="Building your PDF\u2026 (big journals can take a few minutes)")
            cfg = json.loads((folder / "conference.json").read_text(encoding="utf-8"))
            pdf = OUT_DIR / f"{fc.safe_name(cfg['title'])} - Study Journal.pdf"
            pages = bj.build(folder, pdf)
        set_state(state="done", percent=100, pdf=pdf.name, pages=pages,
                  message="Your journal is ready!")
    except fc.Cancelled:
        set_state(state="cancelled", message="Stopped. Anything already downloaded is kept, "
                                             "so you can pick up where you left off.")
    except SystemExit as e:
        set_state(state="error", error=str(e) or "No talks were found.")
    except Exception as e:  # friendly messages for the common problems
        import requests
        add_log(traceback.format_exc())
        if isinstance(e, (requests.exceptions.ConnectionError, requests.exceptions.Timeout)):
            msg = "Couldn\u2019t reach the website. Please check your internet connection and try again."
        elif isinstance(e, requests.exceptions.HTTPError):
            code = getattr(e.response, "status_code", "")
            msg = ("That page wasn\u2019t found. Please check the link." if code == 404
                   else f"The website returned an error ({code}). Please try again in a few minutes.")
        elif isinstance(e, (ValueError, RuntimeError)):
            msg = str(e)
        else:
            msg = f"Something went wrong: {e}"
        set_state(state="error", error=msg)


def open_path(path: Path):
    if sys.platform.startswith("win"):
        os.startfile(str(path))                       # noqa: attribute only exists on Windows
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(path)])


# ------------------------------------------------------------------ web server
class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    # -- helpers
    def _host_ok(self):
        host = (self.headers.get("Host") or "").lower()
        return host in (f"127.0.0.1:{PORT}", f"localhost:{PORT}")

    def _send(self, code, body, ctype="application/json; charset=utf-8", headers=None):
        data = body if isinstance(body, bytes) else body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def _json(self, obj, code=200):
        self._send(code, json.dumps(obj))

    # -- routes
    def do_GET(self):
        if not self._host_ok():
            return self._send(403, "Forbidden", "text/plain")
        path = self.path.split("?")[0]
        if path == "/":
            page = PAGE.replace("__TOKEN__", TOKEN).replace("__OUTDIR__", str(OUT_DIR))
            return self._send(200, page, "text/html; charset=utf-8")
        if path == "/api/status":
            with LOCK:
                snap = dict(STATE)
                snap["log"] = STATE["log"][-60:]
            snap["output_dir"] = str(OUT_DIR)
            return self._json(snap)
        if path.startswith("/download/"):
            name = unquote(path[len("/download/"):])
            f = (OUT_DIR / name).resolve()
            if f.parent != OUT_DIR.resolve() or f.suffix.lower() != ".pdf" or not f.is_file():
                return self._send(404, "Not found", "text/plain")
            return self._send(200, f.read_bytes(), "application/pdf",
                              {"Content-Disposition": f"attachment; filename*=UTF-8''{quote(f.name)}"})
        self._send(404, "Not found", "text/plain")

    def do_POST(self):
        if not self._host_ok() or self.headers.get("X-Token") != TOKEN:
            return self._send(403, "Forbidden", "text/plain")
        length = int(self.headers.get("Content-Length") or 0)
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
        except ValueError:
            body = {}
        path = self.path.split("?")[0]

        if path == "/api/check":
            return self._json(describe_url(body.get("url", "")))

        if path == "/api/start":
            with LOCK:
                if STATE["state"] == "running":
                    return self._json({"ok": False, "message": "A journal is already being made."})
            url = normalize_url(body.get("url", ""))
            info = describe_url(url)
            if not info["ok"]:
                return self._json(info)
            threading.Thread(target=run_job, daemon=True,
                             args=(url, (body.get("name") or "").strip()[:80],
                                   bool(body.get("oldest_first")))).start()
            return self._json({"ok": True})

        if path == "/api/stop":
            fc.STOP.set()
            return self._json({"ok": True})

        if path in ("/api/open_pdf", "/api/open_folder"):
            try:
                target = OUT_DIR
                if path == "/api/open_pdf":
                    with LOCK:
                        name = STATE.get("pdf")
                    target = OUT_DIR / name if name else OUT_DIR
                open_path(target)
                return self._json({"ok": True})
            except Exception:
                return self._json({"ok": False})

        if path == "/api/quit":
            self._json({"ok": True})
            threading.Thread(target=SERVER.shutdown, daemon=True).start()
            return
        self._send(404, "Not found", "text/plain")


PAGE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Conference Journal Maker</title>
<style>
  :root { --bg:#f4f6fa; --card:#ffffff; --ink:#1d2433; --muted:#5d667a; --accent:#1f3a5f;
          --accent-ink:#ffffff; --line:#d5dbe6; --ok:#1c7a4a; --okbg:#e8f5ee; --bad:#a32c2c;
          --badbg:#fbeaea; --field:#ffffff; }
  @media (prefers-color-scheme: dark) {
    :root { --bg:#12151c; --card:#1b2030; --ink:#e8ecf5; --muted:#a2abbf; --accent:#8fb4ee;
            --accent-ink:#0f1726; --line:#2f374a; --ok:#6fd1a0; --okbg:#173326; --bad:#f19a9a;
            --badbg:#3a1f22; --field:#141826; }
  }
  * { box-sizing:border-box; }
  body { margin:0; background:var(--bg); color:var(--ink);
         font:16px/1.5 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif; }
  main { max-width:680px; margin:0 auto; padding:32px 20px 48px; }
  h1 { font-family:Georgia,"Times New Roman",serif; color:var(--accent); font-size:2rem;
       margin:0 0 4px; }
  .sub { color:var(--muted); margin:0 0 24px; }
  .card { background:var(--card); border:1px solid var(--line); border-radius:14px;
          padding:22px; margin-bottom:18px; }
  label.step { display:block; font-weight:600; margin-bottom:6px; }
  label.step span { color:var(--muted); font-weight:400; }
  input[type=text] { width:100%; padding:12px 14px; font-size:1rem; border:1px solid var(--line);
         border-radius:10px; background:var(--field); color:var(--ink); }
  input[type=text]:focus { outline:3px solid color-mix(in srgb,var(--accent) 35%,transparent);
         border-color:var(--accent); }
  .chip { margin-top:8px; padding:8px 12px; border-radius:8px; font-size:.92rem; display:none; }
  .chip.ok { display:block; background:var(--okbg); color:var(--ok); }
  .chip.bad { display:block; background:var(--badbg); color:var(--bad); }
  .examples { margin-top:10px; font-size:.9rem; color:var(--muted); }
  .examples button { background:none; border:1px solid var(--line); color:var(--accent);
         border-radius:999px; padding:3px 11px; margin:3px 4px 0 0; cursor:pointer; font-size:.88rem; }
  .examples button:hover { background:color-mix(in srgb,var(--accent) 12%,transparent); }
  .row { margin-top:18px; }
  .check { display:none; align-items:center; gap:8px; margin-top:16px; }
  .check input { width:18px; height:18px; }
  button.primary { width:100%; margin-top:22px; padding:15px; font-size:1.1rem; font-weight:600;
         background:var(--accent); color:var(--accent-ink); border:0; border-radius:12px; cursor:pointer; }
  button.primary:disabled { opacity:.45; cursor:not-allowed; }
  button.secondary { padding:11px 18px; font-size:1rem; border:1px solid var(--accent);
         background:transparent; color:var(--accent); border-radius:10px; cursor:pointer; }
  a.secondary { text-decoration:none; display:inline-block; }
  .bar { height:14px; background:var(--line); border-radius:999px; overflow:hidden; margin:14px 0 8px; }
  .bar > div { height:100%; width:0; background:var(--accent); transition:width .5s ease; }
  .msg { font-weight:600; }
  .muted { color:var(--muted); font-size:.92rem; }
  .done { background:var(--okbg); border-color:var(--ok); }
  .done h2 { margin:0 0 6px; color:var(--ok); font-size:1.3rem; }
  .actions { display:flex; flex-wrap:wrap; gap:10px; margin-top:14px; }
  .error { background:var(--badbg); border-color:var(--bad); color:var(--bad); }
  details { margin-top:10px; }
  summary { cursor:pointer; color:var(--muted); font-size:.92rem; }
  pre { background:var(--field); border:1px solid var(--line); border-radius:8px; padding:10px;
        max-height:200px; overflow:auto; font-size:.8rem; white-space:pre-wrap; }
  .help { font-size:.95rem; color:var(--muted); }
  .help ol { padding-left:20px; margin:8px 0 0; }
  footer { text-align:center; color:var(--muted); font-size:.88rem; margin-top:26px; }
  footer button { background:none; border:0; color:var(--muted); text-decoration:underline;
         cursor:pointer; font-size:.88rem; }
  [hidden] { display:none !important; }
</style>
</head>
<body>
<main>
  <h1>Conference Journal Maker</h1>
  <p class="sub">Turn General Conference talks into a printable study journal &mdash; with room to write beside every talk.</p>

  <section class="card" id="form">
    <label class="step" for="url">1. Paste a link from churchofjesuschrist.org</label>
    <input type="text" id="url" placeholder="https://www.churchofjesuschrist.org/study/general-conference/&hellip;"
           autocomplete="off" spellcheck="false">
    <div class="chip" id="chip"></div>
    <div class="examples">Or try an example:
      <button type="button" data-url="https://www.churchofjesuschrist.org/study/general-conference/2026/10?lang=eng">October 2026 conference</button>
      <button type="button" data-url="https://www.churchofjesuschrist.org/study/general-conference/speakers/dallin-h-oaks?lang=eng">Talks by Dallin H. Oaks</button>
      <button type="button" data-url="https://www.churchofjesuschrist.org/study/general-conference/topics/atonement?lang=eng">Talks on the Atonement</button>
    </div>

    <div class="row">
      <label class="step" for="name">2. Your name <span>(optional &mdash; printed on the cover)</span></label>
      <input type="text" id="name" maxlength="80" placeholder="e.g. Jane Smith" autocomplete="name">
    </div>

    <label class="check" id="oldestRow"><input type="checkbox" id="oldest"> Put the oldest talks first (otherwise newest first)</label>

    <button class="primary" id="go" disabled>Create my journal</button>
  </section>

  <section class="card" id="progress" hidden>
    <div class="msg" id="pmsg">Starting&hellip;</div>
    <div class="bar"><div id="pbar"></div></div>
    <div class="muted" id="psub">You can leave this page open and do something else. Please don&rsquo;t close the black window.</div>
    <div class="actions"><button class="secondary" id="stop">Stop</button></div>
    <details><summary>Show details</summary><pre id="log"></pre></details>
  </section>

  <section class="card done" id="done" hidden>
    <h2>&#10003; Your journal is ready!</h2>
    <div id="dsub"></div>
    <div class="actions">
      <button class="primary" id="openPdf" style="width:auto;margin:0;padding:12px 22px">Open my journal</button>
      <button class="secondary" id="openFolder">Show in folder</button>
      <a class="secondary" id="dl" href="#" download style="padding:11px 18px;border:1px solid var(--accent);border-radius:10px;color:var(--accent)">Download a copy</a>
      <button class="secondary" id="again">Make another</button>
    </div>
    <div class="muted" id="where" style="margin-top:12px"></div>
  </section>

  <section class="card error" id="errbox" hidden>
    <strong id="errmsg"></strong>
    <div class="actions"><button class="secondary" id="retry">Try again</button></div>
    <details><summary>Technical details</summary><pre id="errlog"></pre></details>
  </section>

  <section class="card help">
    <details>
      <summary><strong>How do I find a link?</strong></summary>
      <ol>
        <li>Go to <strong>churchofjesuschrist.org</strong> and open <strong>General Conference</strong>.</li>
        <li>Open a conference &mdash; <em>or</em> click a speaker&rsquo;s name &mdash; <em>or</em> choose a topic.</li>
        <li>Copy the address from your browser&rsquo;s address bar and paste it above.</li>
      </ol>
      <p>Printing tip: print double-sided and three-hole punch it for a binder.</p>
    </details>
  </section>

  <footer>Journals are saved in <span id="outdir">__OUTDIR__</span><br>
    <button id="quit">Quit Journal Maker</button></footer>
</main>

<script>
const TOKEN = "__TOKEN__";
const $ = id => document.getElementById(id);
let checkTimer = null, poll = null, lastKind = "";

async function post(path, body) {
  const r = await fetch(path, {method: "POST",
    headers: {"Content-Type": "application/json", "X-Token": TOKEN},
    body: JSON.stringify(body || {})});
  return r.json();
}
function show(id, on) { $(id).hidden = !on; }

async function check() {
  const url = $("url").value.trim();
  const chip = $("chip");
  if (!url) { chip.className = "chip"; $("go").disabled = true; $("oldestRow").style.display = "none"; return; }
  const r = await post("/api/check", {url});
  chip.className = "chip " + (r.ok ? "ok" : "bad");
  chip.textContent = (r.ok ? "\u2713 " : "") + r.message;
  $("go").disabled = !r.ok;
  lastKind = r.ok ? r.kind : "";
  $("oldestRow").style.display = (r.ok && r.kind !== "conference") ? "flex" : "none";
}
$("url").addEventListener("input", () => { clearTimeout(checkTimer); checkTimer = setTimeout(check, 250); });
document.querySelectorAll(".examples button").forEach(b => b.addEventListener("click", () => {
  $("url").value = b.dataset.url; check(); }));

$("go").addEventListener("click", async () => {
  const r = await post("/api/start", {url: $("url").value.trim(), name: $("name").value,
                                       oldest_first: $("oldest").checked});
  if (!r.ok) { const c = $("chip"); c.className = "chip bad"; c.textContent = r.message; return; }
  show("form", false); show("done", false); show("errbox", false); show("progress", true);
  startPolling();
});
$("stop").addEventListener("click", () => { $("stop").disabled = true; post("/api/stop"); });
$("again").addEventListener("click", reset);
$("retry").addEventListener("click", reset);
function reset() {
  show("done", false); show("errbox", false); show("progress", false); show("form", true);
  $("stop").disabled = false;
}
$("openPdf").addEventListener("click", async () => {
  const r = await post("/api/open_pdf");
  if (!r.ok) alert("Couldn\u2019t open it automatically \u2014 use \u201cDownload a copy\u201d instead.");
});
$("openFolder").addEventListener("click", async () => {
  const r = await post("/api/open_folder");
  if (!r.ok) alert("Couldn\u2019t open the folder automatically. It is: " + $("outdir").textContent);
});
$("quit").addEventListener("click", async () => {
  await post("/api/quit");
  document.body.innerHTML = "<main><h1>Journal Maker has closed</h1><p class='sub'>You can close this tab.</p></main>";
});

function startPolling() { stopPolling(); poll = setInterval(tick, 700); tick(); }
function stopPolling() { if (poll) clearInterval(poll); poll = null; }

async function tick() {
  let s;
  try { s = await (await fetch("/api/status")).json(); } catch (e) { return; }
  $("outdir").textContent = s.output_dir;
  if (s.state === "running") {
    $("pmsg").textContent = s.message || "Working\u2026";
    $("pbar").style.width = s.percent + "%";
    $("log").textContent = s.log.join("\n");
  } else if (s.state === "done") {
    stopPolling(); show("progress", false); show("done", true);
    let t = s.pages + " pages.";
    if (s.failed) t += " (" + s.failed + " talk" + (s.failed > 1 ? "s" : "") + " couldn\u2019t be downloaded \u2014 run it again to retry those.)";
    $("dsub").textContent = t;
    $("dl").href = "/download/" + encodeURIComponent(s.pdf);
    $("where").textContent = "Saved as \u201c" + s.pdf + "\u201d in " + s.output_dir;
  } else if (s.state === "cancelled") {
    stopPolling(); show("progress", false);
    $("errmsg").textContent = s.message; $("errlog").textContent = ""; show("errbox", true);
  } else if (s.state === "error") {
    stopPolling(); show("progress", false);
    $("errmsg").textContent = s.error; $("errlog").textContent = s.log.join("\n"); show("errbox", true);
  }
}
// If the page is opened/refreshed while a job is running, pick it back up.
fetch("/api/status").then(r => r.json()).then(s => {
  if (s.state === "running") { show("form", false); show("progress", true); startPolling(); }
});
</script>
</body>
</html>
"""


def main():
    global PORT, OUT_DIR, SERVER
    ap = argparse.ArgumentParser(description="Conference Journal Maker (local web page)")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--out", help="folder where journals are saved "
                                  "(default: Documents/Conference Journals)")
    ap.add_argument("--no-browser", action="store_true", help="don't open the browser automatically")
    args = ap.parse_args()

    OUT_DIR = Path(args.out).expanduser() if args.out else default_output_dir()
    PORT = args.port
    while True:
        try:
            SERVER = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
            break
        except OSError:
            PORT += 1
    url = f"http://127.0.0.1:{PORT}/"
    print("=" * 60)
    print("  Conference Journal Maker is running.")
    print(f"  If your browser didn't open, go to:  {url}")
    print("  Keep this window open while you use it.")
    print("  To quit: click 'Quit' on the page, or close this window.")
    print("=" * 60)
    if not args.no_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        SERVER.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
