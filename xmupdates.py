#!/usr/bin/env python3

import json
import math
import sys

import httpx
import requests
import urllib3

CATALOGS = {
    6: "ipc",
    5: "dvr",
}

# The vendor rebranded from XiongMai (XM030) to JFTech and moved the catalog here
# in July 2026; `baike.xm030.cn` is now NXDOMAIN. The endpoint is otherwise
# unchanged — same path, params and response schema. Firmware binaries still live
# on `download.xm030.cn`, so download_firmwares.py is unaffected.
PAGINATION_URL = "https://baike.jftech.com/download/pagination.do"

# Since late September 2026 the catalog host sits behind Huawei CloudWAF, which
# answers the default python-requests User-Agent with HTTP 418 and an HTML
# "访问被拦截" (access blocked) page. A browser User-Agent gets the JSON.
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/128.0 Safari/537.36",
}

# The en.jftech.com "Software Download" page is a second, overlapping list that
# also carries firmware missing from the catalog above (notably YK-style DVRs).
# Its ids are a separate namespace from the catalog's. It has a valid cert, but
# answers HTTP/1.1 requests with a 301 to plain http://, which turns the POST
# into a GET the API rejects — so it is fetched over HTTP/2 with httpx, and any
# redirect is treated as a failure rather than followed into plaintext.
PORTAL_API = "https://en.jftech.com/api-portal/"
PORTAL_PAGE_SIZE = 100
PORTAL_FILE = "items.portal"

# The old host served a cert issued for a different domain, expired in 2019. We
# have not been able to check the new host's cert (it is region-restricted and
# unreachable from outside CN), so verification stays off.
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


class PortalError(Exception):
    """The portal answered, but not with usable data."""


def get_rows(param, num):
    r = requests.get(
        PAGINATION_URL,
        params={"page": 1, "rows": num, "paramValue": param},
        headers=HEADERS,
        verify=False,
        timeout=60,
    )
    r.raise_for_status()
    return r.json()


def clean_row(row):
    url = row.get("downloadUrl")
    if isinstance(url, str):
        row["downloadUrl"] = url.strip()
    return row


def portal_post(client, path, body):
    r = client.post(PORTAL_API + path, json=body)
    if r.is_redirect:
        raise PortalError(
            f"{path}: redirected to {r.headers.get('location')!r} — the portal does "
            "this to HTTP/1.1 clients; is HTTP/2 (the h2 package) available?"
        )
    if r.http_version != "HTTP/2":
        raise PortalError(f"{path}: negotiated {r.http_version}, expected HTTP/2")
    r.raise_for_status()
    data = r.json()
    if data.get("code") != 2000:
        raise PortalError(f"{path}: code={data.get('code')} msg={data.get('msg')!r}")
    return data["data"]


def portal_titles(client):
    """Return {titleId: name} for every firmware list on the portal."""
    tree = portal_post(client, "support/title/allTitle", {})
    firmware = [node for node in tree if node["name"] == "Firmware"]
    if len(firmware) != 1:
        raise PortalError(f"expected one 'Firmware' menu node, found {len(firmware)}")
    titles = {c["id"]: c["name"] for c in firmware[0]["children"] if c["pageType"] == "list"}
    if not titles:
        raise PortalError("'Firmware' menu node has no list children")
    return titles


def portal_title_rows(client, title_id):
    rows = []
    page = 1
    total = None
    while True:
        data = portal_post(client, "support/pageElement/query",
                           {"titleId": title_id, "page": page, "limit": PORTAL_PAGE_SIZE})
        total = data["total"]
        batch = data["data"] or []
        if not batch or page > math.ceil(total / PORTAL_PAGE_SIZE) + 1:
            break
        rows.extend(batch)
        if len(rows) >= total:
            break
        page += 1
    if len(rows) != total:
        raise PortalError(f"titleId={title_id}: got {len(rows)} rows, total={total}")
    return rows


def fetch_portal():
    # All-or-nothing: a partial list would show up as a mass deletion in the diff.
    with httpx.Client(http2=True, follow_redirects=False, timeout=60) as client:
        titles = portal_titles(client)
        rows = []
        for title_id in sorted(titles):
            rows.extend(portal_title_rows(client, title_id))
    ids = [r["id"] for r in rows]
    if len(ids) != len(set(ids)):
        raise PortalError("duplicate row ids across portal titles")
    for r in rows:
        if isinstance(r.get("toAddress"), str):
            r["toAddress"] = r["toAddress"].strip()
    rows.sort(key=lambda r: r["id"])
    return {
        "rows": rows,
        "titles": {str(k): v for k, v in titles.items()},
        "total": len(rows),
    }


def write_json(fname, data):
    with open(fname, "w") as f:
        json.dump(data, f, sort_keys=True, indent=4)
        f.write("\n")


def main():
    # A catalog we can't reach is the vendor's problem, not a reason to lose the
    # other catalog's refresh — collect failures and keep going. Anything not a
    # RequestException (e.g. KeyError on a missing "total") is a schema change and
    # deserves its traceback.
    failures = []
    for param, suffix in CATALOGS.items():
        fname = f"items.{suffix}"
        try:
            total = get_rows(param, 1)["total"]
            items = get_rows(param, total)
        except requests.exceptions.RequestException as e:
            print(f"{fname}: vendor endpoint unreachable: {e}", file=sys.stderr)
            failures.append((fname, PAGINATION_URL))
            continue
        rows = sorted((clean_row(r) for r in items["rows"]), key=lambda r: r["id"])
        if not rows:
            # Writing this out would commit a destructive diff over a good catalog.
            print(f"{fname}: vendor returned 0 rows (total={total}); "
                  "refusing to overwrite", file=sys.stderr)
            failures.append((fname, PAGINATION_URL))
            continue
        items["rows"] = rows
        print(f"Writing {fname} ({len(rows)} rows)...")
        write_json(fname, items)

    # Same policy for the portal: transport and API-level errors are collected,
    # a KeyError/TypeError on the response shape is a schema change and crashes.
    try:
        portal = fetch_portal()
    except (httpx.HTTPError, ValueError, PortalError) as e:
        print(f"{PORTAL_FILE}: portal refresh failed: {e}", file=sys.stderr)
        failures.append((PORTAL_FILE, PORTAL_API))
    else:
        if portal["rows"]:
            print(f"Writing {PORTAL_FILE} ({portal['total']} rows, "
                  f"{len(portal['titles'])} titles)...")
            write_json(PORTAL_FILE, portal)
        else:
            print(f"{PORTAL_FILE}: portal returned 0 rows; refusing to overwrite",
                  file=sys.stderr)
            failures.append((PORTAL_FILE, PORTAL_API))

    if failures:
        print(
            "\nFailed to refresh:\n"
            + "".join(f"  {fname} (endpoint: {url})\n" for fname, url in failures)
            + "The vendor has moved the catalog host before (baike.xm030.cn -> "
            "baike.jftech.com, July 2026); check whether it moved again.\n"
            "An HTTP 418 with an HTML body is the CloudWAF bot block; check "
            "whether it now rejects HEADERS too.\n"
            "Archiving of already-known firmware is unaffected and runs anyway.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
