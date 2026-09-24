# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repo is

A self-updating mirror of the XiongMai / JFTech IP-camera and DVR firmware catalogs, maintained for OpenIPC. It is mostly data (`items.ipc`, `items.dvr`, `items.portal`, `archive/index.json`) plus two standalone Python scripts run by a weekly GitHub Actions cron. Most commits are made by `xmupdates-bot`, not by people.

There is no test suite, linter config, or build step.

## Commands

```sh
pip install -r requirements.txt            # requests, beautifulsoup4, httpx[http2]

python xmupdates.py                        # refresh items.ipc / items.dvr / items.portal from the vendor

# Dry run of the downloader: resolves ZIP URLs and prints the plan, no download/upload/commit
GH_TOKEN=$(gh auth token) python download_firmwares.py --max-per-run 5 --dry-run
```

Without `--dry-run`, `download_firmwares.py` uploads to the real `firmware-archive` GitHub Release with `gh release upload`, and it runs `git commit` + `git push` on `archive/index.json` itself (every `--commit-every` successes). Do not run it for real locally unless that is the intent.

## Pipeline

`.github/workflows/weekly-update.yml` runs on Mondays at 04:17 UTC and can also be started manually with `workflow_dispatch`, which accepts `max_per_run`, `commit_every` and `dry_run`. It has two jobs:

1. **refresh-catalog**: runs `xmupdates.py`, which calls `https://baike.jftech.com/download/pagination.do` for each `paramValue` (6 = ipc, 5 = dvr). It first requests 1 row to read `total`, then requests all rows. It sorts rows by `id` and writes JSON with `sort_keys=True, indent=4`. It then refreshes `items.portal` from the en.jftech.com portal API (`POST api-portal/support/title/allTitle` to discover the lists under the "Firmware" menu, then `POST api-portal/support/pageElement/query` per `titleId`, 100 rows a page). The portal write is all-or-nothing: a redirect, a non-2000 `code`, a row count that doesn't match `total`, a section that returns no rows, or a duplicate id fails it without touching the file. All three files are written atomically (`.tmp` + `os.replace`). The commit step runs `if: always()`, so a partial refresh still gets committed.
2. **download-firmwares**: runs `download_firmwares.py` against the catalog on `main`. It never contacts the catalog host, so it runs even when the refresh fails (`if: !cancelled()`).

Flow inside `download_firmwares.py`:
- Rows come from two sources. Catalog rows (`items.ipc`/`items.dvr`) are keyed by their numeric `id`, with assets named `id{id}__…`. Portal rows (`items.portal`) are keyed `p{id}`, with assets named `p{id}__…`, their `version` is the portal `name`, and their index `name` is the portal `description`. Pending rows from the two sources are interleaved round-robin before `--max-per-run` is applied.
- A catalog row is pending when its `(id, downloadUrl)` pair is not yet in `archive/index.json` under any of `revisions`, `unavailable` or `data_errors`. URLs are compared with `landing_key()`: for http(s) URLs on the vendor landing hosts (`LANDING_HOSTS`), only the numeric id behind `/d/<b64>` counts (anything else, including unparsable URLs, compares as the raw string), so a host move or missing base64 padding doesn't look like a new revision. The dedupe key is the landing URL, not `version`: a re-publish under the same id shows up as a new `downloadUrl` and gets appended as a new revision. Old revisions are never replaced.
- A portal row is additionally skipped when its `landing_key()` is in the current catalog, is recorded anywhere in the index, or was already claimed by a lower portal id in this run. On overlap the catalog wins.
- `downloadUrl` (on `download.jftech.com`, formerly `download.xm030.cn`) is an HTML landing page. The script scrapes the real `.zip` link from it. That link's host must be `myhuaweicloud.com` (Huawei OBS) or `ksyun.com` (Kingsoft KS3), or a subdomain of one (dot-delimited, so `evilksyun.com` doesn't count), or on one of the `LANDING_HOSTS` itself (a few `/ss/…zip` files; from `download.xm030.cn` these download without TLS verification, like its landing pages).
- Outcomes are recorded in the index entry:
  - `revisions[]`: success. Includes sha256, size and `asset_url`.
  - `unavailable[]`: the page shows an offline marker (`文件已过期下线` / `The file has expired`), or the CDN returns 404/410.
  - `data_errors[]`: `downloadUrl` is not an http(s) URL.
  - A portal row whose landing page is not on one of the `LANDING_HOSTS` is a transient failure, not a data error, so rows aren't lost for good if the vendor moves the host.
  - Any other exception counts as a transient failure. Nothing is recorded, so the row is retried on the next run.
- Asset names are `id{id}__{safe_version}__{safe_obs_filename}` (catalog) or `p{id}__…` (portal), so different versions never collide on the release. The obs filename goes through `safe_token()` because GitHub silently renames assets containing other characters, e.g. `(`, `（`. Before that was done, 78 index entries recorded names that didn't exist; they were repaired by sha256. Uploads never use `--clobber`. `pick_asset_name()` checks the release's asset digests. An identical binary under the same name is reused. Otherwise the first free name is taken, trying `__{sha256[:8]}` and then the full sha256 inserted before the filename. If the asset list can't be fetched, a real run aborts rather than risk a collision.
- New revisions also record `zip_url`, the resolved ZIP link.
- `archive/index.json` is written atomically via a `.tmp` file and `replace`, with `sort_keys=True, indent=2`.

## Conventions and gotchas

- **TLS verification is off on purpose**, but only for the vendor hosts. `download.xm030.cn` has an expired, mismatched cert, and the JFTech catalog host's cert couldn't be checked because the host is region-restricted. `session_for()` turns verification off only for `LANDING_HOST` (`download.xm030.cn`); `download.jftech.com` has a valid cert and stays verified. Keep it on for the CDN and GitHub.
- **Vendor host history:** the catalog moved from `baike.xm030.cn` (now NXDOMAIN) to `baike.jftech.com` in July 2026. The endpoint, params and schema stayed the same. In September 2026 every catalog `downloadUrl` moved from `download.xm030.cn` to `download.jftech.com`. The `/d/<b64>` ids and page contents are the same on both hosts, and the index still records the old host for earlier revisions. That's why dedupe goes through `landing_key()` and doesn't compare raw URLs. If the refresh starts failing, first check whether the host moved again.
- **The catalog host is behind Huawei CloudWAF** (since late September 2026). It answers the default `python-requests` User-Agent with HTTP 418 and an HTML "访问被拦截" page, so `get_rows()` sends the browser User-Agent in `HEADERS`. A 418 with an HTML body means the WAF is blocking again.
- **The portal API needs HTTP/2.** Over HTTPS it answers HTTP/1.1 requests with a 301 to plain `http://`, which turns the POST into a GET that the API rejects (`code: -91001`). That is why `xmupdates.py` uses `httpx` with `http2=True` and `follow_redirects=False`, and fails if a redirect comes back or the connection negotiated anything other than HTTP/2. Its cert is valid, so TLS verification stays on. Don't switch it to `requests`.
- **Portal ids are not catalog ids.** They are a separate number space; 268 of them collide with catalog ids for unrelated firmware. That's why portal index keys carry the `p` prefix.
- **Some portal rows prefix `name`, `description` and `toAddress` with circled digits** (`①https://…`). `items.portal` keeps them verbatim; the downloader extracts the URL with `LANDING_URL_RE`.
- **Catalog data is stored almost verbatim.** Only `downloadUrl` is whitespace-stripped (`clean_row`). `name` and `version` can contain leading or trailing spaces and `\n`. That is real vendor data, so don't "clean up" existing rows, because it would produce huge diffs. `safe_token()` cleans values only when building filenames.
- `xmupdates.py` refuses to overwrite a catalog if the vendor returns 0 rows, and exits non-zero if any of the three sources fails. For the catalogs, a `KeyError` or other exception that isn't a `RequestException` means the vendor schema changed, and it is left to crash on purpose. For the portal, `httpx.HTTPError`, `ValueError` (bad JSON) and `PortalError` are the collected failures; a `KeyError`/`TypeError` crashes.
- The workflow and `ensure_release_exists()` both contain the logic that creates the `firmware-archive` release. Keep the two in sync.
- `archive/000529B2/`, `000529E9/` and `000559A7/` hold legacy `.bin` files from before the Releases-based mirror. Don't add to them. `*.zip` is gitignored because firmware binaries belong in Release assets, not git.
- Known limitations of the cross-source dedupe: if the content behind an already-recorded landing page changes, it isn't re-archived under another key; if the catalog later adds a landing page first archived from the portal, it gets archived twice.
- The bot pushes to `main` most weeks (only when the catalog or index changed). Pull before editing `items.*` or `archive/index.json` by hand to avoid conflicts.
- The README and `archive/README.md` document the index schema. Update them if the schema changes.
