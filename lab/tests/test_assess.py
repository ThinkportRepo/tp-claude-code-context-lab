import functools
import http.server
import sys
import tempfile
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from labkit import assess, config, preflight  # noqa: E402

CHROME = preflight.chrome_path(Path.home())

# Test sites, each served as /lab-x/index.html by a local stand-in for GitHub Pages.
GAME = """<!doctype html><html><head><title>t</title></head><body>
<p id="status">X to move</p><button id="restart">Restart</button><div id="board"></div>
<script src="app.js"></script></body></html>"""
GAME_JS = """const board = document.getElementById("board");
let turn = "X";
for (let i = 0; i < 9; i++) {
  const cell = document.createElement("div");
  cell.addEventListener("click", () => {
    if (cell.textContent) return;
    cell.textContent = turn;
    turn = turn === "X" ? "O" : "X";
  });
  board.append(cell);
}
"""
BROKEN = """<!doctype html><html><head><script src="missing.js"></script></head>
<body><button onclick="this.textContent='x'">go</button></body></html>"""
STATIC = "<!doctype html><html><body><h1>Hello GitHub Pages</h1></body></html>"
EMPTY = "<!doctype html><html><body></body></html>"
CLOCK = """<!doctype html><html><body><p id="t">0</p><button>does nothing</button>
<script>let n = 0; setInterval(() => { document.getElementById("t").textContent = ++n; }, 20);</script></body></html>"""
BODY_CLASS = """<!doctype html><html><body><p>theme</p><button>dark</button>
<script>document.querySelector("button").addEventListener("click", () => document.body.classList.toggle("dark"));</script>
</body></html>"""
CHECKBOX = """<!doctype html><html><body><label><input type="checkbox"> remember</label><p id="s">off</p>
<script>document.querySelector("input").addEventListener("change", (e) => {
  document.getElementById("s").textContent = e.target.checked ? "on" : "off"; });</script></body></html>"""
PLAIN_CHECKBOX = """<!doctype html><html><body><label><input type="checkbox"> remember</label></body></html>"""
SLOW_CLOCK = """<!doctype html><html><body><p id="t">0</p><div id="b"></div>
<script>let n = 0; setInterval(() => { document.getElementById("t").textContent = ++n; }, 1000);
for (let i = 0; i < 9; i++) { const c = document.createElement("button"); c.onclick = () => {};
  document.getElementById("b").append(c); }</script></body></html>"""
ANIMATION = """<!doctype html><html><body><ul>""" + "<li>x</li>" * 8 + """</ul>
<button>a</button><button>b</button><button>c</button>
<script>const items = document.querySelectorAll("li"); let i = 0;
setInterval(() => { items[i % 8].classList.toggle("on"); i++; }, 60);</script></body></html>"""
VS_COMPUTER = """<!doctype html><html><head><style>.pop{color:red}</style></head><body>
<p id="s">your move</p><div id="b"></div>
<script>const b = document.getElementById("b"), s = document.getElementById("s");
for (let i = 0; i < 9; i++) { const c = document.createElement("button"); b.append(c);
  c.addEventListener("click", () => {
    if (c.textContent) return;
    c.textContent = "X"; c.classList.add("pop"); s.textContent = "thinking";
    setTimeout(() => c.classList.remove("pop"), 300);
    setTimeout(() => { const free = [...b.children].find((x) => !x.textContent);
      if (free) free.textContent = "O"; s.textContent = "your move"; }, 500);
  }); }</script></body></html>"""
RADIOS = """<!doctype html><html><body><label><input type="radio" name="m"> easy</label>
<label><input type="radio" name="m"> hard</label></body></html>"""
LABEL = """<!doctype html><html><body><input type="checkbox" id="x"><label for="x">sound</label>
<script>document.addEventListener("click", () => {});</script></body></html>"""
LATER_RELOAD = """<!doctype html><html><body><p>game</p>
<button onclick="setTimeout(() => location.reload(), __DELAY__)">New game</button></body></html>"""
SPLASH = """<!doctype html><html><body><p>loading</p><button>dead</button>
<script>if (!sessionStorage.getItem("seen")) { sessionStorage.setItem("seen", "1");
  setTimeout(() => { location.href = "./?game"; }, 800); }</script></body></html>"""
CANVAS = """<!doctype html><html><body><canvas width="50" height="50"></canvas>
<script>const c = document.querySelector("canvas");
c.addEventListener("click", () => { const g = c.getContext("2d"); g.fillStyle = "red"; g.fillRect(0, 0, 50, 50); });
</script></body></html>"""
RELOADS = """<!doctype html><html><body><p>game</p><button onclick="location.reload()">New game</button></body></html>"""
DELEGATED = """<!doctype html><html><body><p id="s">X</p><div id="b"><div class="c"></div><div class="c"></div></div>
<script>document.addEventListener("click", (e) => {
  if (e.target.classList.contains("c")) e.target.textContent = "X"; });</script></body></html>"""
VISITS = """<!doctype html><html><body><p id="n"></p>
<script>const n = Number(localStorage.getItem("visits") || 0) + 1; localStorage.setItem("visits", n);
document.getElementById("n").textContent = n;</script></body></html>"""
THROWS = """<!doctype html><html><body><p>text</p><button>b</button>
<script>document.querySelector("button").onclick = () => { throw new Error("boom"); };</script></body></html>"""


class Inject(unittest.TestCase):
    TAG = b"<script src=x></script>"

    def test_after_the_head_tag(self):
        self.assertEqual(
            assess.inject(b'<html><HEAD lang="x"><title>t</title>', self.TAG),
            b'<html><HEAD lang="x">' + self.TAG + b"<title>t</title>",
        )

    def test_after_the_html_tag_without_head(self):
        self.assertEqual(assess.inject(b"<!doctype html><html><body>", self.TAG),
                         b"<!doctype html><html>" + self.TAG + b"<body>")

    def test_at_the_start_without_either(self):
        self.assertEqual(assess.inject(b"<p>hi</p>", self.TAG), self.TAG + b"<p>hi</p>")

    def test_after_the_doctype_without_either(self):
        # Before the doctype the page would render in quirks mode.
        self.assertEqual(assess.inject(b"<!DOCTYPE html><title>t</title>", self.TAG),
                         b"<!DOCTYPE html>" + self.TAG + b"<title>t</title>")

    def test_tags_in_comments_are_skipped(self):
        page = b"<!doctype html><!-- <head> --><html><head><title>t</title>"
        self.assertEqual(assess.inject(page, self.TAG),
                         b"<!doctype html><!-- <head> --><html><head>" + self.TAG + b"<title>t</title>")


class LocalLocation(unittest.TestCase):
    def test_a_redirect_on_the_site_stays_on_the_proxy(self):
        for location in ("https://o.github.io/lab-x/?lab-check=12", "/lab-x/?lab-check=12"):
            with self.subTest(location=location):
                self.assertEqual(assess.local_location("https://o.github.io", location), ("/lab-x/", None))
        self.assertEqual(
            assess.local_location("https://o.github.io", "/a?v=2&lab-check=1"), ("/a?v=2", None)
        )

    def test_a_redirect_off_the_site_is_named(self):
        location, problem = assess.local_location("https://o.github.io", "https://example.com/x")
        self.assertEqual(location, "https://example.com/x")
        self.assertEqual(problem, "redirected outside the site to https://example.com/x")


class UpstreamUrl(unittest.TestCase):
    def test_cache_busting_query_is_added_or_appended(self):
        url = assess.upstream_url("https://o.github.io", "/lab-x/")
        self.assertRegex(url, r"^https://o\.github\.io/lab-x/\?lab-check=\d+$")
        self.assertRegex(assess.upstream_url("https://o.github.io", "/a.js?v=2"), r"/a\.js\?v=2&lab-check=\d+$")


class Result(unittest.TestCase):
    def test_failed_steps_from_the_dumped_dom(self):
        dom = (
            '<html><body></body><pre id="lab-assessment">'
            '{"steps": {"a": true, "b": false}, "errors": ["e &amp; f"]}</pre></html>'
        )
        self.assertEqual(assess.outcome("page_ok", dom), (["page_ok:b"], ["e & f"]))

    def test_no_result_or_no_steps_fail(self):
        self.assertEqual(assess.outcome("page_ok", "<html></html>"), (["page_ok:no_result"], []))
        self.assertEqual(assess.outcome("page_ok", None), (["page_ok:no_result"], []))
        empty = '<pre id="lab-assessment">{"steps": {}, "errors": []}</pre>'
        self.assertEqual(assess.outcome("browser_script", empty), (["browser_script:no_steps"], []))


@unittest.skipUnless(CHROME, "Google Chrome is not installed")
class InChrome(unittest.TestCase):
    """assess.run against local sites in real headless Chrome."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve()
        self.site = self.root / "pages" / "lab-x"
        self.site.mkdir(parents=True)
        handler = functools.partial(_QuietHandler, directory=str(self.root / "pages"))
        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        self.url = f"http://127.0.0.1:{server.server_address[1]}/lab-x/"

    def run_checks(self, html, *checks, files=None, script=None):
        (self.site / "index.html").write_text(html)
        for name, text in (files or {}).items():
            (self.site / name).write_text(text)
        if script:
            (self.root / config.ASSESSMENT_SCRIPT).write_text(script)
        return assess.run(config.Assessment(checks), self.url, self.root, CHROME)

    def test_a_working_page_passes_both_generic_checks(self):
        failed, errors = self.run_checks(GAME, "page_ok", "reacts_to_clicks", files={"app.js": GAME_JS})
        self.assertEqual((failed, errors), ([], []))

    def test_a_missing_script_is_an_error(self):
        failed, errors = self.run_checks(BROKEN, "page_ok")
        self.assertEqual(failed, ["page_ok:no_errors"])
        self.assertIn("missing.js", errors[0])

    def test_an_empty_page_has_no_content(self):
        self.assertEqual(self.run_checks(EMPTY, "page_ok")[0], ["page_ok:content"])

    def test_a_static_page_has_nothing_to_click(self):
        failed, _ = self.run_checks(STATIC, "page_ok", "reacts_to_clicks")
        self.assertEqual(failed, ["reacts_to_clicks:clickable", "reacts_to_clicks:reacts"])

    def test_a_click_that_throws_fails(self):
        failed, errors = self.run_checks(THROWS, "reacts_to_clicks")
        self.assertEqual(failed, ["reacts_to_clicks:reacts", "reacts_to_clicks:no_errors"])
        self.assertIn("boom", errors[0])

    def test_browser_script_steps(self):
        script = (
            "window.labAssess = async (doc) => ({ title: doc.title === 't',"
            " cells: doc.querySelectorAll('#board div').length === 8 });\n"
        )
        failed, _ = self.run_checks(GAME, "browser_script", files={"app.js": GAME_JS}, script=script)
        self.assertEqual(failed, ["browser_script:cells"])

    def test_each_check_gets_a_fresh_browser(self):
        # The page counts its visits in localStorage; each check must be the first visit.
        script = "window.labAssess = async (doc) => ({ first: doc.getElementById('n').textContent === '1' });\n"
        failed, _ = self.run_checks(VISITS, "page_ok", "browser_script", script=script)
        self.assertEqual(failed, [])

    def test_an_unreachable_site_fails_with_the_reason(self):
        failed, errors = assess.run(config.Assessment(("page_ok",)), "http://127.0.0.1:9/lab-x/", self.root, CHROME)
        self.assertEqual(failed, ["page_ok:no_result"])
        self.assertIn("/lab-x/", errors[0])

    def test_a_chrome_that_fails_is_reported(self):
        (self.site / "index.html").write_text(STATIC)
        failed, errors = assess.run(config.Assessment(("page_ok",)), self.url, self.root, str(self.root / "nochrome"))
        self.assertEqual(failed, ["page_ok:chrome_failed"])
        self.assertTrue(errors)

    def test_a_step_that_is_not_true_fails_even_if_undefined(self):
        script = "window.labAssess = async (doc) => ({ ok: true, board: doc.querySelector('#nope')?.isConnected });\n"
        failed, _ = self.run_checks(STATIC, "browser_script", script=script)
        self.assertEqual(failed, ["browser_script:board"])

    def test_a_page_that_changes_by_itself_is_no_reaction(self):
        failed, _ = self.run_checks(CLOCK, "reacts_to_clicks")
        self.assertEqual(failed, ["reacts_to_clicks:reacts"])

    def test_reactions_outside_the_body_markup_count(self):
        for name, page in (("body class", BODY_CLASS), ("checkbox", CHECKBOX), ("canvas", CANVAS)):
            with self.subTest(page=name):
                self.assertEqual(self.run_checks(page, "reacts_to_clicks")[0], [])

    def test_changes_slower_than_clicks_are_no_reaction(self):
        for name, page in (("1 s clock", SLOW_CLOCK), ("animation over several nodes", ANIMATION)):
            with self.subTest(page=name):
                self.assertEqual(self.run_checks(page, "reacts_to_clicks")[0], ["reacts_to_clicks:reacts"])

    def test_a_click_with_delayed_follow_ups_is_a_reaction(self):
        # The cell flashes, the status changes and the computer answers later: still a reaction.
        self.assertEqual(self.run_checks(VS_COMPUTER, "reacts_to_clicks")[0], [])

    def test_inputs_the_browser_toggles_by_itself_are_no_reaction(self):
        # A checkbox, a radio group or a label's checkbox change without any script.
        for name, page in (("checkbox", PLAIN_CHECKBOX), ("radios", RADIOS), ("label", LABEL)):
            with self.subTest(page=name):
                self.assertEqual(self.run_checks(page, "reacts_to_clicks")[0], ["reacts_to_clicks:reacts"])

    def test_a_reload_started_after_the_click_counts(self):
        # Also after the click window: the page must not reload until Chrome times out.
        for delay in ("0", "400"):
            with self.subTest(delay=delay):
                self.assertEqual(self.run_checks(LATER_RELOAD.replace("__DELAY__", delay), "reacts_to_clicks")[0], [])

    def test_a_page_that_navigates_by_itself_is_no_reaction(self):
        self.assertEqual(self.run_checks(SPLASH, "reacts_to_clicks")[0], ["reacts_to_clicks:reacts"])

    def test_a_reload_on_click_counts_as_a_reaction(self):
        self.assertEqual(self.run_checks(RELOADS, "reacts_to_clicks")[0], [])

    def test_a_handler_on_the_document_is_found(self):
        self.assertEqual(self.run_checks(DELEGATED, "reacts_to_clicks")[0], [])

    def test_a_redirect_reaches_chrome(self):
        # The server redirects /lab-x to /lab-x/; app.js must resolve against the new URL.
        (self.site / "index.html").write_text(GAME)
        (self.site / "app.js").write_text(GAME_JS)
        url = self.url.rstrip("/")
        self.assertEqual(assess.run(config.Assessment(("page_ok",)), url, self.root, CHROME), ([], []))


class _QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


if __name__ == "__main__":
    unittest.main()
