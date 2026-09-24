# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repo is

A self-updating mirror of the XiongMai / JFTech IP-camera and DVR firmware catalogs, maintained for OpenIPC. It is mostly data (`items.ipc`, `items.dvr`, `archive/index.json`) plus two standalone Python scripts run by a weekly GitHub Actions cron. Most commits are made by `xmupdates-bot`, not by people.

There is no test suite, linter config, or build step.

## Commands

```sh
pip install -r requirements.txt            # requests, beautifulsoup4

python xmupdates.py                        # refresh items.ipc / items.dvr from the vendor

# Dry run of the downloader: resolves ZIP URLs and prints the plan, no download/upload/commit
GH_TOKEN=$(gh auth token) python download_firmwares.py --max-per-run 5 --dry-run
```

Without `--dry-run`, `download_firmwares.py` uploads to the real `firmware-archive` GitHub Release with `gh release upload --clobber`, and it runs `git commit` + `git push` on `archive/index.json` itself (every `--commit-every` successes). Do not run it for real locally unless that is the intent.

## Pipeline

`.github/workflows/weekly-update.yml` runs on Mondays at 04:17 UTC and can also be started manually with `workflow_dispatch`, which accepts `max_per_run`, `commit_every` and `dry_run`. It has two jobs:

1. **refresh-catalog**: runs `xmupdates.py`, which calls `https://baike.jftech.com/download/pagination.do` for each `paramValue` (6 = ipc, 5 = dvr). It first requests 1 row to read `total`, then requests all rows. It sorts rows by `id` and writes JSON with `sort_keys=True, indent=4`. The commit step runs `if: always()`, so a partial refresh still gets committed.
2. **download-firmwares**: runs `download_firmwares.py` against the catalog on `main`. It never contacts the catalog host, so it runs even when the refresh fails (`if: !cancelled()`).

Flow inside `download_firmwares.py`:
- A catalog row is pending when its `(id, downloadUrl)` pair is not yet in `archive/index.json` under any of `revisions`, `unavailable` or `data_errors`. The dedupe key is the landing URL, not `version`: a re-publish under the same id shows up as a new `downloadUrl` and gets appended as a new revision. Old revisions are never replaced.
- `downloadUrl` (on `download.xm030.cn`) is an HTML landing page. The script scrapes the real `.zip` link from it. That link must be on a host ending in `myhuaweicloud.com` (Huawei OBS) or `ksyun.com` (Kingsoft KS3).
- Outcomes are recorded in the index entry:
  - `revisions[]`: success. Includes sha256, size and `asset_url`.
  - `unavailable[]`: the page shows an offline marker (`文件已过期下线` / `The file has expired`), or the CDN returns 404/410.
  - `data_errors[]`: `downloadUrl` is not an http(s) URL.
  - Any other exception counts as a transient failure. Nothing is recorded, so the row is retried on the next run.
- Asset names are `id{id}__{safe_version}__{obs_filename}`, so different versions never collide on the release.
- `archive/index.json` is written atomically via a `.tmp` file and `replace`, with `sort_keys=True, indent=2`.

## Conventions and gotchas

- **TLS verification is off on purpose**, but only for the vendor hosts. `download.xm030.cn` has an expired, mismatched cert, and the JFTech catalog host's cert couldn't be checked because the host is region-restricted. `session_for()` turns verification off only for `LANDING_HOST`. Keep it on for the CDN and GitHub.
- **Vendor host history:** the catalog moved from `baike.xm030.cn` (now NXDOMAIN) to `baike.jftech.com` in July 2026. The endpoint, params and schema stayed the same. Binaries are still served from `download.xm030.cn`. If the refresh starts failing, first check whether the host moved again.
- **Catalog data is stored almost verbatim.** Only `downloadUrl` is whitespace-stripped (`clean_row`). `name` and `version` can contain leading or trailing spaces and `\n`. That is real vendor data, so don't "clean up" existing rows, because it would produce huge diffs. `safe_token()` cleans values only when building filenames.
- `xmupdates.py` refuses to overwrite a catalog if the vendor returns 0 rows, and exits non-zero if either catalog fails. A `KeyError` or other exception that isn't a `RequestException` means the vendor schema changed, and it is left to crash on purpose.
- The workflow and `ensure_release_exists()` both contain the logic that creates the `firmware-archive` release. Keep the two in sync.
- `archive/000529B2/`, `000529E9/` and `000559A7/` hold legacy `.bin` files from before the Releases-based mirror. Don't add to them. `*.zip` is gitignored because firmware binaries belong in Release assets, not git.
- The bot pushes to `main` most weeks (only when the catalog or index changed). Pull before editing `items.*` or `archive/index.json` by hand to avoid conflicts.
- The README and `archive/README.md` document the index schema. Update them if the schema changes.
