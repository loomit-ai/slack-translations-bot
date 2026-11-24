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

## Try translations outside Slack

Two terminal commands try how a model translates, with no Slack and no Slack tokens. They call the
bot's own translator with its own prompt and request, so a model that does well here behaves the
same in the bot. Only the model changes, and it is chosen by an env file you name on the command
line. With a model on your own computer no text leaves the machine, so real messages can be tried.

**Set up.** Put everything private in `lab/`; git ignores that folder and the tools refuse to write
results anywhere git would track.

```sh
mkdir -p lab
```

Create `lab/local.env` (any name works, so several profiles can sit side by side):

```text
# A model on this computer, here through Ollama (start it first, with the model loaded).
# LM Studio: TRANSLATOR_BASE_URL=http://localhost:1234/v1 and the model name it shows.
TRANSLATOR_BASE_URL=http://localhost:11434/v1
TRANSLATOR_MODEL=qwen2.5:7b
TRANSLATOR_API_KEY=local
# Optional. Seconds to wait for one answer, 1 to 60 (the bot uses 20). The first call can be slow.
TRANSLATOR_TIMEOUT=60
# Optional. Extra fields for the request, as one JSON object.
# TRANSLATOR_EXTRA_BODY={"reasoning_effort": "none"}
```

For a hosted model such as OpenRouter write a second file with `https://openrouter.ai/api/v1`, the
model name and your key. **Remove names and identifiers from a sample before sending it to a hosted
model** (decision 5); a local model needs no such care.

**One message.** Type it, pass `TEXT=`, or pipe it in. `TARGET` is the language to translate into
(default `en`), so English to Korean works too.

```sh
make translate-one ENV=lab/local.env TEXT="파란 상자는 어디에 있나요?"
echo "Where is the blue box?" | make translate-one ENV=lab/local.env TARGET=ko
```

It prints the model, the endpoint host, the target, the time, the language Lingua detects with its
lead and what the bot would do with such a message (this helps to tune `CLASSIFIER_MIN_CONFIDENCE`,
0.7 by default; `--no-detect` skips it), whether every Slack mention, link, emoji code and code
block of the source survived unchanged, and then the translation.

**A file of messages.** One message per line in a `.txt`, or a `.csv` with a `message` column (or
`text` or `source`; otherwise the first column).

```sh
make translate-file ENV=lab/local.env FILE=lab/sample.txt TARGET=en
```

Each message is translated once; one that fails becomes a row, not a stop. The results go to
`lab/results/` as a CSV that opens in a spreadsheet with the Korean intact: source, translation,
target, seconds, whether it worked, the error kind, the Slack token check, Lingua's language and
lead, the model and endpoint, token counts, and empty `rating` (`usable`, `needs edits` or `wrong`)
and `notes` columns for you. Progress and the summary never print message text.

If something looks wrong: `unavailable (HTTP 500)` from a local server usually means the server
could not load the model, so read its own log (Ollama says when the model does not fit in memory);
`timeout` means raise `TRANSLATOR_TIMEOUT`; `invalid_response` means an empty or cut-off answer, so
try a model that does not print its reasoning. `make fake-translator` runs a fake endpoint to try
the tools without any model (`TRANSLATOR_BASE_URL=http://127.0.0.1:8089`).

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
scripts/            checks and the translation tools (`translate_one.py`, `translate_file.py`)
lab/                private samples, env files and results; ignored by git, not in the repository
```

Imports flow `delivery` to `actions` and `infrastructure` to `domain`. `make check` enforces it
(FF-01), and also fails if code under `src/` can post publicly with the bot token (FF-02).
