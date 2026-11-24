# slack-translator

A Slack app that gives X3M staff private translations of customer messages. Detection runs inside
the bot with Lingua. Translation goes to an OpenAI-style endpoint, and only after a member asks.

Status: milestone 0.1 in progress. The skeleton, shared `domain/` types and checks exist; the
Slack wiring does not yet. The behaviour contract is `docs/Specification.md`.

## Run the checks

```sh
make install   # uv sync --frozen
make check     # lint, format check, pyright, import-linter, FF-02 guard, tests
```

`make help` lists every target. `make image` builds the container image.

## Run the bot locally

1. Create the development Slack app from `slack/manifest.development.yaml` and install it in the
   test workspace. Invite the bot to every channel you put in the allowlist.
2. `cp .env.example .env` and fill in the secrets. `.env` is ignored by git.
3. `make run` starts the bot in Socket Mode.

All configuration comes from environment variables. `.env.example` lists every key; section 6 of
the Architecture contract explains each one. A rejected value stops the process with exit code 1
and a list of key names. Values are never printed.

## Layout

```text
src/slack_translator/
  domain/           pure rules, ports and value types; imports no other layer
  actions/          one module per use case; depends on domain only
  infrastructure/   adapters (translator, Lingua, Slack gateway, telemetry); depends on domain
  delivery/         settings, Bolt listeners, views, wiring; the only layer that imports slack_bolt
tests/unit/         mirrors src/slack_translator/, one test_<module>.py per module
tests/integration/  through create_app with the fakes
tests/live/         needs a real Slack app; not part of make test
tests/support/      fake Slack and fake translator servers
slack/              app manifests for development and staging
```

Imports flow `delivery` to `actions` and `infrastructure` to `domain`. `make check` enforces it
(FF-01), and also fails if code under `src/` can post publicly with the bot token (FF-02).
