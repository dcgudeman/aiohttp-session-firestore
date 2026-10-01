# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/),
and this project adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.1.2] - 2026-10-01

### Fixed

- Generate session IDs with cryptographically secure randomness by default.
- Validate session keys before accessing Firestore; treat malformed cookies as
  missing sessions and reject invalid custom-generated keys.
- Persist per-session lifetimes, including `None`, and keep cookie expiration and
  loading consistent with the stored Firestore deadline.
- Prevent stale requests from recreating sessions deleted by logout.

### Added

- Regression tests for session keys and expiration, plus local Firestore emulator
  tests for persistence and logout through aiohttp middleware.

### Changed

- Update Ruff and mypy pre-commit hooks and their minimum supported development
  versions; verify compatibility with current dependencies on Python 3.14.

## [0.1.1] - 2026-02-20

### Changed

- Default `cookie_name` changed from `"AIOHTTP_SESSION"` to `"__session"` for
  Firebase Hosting compatibility (the only cookie name Firebase Hosting forwards).
- README: added documentation explaining the `__session` cookie name choice and
  the consequences of changing it on GCP.

## [0.1.0] - 2026-02-20

### Added

- `FirestoreStorage` — async Firestore session backend for `aiohttp-session`.
- Firestore auto-generated document IDs by default (customizable via `key_factory`).
- Firestore-aware default JSON encoder (handles `DatetimeWithNanoseconds`).
- Server-side expiration check on every read.
- Firestore TTL-compatible `expire` field (UTC `datetime`).
- Skips Firestore writes for new empty sessions (cost optimization).
- Full type annotations with `py.typed` marker (PEP 561).
- Unit test suite with mocked Firestore client.
- CI via GitHub Actions (lint, typecheck, test on Python 3.12 & 3.13).
- Apache 2.0 license.

[Unreleased]: https://github.com/dcgudeman/aiohttp-session-firestore/compare/v0.1.2...HEAD
[0.1.2]: https://github.com/dcgudeman/aiohttp-session-firestore/compare/v0.1.1...v0.1.2
[0.1.1]: https://github.com/dcgudeman/aiohttp-session-firestore/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/dcgudeman/aiohttp-session-firestore/releases/tag/v0.1.0
