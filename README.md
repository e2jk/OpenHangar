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
<!-- ALL-CONTRIBUTORS-BADGE:START - Do not remove or modify this section -->
[![All Contributors](https://img.shields.io/badge/all_contributors-2-orange.svg?style=flat-square)](#contributors-)
<!-- ALL-CONTRIBUTORS-BADGE:END -->

Self-hosted, open-source platform for pilots, owner-operators, and clubs to manage aircraft maintenance, log flights, track costs and documents, and monitor pilot currency — all in one place.

**[→ Live demo](https://openhangar-demo.devolenvol.eu/)**

## Features

- **Fleet management** — model aircraft (airframe, engines, props, avionics); lightweight placeholders for quick onboarding; archive without losing history
- **Maintenance tracking** — calendar and hours-based triggers; engine/propeller TBO & life-limited components; green/yellow/red dashboard status
- **Flight logging** — unified aircraft + pilot logbook entry; counter photo proofs; GPS-file autofill; fuel & oil tracking
- **Pilot logbook** — EASA FCL.050 column mapping; passenger/night currency monitoring; FSTD sessions; CSV/Excel import; crew confirmation between pilots
- **GPS tracks** — GPX/KML/Garmin import; per-flight maps; track image & animation exports
- **Airworthiness** — AD/SIB/ARC/STC tracking with automatic EASA sync
- **Reservations** — per-aircraft booking calendar with approval workflow and cost estimation
- **Snags, mass & balance, share links** — grounding-aware defect list; CG envelope checks; public QR status pages
- **Cost tracking** — expenses with receipts; recurring fixed costs; operating-cost (wet-rate) dashboard
- **Document management** — inline viewer; sensitive-document access controls; Syncthing-friendly storage
- **Notifications & PWA** — per-user email alerts; installable app with offline flight logging; mobile-first UI with a bottom tab bar, thumb-reachable actions, and touch-friendly forms
- **Security** — role-based access with per-aircraft permissions; TOTP 2FA; encrypted AES-256-GCM backups with built-in scheduling & retention
- **Multi-language** — English, French, Dutch; translations managed via [Weblate](https://hosted.weblate.org/engage/openhangar/) *(setup in progress)*

## Getting started

### 🤖 Install with your AI assistant (easiest)

Using ChatGPT, Claude, Codex, Gemini or another AI assistant? Copy this into it:

```text
Install OpenHangar for me by following
https://raw.githubusercontent.com/e2jk/OpenHangar/main/docs/agent-install.md
```

It asks you a few questions (home network only, or reachable from the internet with HTTPS? quick setup, or go through the settings?), downloads the images while you answer, then installs and checks that everything works. Assistants that can run commands do it for you; chat-only ones walk you through one command at a time. Your passwords and keys are generated on your machine and never go through the chat.

### Install by hand

OpenHangar is deployed as a Docker Compose stack:

- **[Raspberry Pi / home network guide](docs/raspberry-pi.md)**: step by step, no prior Docker knowledge needed. Works on any 64-bit Linux machine.
- **[Self-hosting guide](docs/self-hosting.md)**: internet-facing setup with HTTPS (Traefik + Let's Encrypt), configuration reference, and upgrade procedure.

## Documentation

| Document | Audience | Description |
|---|---|---|
| [docs/user-guide.md](docs/user-guide.md) | End users | Feature overview, key flows, glossary, contributing translations |
| [docs/logbook_airplane.md](docs/logbook_airplane.md) | End users | Aircraft logbook fields, EASA/FAA columns, counter types |
| [docs/logbook_pilot.md](docs/logbook_pilot.md) | End users | Pilot personal logbook, EASA FCL.050 mapping, currency rules |
| [docs/agent-install.md](docs/agent-install.md) | AI assistants | Guided install for an AI assistant to follow on the user's behalf |
| [docs/raspberry-pi.md](docs/raspberry-pi.md) | Administrators | Step-by-step home-network install (Raspberry Pi or any Linux box) |
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

## Contributors ✨

Thanks goes to these wonderful people ([emoji key](https://allcontributors.org/docs/en/emoji-key)):

<!-- ALL-CONTRIBUTORS-LIST:START - Do not remove or modify this section -->
<!-- prettier-ignore-start -->
<!-- markdownlint-disable -->
<table>
  <tbody>
    <tr>
      <td align="center" valign="top" width="14.28%"><a href="https://github.com/e2jk"><img src="https://avatars.githubusercontent.com/u/229379?v=4?s=100" width="100px;" alt="Emilien Klein"/><br /><sub><b>Emilien Klein</b></sub></a><br /><a href="https://github.com/e2jk/OpenHangar/commits?author=e2jk" title="Code">💻</a> <a href="https://github.com/e2jk/OpenHangar/commits?author=e2jk" title="Documentation">📖</a> <a href="https://github.com/e2jk/OpenHangar/commits?author=e2jk" title="Tests">⚠️</a> <a href="#translation-e2jk" title="Translation">🌍</a> <a href="#infra-e2jk" title="Infrastructure (Hosting, Build-Tools, etc)">🚇</a> <a href="#maintenance-e2jk" title="Maintenance">🚧</a> <a href="#security-e2jk" title="Security">🛡️</a> <a href="#projectManagement-e2jk" title="Project Management">📆</a> <a href="#ideas-e2jk" title="Ideas, Planning, & Feedback">🤔</a></td>
      <td align="center" valign="top" width="14.28%"><a href="https://kristofdv.be"><img src="https://avatars.githubusercontent.com/u/35935156?v=4?s=100" width="100px;" alt="Kristof De Vos"/><br /><sub><b>Kristof De Vos</b></sub></a><br /><a href="https://github.com/e2jk/OpenHangar/commits?author=kristofdv" title="Code">💻</a> <a href="https://github.com/e2jk/OpenHangar/commits?author=kristofdv" title="Documentation">📖</a> <a href="https://github.com/e2jk/OpenHangar/commits?author=kristofdv" title="Tests">⚠️</a> <a href="#translation-kristofdv" title="Translation">🌍</a> <a href="#a11y-kristofdv" title="Accessibility">️️️️♿️</a></td>
    </tr>
  </tbody>
</table>

<!-- markdownlint-restore -->
<!-- prettier-ignore-end -->

<!-- ALL-CONTRIBUTORS-LIST:END -->

This project follows the [all-contributors](https://github.com/all-contributors/all-contributors) specification: every kind of contribution counts, not only code — translations, bug reports, ideas, reviews, documentation. Only people are listed here; the bots and AI tools used along the way (dependency updates, CI, coding assistants) are not.

## License

[MIT](LICENSE). Some runtime dependencies carry their own (copyleft or
content-specific) licenses — see [THIRD_PARTY_LICENSES.md](THIRD_PARTY_LICENSES.md)
for what's used and why it doesn't affect OpenHangar's own license.
