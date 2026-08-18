# Issue screenshots

Captured symptoms kept for reference, so a bug that reappears is recognisable
from what the user actually sees rather than from a log line.

Both screenshots below are **resolved**. They are the "before" state of the
work in `docs/01-orientation/oracle-always-free.md`.

| Screenshot | Symptom | Cause | Resolved by |
|---|---|---|---|
| `no-backend-connected.png` | *"No backend connected. This is a UI preview — run 9XAIPal locally, or set `VITE_API_BASE_URL` to a reachable backend"* | The hosted SPA had no `VITE_API_BASE_URL`, so every call went to the static host and 404'd. It is a **build-time** value, so setting it requires a redeploy. | Backend deployed to its own always-on host; `VITE_API_BASE_URL` set and the frontend rebuilt. |
| `upload-failed-404.png` | Upload reaches *"Choosing extractor…"*, then **Extracting structure** and **Chunking** both fail with `Upload failed: 404` | Same root cause — the UI was talking to a host with no `/api`. | As above. |

Note the misleading detail in both: the card says **"runs locally"** and names
MinerU, because that is the default extractor in the UI copy. The deployed
stack uses `EXTRACTOR_PROVIDER=vlm` and never invokes MinerU, so that label
does not reflect what the server actually does.

Screenshots are stripped of EXIF/XMP metadata before being committed.
