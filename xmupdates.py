#!/usr/bin/env python3

import json
import sys

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

# The old host served a cert issued for a different domain, expired in 2019. We
# have not been able to check the new host's cert (it is region-restricted and
# unreachable from outside CN), so verification stays off.
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


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
            failures.append(suffix)
            continue
        rows = sorted((clean_row(r) for r in items["rows"]), key=lambda r: r["id"])
        if not rows:
            # Writing this out would commit a destructive diff over a good catalog.
            print(f"{fname}: vendor returned 0 rows (total={total}); "
                  "refusing to overwrite", file=sys.stderr)
            failures.append(suffix)
            continue
        items["rows"] = rows
        print(f"Writing {fname} ({len(rows)} rows)...")
        with open(fname, "w") as f:
            json.dump(items, f, sort_keys=True, indent=4)
            f.write("\n")

    if failures:
        print(
            f"\nFailed to refresh: {', '.join(failures)}.\n"
            f"Endpoint: {PAGINATION_URL}\n"
            "The vendor has moved this host before (baike.xm030.cn -> "
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
