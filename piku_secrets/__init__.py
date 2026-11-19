"""Write-only app secrets for piku.

Secrets live in ENV_ROOT/<app>/SECRETS (mode 0600) as a uwsgi ini file, and
reach the app through piku's UWSGI_INCLUDE_FILE setting. They never appear in
ENV, LIVE_ENV or the generated uwsgi vassal configs, so `config`,
`config:get` and `config:live` never show them. `run` (and therefore `shell`
and `tmux`) loads them for debugging.
"""

import __main__ as piku

from os import O_CREAT, O_EXCL, O_WRONLY, environ, fdopen, getpid, open as os_open, replace
from os.path import exists, join
from re import match
from sys import exit, stdin

from click import argument, echo, group, pass_context, prompt

SECRETS_FILE = 'SECRETS'
INCLUDE_FILE = 'SECRETS.include'
INCLUDE_SETTING = 'UWSGI_INCLUDE_FILE'
KEY_PATTERN = r'^[A-Za-z_][A-Za-z0-9_]*$'
# uwsgi expands these inside config values; % is escaped as %%, the others are refused
UNSUPPORTED = ('$(', '@(')


def secrets_path(app):
    return join(piku.ENV_ROOT, app, SECRETS_FILE)


def include_path(app):
    return join(piku.ENV_ROOT, app, INCLUDE_FILE)


def write_private(filename, text):
    """Atomically replace filename with text, readable only by the owner."""
    tmp = '{}.{}.tmp'.format(filename, getpid())
    with fdopen(os_open(tmp, O_CREAT | O_EXCL | O_WRONLY, 0o600), 'w') as h:
        h.write(text)
    replace(tmp, filename)


def load_secrets(app):
    """Return {KEY: value} from the app's SECRETS file."""
    found = {}
    filename = secrets_path(app)
    if not exists(filename):
        return found
    with open(filename) as h:
        for line in h:
            name, sep, rest = line.partition('=')
            if not sep or name.strip() != 'env':
                continue
            key, sep, value = rest.strip().partition('=')
            if sep:
                found[key] = value.replace('%%', '%')
    return found


def save_secrets(app, found):
    lines = ['[uwsgi]']
    for key in sorted(found):
        lines.append('env = {}={}'.format(key, found[key].replace('%', '%%')))
    write_private(secrets_path(app), '\n'.join(lines) + '\n')


def ensure_include(app):
    """Point the app's UWSGI_INCLUDE_FILE at the SECRETS file. Returns False on conflict."""
    config_file = join(piku.ENV_ROOT, app, 'ENV')
    env = piku.parse_settings(config_file, {})
    wanted = include_path(app)
    current = env.get(INCLUDE_SETTING)
    if current and current != wanted:
        echo("Error: {} is already set to '{}'. Unset it before using secrets.".format(INCLUDE_SETTING, current), err=True)
        return False
    write_private(wanted, 'ini={}\n'.format(secrets_path(app)))
    if current != wanted:
        env[INCLUDE_SETTING] = wanted
        piku.write_config(config_file, env)
    return True


def read_value():
    if stdin.isatty():
        return prompt('Value', hide_input=True)
    return stdin.readline().rstrip('\r\n')


@group()
def secrets():
    pass


@secrets.command("secrets:set")
@argument('app')
@argument('key')
def cmd_secrets_set(app, key):
    """e.g.: pass show db | piku secrets:set <app> DB_PASSWORD"""
    app = piku.exit_if_invalid(app)
    if not match(KEY_PATTERN, key):
        echo("Error: invalid key '{}'.".format(key), err=True)
        exit(1)
    value = read_value()
    if not value:
        echo("Error: no value given on stdin.", err=True)
        exit(1)
    if value != value.strip():
        echo("Error: values with leading or trailing whitespace are not supported.", err=True)
        exit(1)
    if any(token in value for token in UNSUPPORTED):
        echo("Error: values containing {} are not supported by uwsgi.".format(' or '.join(UNSUPPORTED)), err=True)
        exit(1)
    if not ensure_include(app):
        exit(1)
    found = load_secrets(app)
    found[key] = value
    save_secrets(app, found)
    if key in piku.parse_settings(join(piku.ENV_ROOT, app, 'ENV'), {}):
        echo("Warning: {} is also set with config:set; that value takes precedence.".format(key))
    echo("Setting {}=******** (secret) for '{}'".format(key, app))
    piku.do_deploy(app)


@secrets.command("secrets:unset")
@argument('app')
@argument('keys', nargs=-1)
def cmd_secrets_unset(app, keys):
    """e.g.: piku secrets:unset <app> DB_PASSWORD"""
    app = piku.exit_if_invalid(app)
    found = load_secrets(app)
    removed = [k for k in keys if k in found]
    for key in removed:
        del found[key]
        echo("Unsetting {} (secret) for '{}'".format(key, app))
    if removed:
        save_secrets(app, found)
        piku.do_deploy(app)


@secrets.command("secrets:list")
@argument('app')
def cmd_secrets_list(app):
    """e.g.: piku secrets:list <app> (names only)"""
    app = piku.exit_if_invalid(app)
    for key in sorted(load_secrets(app)):
        echo(key)


@secrets.command("run")
@argument('app')
@argument('cmd', nargs=-1)
@pass_context
def cmd_run(ctx, app, cmd):
    """e.g.: piku run <app> ls -- -al (includes secrets)"""
    app = piku.exit_if_invalid(app)
    environ.update(load_secrets(app))
    ctx.forward(piku.cmd_run)


def cli_commands():
    return secrets
