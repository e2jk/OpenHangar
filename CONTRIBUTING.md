# Contributing

## Bugs and feature requests

Open an issue. For security vulnerabilities, follow [SECURITY.md](SECURITY.md).

## Pull requests

1. Fork the repo and create a branch from `main`.
2. Run the test suite: see [docs/development.md](docs/development.md).
3. Ensure `ruff`, `mypy`, and all tests pass before pushing (pre-push hook enforces this).
4. Open a PR against `main` with a clear description of what and why.

## Translations

All UI strings must be wrapped in `_()` and translated. See [docs/dev-i18n.md](docs/dev-i18n.md).

## Recognition

Every contributor is credited in the README's **Contributors** section,
following the [all-contributors](https://allcontributors.org) specification —
for any kind of contribution: code, translations, bug reports, ideas, reviews,
documentation. Only people are listed, not bots or AI tools. If you've
contributed and aren't listed yet (or something's missing next to your name),
say so in an issue or in your PR. The list lives in `.all-contributorsrc`; the
README table is generated from it (`npx all-contributors-cli generate`).

CI checks that the list is current (`scripts/check_contributors.py`): the
README table must match `.all-contributorsrc`, and every person who authored
commits or merged pull requests — including the author of the pull request
being checked — must be in it. On a pull request that's only a reminder
(a warning, never a failure): you're welcome to add yourself, otherwise a
maintainer will do it for you. Bots are never expected.
