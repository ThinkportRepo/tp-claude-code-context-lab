"""Assessment: checks of the finished app, run once after the last turn.

Each check loads the published site once in headless Chrome. A local HTTP
server stands between Chrome and the site: it passes every request on (with
a cache-busting query, as the goal check does) and injects the lab's probe
script into HTML pages, ahead of the page's own scripts. For
`browser_script` it also injects the scenario's ASSESSMENT_SCRIPT, which
defines `window.labAssess = async (document) => ({step: true|false, ...})`.

After the page's `load` event the probe runs the check and writes
`{"steps": {...}, "errors": [...]}` into a `<pre id="lab-assessment">`,
which the lab reads from Chrome's `--dump-dom` output. A step that is not
`true` fails. Nothing here sends follow-ups or touches the goal.
"""

import html
import http.client
import http.server
import json
import os
import re
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from . import config
from .github import without_credentials

# Virtual time Chrome gives the page (timers run without real waiting).
VIRTUAL_TIME_MS = 10000
CHROME_TIMEOUT_S = 60
_FETCH_TIMEOUT_S = 30
_LAB_PATH = "/__lab__/"
_RESULT = re.compile(r'<pre id="lab-assessment">(.*?)</pre>', re.DOTALL)
_HEAD = re.compile(rb"<head\b[^>]*>", re.IGNORECASE)
_HTML = re.compile(rb"<html\b[^>]*>", re.IGNORECASE)

# Runs before the page's scripts: records errors and click handlers, then
# runs CHECK after `load` and writes the result into the DOM.
PROBE = r"""(() => {
  const CHECK = __CHECK__;
  const errors = [];
  addEventListener("error", (e) => {
    const t = e.target;
    const source = t && t !== window && (t.src || t.href);
    errors.push(source ? `failed to load ${source}` : String(e.message));
  }, true);
  addEventListener("unhandledrejection", (e) => errors.push(`unhandled rejection: ${e.reason}`));

  // Elements that get a click handler, through addEventListener or `onclick`.
  const clickable = new Set();
  const CLICKS = ["click", "pointerdown", "pointerup", "mousedown", "mouseup"];
  const add = EventTarget.prototype.addEventListener;
  EventTarget.prototype.addEventListener = function (type, ...rest) {
    if (CLICKS.includes(type) && this instanceof Element) clickable.add(this);
    return add.call(this, type, ...rest);
  };
  const onclick = Object.getOwnPropertyDescriptor(HTMLElement.prototype, "onclick");
  Object.defineProperty(HTMLElement.prototype, "onclick", {
    ...onclick,
    set(handler) { if (handler) clickable.add(this); onclick.set.call(this, handler); },
  });

  const tick = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
  const state = () => document.body.innerHTML
    + JSON.stringify([...document.querySelectorAll("input, select, textarea")].map((e) => e.value));
  // Click targets in document order; a handler on a container stands for its innermost elements.
  const targets = () => {
    const found = new Set(document.querySelectorAll(
      'button:not([disabled]), [role="button"], [onclick], input[type="button"]'));
    for (const el of clickable) {
      const leaves = [...el.querySelectorAll("*")].filter((c) => c.children.length === 0);
      for (const c of leaves.length ? leaves : [el]) found.add(c);
    }
    return [...found].filter((el) => el.isConnected)
      .sort((a, b) => a.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING ? -1 : 1);
  };

  const checks = {
    page_ok: async () => ({
      no_errors: errors.length === 0,
      content: !!document.body && (document.body.innerText.trim() !== ""
        || !!document.body.querySelector("canvas, svg, img, video")),
    }),
    // Clicks targets until one changes the page; links and forms must not navigate away.
    reacts_to_clicks: async () => {
      add.call(document, "click", (e) => { if (e.target.closest("a[href]")) e.preventDefault(); }, true);
      add.call(document, "submit", (e) => e.preventDefault(), true);
      const before = errors.length;
      const all = targets().slice(0, 20);
      let reacts = false;
      for (const el of all) {
        const old = state();
        el.click();
        await tick(100);
        if (state() !== old) { reacts = true; break; }
      }
      return { clickable: all.length > 0, reacts, no_errors: errors.length === before };
    },
    browser_script: async () => (typeof window.labAssess === "function"
      ? await window.labAssess(document) : { defines_labAssess: false }),
  };

  add.call(window, "load", async () => {
    await tick(500);
    let steps;
    try {
      steps = await checks[CHECK]();
    } catch (e) {
      errors.push(String(e && e.stack || e));
      steps = { runs: false };
    }
    const out = document.createElement("pre");
    out.id = "lab-assessment";
    out.textContent = JSON.stringify({ steps, errors });
    document.documentElement.append(out);
  });
})();
"""


def run(
    assessment: config.Assessment, site_url: str, lab_root: Path, chrome: str
) -> tuple[list[str], list[str]]:
    """Run every check of `assessment` against the site at `site_url`.

    Returns (failed, errors): the failed steps as "<check>:<step>", in check
    order, and the errors the pages reported. A check without a result (the
    site or Chrome failed) fails as "<check>:no_result".
    """
    failed: list[str] = []
    errors: list[str] = []
    for check in assessment.checks:
        scripts = {"probe.js": PROBE.replace("__CHECK__", json.dumps(check))}
        if check == "browser_script":
            scripts["assessment.js"] = (lab_root / config.ASSESSMENT_SCRIPT).read_text()
        with _proxy(site_url, scripts) as local_url:
            check_failed, check_errors = outcome(check, _dump_dom(chrome, local_url))
        failed += check_failed
        errors += check_errors
    return failed, errors


def inject(page: bytes, tags: bytes) -> bytes:
    """`page` with `tags` right after its <head> tag, else after <html>, else at the start."""
    for pattern in (_HEAD, _HTML):
        m = pattern.search(page)
        if m:
            return page[: m.end()] + tags + page[m.end():]
    return tags + page


def outcome(check: str, dom: str | None) -> tuple[list[str], list[str]]:
    """(failed steps as "<check>:<step>", errors) from Chrome's dumped DOM."""
    m = _RESULT.search(dom or "")
    if not m:
        return [f"{check}:no_result"], []
    result = json.loads(html.unescape(m.group(1)))
    steps = result.get("steps")
    errors = [str(e) for e in result.get("errors") or []]
    if not isinstance(steps, dict) or not steps:
        return [f"{check}:no_steps"], errors
    return [f"{check}:{name}" for name, ok in steps.items() if ok is not True], errors


class _Proxy(http.server.ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, upstream: str, scripts: dict[str, str]):
        super().__init__(("127.0.0.1", 0), _Handler)
        self.upstream = upstream
        self.scripts = {name: text.encode() for name, text in scripts.items()}
        self.tags = b"".join(f'<script src="{_LAB_PATH}{name}"></script>'.encode() for name in scripts)


class _Handler(http.server.BaseHTTPRequestHandler):
    server: _Proxy

    def do_GET(self):
        name = self.path[len(_LAB_PATH):] if self.path.startswith(_LAB_PATH) else None
        if name is not None:
            body = self.server.scripts.get(name)
            status, ctype = (200, "text/javascript") if body is not None else (404, "text/plain")
            self._send(status, ctype, body or b"")
            return
        url = self.server.upstream + self.path
        url += f"{'&' if '?' in url else '?'}lab-check={time.monotonic_ns()}"
        try:
            with urllib.request.urlopen(url, timeout=_FETCH_TIMEOUT_S) as resp:
                status, ctype, body = resp.status, resp.headers.get("Content-Type", ""), resp.read()
        except urllib.error.HTTPError as e:
            status, ctype, body = e.code, e.headers.get("Content-Type", ""), e.read()
            e.close()
        except (urllib.error.URLError, OSError, http.client.HTTPException):
            status, ctype, body = 502, "text/plain", b"upstream failed"
        if "text/html" in ctype:
            body = inject(body, self.server.tags)
        self._send(status, ctype, body)

    def _send(self, status: int, ctype: str, body: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


class _proxy:
    """Context manager: a running _Proxy for `site_url`; yields the local URL of the site."""

    def __init__(self, site_url: str, scripts: dict[str, str]):
        parts = urllib.parse.urlsplit(site_url)
        self.server = _Proxy(f"{parts.scheme}://{parts.netloc}", scripts)
        self.path = urllib.parse.urlunsplit(("", "", parts.path or "/", parts.query, ""))

    def __enter__(self) -> str:
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        return f"http://127.0.0.1:{self.server.server_address[1]}{self.path}"

    def __exit__(self, *exc):
        self.server.shutdown()
        self.server.server_close()


def _dump_dom(chrome: str, url: str) -> str | None:
    """The DOM of `url` after VIRTUAL_TIME_MS in a throwaway headless Chrome; None if Chrome fails."""
    with tempfile.TemporaryDirectory(prefix="lab-chrome-") as profile:
        try:
            cp = subprocess.run(
                [chrome, "--headless", "--disable-gpu", "--no-first-run", "--no-default-browser-check",
                 f"--user-data-dir={profile}", f"--virtual-time-budget={VIRTUAL_TIME_MS}", "--dump-dom", url],
                env=without_credentials(os.environ), stdin=subprocess.DEVNULL,
                capture_output=True, text=True, timeout=CHROME_TIMEOUT_S,
            )  # fmt: skip
        except (OSError, subprocess.SubprocessError):
            return None
    return cp.stdout if cp.returncode == 0 else None
