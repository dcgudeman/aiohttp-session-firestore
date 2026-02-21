# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/),
and this project adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.1.0] - TBD

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

[Unreleased]: https://github.com/dcgudeman/aiohttp-session-firestore/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/dcgudeman/aiohttp-session-firestore/releases/tag/v0.1.0
