# piku-secrets

A [piku](https://github.com/piku/piku) plugin for write-only app secrets.

Secrets are like `config` values, but are not returned by `piku <app> config`,
`piku <app> config:get` and `piku <app> config:live`.

## Install

Copy or clone the `piku_secrets` folder into the plugins folder of each server
user that runs piku:

```bash
git clone https://github.com/EpicVoyage/piku_secrets.git ~/piku-secrets
mkdir -p ~/.piku/plugins
ln -s ~/piku-secrets/piku_secrets ~/.piku/plugins/piku_secrets
```

The folder must be named `piku_secrets`, not `secrets`: piku imports plugins by
folder name, and `secrets` would load Python's standard library module instead.

## Usage

From your app's folder, pipe the value in on stdin so it stays out of your
shell history and the SSH command line:

```bash
pass show myapp/db | piku secrets:set DB_PASSWORD
```

```bash
piku secrets:set DB_PASSWORD < db_password.txt
```

| Command | What it does |
|---|---|
| `secrets:set <app> KEY` | Reads the value from stdin, stores it, redeploys the app |
| `secrets:unset <app> KEY...` | Removes secrets, redeploys the app |
| `secrets:list <app>` | Prints secret names, never values |
| `run <app> <cmd>` | Core `run`, with secrets added to the environment |

When run on a server terminal (stdin is a TTY), `secrets:set` prompts for the
value with hidden input instead. The `piku` client does not allocate a TTY
for these commands, so from your workstation always pipe the value.

## How it works

- Secrets live in `~/.piku/envs/<app>/SECRETS` (mode `0600`) as a uwsgi ini
  file: one `env = KEY=value` line per secret.
- The plugin sets the app's `UWSGI_INCLUDE_FILE` to
  `~/.piku/envs/<app>/SECRETS.include`, which contains `ini=<SECRETS path>`.
  Every generated uwsgi config then has an `ini = ...` line that loads the
  secrets when the process starts. The values never appear in `ENV`,
  `LIVE_ENV` or `~/.piku/uwsgi-enabled/`.
- The plugin's `run` adds the secrets to the environment, then hands off to
  piku's own `run`. This works because piku checks plugin commands before its
  own.

## Limits

- **Values set with `config:set` win.** If the same key is set with both
  `config:set` and `secrets:set`, the `config:set` value is used in `run`.
  `secrets:set` warns when this happens.
- **`UWSGI_INCLUDE_FILE` is taken over.** If an app already sets it to its own
  file, `secrets:set` refuses to run until you unset it.
- **Values are single-line**, with no leading or trailing whitespace. uwsgi
  expands `$(...)` and `@(...)` inside config values, so values containing
  them are refused; `%` is escaped automatically.
- **Anyone who can use `run` for an app can read its secrets.** That is
  intended: `run` is the debugging tool.
- **The plugin repeats core `run`'s arguments** (`app`, then `cmd...`). If
  piku changes them, update `cmd_run` here; `test_run_passes_options_after_double_dash`
  is there to catch that.

## Tests

The tests drive a real `piku.py` against a temporary `PIKU_ROOT`, using only
the standard library and `click`:

```bash
PIKU_PY=/path/to/piku.py python3 -m unittest discover -s tests -v
```

`PIKU_PY` defaults to `../piku.py`, relative to this folder.

The tests check that generated uwsgi configs reference the `SECRETS` file, but
they do not start uwsgi. Before relying on the plugin, deploy a test app and
confirm the secret reaches the process without printing it, for example with
this Procfile line and `piku logs`:

```
worker: sh -c 'test -n "$DB_PASSWORD" && echo "secret present"; sleep 3600'
```
