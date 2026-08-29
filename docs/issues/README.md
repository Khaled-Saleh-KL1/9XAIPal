# Issue screenshots

Captured symptoms kept for reference, so a bug that reappears is recognisable
from what the user actually sees rather than from a log line.

Both screenshots below are **resolved**. They are the "before" state of moving
the backend to its own always-on host — see `backend/DEPLOYMENT-PRODUCTION.md`
for the deployment as it exists today.

| Screenshot | Symptom | Cause | Resolved by |
|---|---|---|---|
| `no-backend-connected.png` | *"No backend connected. This is a UI preview — run 9XAIPal locally, or set `VITE_API_BASE_URL` to a reachable backend"* | The hosted SPA had no `VITE_API_BASE_URL`, so every call went to the static host and 404'd. It is a **build-time** value, so setting it requires a redeploy. | Backend deployed to its own always-on host; `VITE_API_BASE_URL` set and the frontend rebuilt. |
| `upload-failed-404.png` | Upload reaches *"Choosing extractor…"*, then **Extracting structure** and **Chunking** both fail with `Upload failed: 404` | Same root cause — the UI was talking to a host with no `/api`. | As above. |

Note the misleading detail in both: the card says **"runs locally"** and names
MinerU, because that is the default extractor in the UI copy. At the time
these screenshots were taken, the deployed stack used cloud-VLM extraction and
never invoked MinerU, so that label didn't reflect what the server actually
did — the current deployment runs local MinerU extraction instead (see
`backend/DEPLOYMENT-PRODUCTION.md` §2), so this specific mismatch no longer
applies, though the underlying UI copy is still worth keeping honest as the
deployment continues to change.

Screenshots are stripped of EXIF/XMP metadata before being committed.
