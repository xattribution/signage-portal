# Third-party components and distribution

No project-wide open-source license has been assigned by this repository-preparation pass. Retain the supplied notices and have the owner select a project license before public distribution. This document does not replace upstream license texts or a complete software bill of materials.

The source declares Python dependencies in `web/requirements.txt`, `worker/requirements.txt` and `requirements-dev.txt`. The Dockerfiles install additional Debian packages, including the conversion tools and fonts. Their upstream licenses and notices remain applicable; a release bill of materials must cover the actual resolved packages and image contents, not only direct pins.

`web/install_hls.py` installs a checksum-pinned HLS.js release at build time and preserves the upstream notice and provenance. The source package does not contain the compiled HLS dependency; successful acquisition/build remains required. `deploy/relay/` is an optional MediaMTX configuration example, not a bundled relay binary or verified integration.

Browser screenshots and preview fixtures use sample content. They are test/documentation evidence, not office content. No font files are added to this source package; required fonts are installed inside the worker image from operating-system packages.
