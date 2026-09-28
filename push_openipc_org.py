#!/usr/bin/env python3
"""Tell openipc.org what this mirror holds, after each weekly run.

openipc.org's board catalogue offers every archived stock build for a device
ID, so people can stay on vendor firmware -- and go back to an earlier build
when a new one misbehaves. It never polls GitHub: this job pushes the whole
list once per run, and the push replaces what the site had
(https://github.com/OpenIPC/website/blob/master/service/internal/vendorfw/PUSH.md).

Needs `permissions: id-token: write`: the site checks a GitHub Actions OIDC
token (audience https://openipc.org) from this repository's
weekly-update.yml on main. There is no secret.

  python push_openipc_org.py [--dry-run] [--url https://dev.openipc.org/api/v1/vendor-firmware]
"""

import argparse
import gzip
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request

URL = "https://openipc.org/api/v1/vendor-firmware"
AUDIENCE = "https://openipc.org"
DEVICE_ID = re.compile(r"^[0-9A-Z]{8}$")


def items(index):
    """Every archived file, keyed by its asset name, its device ID the
    version's first eight characters (000559A7.1 -> 000559A7). The vendor
    re-publishes under the same version, and each archived file counts. The
    same list the site's `openipc vendor-firmware import-history` builds."""
    out, seen = [], set()
    for e in index.values():
        name = (e.get("name") or "").strip()
        for r in e.get("revisions", []):
            version = (r.get("version") or "").strip()
            dev = version[:8].upper()
            url = r.get("asset_url") or ""
            if not url or not name or not DEVICE_ID.match(dev) or url in seen:
                continue
            seen.add(url)
            it = {"key": url.rsplit("/", 1)[-1], "device_id": dev, "version": version, "build": name, "asset_url": r["asset_url"]}
            if r.get("sha256"):
                it["sha256"] = r["sha256"].lower()
            if r.get("size"):
                it["size"] = r["size"]
            if r.get("archived_at"):
                it["published_at"] = r["archived_at"]
            out.append(it)
    return out


def oidc_token():
    url, bearer = os.environ.get("ACTIONS_ID_TOKEN_REQUEST_URL"), os.environ.get("ACTIONS_ID_TOKEN_REQUEST_TOKEN")
    if not url or not bearer:
        sys.exit("no OIDC token: the job needs `permissions: id-token: write`")
    req = urllib.request.Request(url + "&audience=" + AUDIENCE, headers={"Authorization": "Bearer " + bearer})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)["value"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="print the push's size and stop")
    ap.add_argument("--url", default=URL)
    args = ap.parse_args()
    with open("archive/index.json") as f:
        body = {"schema": 1, "source": "xmupdates", "items": items(json.load(f))}
    data = gzip.compress(json.dumps(body).encode())
    print(f"{len(body['items'])} items, {len(data)} bytes gzipped")
    if args.dry_run:
        return
    token = oidc_token()
    for attempt in range(5):
        req = urllib.request.Request(args.url, data=data, method="POST", headers={
            "Authorization": "Bearer " + token, "Content-Type": "application/json", "Content-Encoding": "gzip"})
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                print(r.status, r.read().decode())
                return
        except urllib.error.HTTPError as e:
            msg = e.read().decode(errors="replace")
            if e.code < 500:
                sys.exit(f"refused: {e.code} {msg}")
            print(f"attempt {attempt + 1}: {e.code} {msg}")
        except urllib.error.URLError as e:
            print(f"attempt {attempt + 1}: {e.reason}")
        time.sleep(30 * (attempt + 1))
    sys.exit("openipc.org did not take the push")


if __name__ == "__main__":
    main()
