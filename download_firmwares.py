#!/usr/bin/env python3
"""Download firmwares listed in items.ipc / items.dvr / items.portal that aren't yet archived.

For each row whose (key, downloadUrl) has not been seen before:
  1. Fetch the XM030 landing page (TLS verify disabled — vendor cert is expired).
  2. Parse out the OBS ZIP URL.
  3. Download the ZIP, compute sha256.
  4. Upload to the GitHub Release `firmware-archive` (one rolling release).
  5. Append a revision entry to archive/index.json and commit periodically.

Catalog rows (items.ipc / items.dvr) are keyed by their numeric catalog id.
Portal rows (items.portal) have ids from a separate namespace, so they are keyed
"p<id>". A portal row whose landing page is already in the catalog, or already
recorded anywhere in the index, is skipped: the catalog wins on overlap.

Index schema:

    {
      "<catalog_id> | p<portal_id>": {
        "name": "...",
        "downloadMenuId": 6,
        "revisions": [
          {
            "version": "000809Q4.1",
            "downloadUrl": "https://download.xm030.cn/d/...",
            "filename": "id2281__000809Q4.1__IPC_...zip",
            "sha256": "...",
            "size": 6798757,
            "release_tag": "firmware-archive",
            "asset_url": "https://github.com/.../firmware-archive/id2281__...zip",
            "zip_url": "https://obs-xm-customer.obs...myhuaweicloud.com/...zip",
            "archived_at": "2026-05-04T12:00:00Z"
          }
        ]
      }
    }

Portal entries carry "source": "portal" and "titleId" in place of
"downloadMenuId", and use the row's description as "name". "zip_url" is only
present on revisions archived after it was introduced.

Asset filenames embed both the row key and the full version so a
re-published firmware never overwrites the previous binary; if the name is
still taken by a different binary, the sha256 prefix is added to it. Old revisions
stay on the release indefinitely (downgrades stay possible).
"""

import argparse
import base64
import binascii
import datetime as dt
import hashlib
import itertools
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib3
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parent
INDEX_PATH = ROOT / "archive" / "index.json"
CATALOG_FILES = [ROOT / "items.ipc", ROOT / "items.dvr"]
PORTAL_FILE = ROOT / "items.portal"
# cctvsp.ru's firmware archive (cctvsp.py): the seller's own builds, several
# for device IDs whose firmware the vendor withdrew. Archived like the vendor's,
# pushed to openipc.org with the archive named as their origin.
CCTVSP_FILE = ROOT / "items.cctvsp"
CCTVSP_ORIGIN = "cctvsp.ru"
# The only host a cctvsp.ru download may come from, redirects included.
CCTVSP_HOSTS = ("www.cctvsp.ru", "cctvsp.ru")
RELEASE_TAG = "firmware-archive"
LANDING_HOST = "download.xm030.cn"
# In September 2026 the vendor moved every catalog downloadUrl to this host. It
# serves the same /d/<b64> ids with the same content, and has a valid cert.
LANDING_HOSTS = (LANDING_HOST, "download.jftech.com")
LANDING_ID_RE = re.compile(r"/d/([A-Za-z0-9+/=]+)")
# Vendor hosts ZIPs on either Huawei OBS or Kingsoft Cloud KS3 depending on age.
# A few are served from a landing host itself (/ss/...); from download.xm030.cn
# that means no TLS verification — same trust as its landing pages.
ZIP_HOST_SUFFIXES = ("myhuaweicloud.com", "ksyun.com")
SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9._-]+")
# Some portal rows prefix toAddress with a circled digit ("①https://...").
LANDING_URL_RE = re.compile(r"https?://\S+")
INDEX_SECTIONS = ("revisions", "unavailable", "data_errors")
OFFLINE_MARKERS = ("文件已过期下线", "The file has expired")

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


class FirmwareUnavailable(Exception):
    """Vendor has taken the firmware offline."""


class CatalogDataError(Exception):
    """The catalog row itself is malformed (e.g. downloadUrl is not a URL)."""


def load_index():
    if INDEX_PATH.exists():
        with INDEX_PATH.open() as f:
            return json.load(f)
    return {}


def save_index(index):
    tmp = INDEX_PATH.with_suffix(".json.tmp")
    with tmp.open("w") as f:
        json.dump(index, f, sort_keys=True, indent=2)
        f.write("\n")
    tmp.replace(INDEX_PATH)


def catalog_items():
    items = []
    for path in CATALOG_FILES:
        with path.open() as f:
            data = json.load(f)
        for row in data["rows"]:
            items.append({
                "source": "catalog",
                "key": str(row["id"]),
                "asset_id": f"id{row['id']}",
                "label": f"id={row['id']}",
                "version": row.get("version", "") or "",
                "landing": (row.get("downloadUrl") or "").strip(),
                "meta": {"name": row.get("name", ""),
                         "downloadMenuId": row.get("downloadMenuId")},
            })
    return items


def portal_items():
    if not PORTAL_FILE.exists():
        return []
    with PORTAL_FILE.open() as f:
        data = json.load(f)
    items = []
    for row in sorted(data["rows"], key=lambda r: r["id"]):
        raw = (row.get("toAddress") or "").strip()
        m = LANDING_URL_RE.search(raw)
        key = f"p{row['id']}"
        items.append({
            "source": "portal",
            "key": key,
            "asset_id": key,
            "label": key,
            "version": row.get("name", "") or "",
            "landing": m.group(0) if m else raw,
            "meta": {"name": row.get("description", ""),
                     "source": "portal",
                     "titleId": row.get("titleId")},
        })
    return items


def cctvsp_items():
    """cctvsp.ru's rows, keyed c{item id}. Its test builds are not archived."""
    if not CCTVSP_FILE.exists():
        return []
    with CCTVSP_FILE.open() as f:
        data = json.load(f)
    items = []
    for row in sorted(data["rows"], key=lambda r: r["id"]):
        filename = (row.get("filename") or "").strip()
        if not filename or filename.lower().startswith("test_"):
            continue
        key = f"c{row['id']}"
        items.append({
            "source": "cctvsp",
            "key": key,
            "asset_id": key,
            "label": f"cctvsp {key}",
            # The device ID first, as the vendor's versions have it: the push
            # reads it from the version's first eight characters.
            "version": f"{row['device_id']}.{(row.get('version') or row.get('updated') or '0').strip()}",
            # The download link, not the page: its hash changes with the file,
            # so a replaced build becomes a new revision.
            "landing": (row.get("downloadUrl") or "").strip(),
            "filename": filename,
            "published": row.get("updated"),
            "meta": {"name": Path(filename).stem, "source": "cctvsp",
                     "origin": CCTVSP_ORIGIN, "page": row.get("page")},
        })
    return items


def pending_items(index, catalog, portal, cctvsp=()):
    """Rows to process this run: catalog and portal interleaved."""
    pending_catalog = []
    seen_in_batch = set()
    for item in catalog:
        landing = item["landing"]
        if not landing:
            continue
        key = (item["key"], landing_key(landing))
        if key in seen_in_batch:
            continue
        if revision_seen(index, item["key"], landing):
            continue
        seen_in_batch.add(key)
        pending_catalog.append(item)

    # Landing pages already owned by the catalog or recorded in the index are
    # not archived again under a portal key; the first (lowest) portal id wins
    # when the portal lists one landing page twice.
    taken = {landing_key(i["landing"]) for i in catalog if i["landing"]}
    for entry in index.values():
        for section in INDEX_SECTIONS:
            taken.update(landing_key(r["downloadUrl"])
                         for r in entry.get(section, []) if r.get("downloadUrl"))
    pending_portal = []
    for item in portal:
        landing = item["landing"]
        if not landing or revision_seen(index, item["key"], landing):
            continue
        lkey = landing_key(landing)
        if lkey in taken:
            continue
        taken.add(lkey)
        pending_portal.append(item)

    pending_cctvsp = [i for i in cctvsp if i["landing"] and not revision_seen(index, i["key"], i["landing"])]

    # Round-robin, so rows that keep failing on one side can't starve the others.
    return [i for group in itertools.zip_longest(pending_catalog, pending_portal, pending_cctvsp)
            for i in group if i is not None]


def landing_key(url):
    """Identity of a landing page, independent of which vendor host serves it.

    download.xm030.cn and download.jftech.com serve the same /d/<b64> ids, so a
    host move must not look like a new revision of every row.
    """
    url = (url or "").strip()
    try:
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower()
    except ValueError:  # e.g. an unbalanced "[" in the authority
        return url
    m = LANDING_ID_RE.fullmatch(parsed.path)
    if parsed.scheme in ("http", "https") and host in LANDING_HOSTS and m:
        try:
            b64 = m.group(1).rstrip("=")
            return ("landing", int(base64.b64decode(b64 + "=" * (-len(b64) % 4), validate=True)))
        except (binascii.Error, ValueError):
            pass
    return url


def revision_seen(index, rid, download_url):
    entry = index.get(rid)
    if not entry:
        return False
    key = landing_key(download_url)
    for section in INDEX_SECTIONS:
        if any(landing_key(r.get("downloadUrl")) == key for r in entry.get(section, [])):
            return True
    return False


def session_for(url):
    s = requests.Session()
    host = urlparse(url).hostname or ""
    s.verify = host != LANDING_HOST  # vendor TLS cert is expired
    return s


def resolve_zip_url(landing_url):
    try:
        parsed = urlparse(landing_url)
        parsed.hostname  # raises on a malformed authority
    except ValueError as e:
        raise CatalogDataError(f"downloadUrl is not a valid URL: {landing_url!r} ({e})")
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise CatalogDataError(f"downloadUrl is not an http(s) URL: {landing_url!r}")
    s = session_for(landing_url)
    r = s.get(landing_url, timeout=60)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    for a in soup.find_all("a", href=True):
        href = urljoin(landing_url, a["href"].strip())
        parsed_href = urlparse(href)
        if not parsed_href.path.lower().endswith(".zip"):
            continue
        host = (parsed_href.hostname or "").lower()
        if host in LANDING_HOSTS or any(host == suffix or host.endswith("." + suffix)
                                        for suffix in ZIP_HOST_SUFFIXES):
            return href
    if any(marker in r.text for marker in OFFLINE_MARKERS):
        raise FirmwareUnavailable("vendor took the firmware offline")
    raise RuntimeError(
        f"No ZIP link found on {landing_url}. Page snippet: {r.text[:500]!r}"
    )


def seller_download(url):
    """A cctvsp.ru row's download link, checked like a vendor landing page:
    https on the seller's own host, or the row is a data error."""
    try:
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower()
    except ValueError as e:
        raise CatalogDataError(f"download link is not a valid URL: {url!r} ({e})")
    if parsed.scheme != "https" or host not in CCTVSP_HOSTS:
        raise CatalogDataError(f"download link is not https on {'/'.join(CCTVSP_HOSTS)}: {url!r}")
    return url


def download_zip(url, dest, hosts=None):
    """Download url to dest; with hosts, every hop of a redirect and the final
    answer must be on one of them."""
    s = session_for(url)
    sha = hashlib.sha256()
    size = 0
    with s.get(url, stream=True, timeout=300) as r:
        if hosts is not None:
            for hop in [*r.history, r]:
                host = (urlparse(hop.url).hostname or "").lower()
                if urlparse(hop.url).scheme != "https" or host not in hosts:
                    raise RuntimeError(f"{url} led to {hop.url}, not https on {'/'.join(hosts)}")
        if r.status_code in (404, 410):
            raise FirmwareUnavailable(f"CDN returned {r.status_code} for {url}")
        r.raise_for_status()
        with dest.open("wb") as f:
            for chunk in r.iter_content(chunk_size=1 << 16):
                if not chunk:
                    continue
                f.write(chunk)
                sha.update(chunk)
                size += len(chunk)
    return sha.hexdigest(), size


def safe_token(value):
    cleaned = SAFE_NAME_RE.sub("_", (value or "").strip()).strip("._-")
    return cleaned or "unknown"


def asset_name_for(asset_id, version, obs_url, disambiguator=None, filename=None):
    # GitHub silently rewrites asset names with characters outside this set
    # (e.g. "00000107(NBD7024H-P).zip" -> "00000107.NBD7024H-P.zip"), which
    # would leave the recorded filename/asset_url pointing at nothing.
    # A download link that is a query (cctvsp.ru's) names its file separately.
    obs_filename = safe_token(filename or Path(urlparse(obs_url).path).name)
    parts = [asset_id, safe_token(version)]
    if disambiguator:
        parts.append(disambiguator)
    return "__".join(parts + [obs_filename])


def pick_asset_name(existing_assets, asset_id, version, obs_url, sha256, filename=None):
    """Return (name, already_uploaded) that never replaces a different binary."""
    for disambiguator in (None, sha256[:8], sha256):
        name = asset_name_for(asset_id, version, obs_url, disambiguator, filename)
        digest = existing_assets.get(name)
        if digest is None:
            return name, False
        if digest == sha256:
            return name, True
    raise RuntimeError(f"every candidate asset name for {obs_url} holds a different binary")


def gh(*args, check=True, capture=False):
    return subprocess.run(
        ["gh", *args],
        check=check,
        capture_output=capture,
        text=True,
    )


def ensure_release_exists():
    res = gh("release", "view", RELEASE_TAG, check=False, capture=True)
    if res.returncode == 0:
        return
    print(f"Creating release {RELEASE_TAG}...")
    gh(
        "release", "create", RELEASE_TAG,
        "--title", "Firmware archive",
        "--notes", "Mirror of XiongMai firmware binaries. See archive/index.json for the full mapping from catalog id to asset URL.",
    )


def existing_release_assets():
    """Map asset name -> sha256 hex digest ("" if GitHub reports none)."""
    res = gh(
        "release", "view", RELEASE_TAG, "--json", "assets",
        check=False, capture=True,
    )
    if res.returncode != 0:
        # Without the list we can't tell a free name from someone else's binary.
        raise RuntimeError(f"cannot list {RELEASE_TAG} assets: {res.stderr.strip()}")
    assets = {}
    for a in json.loads(res.stdout).get("assets", []):
        digest = a.get("digest") or ""
        assets[a["name"]] = digest.removeprefix("sha256:")
    return assets


def upload_asset(local_path, asset_name):
    target = local_path.with_name(asset_name)
    if target != local_path:
        shutil.move(str(local_path), target)
    # No --clobber: pick_asset_name() only hands out free names, and if the
    # asset list was stale the upload fails (and is retried) instead of
    # replacing an existing binary.
    gh("release", "upload", RELEASE_TAG, str(target))
    return target


def asset_url(repo_slug, asset_name):
    return f"https://github.com/{repo_slug}/releases/download/{RELEASE_TAG}/{asset_name}"


def repo_slug():
    if slug := os.environ.get("GITHUB_REPOSITORY"):
        return slug
    res = gh("repo", "view", "--json", "nameWithOwner", "--jq", ".nameWithOwner",
            check=False, capture=True)
    if res.returncode == 0 and res.stdout.strip():
        return res.stdout.strip()
    return "OpenIPC/xmupdates"


def git(*args, check=True, capture=False):
    return subprocess.run(
        ["git", *args],
        check=check,
        capture_output=capture,
        text=True,
        cwd=ROOT,
    )


def commit_and_push(count):
    git("add", str(INDEX_PATH.relative_to(ROOT)))
    res = git("diff", "--cached", "--quiet", check=False)
    if res.returncode == 0:
        return
    git("commit", "-m", f"archive: +{count} firmware revisions")
    git("push", check=False)


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--max-per-run", type=int, default=25,
                   help="Max number of firmware revisions to download in one invocation.")
    p.add_argument("--commit-every", type=int, default=5,
                   help="Stage and push archive/index.json every N successful uploads.")
    p.add_argument("--dry-run", action="store_true",
                   help="Resolve OBS URLs and print plan; don't download or upload.")
    return p.parse_args()


def main():
    args = parse_args()
    index = load_index()
    pending = pending_items(index, catalog_items(), portal_items(), cctvsp_items())
    if args.max_per_run > 0:
        pending = pending[: args.max_per_run]

    if not pending:
        print("Nothing to download — index is up to date.")
        return 0

    slug = repo_slug()
    if not args.dry_run:
        ensure_release_exists()
    try:
        existing_assets = existing_release_assets()
    except RuntimeError as e:
        if not args.dry_run:
            print(f"Aborting: {e}", file=sys.stderr)
            return 1
        print(f"warning: {e}; dry run continues without it", file=sys.stderr)
        existing_assets = {}
    successes = 0
    failures = 0
    since_commit = 0

    unavailable_count = 0
    data_error_count = 0

    def entry_for(item):
        entry = index.setdefault(item["key"], {"revisions": []})
        entry.update(item["meta"])
        return entry

    def now():
        return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    def maybe_commit():
        nonlocal since_commit
        if not args.dry_run:
            save_index(index)
            since_commit += 1
            if since_commit >= args.commit_every:
                commit_and_push(since_commit)
                since_commit = 0

    def record_unavailable(item):
        entry_for(item).setdefault("unavailable", []).append({
            "version": item["version"],
            "downloadUrl": item["landing"],
            "checked_at": now(),
        })
        maybe_commit()

    for item in pending:
        landing = item["landing"]
        version = item["version"]
        print(f"\n[{item['label']}] version={version!r} -> {landing}")
        try:
            parsed = urlparse(landing)
            if (item["source"] == "portal" and parsed.scheme in ("http", "https")
                    and (parsed.hostname or "").lower() not in LANDING_HOSTS):
                # Not recorded: if the vendor moved its landing host, these rows
                # must come back once LANDING_HOSTS knows about the new one.
                raise RuntimeError(
                    f"portal landing page is not on {' / '.join(LANDING_HOSTS)}; "
                    "has the vendor moved its download host?")
            try:
                # cctvsp.ru links its file directly; the vendor's landing
                # pages are scraped for theirs.
                obs_url = seller_download(landing) if item["source"] == "cctvsp" else resolve_zip_url(landing)
            except FirmwareUnavailable:
                print("  vendor reports firmware offline; recording in index.")
                record_unavailable(item)
                unavailable_count += 1
                continue
            except CatalogDataError as e:
                print(f"  catalog data error ({e}); recording in index.")
                entry_for(item).setdefault("data_errors", []).append({
                    "version": version,
                    "downloadUrl": landing,
                    "reason": str(e),
                    "checked_at": now(),
                })
                maybe_commit()
                data_error_count += 1
                continue
            filename = item.get("filename")
            asset_name = asset_name_for(item["asset_id"], version, obs_url, filename=filename)
            print(f"  zip: {obs_url}")
            print(f"  asset: {asset_name}")

            if args.dry_run:
                if asset_name in existing_assets:
                    print("  asset name already on the release; would compare sha256.")
                successes += 1
                continue

            try:
                with tempfile.TemporaryDirectory() as td:
                    tmp_path = Path(td) / safe_token(filename or Path(urlparse(obs_url).path).name)
                    sha256, size = download_zip(obs_url, tmp_path,
                                                CCTVSP_HOSTS if item["source"] == "cctvsp" else None)
                    print(f"  sha256={sha256}  size={size}")
                    picked, uploaded_already = pick_asset_name(
                        existing_assets, item["asset_id"], version, obs_url, sha256, filename)
                    if picked != asset_name:
                        print(f"  name taken by a different binary; using {picked}")
                    asset_name = picked
                    if uploaded_already:
                        print("  identical asset already on the release; reusing it.")
                    else:
                        upload_asset(tmp_path, asset_name)
                        existing_assets[asset_name] = sha256
            except FirmwareUnavailable as e:
                print(f"  CDN reports binary missing ({e}); recording in index.")
                record_unavailable(item)
                unavailable_count += 1
                continue

            revision = {
                "version": version,
                "downloadUrl": landing,
                "filename": asset_name,
                "sha256": sha256,
                "size": size,
                "release_tag": RELEASE_TAG,
                "asset_url": asset_url(slug, asset_name),
                "zip_url": obs_url,
                "archived_at": now(),
            }
            if item.get("published"):
                # When the seller says it published the build (cctvsp.ru's "Обновлено").
                revision["published_at"] = item["published"] + "T00:00:00Z"
            entry_for(item).setdefault("revisions", []).append(revision)
            save_index(index)
            successes += 1
            since_commit += 1
            if since_commit >= args.commit_every:
                commit_and_push(since_commit)
                since_commit = 0
        except Exception as e:
            failures += 1
            print(f"  FAILED: {e!r}", file=sys.stderr)

    if since_commit > 0 and not args.dry_run:
        commit_and_push(since_commit)

    total_revisions = sum(len(v.get("revisions", [])) for v in index.values())
    total_unavailable = sum(len(v.get("unavailable", [])) for v in index.values())
    total_data_errors = sum(len(v.get("data_errors", [])) for v in index.values())
    print(f"\nDone. successes={successes} unavailable={unavailable_count} "
          f"data_errors={data_error_count} failures={failures} "
          f"total_revisions={total_revisions} total_unavailable={total_unavailable} "
          f"total_data_errors={total_data_errors}")
    if successes == 0 and unavailable_count == 0 and data_error_count == 0 and failures > 0:
        return 1
    return 0

if __name__ == "__main__":
    sys.exit(main())
