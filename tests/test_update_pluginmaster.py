"""Offline tests for scripts/update_pluginmaster.py (network stubbed)."""
import contextlib
import importlib.util
import io
import json
import os
import pathlib
import tempfile
import unittest
import urllib.error
import zipfile

SCRIPT = pathlib.Path(__file__).resolve().parent.parent / "scripts" / "update_pluginmaster.py"
spec = importlib.util.spec_from_file_location("update_pluginmaster", SCRIPT)
upm = importlib.util.module_from_spec(spec)
spec.loader.exec_module(upm)

PUBLISHED = "2026-10-01T12:00:00Z"


def url_for(repo):
    return f"https://github.com/priestkos/{repo}/releases/download/v1.0.0.0/latest.zip"


def manifest(repo, **over):
    m = {"Author": "PriestKos", "Name": repo, "InternalName": repo, "AssemblyVersion": "1.0.0.0",
         "Punchline": "p", "Description": "d", "RepoUrl": f"https://github.com/priestkos/{repo}",
         "Tags": ["t"], "DalamudApiLevel": 15, "Extra": "ignored"}
    m.update(over)
    return m


def make_zip(files):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in files.items():
            zf.writestr(name, data)
    return buf.getvalue()


def good_zip(repo, **over):
    return make_zip({f"{repo}.json": json.dumps(manifest(repo, **over)), f"{repo}.deps.json": "{}", f"{repo}.dll": "x"})


def expected_entry(repo, version="1.0.0.0"):
    m = manifest(repo, AssemblyVersion=version)
    e = {k: m[k] for k in upm.MANIFEST_KEYS if k in m}
    e.update(ApplicableVersion="any", IsHide=False, IsTestingExclusive=False, LastUpdate=1790856000,
             DownloadLinkInstall=url_for(repo), DownloadLinkUpdate=url_for(repo), DownloadLinkTesting=url_for(repo))
    return e


def http_error(code):
    return urllib.error.HTTPError("u", code, "err", {}, None)


class UpdateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cwd = os.getcwd()
        os.chdir(self.tmp.name)
        self.releases, self.zips = {}, {}
        upm.api_get, upm.download = self.fake_api, self.fake_download

    def tearDown(self):
        os.chdir(self.cwd)
        self.tmp.cleanup()

    def fake_api(self, url):
        r = self.releases[url.split("/")[-3]]
        if isinstance(r, Exception):
            raise r
        return r

    def fake_download(self, url):
        z = self.zips[url]
        if isinstance(z, Exception):
            raise z
        return z

    def add(self, repo, zbytes=None, release=None):
        self.releases[repo] = release if release is not None else {
            "published_at": PUBLISHED, "assets": [{"name": "latest.zip", "browser_download_url": url_for(repo)}]}
        self.zips[url_for(repo)] = zbytes if zbytes is not None else good_zip(repo)

    def run_main(self, repos, previous=None):
        pathlib.Path("repos.json").write_text(json.dumps(repos), encoding="utf-8")
        if previous is not None:
            pathlib.Path("pluginmaster.json").write_text(json.dumps(previous, indent=2) + "\n", encoding="utf-8")
        err = io.StringIO()
        code = 0
        with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
            try:
                upm.main()
            except SystemExit as e:
                code = e.code
        return json.loads(pathlib.Path("pluginmaster.json").read_text(encoding="utf-8")), code, err.getvalue()

    def test_success_format(self):
        self.add("Beta")
        self.add("Alpha")
        self.run_main(["Beta", "Alpha"])
        text = pathlib.Path("pluginmaster.json").read_text(encoding="utf-8")
        want = json.dumps([expected_entry("Alpha"), expected_entry("Beta")], indent=2, ensure_ascii=False) + "\n"
        self.assertEqual(text, want)

    def assert_kept(self, break_repo):
        """Previous entries: Good@0.9 and Bad@0.9. Good updates to 1.0; Bad fails and keeps 0.9."""
        prev = [expected_entry("Bad", "0.9"), expected_entry("Good", "0.9")]
        self.add("Good")
        self.add("Bad")
        break_repo()
        out, code, err = self.run_main(["Good", "Bad"], prev)
        self.assertEqual(out, [expected_entry("Bad", "0.9"), expected_entry("Good")])
        self.assertTrue(code)
        self.assertIn("warning: Bad", err)
        self.assertIn("kept previous entry", err)

    def test_http_errors(self):
        for exc in (http_error(403), http_error(500), http_error(404), urllib.error.URLError("down"), TimeoutError()):
            with self.subTest(exc=exc):
                self.assert_kept(lambda: self.releases.__setitem__("Bad", exc))

    def test_no_latest_zip(self):
        self.assert_kept(lambda: self.releases.__setitem__("Bad", {"published_at": PUBLISHED, "assets": [{"name": "x.zip"}]}))

    def test_download_error(self):
        self.assert_kept(lambda: self.zips.__setitem__(url_for("Bad"), http_error(503)))

    def test_missing_published_at(self):
        self.assert_kept(lambda: self.releases["Bad"].pop("published_at"))

    def test_bad_zips(self):
        cases = {
            "not a zip": b"garbage",
            "zero manifests": make_zip({"Bad.dll": "x"}),
            "two manifests": make_zip({"Bad.json": json.dumps(manifest("Bad")), "Other.json": "{}"}),
            "invalid json": make_zip({"Bad.json": "{nope"}),
            "missing InternalName": make_zip({"Bad.json": json.dumps({k: v for k, v in manifest("Bad").items() if k != "InternalName"})}),
            "missing AssemblyVersion": make_zip({"Bad.json": json.dumps({k: v for k, v in manifest("Bad").items() if k != "AssemblyVersion"})}),
            "missing DalamudApiLevel": make_zip({"Bad.json": json.dumps({k: v for k, v in manifest("Bad").items() if k != "DalamudApiLevel"})}),
        }
        for name, z in cases.items():
            with self.subTest(name):
                self.assert_kept(lambda: self.zips.__setitem__(url_for("Bad"), z))

    def test_previous_matched_by_download_link_not_repourl(self):
        prev = expected_entry("Bad", "0.9")
        prev["RepoUrl"] = "https://elsewhere.example/Bad"
        self.add("Bad")
        self.releases["Bad"] = http_error(500)
        out, code, _ = self.run_main(["Bad"], [prev])
        self.assertEqual(out, [prev])

    def test_failure_without_previous_entry_is_omitted(self):
        self.add("Good")
        self.add("New")
        self.releases["New"] = http_error(404)
        out, code, err = self.run_main(["Good", "New"], [expected_entry("Good")])
        self.assertEqual(out, [expected_entry("Good")])
        self.assertTrue(code)
        self.assertIn("warning: New", err)
        self.assertIn("omitted", err)

    def test_unchanged_output_not_rewritten(self):
        self.add("Good")
        first, _, _ = self.run_main(["Good"])
        path = pathlib.Path("pluginmaster.json")
        os.utime(path, (1, 1))
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            upm.main()
        self.assertEqual(out.getvalue(), "pluginmaster.json unchanged\n")
        self.assertEqual(path.stat().st_mtime, 1)
        self.assertEqual(json.loads(path.read_text(encoding="utf-8")), first)

    def test_nested_json_in_zip_ignored(self):
        self.add("Good", make_zip({"Good.json": json.dumps(manifest("Good")), "sub/x.json": "{}"}))
        out, code, err = self.run_main(["Good"])
        self.assertEqual((out, code), ([expected_entry("Good")], 0), err)

    def test_utf8_bom_manifest(self):
        self.add("Good", make_zip({"Good.json": "\ufeff" + json.dumps(manifest("Good"))}))
        out, code, err = self.run_main(["Good"])
        self.assertEqual((out, code), ([expected_entry("Good")], 0), err)

    def test_repo_removed_from_repos_json_is_dropped(self):
        self.add("Good")
        out, code, _ = self.run_main(["Good"], [expected_entry("Gone"), expected_entry("Good", "0.9")])
        self.assertEqual((out, code), ([expected_entry("Good")], 0))


if __name__ == "__main__":
    unittest.main()
