# Changelog

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
