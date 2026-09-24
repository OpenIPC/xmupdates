# archive/

Tracks which firmwares from the XM030 catalog (`items.ipc`, `items.dvr`) and
the JFTech portal (`items.portal`) have been mirrored.

- `index.json` — keyed by catalog `id` (`"2281"`) or by portal id with a `p`
  prefix (`"p1475"`); the two id spaces are unrelated. Each entry holds device
  metadata (`name`, plus `downloadMenuId` for catalog rows or `source`/`titleId`
  for portal rows) and a `revisions` list with every firmware
  version that has been mirrored for that catalog row. When XiongMai
  publishes a new firmware under the same catalog id, a new revision is
  appended; old binaries stay on the release so downgrades remain possible.
  Maintained automatically by `download_firmwares.py` running in CI.
- `000529B2/`, `000529E9/`, `000559A7/` — legacy folders from before the
  Releases-based archive existed. Kept for history; new firmwares are not
  added here. To migrate one of these into the index, upload the file as a
  release asset and add a corresponding entry to `index.json` by hand.
