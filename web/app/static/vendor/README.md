# Local HLS dependency

`web/install_hls.py` installs the light build of HLS.js 1.7.3 from the official
release ZIP after checking its SHA-256. The web Dockerfile runs this at build time.
No CDN is contacted at display runtime. Native HLS playback does not need this
script, but Chromium-based players normally need the local JavaScript fallback.

The release archive could not be downloaded in the authoring environment. This
source ZIP therefore contains the verified-checksum installer, not the compiled
third-party JavaScript. Building the web image requires network access to that
release, or installing from the exact verified archive in an offline build.
Do not replace a failed download with an unpinned CDN dependency.

Upstream: https://github.com/video-dev/hls.js/releases/tag/v1.7.3
License: Apache-2.0. Preserve the upstream build notices. The installer also copies
any standalone license included in the release and records file provenance.
