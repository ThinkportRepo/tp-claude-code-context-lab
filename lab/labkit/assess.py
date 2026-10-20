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
VIRTUAL_TIME_MS = 30000
CHROME_TIMEOUT_S = 60
_FETCH_TIMEOUT_S = 30
_LAB_PATH = "/__lab__/"
_RESULT = re.compile(r'<pre id="lab-assessment">(.*?)</pre>', re.DOTALL)
_HEAD = re.compile(rb"<head\b[^>]*>", re.IGNORECASE)
_HTML = re.compile(rb"<html\b[^>]*>", re.IGNORECASE)
_DOCTYPE = re.compile(rb"\s*<!doctype\b[^>]*>", re.IGNORECASE)
_COMMENT = re.compile(rb"<!--.*?-->", re.DOTALL)
_CHECK_PARAM = re.compile(r"[?&]lab-check=\d+")

# Runs before the page's scripts: records errors and click handlers, then
# runs CHECK after `load` and writes the result into the DOM.
PROBE = r"""(() => {
  const CHECK = __CHECK__;
  // Set when a click navigates (e.g. a restart that reloads the page).
  const NAVIGATED = "lab-assessment-navigated";
  const errors = [];
  addEventListener("error", (e) => {
    const t = e.target;
    const source = t && t !== window && (t.src || t.href);
    errors.push(source ? `failed to load ${source}` : String(e.message));
  }, true);
  addEventListener("unhandledrejection", (e) => errors.push(`unhandled rejection: ${e.reason}`));

  // Elements that get a click handler, through addEventListener or `onclick`;
  // a handler on the document or window delegates for the whole body.
  const clickable = new Set();
  let delegated = false;
  const CLICKS = ["click", "pointerdown", "pointerup", "mousedown", "mouseup"];
  const add = EventTarget.prototype.addEventListener;
  EventTarget.prototype.addEventListener = function (type, ...rest) {
    if (CLICKS.includes(type)) {
      if (this instanceof Element) clickable.add(this);
      else if (this === document || this === window) delegated = true;
    }
    return add.call(this, type, ...rest);
  };
  const onclick = Object.getOwnPropertyDescriptor(HTMLElement.prototype, "onclick");
  Object.defineProperty(HTMLElement.prototype, "onclick", {
    ...onclick,
    set(handler) { if (handler) clickable.add(this); onclick.set.call(this, handler); },
  });

  const tick = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
  // How long reacts_to_clicks watches the page without clicking: what changes
  // within it (a clock, an animation) is the page's own doing.
  const QUIET_MS = 2000;
  // Inputs a click changes without any script: the clicked one, a label's
  // control, and the other radios of its group.
  const toggledByBrowser = (clicked) => {
    const control = clicked.control || clicked.closest?.("label")?.control || clicked;
    const toggled = new Set([clicked, control]);
    if (control.type === "radio" && control.name) {
      for (const r of document.querySelectorAll('input[type="radio"]')) {
        if (r.name === control.name && r.form === control.form) toggled.add(r);
      }
    }
    return toggled;
  };
  // What a click can change outside the DOM tree: form state and canvas pixels.
  const formState = (ignored) => JSON.stringify([...document.querySelectorAll("input, select, textarea")]
    .filter((e) => !ignored.has(e)).map((e) => [e.value, e.checked, e.selectedIndex]));
  const pixels = () => [...document.querySelectorAll("canvas")]
    .map((c) => { try { return c.toDataURL(); } catch { return ""; } }).join();
  // The nodes that change while `action` runs and `ms` pass.
  const changedNodes = async (action, ms) => {
    const nodes = new Set();
    const observer = new MutationObserver((records) => records.forEach((r) => nodes.add(r.target)));
    observer.observe(document.documentElement,
      { subtree: true, childList: true, attributes: true, characterData: true });
    action();
    await tick(ms);
    observer.disconnect();
    return nodes;
  };
  // Click targets in document order; a handler on a container stands for its innermost elements.
  const targets = () => {
    const found = new Set(document.querySelectorAll('button:not([disabled]), [role="button"], [onclick],'
      + ' input[type="button"], input[type="checkbox"], input[type="radio"]'));
    for (const el of [...clickable, ...(delegated ? [document.body] : [])]) {
      const leaves = [...el.querySelectorAll("*")].filter((c) => c.children.length === 0);
      for (const c of leaves.length ? leaves : [el]) found.add(c);
    }
    return [...found].filter((el) => el.isConnected && el.tagName !== "SCRIPT")
      .sort((a, b) => a.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING ? -1 : 1);
  };

  const checks = {
    page_ok: async () => ({
      no_errors: errors.length === 0,
      content: !!document.body && (document.body.innerText.trim() !== ""
        || !!document.body.querySelector("canvas, svg, img, video")),
    }),
    // Clicks targets until one changes the page. Nodes that changed during
    // QUIET_MS without a click do not count, so changes that repeat slower
    // or start later can still pass. Links and forms must not navigate away;
    // a click that reloads the page (within the click window) counts.
    reacts_to_clicks: async () => {
      add.call(document, "click", (e) => { if (e.target.closest?.("a[href]")) e.preventDefault(); }, true);
      add.call(document, "submit", (e) => e.preventDefault(), true);
      const before = errors.length;
      // Set while a click and its window run; a navigation then is the click's.
      let clicking = false;
      add.call(window, "beforeunload", () => {
        if (clicking) sessionStorage.setItem(NAVIGATED, JSON.stringify(errors.slice(before)));
      });
      const all = targets().slice(0, 50);
      const startPixels = pixels();
      const own = await changedNodes(() => {}, QUIET_MS);
      const canvasMoves = pixels() !== startPixels;
      let reacts = false;
      for (const el of all) {
        const ignored = toggledByBrowser(el);
        const form = formState(ignored);
        const image = pixels();
        clicking = true;
        const nodes = await changedNodes(() => el.click(), 100);
        clicking = false;
        if ([...nodes].some((n) => !own.has(n)) || formState(ignored) !== form
            || (!canvasMoves && pixels() !== image)) {
          reacts = true;
          break;
        }
      }
      return { clickable: all.length > 0, reacts, no_errors: errors.length === before };
    },
    browser_script: async () => (typeof window.labAssess === "function"
      ? await window.labAssess(document) : { defines_labAssess: false }),
  };

  // Steps as true/false: anything but `true`, including `undefined`, fails.
  const asSteps = (raw) => (raw && typeof raw === "object" && !Array.isArray(raw)
    ? Object.fromEntries(Object.entries(raw).map(([name, ok]) => [name, ok === true]))
    : { returns_steps: false });
  const report = (steps, errs) => {
    const out = document.createElement("pre");
    out.id = "lab-assessment";
    out.textContent = JSON.stringify({ steps, errors: errs.map(String) });
    document.documentElement.append(out);
  };

  add.call(window, "load", async () => {
    const navigated = sessionStorage.getItem(NAVIGATED);
    if (CHECK === "reacts_to_clicks" && navigated !== null) {
      sessionStorage.removeItem(NAVIGATED);
      const before = JSON.parse(navigated);
      report({ clickable: true, reacts: true, no_errors: before.length === 0 }, before);
      return;
    }
    await tick(500);
    let steps;
    try {
      steps = asSteps(await checks[CHECK]());
    } catch (e) {
      errors.push(String(e && e.stack || e));
      steps = { runs: false };
    }
    report(steps, errors);
  });
})();
"""


def run(
    assessment: config.Assessment, site_url: str, lab_root: Path, chrome: str
) -> tuple[list[str], list[str]]:
    """Run every check of `assessment` against the site at `site_url`.

    Returns (failed, errors): the failed steps as "<check>:<step>", in check
    order, and the errors the pages, the site or Chrome reported. A check
    whose Chrome fails fails as "<check>:chrome_failed", one whose page
    writes no result (e.g. the site is unreachable) as "<check>:no_result".
    """
    failed: list[str] = []
    errors: list[str] = []
    for check in assessment.checks:
        scripts = {"probe.js": PROBE.replace("__CHECK__", json.dumps(check))}
        if check == "browser_script":
            scripts["assessment.js"] = (lab_root / config.ASSESSMENT_SCRIPT).read_text()
        with _proxy(site_url, scripts) as proxy:
            dom, problem = _dump_dom(chrome, proxy.url)
        if dom is None:
            check_failed, check_errors = [f"{check}:chrome_failed"], [problem]
        else:
            check_failed, check_errors = outcome(check, dom)
            if check_failed == [f"{check}:no_result"]:
                check_errors += proxy.problems or ["the page wrote no result"]
        failed += check_failed
        errors += check_errors
    return failed, errors


def inject(page: bytes, tags: bytes) -> bytes:
    """`page` with `tags` right after its <head> tag, else after <html>, else after the doctype.

    Tags inside comments do not count.
    """
    comments = [m.span() for m in _COMMENT.finditer(page)]
    for pattern in (_HEAD, _HTML):
        for m in pattern.finditer(page):
            if not any(start <= m.start() < end for start, end in comments):
                return page[: m.end()] + tags + page[m.end():]
    doctype = _DOCTYPE.match(page)
    at = doctype.end() if doctype else 0
    return page[:at] + tags + page[at:]


def local_location(upstream: str, location: str) -> tuple[str, str | None]:
    """A redirect's Location for Chrome: back onto the proxy, without the cache-busting query.

    A Location on another host or scheme stays as it is, so Chrome leaves
    the proxy and the page gets no probe; the second value names that.
    """
    location = _CHECK_PARAM.sub("", location.removeprefix(upstream))
    if urllib.parse.urlsplit(location).netloc:
        return location, f"redirected outside the site to {location}"
    return location, None


def upstream_url(upstream: str, path: str) -> str:
    """The site URL for `path`, with a cache-busting query, as the goal check uses."""
    url = upstream + path
    return f"{url}{'&' if '?' in url else '?'}lab-check={time.monotonic_ns()}"


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


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Hand redirects to Chrome, so it resolves relative URLs against the new location."""

    def redirect_request(self, *args):
        return None


_OPENER = urllib.request.build_opener(_NoRedirect)


class _Proxy(http.server.ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, upstream: str, scripts: dict[str, str]):
        super().__init__(("127.0.0.1", 0), _Handler)
        self.upstream = upstream
        self.scripts = {name: text.encode() for name, text in scripts.items()}
        self.tags = b"".join(f'<script src="{_LAB_PATH}{name}"></script>'.encode() for name in scripts)
        self.problems: list[str] = []  # requests the site did not answer


class _Handler(http.server.BaseHTTPRequestHandler):
    server: _Proxy

    def do_GET(self):
        name = self.path[len(_LAB_PATH):] if self.path.startswith(_LAB_PATH) else None
        if name is not None:
            body = self.server.scripts.get(name)
            status, ctype = (200, "text/javascript") if body is not None else (404, "text/plain")
            self._send(status, ctype, body or b"")
            return
        location = None
        try:
            url = upstream_url(self.server.upstream, self.path)
            with _OPENER.open(url, timeout=_FETCH_TIMEOUT_S) as resp:
                status, ctype, body = resp.status, resp.headers.get("Content-Type", ""), resp.read()
        except urllib.error.HTTPError as e:
            status, ctype, body = e.code, e.headers.get("Content-Type", ""), e.read()
            location = e.headers.get("Location")
            e.close()
        except (urllib.error.URLError, OSError, http.client.HTTPException) as exc:
            self.server.problems.append(f"{self.path}: the site did not answer ({exc})")
            status, ctype, body = 502, "text/plain", b"upstream failed"
        if "text/html" in ctype:
            body = inject(body, self.server.tags)
        if location:
            location, problem = local_location(self.server.upstream, location)
            if problem:
                self.server.problems.append(f"{self.path}: {problem}")
        self._send(status, ctype, body, location)

    def _send(self, status: int, ctype: str, body: bytes, location: str | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        if location:
            self.send_header("Location", location)
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


class _proxy:
    """Context manager: a running _Proxy for `site_url`.

    Yields itself: `url` is the site's local URL, `problems` lists the
    requests the site did not answer.
    """

    def __init__(self, site_url: str, scripts: dict[str, str]):
        parts = urllib.parse.urlsplit(site_url)
        self.server = _Proxy(f"{parts.scheme}://{parts.netloc}", scripts)
        self.path = urllib.parse.urlunsplit(("", "", parts.path or "/", parts.query, ""))
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}{self.path}"
        self.problems = self.server.problems

    def __enter__(self) -> "_proxy":
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        return self

    def __exit__(self, *exc):
        self.server.shutdown()
        self.server.server_close()


def _dump_dom(chrome: str, url: str) -> tuple[str | None, str]:
    """(DOM of `url` after VIRTUAL_TIME_MS in a throwaway headless Chrome, "").

    (None, why) if Chrome does not start, times out or exits non-zero.
    """
    with tempfile.TemporaryDirectory(prefix="lab-chrome-") as profile:
        try:
            cp = subprocess.run(
                [chrome, "--headless", "--disable-gpu", "--no-first-run", "--no-default-browser-check",
                 f"--user-data-dir={profile}", f"--virtual-time-budget={VIRTUAL_TIME_MS}", "--dump-dom", url],
                env=without_credentials(os.environ), stdin=subprocess.DEVNULL,
                capture_output=True, text=True, timeout=CHROME_TIMEOUT_S,
            )  # fmt: skip
        except subprocess.TimeoutExpired:
            return None, f"Chrome did not finish within {CHROME_TIMEOUT_S} s"
        except (OSError, subprocess.SubprocessError) as exc:
            return None, f"Chrome did not start ({exc})"
    if cp.returncode != 0:
        last = (cp.stderr.strip().splitlines() or ["no output"])[-1]
        return None, f"Chrome exited {cp.returncode}: {last}"
    return cp.stdout, ""
