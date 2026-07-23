# OpenHangar — Aircraft ops manager and logbook for owner-operators and clubs, self-hosted and open-source

[![CI](https://github.com/e2jk/OpenHangar/actions/workflows/ci.yml/badge.svg)](https://github.com/e2jk/OpenHangar/actions/workflows/ci.yml)
[![Fuzzing](https://github.com/e2jk/OpenHangar/actions/workflows/fuzzing.yml/badge.svg)](https://github.com/e2jk/OpenHangar/actions/workflows/fuzzing.yml)
[![OpenSSF Scorecard](https://api.securityscorecards.dev/projects/github.com/e2jk/OpenHangar/badge)](https://securityscorecards.dev/viewer/?uri=github.com/e2jk/OpenHangar)
[![OpenSSF Best Practices](https://www.bestpractices.dev/projects/12831/badge)](https://www.bestpractices.dev/projects/12831)
[![Coverage](https://e2jk.github.io/OpenHangar/coverage/badge.svg)](https://e2jk.github.io/OpenHangar/coverage/)
[![Fuzz harnesses](https://e2jk.github.io/OpenHangar/fuzz-coverage/badge.svg)](https://e2jk.github.io/OpenHangar/fuzz-coverage/)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue)](https://github.com/e2jk/OpenHangar/blob/main/LICENSE)
[![Docker](https://img.shields.io/badge/docker-ghcr.io%2Fe2jk%2Fopenhangar-blue?logo=docker)](https://github.com/e2jk/OpenHangar/pkgs/container/openhangar)
[![Last commit](https://img.shields.io/github/last-commit/e2jk/OpenHangar)](https://github.com/e2jk/OpenHangar/commits/main)
[![Translations](https://img.shields.io/badge/translations-Weblate-brightgreen?logo=weblate)](https://hosted.weblate.org/engage/openhangar/)
[![Try the demo](https://img.shields.io/badge/try%20the%20demo-live-brightgreen)](https://openhangar-demo.devolenvol.eu/)

Self-hosted, open-source platform for pilots, owner-operators, and clubs to manage aircraft maintenance, log flights, track costs and documents, and monitor pilot currency — all in one place.

**[→ Live demo](https://openhangar-demo.devolenvol.eu/)**

## Features

- **Fleet management** — model aircraft (airframe, engines, props, avionics); lightweight placeholders for quick onboarding; archive without losing history
- **Maintenance tracking** — calendar and hours-based triggers; engine/propeller TBO & life-limited components; green/yellow/red dashboard status
- **Flight logging** — unified aircraft + pilot logbook entry; counter photo proofs; GPS-file autofill; fuel & oil tracking
- **Pilot logbook** — EASA FCL.050 column mapping; passenger/night currency monitoring; FSTD sessions; CSV/Excel import
- **GPS tracks** — GPX/KML/Garmin import; per-flight maps; track image & animation exports
- **Airworthiness** — AD/SIB/ARC/STC tracking with automatic EASA sync
- **Reservations** — per-aircraft booking calendar with approval workflow and cost estimation
- **Snags, mass & balance, share links** — grounding-aware defect list; CG envelope checks; public QR status pages
- **Cost tracking** — expenses with receipts; recurring fixed costs; operating-cost (wet-rate) dashboard
- **Document management** — inline viewer; sensitive-document access controls; Syncthing-friendly storage
- **Notifications & PWA** — per-user email alerts; installable app with offline flight logging
- **Security** — role-based access with per-aircraft permissions; TOTP 2FA; encrypted AES-256-GCM backups with built-in scheduling & retention
- **Multi-language** — English, French, Dutch; translations managed via [Weblate](https://hosted.weblate.org/engage/openhangar/) *(setup in progress)*

## Getting started

OpenHangar is deployed as a Docker container. See the **[self-hosting guide](docs/self-hosting.md)** for a Docker Compose example, configuration reference, and upgrade procedure.

## Documentation

| Document | Audience | Description |
|---|---|---|
| [docs/user-guide.md](docs/user-guide.md) | End users | Feature overview, key flows, glossary, contributing translations |
| [docs/logbook_airplane.md](docs/logbook_airplane.md) | End users | Aircraft logbook fields, EASA/FAA columns, counter types |
| [docs/logbook_pilot.md](docs/logbook_pilot.md) | End users | Pilot personal logbook, EASA FCL.050 mapping, currency rules |
| [docs/self-hosting.md](docs/self-hosting.md) | Administrators | Docker deployment, configuration, upgrade procedure |
| [docs/configuration.md](docs/configuration.md) | Administrators | All environment variables — core, email, demo mode |
| [docs/backup_restore.md](docs/backup_restore.md) | Administrators | Backup scheduling, restore procedure, file format |
| [docs/development.md](docs/development.md) | Contributors | Local setup, running tests, architecture, domain model |
| [docs/implementation_plan.md](docs/implementation_plan.md) | Contributors | Phased delivery roadmap with completion status |
| [docs/demo-deployment.md](docs/demo-deployment.md) | Maintainers | Public demo instance design and operation |
| [docs/dev-i18n.md](docs/dev-i18n.md) | Maintainers | i18n workflow — Weblate setup, adding languages, pybabel guide |

## Contributing

- **Code**: see [docs/development.md](docs/development.md) for local setup, architecture, and coding standards.
- **Translations**: visit [hosted.weblate.org](https://hosted.weblate.org/engage/openhangar/) — no technical knowledge required.
- **Roadmap feedback**: use GitHub issue reactions or the voting label to signal interest.

Roadmap decisions are guided by maintainers and community input. Feature requests and discussion via GitHub Issues and PRs.

## License

[MIT](LICENSE)
