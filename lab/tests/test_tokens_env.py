import contextlib
import io
import os
import stat
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from labkit import env, tokens  # noqa: E402

# Fake value in the token format; not a real credential.
FAKE = "sk-ant-oat01-" + "A1b2_C3d-" * 10 + "XYZ"


class ExtractClaudeToken(unittest.TestCase):
    def test_token_between_ansi_codes(self):
        out = f"\x1b[32m✓ Token created\x1b[0m\r\n\x1b[1m{FAKE}\x1b[22m\r\nStore it safely"
        self.assertEqual(tokens.extract_claude_token(out), FAKE)

    def test_osc_link_is_stripped(self):
        out = f"\x1b]8;;https://example.invalid\x07link\x1b]8;;\x07 {FAKE}\n"
        self.assertEqual(tokens.extract_claude_token(out), FAKE)

    def test_no_token(self):
        self.assertIsNone(tokens.extract_claude_token("Login cancelled\n"))

    def test_last_token_wins(self):
        first = FAKE[:-3] + "AAA"
        self.assertEqual(tokens.extract_claude_token(f"{first}\n{FAKE}\n"), FAKE)


class ScreenMask(unittest.TestCase):
    """What `tokens claude` passes on to the terminal never contains the token."""

    BODY = FAKE[len("sk-ant-oat01-"):]

    def shown(self, *chunks):
        screen = tokens._ScreenMask()
        return b"".join(screen.feed(c.encode()) for c in chunks) + screen.flush()

    def assert_hidden(self, text):
        self.assertNotIn(FAKE, text)
        for i in range(0, len(self.BODY) - 12, 6):  # no longer piece of the secret part either
            self.assertNotIn(self.BODY[i:i + 12], text)

    def test_token_in_one_read_is_masked(self):
        text = self.shown(f"\x1b[1m{FAKE}\x1b[22m\r\nStore it safely").decode()
        self.assert_hidden(text)
        self.assertIn(env.mask(FAKE), text)
        self.assertIn("Store it safely", text)

    def test_token_split_across_two_reads_is_masked(self):
        for cut in (5, 12, 13, 40, len(FAKE) - 1, len(FAKE)):
            with self.subTest(cut=cut):
                text = self.shown(f"Your token: {FAKE[:cut]}", f"{FAKE[cut:]}\r\nDone").decode()
                self.assert_hidden(text)
                self.assertIn(env.mask(FAKE), text)
                self.assertTrue(text.startswith("Your token: ") and text.endswith("\r\nDone"), text)

    def test_other_output_passes_unchanged(self):
        self.assertEqual(self.shown("Opening browser", " to sign in…\r\n"), "Opening browser to sign in…\r\n".encode())


class CreateClaudeToken(unittest.TestCase):
    """create_claude_token with pty.spawn replaced by a pipe the test writes to."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.env_file = Path(tmp.name) / ".env"

    def run_setup_token(self, *chunks, delay=None):
        """Feed chunks to the read callback, one read each.

        With delay, the last chunk is written that many seconds after the
        read of the one before it started, like a child that stalls mid-token.
        """
        shown = []

        def fake_spawn(argv, master_read):
            r, w = os.pipe()
            timer = None
            try:
                for i, chunk in enumerate(chunks):
                    if timer is not None:
                        break  # the delayed chunk is on its way
                    os.write(w, chunk.encode())
                    if delay is not None and i == len(chunks) - 2:
                        timer = threading.Timer(delay, os.write, (w, chunks[-1].encode()))
                        timer.start()
                    shown.append(master_read(r))
                if timer is not None:
                    timer.join()
                os.close(w)
                while data := master_read(r):
                    shown.append(data)
            finally:
                os.close(r)
            return 0

        out = io.StringIO()
        with mock.patch.object(tokens.shutil, "which", return_value="/fake/claude"), \
             mock.patch.object(tokens.pty, "spawn", side_effect=fake_spawn), \
             contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            code = tokens.create_claude_token(self.env_file)
        return code, b"".join(shown).decode(), out.getvalue()

    def test_token_hidden_on_screen_but_stored(self):
        code, screen, out = self.run_setup_token(f"Token: {FAKE[:30]}", f"{FAKE[30:]}\r\n")
        self.assertEqual(code, 0)
        self.assertNotIn(FAKE[14:-4], screen + out)
        self.assertIn(env.mask(FAKE), screen)
        self.assertEqual(env.load(self.env_file, {})["CLAUDE_CODE_OAUTH_TOKEN"], FAKE)

    def test_read_never_returns_empty_while_holding_back(self):
        # An empty read result ends pty.spawn's copy loop, so a chunk that is
        # all held back waits briefly for the rest instead.
        code, screen, _ = self.run_setup_token("sk-ant-o", f"{FAKE[8:]}\r\n", delay=0.05)
        self.assertEqual(code, 0)
        self.assertNotIn(FAKE[14:-4], screen)
        self.assertEqual(env.load(self.env_file, {})["CLAUDE_CODE_OAUTH_TOKEN"], FAKE)

    def test_warns_before_and_clears_the_screen_after(self):
        _, _, out = self.run_setup_token(f"{FAKE}\r\n")
        warning = out.index("don't share or record your screen")
        clear = out.index("\x1b[H\x1b[2J\x1b[3J")
        self.assertLess(warning, clear)
        self.assertLess(clear, out.index("Stored CLAUDE_CODE_OAUTH_TOKEN="))

    def test_replacing_a_stored_token_warns_that_the_old_one_stays_valid(self):
        old = FAKE[:-3] + "OLD"
        env.upsert(self.env_file, "CLAUDE_CODE_OAUTH_TOKEN", old)
        _, _, out = self.run_setup_token(f"{FAKE}\r\n")
        self.assertIn("the old token stays valid until you revoke it on claude.ai", out)
        self.assertNotIn(old, out)
        _, _, out = self.run_setup_token(f"{FAKE}\r\n")  # same token again: nothing replaced
        self.assertNotIn("the old token stays valid", out)


class Upsert(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = Path(self.dir.name) / ".env"

    def tearDown(self):
        self.dir.cleanup()

    def test_creates_file_with_mode_600(self):
        env.upsert(self.path, "K", "v")
        self.assertEqual(self.path.read_text(), "K=v\n")
        self.assertEqual(stat.S_IMODE(self.path.stat().st_mode), 0o600)

    def test_replaces_key_and_keeps_other_lines(self):
        self.path.write_text("# comment\nA=1\nK=old\nB=2\nK=dup\n")
        env.upsert(self.path, "K", "new")
        self.assertEqual(self.path.read_text(), "# comment\nA=1\nK=new\nB=2\n")

    def test_tightens_existing_permissions(self):
        self.path.write_text("A=1\n")
        self.path.chmod(0o644)
        env.upsert(self.path, "K", "v")
        self.assertEqual(stat.S_IMODE(self.path.stat().st_mode), 0o600)


class Mask(unittest.TestCase):
    def test_hides_middle(self):
        masked = env.mask(FAKE)
        self.assertTrue(masked.startswith("sk-ant-oat01-"))
        self.assertNotIn(FAKE[20:60], masked)


if __name__ == "__main__":
    unittest.main()
