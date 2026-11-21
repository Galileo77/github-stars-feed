
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

BASE = "https://api.github.com"
TOKEN = os.environ.get("GH_TOKEN", "")
NOW = datetime.now(timezone.utc)
SINCE = NOW - timedelta(hours=24)


def github(path, params=None):
    url = BASE + path
    if params:
        url += "?" + urllib.parse.urlencode(params)

    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "github-stars-feed",
    }
    if TOKEN:
        headers["Authorization"] = f"Bearer {TOKEN}"

    for attempt in range(4):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=30) as response:
                return json.load(response)
        except urllib.error.HTTPError as exc:
            if exc.code in (403, 429, 500, 502, 503, 504) and attempt < 3:
                time.sleep(2 ** attempt * 3)
                continue
            raise


def pages(path, params=None, stop=None):
    results = []
    page = 1
    while True:
        query = dict(params or {})
        query.update({"per_page": 100, "page": page})
        batch = github(path, query)
        if not isinstance(batch, list):
            raise ValueError(f"Unexpected API response: {path}")
        for item in batch:
            if stop and stop(item):
                return results
            results.append(item)
        if len(batch) < 100:
            break
        page += 1
    return results


def parse_date(value):
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def recent(value):
    dt = parse_date(value)
    return dt is not None and SINCE <= dt <= NOW


def collect(repo):
    name = repo["full_name"]
    path = "/repos/" + name

    commits = pages(
        path + "/commits",
        {"since": SINCE.isoformat(), "until": NOW.isoformat()},
    )
    releases = pages(path + "/releases")

    # Pull requests are paginated by updated_at.
    # Stop once entries are older than the reporting window.
    pulls = pages(
        path + "/pulls",
        {"state": "all", "sort": "updated", "direction": "desc"},
        stop=lambda p: not recent(p.get("updated_at")),
    )

    return {
        "repository": name,
        "commits": [
            {
                "sha": c["sha"],
                "message": c["commit"]["message"].splitlines()[0],
                "date": c["commit"]["committer"]["date"],
                "url": c["html_url"],
            }
            for c in commits
            if recent(c["commit"]["committer"]["date"])
        ],
        "releases": [
            {
                "tag": r["tag_name"],
                "name": r.get("name"),
                "published_at": r.get("published_at"),
                "prerelease": r.get("prerelease", False),
                "url": r["html_url"],
            }
            for r in releases
            if recent(r.get("published_at"))
        ],
        "pull_requests": [
            {
                "number": p["number"],
                "title": p["title"],
                "state": p["state"],
                "updated_at": p["updated_at"],
                "url": p["html_url"],
            }
            for p in pulls
            if recent(p.get("updated_at"))
        ],
    }


def main():
    # Always fetch the current complete star list.
    stars = pages("/users/Galileo77/starred")
    repositories = []
    errors = []

    for index, repo in enumerate(stars, 1):
        name = repo["full_name"]
        print(f"[{index}/{len(stars)}] {name}", flush=True)
        try:
            result = collect(repo)
            if any(result[k] for k in ("commits", "releases", "pull_requests")):
                repositories.append(result)
        except Exception as exc:
            errors.append({"repository": name, "error": str(exc)})
            print(f"ERROR: {name}: {exc}", flush=True)

    data = {
        "generated_at": NOW.isoformat(),
        "period_start": SINCE.isoformat(),
        "period_end": NOW.isoformat(),
        "total_repositories": len(stars),
        "checked_repositories": len(stars) - len(errors),
        "failed_repositories": errors,
        "complete": not errors,
        "repositories_with_changes": len(repositories),
        "repositories": repositories,
    }

    Path("changes.json").write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    if errors:
        raise SystemExit(f"{len(errors)} repositories could not be checked")


if __name__ == "__main__":
    main()
