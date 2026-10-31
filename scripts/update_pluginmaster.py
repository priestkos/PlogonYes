#!/usr/bin/env python3
import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime
from io import BytesIO
from zipfile import ZipFile

OWNER, REPOS_FILE, OUTPUT = "priestkos", "repos.json", "pluginmaster.json"
MANIFEST_KEYS = (
    "Author", "Name", "InternalName", "AssemblyVersion", "Punchline", "Description",
    "RepoUrl", "Tags", "DalamudApiLevel", "IconUrl", "Changelog", "CategoryTags", "ImageUrls",
)


def api_get(url):
    req = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json"})
    if token := os.environ.get("GITHUB_TOKEN"):
        req.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read().decode())


def main():
    with open(REPOS_FILE, encoding="utf-8") as f:
        repos = json.load(f)
    entries = []
    for repo in repos:
        try:
            release = api_get(f"https://api.github.com/repos/{OWNER}/{repo}/releases/latest")
        except urllib.error.HTTPError as err:
            if err.code == 404:
                print(f"skip {repo}: no release yet", file=sys.stderr)
                continue
            raise
        asset = next((a for a in release.get("assets", []) if a.get("name") == "latest.zip"), None)
        if not asset:
            print(f"skip {repo}: release has no latest.zip asset", file=sys.stderr)
            continue
        url = asset["browser_download_url"]
        with urllib.request.urlopen(url) as resp:
            zbytes = resp.read()
        with ZipFile(BytesIO(zbytes)) as zf:
            roots = [n for n in zf.namelist() if "/" not in n and n.endswith(".json") and not n.endswith(".deps.json")]
            if len(roots) != 1:
                raise ValueError(f"{repo}: expected one manifest json, found {roots!r}")
            manifest = json.loads(zf.read(roots[0]).decode("utf-8-sig"))
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
        entries.append(entry)
    entries.sort(key=lambda e: e.get("Name", ""))
    new_text = json.dumps(entries, indent=2, ensure_ascii=False) + "\n"
    try:
        with open(OUTPUT, encoding="utf-8") as f:
            old_text = f.read()
    except FileNotFoundError:
        old_text = None
    if old_text == new_text:
        print(f"{OUTPUT} unchanged")
        return
    with open(OUTPUT, "w", encoding="utf-8") as f:
        f.write(new_text)
    print(f"wrote {OUTPUT}")


if __name__ == "__main__":
    main()
