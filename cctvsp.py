#!/usr/bin/env python3
"""Refresh items.cctvsp: the firmware archive of cctvsp.ru, a Russian seller
of Xiongmai cameras.

  python cctvsp.py

https://www.cctvsp.ru/support/proshivki lists about forty firmware pages, each
for one XM device ID ("Прошивка для IP камеры 00001532 53H20L"), naming the
modules it is for, with the file hosted by the seller. Many are for device IDs
whose firmware the vendor has withdrawn; some are the seller's own builds
(IPEYE_... files carry the IPeye cloud). download_firmwares.py archives them
like the vendor's, and push_openipc_org.py tells openipc.org which archive
each came from.

One row per page, keyed by the seller's item id (the download link's
item_id). The write is all-or-nothing, like the portal: a list page or an item
page that fails, a page that no longer parses, or no rows at all leaves
items.cctvsp untouched and exits non-zero.
"""

import json
import os
import re
import sys
import time
from urllib.parse import parse_qs, urljoin, urlparse

import requests
from bs4 import BeautifulSoup

BASE = "https://www.cctvsp.ru"
LIST = BASE + "/support/proshivki"
OUT = "items.cctvsp"
HEADERS = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
                         "Chrome/128.0 Safari/537.36"}
DELAY = 1.0
DEVICE_ID = re.compile(r"\b([0-9A-Za-z]{8})\b")
MONTHS = {m: i for i, m in enumerate(("января", "февраля", "марта", "апреля", "мая", "июня", "июля",
                                      "августа", "сентября", "октября", "ноября", "декабря"), 1)}


def seller_link(url):
    """A download link on the seller's own site, over https."""
    u = urlparse(url)
    return u.scheme == "https" and (u.hostname or "").lower() == urlparse(BASE).hostname


class ArchiveError(Exception):
    """The archive did not answer as it did when this was written."""


def get(session, url):
    time.sleep(DELAY)
    r = session.get(url, headers=HEADERS, timeout=60)
    if r.status_code != 200:
        raise ArchiveError(f"{url}: HTTP {r.status_code}")
    return BeautifulSoup(r.text, "html.parser")


def main_part(soup):
    main = soup.find("main", id="content")
    if main is None:
        raise ArchiveError("no <main id=content>: the page layout changed")
    return main


def last_page(main):
    """The list's page count, from its pagination block: the highest
    /proshivki/<n> it links. The block is required -- without it the count
    cannot be known, and guessing "one page" would drop the rest."""
    block = main.select_one(".zoo-pagination")
    if block is None:
        raise ArchiveError("no pagination block (.zoo-pagination): the list's layout changed")
    nums = [int(m.group(1)) for a in block.select("a[href]")
            if (m := re.fullmatch(r"/support/proshivki/(\d+)", a["href"].strip()))]
    return max(nums, default=1)


def pages(session):
    """Every firmware page the list links, on every page of the list. The page
    count is read from the first page's pagination and each page is asked for
    by number, so a missing "next" link cannot end the walk early."""
    first = main_part(get(session, LIST))
    total = last_page(first)
    out = []
    for n in range(1, total + 1):
        main = first if n == 1 else main_part(get(session, f"{LIST}/{n}"))
        # A page does not link itself, so its own number counts too.
        if max(last_page(main), n) != total:
            raise ArchiveError(f"list page {n} says the list has {max(last_page(main), n)} pages, page 1 said {total}")
        found = 0
        for a in main.select('a[href^="/support/proshivka"]'):
            href = urljoin(BASE, a["href"])
            found += 1
            if href not in out:
                out.append(href)
        if found == 0:
            raise ArchiveError(f"list page {n} of {total} links no firmware")
    return out


def spec(main, label):
    for li in main.select("ul.pos-specification li"):
        strong = li.find("strong")
        if strong and strong.get_text(strip=True).rstrip(":").strip() == label:
            strong.extract()
            return li.get_text(" ", strip=True)
    return None


def russian_date(text):
    """"Четверг, 21 марта 2019" -> 2019-03-21."""
    m = re.search(r"(\d{1,2})\s+([а-я]+)\s+(\d{4})", (text or "").lower())
    if not m or m.group(2) not in MONTHS:
        return None
    return f"{int(m.group(3)):04d}-{MONTHS[m.group(2)]:02d}-{int(m.group(1)):02d}"


def item(session, url):
    main = main_part(get(session, url))
    h1 = main.select_one("h1.pos-title")
    link = main.select_one('a[href*="method=download"]')
    if h1 is None or link is None:
        raise ArchiveError(f"{url}: no title or no download link")
    title = h1.get_text(" ", strip=True)
    after = title.split("камеры", 1)[-1].split("регистратора", 1)[-1]
    dev = DEVICE_ID.search(after)
    if not dev:
        raise ArchiveError(f"{url}: no device ID in {title!r}")
    href = urljoin(BASE, link["href"])
    if not seller_link(href):
        raise ArchiveError(f"{url}: download link {href} is not on {BASE}")
    item_id = parse_qs(urlparse(href).query).get("item_id", [None])[0]
    if not item_id or not item_id.isdigit():
        raise ArchiveError(f"{url}: no item_id in {href}")
    filename = re.sub(r"^\s*Скачать\s+", "", link.get("title") or "").strip()
    updated = russian_date(spec(main, "Обновлено"))
    version = spec(main, "Версия") or ""
    # A page that has lost any of these no longer reads as it did: the whole
    # refresh stops rather than write a row that drops the build or its date.
    for what, value in (("file name", filename), ("updated date", updated), ("version", version)):
        if not value:
            raise ArchiveError(f"{url}: no {what}")
    media = main.select_one(".pos-media")
    return {
        "id": int(item_id),
        "page": url,
        "title": title,
        "device_id": dev.group(1).upper(),
        "build": after[dev.end():].strip(" ()"),
        "description": media.get_text(" ", strip=True) if media else "",
        "updated": updated,
        "version": version,
        "size": spec(main, "Размер") or "",
        "filename": filename,
        "downloadUrl": href,
    }


def main():
    session = requests.Session()
    try:
        rows = [item(session, url) for url in pages(session)]
    except (requests.RequestException, ArchiveError) as e:
        sys.exit(f"cctvsp: {e}; {OUT} left as it was")
    if not rows:
        sys.exit(f"cctvsp: the archive listed no firmware; {OUT} left as it was")
    ids = [r["id"] for r in rows]
    dup = sorted({i for i in ids if ids.count(i) > 1})
    if dup:
        sys.exit(f"cctvsp: two pages share item ids {dup}; {OUT} left as it was")
    rows.sort(key=lambda r: r["id"])
    tmp = OUT + ".tmp"
    with open(tmp, "w") as f:
        json.dump({"source": LIST, "rows": rows}, f, ensure_ascii=False, sort_keys=True, indent=4)
        f.write("\n")
    os.replace(tmp, OUT)
    print(f"cctvsp: {len(rows)} firmware pages")


if __name__ == "__main__":
    main()
