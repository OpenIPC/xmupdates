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
was unchanged, and firmware binaries are still served from `download.xm030.cn`.

## Layout

| File | What it is |
|---|---|
| [`items.ipc`](items.ipc) | JSON catalog of IP-camera firmwares from XM030. |
| [`items.dvr`](items.dvr) | JSON catalog of DVR/NVR firmwares from XM030. |
| [`archive/index.json`](archive/index.json) | Map from catalog `id` to mirrored binaries on this repo's `firmware-archive` release. |
| [`archive/<prefix>/`](archive) | Legacy folders from before the Releases-based mirror. Not added to. |
| [`xmupdates.py`](xmupdates.py) | Refreshes `items.ipc` / `items.dvr` from the vendor pagination endpoint. |
| [`download_firmwares.py`](download_firmwares.py) | Downloads catalog rows that aren't yet in `archive/index.json` and uploads them as Release assets. |

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

### `archive/index.json` schema

Keyed by catalog `id`. Each entry holds device metadata and a `revisions` list
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
        "archived_at": "2026-05-04T12:00:00Z"
      }
    ]
  }
}
```

## Grabbing a firmware

```sh
# Latest revision for catalog id 2281:
jq -r '.["2281"].revisions[-1].asset_url' archive/index.json | xargs curl -LO

# A specific historical version:
jq -r '.["2281"].revisions[] | select(.version == "000809Q4.1").asset_url' archive/index.json | xargs curl -LO
```

## Automation

A weekly GitHub Actions workflow ([`weekly-update.yml`](.github/workflows/weekly-update.yml))
runs every Monday and:

1. Refreshes `items.ipc` / `items.dvr` and commits any diff.
2. Walks the catalog for rows whose `(id, downloadUrl)` is not yet in
   `archive/index.json`, downloads the ZIP, uploads it to the rolling
   `firmware-archive` GitHub Release, and appends a revision to the index.

The download job is paced (`--max-per-run 25` by default) so the initial
backfill spreads across many cron ticks rather than hammering the vendor
server. Manual `workflow_dispatch` exposes the same knobs.

## Local use

```sh
pip install -r requirements.txt

# Refresh the catalogs only:
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

Unofficial mirror, no warranty. The vendor's TLS certificate on
`download.xm030.cn` is expired; the tooling intentionally disables TLS
verification only for that host. Mirrored firmware binaries remain the
property of XiongMai. Open an issue if you are a rights holder and want
something removed.

## License

The catalog data, index file, and Python tooling in this repository are
released under [CC0-1.0](LICENSE) (public domain dedication). Mirrored
vendor firmware binaries are not covered by that license — they remain
property of their original publisher.
