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

A third list is not the vendor's: [cctvsp.ru's firmware archive](https://www.cctvsp.ru/support/proshivki),
a Russian seller's library of about forty builds keyed by device ID, several
for device IDs whose firmware the vendor has withdrawn. Every file there is the
seller's own build (`IPEYE_…`: the vendor firmware with the IPeye cloud
service added), not stock. It is mirrored into `items.cctvsp`, archived under
`c<item id>` keys marked with `"origin": "cctvsp.ru"`, and openipc.org shows
such a build as the seller's, only for a device ID with no vendor build. Its
`test_…` builds are not archived.

## Layout

| File | What it is |
|---|---|
| [`items.ipc`](items.ipc) | JSON catalog of IP-camera firmwares from XM030. |
| [`items.dvr`](items.dvr) | JSON catalog of DVR/NVR firmwares from XM030. |
| [`items.portal`](items.portal) | JSON list of firmwares from the en.jftech.com portal (IPC and DVR/NVR). |
| [`items.cctvsp`](items.cctvsp) | JSON list of cctvsp.ru's firmware pages (the seller's own IPeye builds): device ID, title, updated date, version, file name, download link. |
| [`archive/index.json`](archive/index.json) | Map from catalog `id` (or `p<portal id>`) to mirrored binaries on this repo's `firmware-archive` release. |
| [`archive/<prefix>/`](archive) | Legacy folders from before the Releases-based mirror. Not added to. |
| [`xmupdates.py`](xmupdates.py) | Refreshes `items.ipc` / `items.dvr` from the vendor pagination endpoint, and `items.portal` from the portal API. |
| [`cctvsp.py`](cctvsp.py) | Refreshes `items.cctvsp` from cctvsp.ru's archive pages; all-or-nothing. |
| [`download_firmwares.py`](download_firmwares.py) | Downloads catalog, portal and cctvsp rows that aren't yet in `archive/index.json` and uploads them as Release assets. |
| [`push_openipc_org.py`](push_openipc_org.py) | Pushes the archive's list to [openipc.org](https://openipc.org/cameras/boards), whose board catalogue offers every stock build per device ID. Runs at the end of the weekly workflow over a GitHub OIDC token; no secret. |

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
names start with the key (`id2281__…` / `p1475__…`) and only use
`[A-Za-z0-9._-]`; always take the link from `asset_url` rather than building it. `zip_url` is only present
on revisions archived since it was added. Entries may also hold `unavailable`
(the vendor took the file offline) and `data_errors` (malformed row) lists.
A revision may hold `repaired`: the vendor's file was broken as published and
`asset_url` serves an exact repair instead — see [Repaired packages](#repaired-packages).

cctvsp entries (`c<id>`) carry `"source": "cctvsp"`, `"origin": "cctvsp.ru"`
and `page` (the file's page there); their `name` is the file name without its
extension, each revision's `downloadUrl` is the seller's download link (its
hash changes when the file does, which makes a new revision), its version is
`<device ID>.<the page's version>`, and `published_at` is the page's
"Обновлено" date.

When a landing page appears in both sources, the catalog row wins and the
portal row is not archived separately. Known limitations: if the content
behind an already-recorded landing page changes, it is not archived again under
another key; and if the catalog later adds a landing page first archived from
the portal, that firmware is archived twice.

## Repaired packages

Rarely, XM publishes a package its own updater refuses. When the damage can be
undone *exactly* — the result matches the checksums the vendor itself recorded
— the revision's `asset_url` serves the repaired package under its usual name,
and the vendor's file is kept for reference as a `…vendor-broken…` asset,
recorded in the revision's `repaired.vendor_original` (never as a revision of
its own, so nothing offers it for download). Each repair is a script under
[`repairs/`](repairs) that rebuilds the package from public inputs.

| Catalog id | Build | What was wrong | Script |
|---|---|---|---|
| 1873 | `000529B2.1` IPC_HI3516EV300_85H50AI, 2021-03-03 | 77 KB of `user-x.cramfs.img` altered after its checksums were taken; stock updater answers `Ret 514` ([#9](https://github.com/OpenIPC/xmupdates/issues/9)) | [`repairs/id1873_000529B2.py`](repairs/id1873_000529B2.py) |

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

1. Refreshes `items.ipc` / `items.dvr` / `items.portal` / `items.cctvsp` and commits any diff.
2. Walks the catalog and portal lists for rows whose `(key, downloadUrl)` is
   not yet in `archive/index.json`, downloads the ZIP, uploads it to the
   rolling `firmware-archive` GitHub Release, and appends a revision to the
   index. Catalog, portal and cctvsp rows are interleaved within each run.

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
