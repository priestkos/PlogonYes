#!/usr/bin/env python3
import json
import os
import sys
import urllib.request
from datetime import datetime
from io import BytesIO
from zipfile import ZipFile

OWNER, REPOS_FILE, OUTPUT = "priestkos", "repos.json", "pluginmaster.json"
MANIFEST_KEYS = (
    "Author", "Name", "InternalName", "AssemblyVersion", "Punchline", "Description",
    "RepoUrl", "Tags", "DalamudApiLevel", "IconUrl", "Changelog", "CategoryTags", "ImageUrls",
)
REQUIRED_KEYS = ("Name", "InternalName", "AssemblyVersion", "DalamudApiLevel")


def api_get(url):
    req = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json"})
    if token := os.environ.get("GITHUB_TOKEN"):
        req.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode())


def download(url):
    with urllib.request.urlopen(url, timeout=120) as resp:
        return resp.read()


def build_entry(repo):
    """Return the pluginmaster entry for repo; raise on any problem."""
    release = api_get(f"https://api.github.com/repos/{OWNER}/{repo}/releases/latest")
    asset = next((a for a in release.get("assets", []) if a.get("name") == "latest.zip"), None)
    if not asset:
        raise ValueError("release has no latest.zip asset")
    url = asset["browser_download_url"]
    with ZipFile(BytesIO(download(url))) as zf:
        roots = [n for n in zf.namelist() if "/" not in n and n.endswith(".json") and not n.endswith(".deps.json")]
        if len(roots) != 1:
            raise ValueError(f"expected one manifest json, found {roots!r}")
        manifest = json.loads(zf.read(roots[0]).decode("utf-8-sig"))
    if missing := [k for k in REQUIRED_KEYS if k not in manifest]:
        raise ValueError(f"manifest missing {missing}")
    entry = {k: manifest[k] for k in MANIFEST_KEYS if k in manifest}
    entry.update(
        ApplicableVersion="any",
        IsHide=False,
        IsTestingExclusive=False,
        LastUpdate=int(datetime.fromisoformat(release["published_at"].replace("Z", "+00:00")).timestamp()),
        DownloadLinkInstall=url,
        DownloadLinkUpdate=url,
        DownloadLinkTesting=url,
    )
    return entry


def main():
    with open(REPOS_FILE, encoding="utf-8") as f:
        repos = json.load(f)
    try:
        with open(OUTPUT, encoding="utf-8") as f:
            old_text = f.read()
    except FileNotFoundError:
        old_text = None
    old_entries = json.loads(old_text) if old_text else []
    entries, failed = [], []
    for repo in repos:
        try:
            entries.append(build_entry(repo))
        except Exception as err:  # one bad repo must not drop or block the others
            failed.append(repo)
            # RepoUrl can point off GitHub (HuntAlerts); the download link is always ours.
            prefix = f"https://github.com/{OWNER}/{repo}/releases/".lower()
            prev = next((e for e in old_entries if e.get("DownloadLinkInstall", "").lower().startswith(prefix)), None)
            if prev:
                entries.append(prev)
            print(f"warning: {repo}: {type(err).__name__}: {err}; "
                  f"{'kept previous entry' if prev else 'no previous entry, omitted'}", file=sys.stderr)
    entries.sort(key=lambda e: e.get("Name", ""))
    new_text = json.dumps(entries, indent=2, ensure_ascii=False) + "\n"
    if old_text == new_text:
        print(f"{OUTPUT} unchanged")
    else:
        with open(OUTPUT, "w", encoding="utf-8") as f:
            f.write(new_text)
        print(f"wrote {OUTPUT}")
    if failed:
        sys.exit(f"failed repos: {', '.join(failed)}")


if __name__ == "__main__":
    main()
