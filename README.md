# xmupdates

Unofficial mirror of the XiongMai / [JFTech](https://baike.jftech.com/) IP camera
and DVR firmware catalogs and binaries, maintained for the
[OpenIPC](https://openipc.org/) community.

XiongMai is the OEM behind a large fraction of off-brand IP cameras and DVRs.
Stock firmware downloads are useful for OpenIPC users who need to recover a
device, compare against a stock image, or roll back a flaky upgrade. The
vendor's download portal is sometimes unreliable (and as of writing, has an
expired TLS certificate), so this repository keeps a self-updating mirror.

The vendor rebranded to JFTech and moved the catalog from `baike.xm030.cn` to
`baike.jftech.com` in July 2026 — the old host, along with the rest of the
`xm030.cn` zone bar `download.xm030.cn`, no longer resolves. The endpoint itself
was unchanged. In September 2026 the landing pages moved too, from
`download.xm030.cn` to `download.jftech.com` (same ids, same content); the
downloader treats both hosts as one, so the move doesn't re-archive anything.

A second list, the [JFTech portal](https://en.jftech.com/#/softwareDownloads)
"Software Download → Firmware" pages, overlaps the catalog but also carries
firmware it lacks (notably YK-style DVR/NVR builds). It is mirrored into
`items.portal`.

## Layout

| File | What it is |
|---|---|
| [`items.ipc`](items.ipc) | JSON catalog of IP-camera firmwares from XM030. |
| [`items.dvr`](items.dvr) | JSON catalog of DVR/NVR firmwares from XM030. |
| [`items.portal`](items.portal) | JSON list of firmwares from the en.jftech.com portal (IPC and DVR/NVR). |
| [`archive/index.json`](archive/index.json) | Map from catalog `id` (or `p<portal id>`) to mirrored binaries on this repo's `firmware-archive` release. |
| [`archive/<prefix>/`](archive) | Legacy folders from before the Releases-based mirror. Not added to. |
| [`xmupdates.py`](xmupdates.py) | Refreshes `items.ipc` / `items.dvr` from the vendor pagination endpoint, and `items.portal` from the portal API. |
| [`download_firmwares.py`](download_firmwares.py) | Downloads catalog and portal rows that aren't yet in `archive/index.json` and uploads them as Release assets. |

### Catalog row schema

Each entry in `items.*` `rows[]`:

```json
{
  "id": 2281,
  "version": "000809Q4.1",
  "name": "IPC_XM530V200_R80XV50B_WIFIXM713G",
  "downloadUrl": "https://download.xm030.cn/d/MDAwMDE2MzU=",
  "downloadMenuId": 6,
  "usage": ""
}
```

`downloadUrl` is a landing page; the actual ZIP lives on Huawei Cloud OBS and
the URL is parsed out of the page HTML.

### Portal row schema

`items.portal` is `{"rows": [...], "titles": {"<titleId>": "<section name>"}, "total": N}`,
fetched from `POST https://en.jftech.com/api-portal/support/pageElement/query`
for every list under the portal's "Firmware" menu. Rows are stored as the API
returns them (only `toAddress` is whitespace-stripped):

```json
{
  "id": 1475,
  "name": "000807AF.1",
  "description": "IPC_XM530V200_X2C-WR_WIFIXM713G.713g.Nat.dss_V5.00.R02",
  "toAddress": "https://download.xm030.cn/d/MDAwMDE2MTU=",
  "titleId": 21,
  "online": true,
  "createdDate": "2024-05-30 13:53:06",
  "updatedDate": "2024-05-30 13:53:06",
  ...
}
```

Note that `name` holds the version and `description` the board/build, and that
portal `id`s are **not** catalog ids — the two number spaces overlap for
unrelated firmware. The API must be called over HTTP/2: it redirects HTTP/1.1
clients to plain `http://`, which breaks the POST.

### `archive/index.json` schema

Keyed by catalog `id` for catalog rows and by `p<id>` (e.g. `"p1475"`) for
portal rows — keys are strings, don't assume they parse as integers. Each entry
holds device metadata and a `revisions` list
so historical firmware versions are kept around (e.g. for downgrades) — when
XiongMai re-publishes a firmware under the same catalog id, a new revision is
appended rather than replacing the old one.

```json
{
  "2281": {
    "name": "IPC_XM530V200_R80XV50B_WIFIXM713G",
    "downloadMenuId": 6,
    "revisions": [
      {
        "version": "000809Q4.1",
        "downloadUrl": "https://download.xm030.cn/d/MDAwMDE2MzU=",
        "filename": "id2281__000809Q4.1__IPC_...zip",
        "sha256": "ab12...",
        "size": 6798757,
        "release_tag": "firmware-archive",
        "asset_url": "https://github.com/OpenIPC/xmupdates/releases/download/firmware-archive/id2281__000809Q4.1__IPC_...zip",
        "zip_url": "https://obs-xm-customer.obs.cn-east-2.myhuaweicloud.com/000809Q4.1IPC_...zip",
        "archived_at": "2026-05-04T12:00:00Z"
      }
    ]
  }
}
```

Portal entries (`p<id>`) carry `"source": "portal"` and `titleId` instead of
`downloadMenuId`, and their `name` is the portal row's `description`. Asset
names start with the key (`id2281__…` / `p1475__…`). `zip_url` is only present
on revisions archived since it was added. Entries may also hold `unavailable`
(the vendor took the file offline) and `data_errors` (malformed row) lists.

When a landing page appears in both sources, the catalog row wins and the
portal row is not archived separately. Known limitations: if the content
behind an already-recorded landing page changes, it is not archived again under
another key; and if the catalog later adds a landing page first archived from
the portal, that firmware is archived twice.

## Grabbing a firmware

```sh
# Latest revision for catalog id 2281:
jq -r '.["2281"].revisions[-1].asset_url' archive/index.json | xargs curl -LO

# A specific historical version:
jq -r '.["2281"].revisions[] | select(.version == "000809Q4.1").asset_url' archive/index.json | xargs curl -LO

# Portal rows are keyed "p<id>":
jq -r '.["p1475"].revisions[-1].asset_url' archive/index.json | xargs curl -LO
```

## Automation

A weekly GitHub Actions workflow ([`weekly-update.yml`](.github/workflows/weekly-update.yml))
runs every Monday and:

1. Refreshes `items.ipc` / `items.dvr` / `items.portal` and commits any diff.
2. Walks the catalog and portal lists for rows whose `(key, downloadUrl)` is
   not yet in `archive/index.json`, downloads the ZIP, uploads it to the
   rolling `firmware-archive` GitHub Release, and appends a revision to the
   index. Catalog and portal rows are interleaved within each run.

The download job is paced (`--max-per-run 25` by default) so the initial
backfill spreads across many cron ticks rather than hammering the vendor
server. Manual `workflow_dispatch` exposes the same knobs.

## Local use

```sh
pip install -r requirements.txt

# Refresh the catalogs (items.ipc, items.dvr, items.portal) only:
python xmupdates.py

# Dry-run the firmware downloader (resolves OBS URLs, no upload):
GH_TOKEN=$(gh auth token) python download_firmwares.py --max-per-run 5 --dry-run
```

The downloader uses `gh release upload` and so requires
[GitHub CLI](https://cli.github.com/) authenticated against this repo.

## Contributing

PRs welcome. If you have a firmware that's missing from the index, open an
issue with the catalog `id`, the original `downloadUrl`, and a `sha256` of the
ZIP — automation will pick it up on the next cron tick once the entry shows
up at the vendor.

## Disclaimer

Unofficial mirror, no warranty. The vendor's TLS certificate on the
legacy `download.xm030.cn` host is expired; the tooling intentionally disables
TLS verification only for that host (landing pages, and the few ZIPs served
from it). Mirrored firmware binaries remain the
property of XiongMai. Open an issue if you are a rights holder and want
something removed.

## License

The catalog data, index file, and Python tooling in this repository are
released under [CC0-1.0](LICENSE) (public domain dedication). Mirrored
vendor firmware binaries are not covered by that license — they remain
property of their original publisher.
