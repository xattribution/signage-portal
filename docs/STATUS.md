# What the status indicators actually measure

## The old “Connected” label

In the supplied top-navigation build, the administrator page calls its own `GET /api/overview` endpoint. A successful refresh sets `#connection-state` to **Connected** and removes the `stale` class from the adjacent LED. Requests normally repeat every four seconds while the page is visible. Failed requests back off to a maximum 30-second retry interval; the request timeout is 15 seconds.

The catch handler changed the label to **Connection interrupted**, added `stale`, and displayed a warning. A browser `offline` event also changed the label. The CSS used green `#88d2af` for the healthy LED and **amber `#e7bc69`** for stale/offline. There was no red state for that LED.

This was real browser-to-portal API refresh logic, not UniFi connectivity, NAS health, ingest success, a broadcast connection, or confirmation that a display showed a frame. In the standalone preview, the API response is simulated in memory, so its “Connected” state did not imply any actual server connection. The original relevant locations were `web/app/static/admin.js` (`refresh()` and `init()`), `admin.html` (`refresh-now`), and `admin.css` (`.connection .led`).

## This revision

The always-visible label and LED are removed. The top bar has a neutral, keyboard-accessible **Refresh workspace** icon. Its tooltip records the last successful workspace refresh; it exposes an `aria-busy` state while a refresh runs. It does not claim overall system health.

Normal refresh succeeds quietly. When workspace updates fail, an explicit **Workspace updates unavailable** notice appears with the last successful refresh time, an explanation that visible data may be stale, and **Retry now**. Browser offline notifications show **Browser is offline**, qualified as the browser's network report. These warnings use a red accent and do not claim that displays stopped playing. Automatic retries remain enabled; success removes the warning and restores keyboard focus from its disappearing retry button to Refresh.

Successful HTTP status alone is insufficient: a 200 response containing an HTML error page or invalid overview structure is rejected. The last valid workspace remains visible. A 304 response can refresh the timestamp only when a previous snapshot exists; it cannot initialize the workspace.

No new server-health polling endpoint, controller connection or background “broadcast connection” was added.

## Keep these separate

| UI signal | Actual evidence | What it does not prove |
|---|---|---|
| Workspace refresh/warning | Administrator browser received, or failed to receive, the portal overview | Display playback, converter/NAS health or UniFi availability |
| Display Online/Offline | Age of a player request/heartbeat against the configured threshold | Television power, HDMI output or successful rendering |
| Stream preview | Server schedule's currently selected content | A physical display showed that content |
| Converter offline | Age of the worker heartbeat in the work directory | Why the worker is unavailable or whether storage will remain healthy |
| Converting / Ready / failed upload | The ingest/conversion/publication job state | Successful playback on every display |
| NAS transfer status | Durable import/export job record | Overall NAS health or independent replication/backup success |

There is no UniFi controller integration. It remains intentionally out of scope. Each display's permanent portal URL still allows stream reassignment without a UniFi API call.
