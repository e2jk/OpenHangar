# OpenHangar — Open-source aircraft ops manager for owner-operators and clubs

[![CI](https://github.com/e2jk/OpenHangar/actions/workflows/ci.yml/badge.svg)](https://github.com/e2jk/OpenHangar/actions/workflows/ci.yml)
[![Coverage](https://e2jk.github.io/OpenHangar/coverage/badge.svg)](https://e2jk.github.io/OpenHangar/coverage/)
[![License](https://img.shields.io/github/license/e2jk/OpenHangar)](https://github.com/e2jk/OpenHangar/blob/main/LICENSE)
[![Docker](https://img.shields.io/badge/docker-ghcr.io%2Fe2jk%2Fopenhangar-blue?logo=docker)](https://github.com/e2jk/OpenHangar/pkgs/container/openhangar)
[![Last commit](https://img.shields.io/github/last-commit/e2jk/OpenHangar)](https://github.com/e2jk/OpenHangar/commits/main)
[![Translations](https://img.shields.io/badge/translations-Weblate-brightgreen?logo=weblate)](https://hosted.weblate.org/)
[![Try the demo](https://img.shields.io/badge/try%20the%20demo-live-brightgreen)](https://openhangar-demo.devolenvol.eu/)

Self-hosted, open-source platform for pilots, owner-operators, and clubs to manage aircraft maintenance, log flights, track costs and documents, and monitor pilot currency — all in one place.

**[→ Live demo](https://openhangar-demo.devolenvol.eu/)**

## Features

- **Fleet management** — model aircraft (airframe, engines, props, avionics); lightweight placeholders for quick onboarding
- **Maintenance tracking** — calendar, hours, and cycles-based triggers; green/yellow/red dashboard status
- **Flight logging** — hobbs/tach entries with optional photo proofs; automatic logbook updates
- **Pilot logbook** — EASA FCL.050 column mapping; passenger/night currency monitoring
- **Cost tracking** — per-flight and periodic expenses; L/gal unit conversion; cost-per-hour reporting
- **Document management** — attach files to aircraft, components, and entries; sensitive-document access controls
- **Encrypted backups** — AES-256-GCM daily backups with SHA-256 verification
- **Multi-language** — English, French, Dutch; translations managed via [Weblate](https://hosted.weblate.org/) *(setup in progress)*

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
- **Translations**: visit [hosted.weblate.org](https://hosted.weblate.org/) — no technical knowledge required.
- **Roadmap feedback**: use GitHub issue reactions or the voting label to signal interest.

Roadmap decisions are guided by maintainers and community input. Feature requests and discussion via GitHub Issues and PRs.

## License

[MIT](LICENSE)
