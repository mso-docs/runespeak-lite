Runespeak Lite is an early Linux alpha: an offline speaking tome powered by Phonon.

Included: webpage/Wikipedia and document reading, local OCR, streaming narration, selectable English/character voices, read-along highlighting, navigation and WAV export.

Downloads:
- The amd64 `.deb` is the desktop app installer for Ubuntu 24.04+ / Debian 12+. Install with `sudo apt install ./runespeak-lite_0.1.0~alpha1_amd64.deb`, then open Runespeak Lite from Applications. First launch shows a setup window while downloading locked dependencies into your user account. No checkout or uv is required.
- The Linux source archive is an alternative alpha download. Extract it, install the Linux prerequisites from README, then run uv sync and the desktop/integration installers. Keep the extracted folder in place.
- The wheel and Python source distribution contain the app. Desktop installers and Firefox companion source are in the Linux source bundle.
- The unsigned Firefox ZIP is a development extension. Load temporarily through Firefox's about:debugging page; it is not Mozilla-signed.
- SHA256SUMS contains checksums for all attached artifacts.

The `.deb` installs system prerequisites through apt; its first launch and first model use require internet. It is not a fully offline bundled executable. Source installations require Python/uv and the documented Linux dependencies. Windows executables and AppImage are deferred. Model/voice files download on first use and are not bundled. No sample narrations or personal documents are included.

Known limits: passage-level rather than word-timestamp highlighting; heuristic reference filtering; imperfect OCR/complex PDF reading order; compositor-dependent file drops; generated-audio limits on seeking. See README for details.
