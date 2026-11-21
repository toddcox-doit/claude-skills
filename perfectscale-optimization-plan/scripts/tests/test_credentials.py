"""One credential entry per tenant: storage, verification, the pscli login that never puts the
secret on a command line, the collector's offer (only when a person is at a terminal), the
onboarding auth command, and the --check preflight."""
import json
import os
import shutil
import stat
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent)); sys.path.insert(0, str(HERE))
import fixtures  # noqa: E402
from psplan import apiv2, credentials, preflight  # noqa: E402
from psplan.config import Config  # noqa: E402

SECRET = "s3cr3t-never-shown"
CID = "client-id-123"


class Home(unittest.TestCase):
    """Each test gets its own HOME, so ~/.perfectscale is a temp folder and no real file is touched."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.home = mock.patch.object(Path, "home", return_value=self.tmp)
        self.home.start()
        self.env = mock.patch.dict(os.environ, {}, clear=False)
        self.env.start()
        os.environ.pop("PERFECTSCALE_CLIENT_ID", None); os.environ.pop("PERFECTSCALE_CLIENT_SECRET", None)

    def tearDown(self):
        self.env.stop(); self.home.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)


class Storage(Home):
    def test_saved_owner_only_and_found_by_profile(self):
        p = credentials.save_env("acme", (CID, SECRET))
        self.assertEqual(p, self.tmp / ".perfectscale" / "acme.env")
        if os.name == "posix":
            self.assertEqual(stat.S_IMODE(p.stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(p.parent.stat().st_mode), 0o700)
        self.assertEqual(apiv2.load_credentials({"profile": "acme"})[0], (CID, SECRET))
        self.assertIsNone(apiv2.load_credentials({"profile": "other"})[0])

    def test_missing_message_names_the_fix(self):
        why = apiv2.load_credentials({"profile": "acme"})[1]
        self.assertIn("new_tenant.py auth --profile acme", why)


class Setup(Home):
    def run_setup(self, answers, api_results):
        asked, out = list(answers), []
        res = list(api_results)
        with mock.patch.object(credentials, "check_api", side_effect=lambda c, b=None: res.pop(0)), \
             mock.patch.object(credentials, "pscli_login", return_value=(True, "pscli profile 'acme' is signed in")) as login:
            creds = credentials.setup("acme", pscli="pscli", out=out.append,
                                      ask=lambda p: asked.pop(0), ask_secret=lambda p: asked.pop(0))
        return creds, out, login

    def test_verified_then_saved_and_pscli_signed_in(self):
        creds, out, login = self.run_setup([CID, SECRET], [(True, "ok")])
        self.assertEqual(creds, (CID, SECRET))
        self.assertTrue((self.tmp / ".perfectscale" / "acme.env").exists())
        login.assert_called_once()
        self.assertNotIn(SECRET, "\n".join(out))

    def test_a_bad_token_is_never_saved_and_is_retried(self):
        creds, out, _ = self.run_setup([CID, "wrong", CID, SECRET], [(False, "HTTP 401"), (True, "ok")])
        self.assertEqual(creds, (CID, SECRET))
        self.assertIn("did not work", "\n".join(out))
        self.assertNotIn("wrong", "\n".join(out))

    def test_giving_up_stores_nothing(self):
        creds, _, login = self.run_setup([CID, "x"] * 3, [(False, "HTTP 401")] * 3)
        self.assertIsNone(creds)
        self.assertFalse((self.tmp / ".perfectscale" / "acme.env").exists())
        login.assert_not_called()

    def test_offer_never_asks_without_a_terminal(self):
        def boom(*a):
            raise AssertionError("asked without a terminal")
        with mock.patch.object(credentials, "can_prompt", return_value=False):
            self.assertIsNone(credentials.offer("acme", "missing", ask=boom, ask_secret=boom, out=lambda m: None))


@unittest.skipUnless(os.name == "posix", "pseudo-terminal login is the macOS/Linux path")
class PscliLogin(Home):
    def test_secret_is_typed_into_the_prompt_not_passed_as_an_argument(self):
        rec = self.tmp / "rec.json"
        fake = self.tmp / "pscli"
        fake.write_text(textwrap.dedent(f"""\
            #!{sys.executable}
            import getpass, json, os, sys
            rec = {str(rec)!r}
            if "status" in sys.argv:
                sys.exit(0 if os.path.exists(rec) else 1)
            cid = input("Client ID: ")
            sec = getpass.getpass("Client secret: ")   # needs a real terminal, like pscli
            json.dump({{"argv": sys.argv, "cid": cid, "secret_ok": sec == {SECRET!r}}}, open(rec, "w"))
            print("Saved profile")
            """))
        fake.chmod(0o755)
        ok, msg = credentials.pscli_login(str(fake), "acme", (CID, SECRET))
        self.assertTrue(ok, msg)
        r = json.loads(rec.read_text())
        self.assertEqual(r["cid"], CID)
        self.assertTrue(r["secret_ok"])
        self.assertNotIn(SECRET, " ".join(r["argv"]))
        self.assertNotIn(SECRET, msg)


class CollectorOffer(Home):
    def collector(self, interactive):
        from psplan.collect import Collector
        cfg = Config.load(fixtures.make(self.tmp / "t"))
        cfg["pscli"] = str(HERE / "fake_pscli.py")
        return Collector(cfg, log=lambda m: None, interactive=interactive)

    def test_interactive_run_offers_and_picks_up_the_token(self):
        c = self.collector(True)
        self.assertIsNone(c.api)
        with mock.patch.object(credentials, "offer", return_value=(CID, SECRET)) as off:
            self.assertTrue(c._ensure_access(pscli_ok=True))
        off.assert_called_once()
        self.assertFalse(off.call_args.kwargs["login_pscli"])          # pscli already signed in
        self.assertIsNotNone(c.api)

    def test_unattended_run_never_offers(self):
        c = self.collector(False)
        with mock.patch.object(credentials, "offer", side_effect=AssertionError("offered")):
            self.assertTrue(c._ensure_access(pscli_ok=True))
        self.assertIsNone(c.api)


class Preflight(Home):
    def test_reports_each_source_and_blocks_only_when_nothing_can_run(self):
        cfg = Config.load(fixtures.make(self.tmp / "t"))
        cfg["pscli"] = str(HERE / "fake_pscli.py")
        with mock.patch.object(credentials, "pscli_status", return_value=(True, "pscli profile 'acme' is signed in")):
            rows = preflight.check(cfg, network=False)
        items = {r[1]: r for r in rows}
        self.assertEqual(items["pscli"][0], "OK")
        self.assertEqual(items["API v2 credentials"][0], "WARN")
        self.assertIn("new_tenant.py auth", items["API v2 credentials"][2])
        self.assertTrue(any(k.startswith("data: ") for k in items))
        self.assertEqual(preflight.report(rows, say=lambda m: None), 0)

        cfg["data_dir"] = str(self.tmp / "empty")                       # nothing collected, pscli out
        with mock.patch.object(credentials, "pscli_status", return_value=(False, "not signed in")):
            rows = preflight.check(cfg, network=False)
        self.assertTrue(any(r[0] == "BLOCK" for r in rows))
        self.assertEqual(preflight.report(rows, say=lambda m: None), 1)

    def test_stored_credentials_are_tested_without_printing_them(self):
        cfg = Config.load(fixtures.make(self.tmp / "t"))
        cfg["profile"] = "acme"
        credentials.save_env("acme", (CID, SECRET))
        with mock.patch.object(credentials, "pscli_status", return_value=(True, "ok")), \
             mock.patch.object(credentials, "check_api", return_value=(True, "works")):
            rows = preflight.check(cfg)
        row = next(r for r in rows if r[1] == "API v2 credentials")
        self.assertEqual(row[0], "OK")
        self.assertIn("token exchange works", row[2])
        self.assertNotIn(SECRET, json.dumps(rows))


class AuthCommand(Home):
    def test_auth_refuses_without_a_terminal(self):
        import new_tenant
        with mock.patch.object(credentials, "can_prompt", return_value=False):
            with self.assertRaises(SystemExit) as cm:
                new_tenant.cmd_auth(type("A", (), {"profile": "acme", "dir": None, "api_only": False})())
        self.assertIn("terminal", str(cm.exception))


if __name__ == "__main__":
    unittest.main()
