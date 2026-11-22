import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request

from datetime import datetime, timedelta, timezone
from pathlib import Path


# --------------------------------------------------
# Configuration
# --------------------------------------------------

BASE = "https://api.github.com"
USERNAME = "Galileo77"
TOKEN = os.environ.get("GH_TOKEN", "")

NOW = datetime.now(timezone.utc)
SINCE = NOW - timedelta(hours=24)

OUTPUT_FILE = Path("changes.json")


# --------------------------------------------------
# GitHub API
# --------------------------------------------------

def github(path, params=None):
    url = BASE + path

    if params:
        url += "?" + urllib.parse.urlencode(params)

    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "github-stars-feed",
        "X-GitHub-Api-Version": "2022-11-28",
    }

    if TOKEN:
        headers["Authorization"] = f"Bearer {TOKEN}"

    for attempt in range(4):
        try:
            request = urllib.request.Request(
                url,
                headers=headers,
            )

            with urllib.request.urlopen(
                request,
                timeout=30,
            ) as response:
                return json.load(response)

        except urllib.error.HTTPError as exc:
            retryable = exc.code in (
                403, 429, 500, 502, 503, 504
            )

            if retryable and attempt < 3:
                delay = (2 ** attempt) * 3

                print(
                    f"Retry {path}: HTTP {exc.code}, "
                    f"waiting {delay}s",
                    flush=True,
                )

                time.sleep(delay)
                continue

            raise


def pages(path, params=None, stop=None):
    results = []
    page = 1

    while True:
        query = dict(params or {})
        query.update({
            "per_page": 100,
            "page": page,
        })

        batch = github(path, query)

        if not isinstance(batch, list):
            raise ValueError(
                f"Unexpected API response: {path}"
            )

        for item in batch:
            if stop and stop(item):
                return results

            results.append(item)

        if len(batch) < 100:
            break

        page += 1

    return results


# --------------------------------------------------
# Date helpers
# --------------------------------------------------

def parse_date(value):
    if not value:
        return None

    return datetime.fromisoformat(
        value.replace("Z", "+00:00")
    )


def recent(value):
    dt = parse_date(value)

    return (
        dt is not None
        and SINCE <= dt <= NOW
    )


# --------------------------------------------------
# Repository analysis
# --------------------------------------------------

def collect(repo):
    name = repo["full_name"]
    path = "/repos/" + name

    errors = []

    def safe_pages(endpoint, params=None, stop=None):
        try:
            return pages(endpoint, params, stop)

        except Exception as exc:
            error = {
                "endpoint": endpoint,
                "error": str(exc),
            }

            errors.append(error)

            print(
                f"WARNING: {name}: "
                f"{endpoint}: {exc}",
                flush=True,
            )

            return []

    # ----------------------------------------------
    # Commits
    # ----------------------------------------------

    commits = safe_pages(
        path + "/commits",
        {
            "since": SINCE.isoformat(),
            "until": NOW.isoformat(),
        },
    )

    # ----------------------------------------------
    # Releases
    # ----------------------------------------------

    releases = safe_pages(
        path + "/releases",
    )

    # ----------------------------------------------
    # Pull requests
    # ----------------------------------------------

    # Pull Requests können für einzelne Repositories
    # deaktiviert sein. In diesem Fall ist ein 404
    # kein fehlgeschlagener Datenabruf.

    pulls = []

    try:
        pulls = pages(
            path + "/pulls",
            {
                "state": "all",
                "sort": "updated",
                "direction": "desc",
            },
            stop=lambda p: not recent(
                p.get("updated_at")
            ),
        )

    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            try:
                repo_info = github(path)

                if repo_info.get("has_pull_requests") is False:
                    print(
                        f"INFO: {name}: Pull Requests "
                        "deaktiviert",
                        flush=True,
                    )
                else:
                    raise ValueError(
                        "PR endpoint returned HTTP 404; "
                        "PR availability not confirmed"
                    )

            except Exception as check_exc:
                errors.append({
                    "endpoint": path + "/pulls",
                    "error": str(check_exc),
                })
        else:
            errors.append({
                "endpoint": path + "/pulls",
                "error": str(exc),
            })

    except Exception as exc:
        errors.append({
            "endpoint": path + "/pulls",
            "error": str(exc),
        })


    # ----------------------------------------------
    # Filter relevant time window
    # ----------------------------------------------

    recent_commits = [
        {
            "sha": c["sha"],
            "message": c["commit"]["message"].splitlines()[0],
            "date": c["commit"]["committer"]["date"],
            "url": c["html_url"],
        }
        for c in commits
        if recent(
            c["commit"]["committer"]["date"]
        )
    ]

    recent_releases = [
        {
            "tag": r["tag_name"],
            "name": r.get("name"),
            "published_at": r.get("published_at"),
            "prerelease": r.get("prerelease", False),
            "url": r["html_url"],
        }
        for r in releases
        if recent(
            r.get("published_at")
        )
    ]

    recent_pulls = [
        {
            "number": p["number"],
            "title": p["title"],
            "state": p["state"],
            "updated_at": p["updated_at"],
            "url": p["html_url"],
        }
        for p in pulls
        if recent(
            p.get("updated_at")
        )
    ]

    return {
        "repository": name,
        "commits": recent_commits,
        "releases": recent_releases,
        "pull_requests": recent_pulls,
        "errors": errors,
    }


# --------------------------------------------------
# Main
# --------------------------------------------------

def main():
    print(
        f"Starting GitHub Stars Digest for {USERNAME}",
        flush=True,
    )

    print(
        f"Period: {SINCE.isoformat()} "
        f"to {NOW.isoformat()}",
        flush=True,
    )

    # Fetch the complete, paginated star list.
    try:
        stars = pages(
            f"/users/{USERNAME}/starred"
        )

    except Exception as exc:
        print(
            f"FATAL: Cannot fetch starred repositories: "
            f"{exc}",
            flush=True,
        )
        raise SystemExit(1)

    print(
        f"Found {len(stars)} starred repositories",
        flush=True,
    )

    if not stars:
        print(
            "FATAL: Star list is empty; "
            "refusing to overwrite existing data.",
            flush=True,
        )
        raise SystemExit(1)

    repositories = []
    errors = []
    checked = 0

    # Check every repository.
    for index, repo in enumerate(stars, 1):
        name = repo["full_name"]

        print(
            f"[{index}/{len(stars)}] Checking {name}",
            flush=True,
        )

        try:
            result = collect(repo)

            if result["errors"]:
                errors.append({
                    "repository": name,
                    "errors": result["errors"],
                })
            else:
                checked += 1

            # Keep repositories with activity or errors.
            if (
                result["commits"]
                or result["releases"]
                or result["pull_requests"]
                or result["errors"]
            ):
                repositories.append(result)

        except Exception as exc:
            errors.append({
                "repository": name,
                "errors": [{
                    "error": str(exc),
                }],
            })

            print(
                f"ERROR: {name}: {exc}",
                flush=True,
            )

    # ----------------------------------------------
    # Summary
    # ----------------------------------------------

    repositories_with_changes = sum(
        1
        for repo in repositories
        if (
            repo["commits"]
            or repo["releases"]
            or repo["pull_requests"]
        )
    )

    total_commits = sum(
        len(repo["commits"])
        for repo in repositories
    )

    total_releases = sum(
        len(repo["releases"])
        for repo in repositories
    )

    total_pulls = sum(
        len(repo["pull_requests"])
        for repo in repositories
    )

    data = {
        "username": USERNAME,
        "generated_at": NOW.isoformat(),
        "period_start": SINCE.isoformat(),
        "period_end": NOW.isoformat(),

        "total_repositories": len(stars),
        "checked_repositories": checked,
        "failed_repositories": errors,
        "complete": not errors,

        "repositories_with_changes": (
            repositories_with_changes
        ),

        "statistics": {
            "commits": total_commits,
            "releases": total_releases,
            "pull_requests": total_pulls,
        },

        "repositories": repositories,
    }

    # ----------------------------------------------
    # Save JSON
    # ----------------------------------------------

    OUTPUT_FILE.write_text(
        json.dumps(
            data,
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    print()
    print("==================================")
    print("GitHub Stars Digest - Summary")
    print("==================================")

    print(
        f"Total repositories: {len(stars)}"
    )

    print(
        f"Fully checked: {checked}"
    )

    print(
        f"Repositories with errors: {len(errors)}"
    )

    print(
        f"Repositories with changes: "
        f"{repositories_with_changes}"
    )

    print(
        f"Commits: {total_commits}"
    )

    print(
        f"Releases: {total_releases}"
    )

    print(
        f"Pull requests: {total_pulls}"
    )

    print(
        f"Complete: {not errors}"
    )

    print(
        f"Saved: {OUTPUT_FILE}"
    )

    if errors:
        print()
        print(
            "WARNING: Some repositories or API "
            "endpoints could not be checked."
        )

        for error in errors:
            print(
                f" - {error['repository']}"
            )

    print()
    print("Finished.", flush=True)


if __name__ == "__main__":
    main()
