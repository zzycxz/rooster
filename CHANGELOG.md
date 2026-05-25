# Changelog

## [0.2.2] - 2026-05-25

### Added
- `Rooster.app` macOS launcher: single-line AppleScript, auto-activates venv, no extra terminal text
- Dashboard pre-built `src/ui/dist/` committed to repo (no Node.js needed after clone)

### Fixed
- macOS Python SSL certificate verification: auto-set `SSL_CERT_FILE` to certifi bundle in config init
- start.bat: auto-activate `.venv\Scripts\activate.bat` before running guardian

### Changed
- Remove `start.command` (logic internalized into `Rooster.app`)
- `.gitignore`: exclude `src/ui/dist/` from global `dist/` ignore rule
- `.env.local.example`: add proxy and SSL_CERT_FILE template
- README: macOS launch instructions with Sequoia Gatekeeper note

## [0.2.1] - 2026-05-24

### Fixed
- pin starlette>=1.0.1 to avoid PYSEC-2026-161 CVE
- test: use tmp_path instead of hardcoded dummy_save.png
- ruff lint / format issues across source

### Changed
- README: switch to English, Chinese version as README.cn.md
- bilingual comments (Chinese / English) across all source files
- rewrite CONTRIBUTING.md
- clean up .env.local comments, add ZHIPU_GLM_KEY
- utils compat shim: lazy-load heavy dependencies

## [0.2.0] - 2026-05-22

- initial open-source release
