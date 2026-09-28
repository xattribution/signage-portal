# Development notes

Keep the project's center clear: persistent playback destinations, content ingest, reusable streams and simple operator workflows. A successful request is not proof of successful playback. Do not add unmeasured health badges or partial hardware-management controls.

## Development setup

Use Python 3.12, Node.js 22+ and a clean virtual environment. Install `requirements-dev.txt`; the graphics tests need Cairo (`libcairo2` on the supplied Linux image). Full conversion checks additionally require ffmpeg, LibreOffice Impress and poppler. Run `scripts/check.sh`. The tests isolate their temporary storage; never point them at live media or a production database.

For the browser-DOM checks, install the pinned Playwright package and a Chromium browser. Generate a preview with `scripts/build-workspace-demo.py`, then run `scripts/navigation-browser-check.py --preview <file> --output <evidence-directory>`. The current harness expects `/usr/bin/chromium`; its no-sandbox flag is restricted to offline test execution, not a deployment recommendation. The harness loads a local document with an in-memory API adapter; it does not validate browser networking, CSP, TLS or hardware playback.

## Boundaries to preserve

All write APIs need authentication and the write guard. Do not expose administrator paths on the playback hostname. Do not put raw SVG, unsafe media decoders, NAS mount privileges or arbitrary server-side URL fetching in the credential-bearing web service. Retain no-follow file access, bounded job contracts, read-only worker input and immutable rendition URLs.

The web process and conversion worker must retain their separate trust boundaries. Live-source relay work belongs in a separate operator-controlled service. Do not add UniFi credentials/control without a verified device/API contract and a specific product decision. Keep runtime secrets/media out of Git.

## Change checklist

Include regression coverage and update the relevant operator guide. Record exactly which dependency versions and device/environment were tested. Mark simulated browser data as simulated. Never change stable display/channel identifiers in a cosmetic refactor. Test keyboard/mobile/reduced-motion behavior for UI changes. Treat real image builds, NAS remount/reboot tests and physical playback as separate release gates.

No project-wide distribution license is selected in this package; retain upstream notices and consult the owner before changing licensing. Report vulnerabilities using [SECURITY.md](SECURITY.md), not a public issue containing credentials or deployable exploit media.
