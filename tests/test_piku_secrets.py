"""Tests for the piku secrets plugin.

They drive a real piku.py against a throwaway PIKU_ROOT. Set PIKU_PY to the
piku.py under test (defaults to the copy next to this repo's parent folder).

    PIKU_PY=/path/to/piku.py python3 -m unittest discover -s tests
"""

import stat
import sys
import unittest
from os import environ, makedirs, stat as os_stat, symlink
from os.path import abspath, dirname, exists, join
from subprocess import run
from tempfile import TemporaryDirectory

HERE = dirname(abspath(__file__))
PLUGIN_SRC = join(dirname(HERE), 'piku_secrets')
PIKU_PY = environ.get('PIKU_PY', join(dirname(dirname(HERE)), 'piku.py'))
APP = 'myapp'
SECRET = 's3cr3t-value'


class PikuSecretsTest(unittest.TestCase):

    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.root = join(self.tmp.name, '.piku')
        self.app_dir = join(self.root, 'apps', APP)
        self.env_dir = join(self.root, 'envs', APP)
        for d in [self.app_dir, self.env_dir, join(self.root, 'plugins')]:
            makedirs(d)
        symlink(PLUGIN_SRC, join(self.root, 'plugins', 'piku_secrets'))
        with open(join(self.app_dir, '.hidden-marker'), 'w') as h:
            h.write('x')

    def tearDown(self):
        self.tmp.cleanup()

    def piku(self, *args, value=None):
        env = dict(environ, PIKU_ROOT=self.root, HOME=self.tmp.name)
        return run([sys.executable, PIKU_PY] + list(args), input=value, env=env,
                   cwd=self.tmp.name, capture_output=True, text=True)

    def read(self, name):
        with open(join(self.env_dir, name)) as h:
            return h.read()

    def write(self, name, text):
        with open(join(self.env_dir, name), 'w') as h:
            h.write(text)

    def set_secret(self, key='API_KEY', value=SECRET):
        result = self.piku('secrets:set', APP, key, value=value + '\n')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result

    def test_set_stores_secret_privately(self):
        result = self.set_secret()
        self.assertIn('API_KEY=******** (secret)', result.stdout)
        self.assertNotIn(SECRET, result.stdout + result.stderr)
        secrets = join(self.env_dir, 'SECRETS')
        self.assertEqual(stat.S_IMODE(os_stat(secrets).st_mode), 0o600)
        self.assertIn('env = API_KEY={}'.format(SECRET), self.read('SECRETS'))
        env = self.read('ENV')
        self.assertNotIn(SECRET, env)
        self.assertIn('UWSGI_INCLUDE_FILE={}'.format(join(self.env_dir, 'SECRETS.include')), env)
        self.assertEqual(self.read('SECRETS.include'), 'ini={}\n'.format(secrets))

    def test_config_commands_never_show_secret(self):
        self.set_secret()
        self.write('LIVE_ENV', 'PORT=5000\n')
        for args in [('config', APP), ('config:get', APP, 'API_KEY'), ('config:live', APP)]:
            result = self.piku(*args)
            self.assertNotIn(SECRET, result.stdout + result.stderr, args)

    def test_list_shows_names_only(self):
        self.set_secret('B_KEY')
        self.set_secret('A_KEY')
        result = self.piku('secrets:list', APP)
        self.assertEqual(result.stdout.split(), ['A_KEY', 'B_KEY'])

    def test_unset_removes_secret(self):
        self.set_secret('A_KEY')
        self.set_secret('B_KEY')
        result = self.piku('secrets:unset', APP, 'A_KEY')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.piku('secrets:list', APP).stdout.split(), ['B_KEY'])

    def test_run_includes_secret(self):
        self.set_secret()
        result = self.piku('run', APP, 'printenv', 'API_KEY')
        self.assertEqual(result.stdout.strip(), SECRET, result.stderr)

    def test_run_passes_options_after_double_dash(self):
        # Guards the plugin's copy of core `run` arguments: piku run app ls -- -al
        result = self.piku('run', APP, 'ls', '--', '-al')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('.hidden-marker', result.stdout)
        self.assertIn('total', result.stdout)

    def test_run_prefers_config_value_over_secret(self):
        self.set_secret()
        self.write('LIVE_ENV', 'API_KEY=from-config\n')
        result = self.piku('run', APP, 'printenv', 'API_KEY')
        self.assertEqual(result.stdout.strip(), 'from-config')

    def test_percent_signs_round_trip(self):
        self.set_secret(value='a%b%%c')
        self.assertIn('env = API_KEY=a%%b%%%%c', self.read('SECRETS'))
        result = self.piku('run', APP, 'printenv', 'API_KEY')
        self.assertEqual(result.stdout.strip(), 'a%b%%c')

    def test_rejects_uwsgi_expansions(self):
        for value in ['$(HOME)', 'x@(file)']:
            result = self.piku('secrets:set', APP, 'API_KEY', value=value + '\n')
            self.assertNotEqual(result.returncode, 0, value)
        self.assertFalse(exists(join(self.env_dir, 'SECRETS')))

    def test_rejects_empty_and_padded_values(self):
        for value in ['', ' padded']:
            result = self.piku('secrets:set', APP, 'API_KEY', value=value + '\n')
            self.assertNotEqual(result.returncode, 0, repr(value))

    def test_rejects_invalid_key(self):
        result = self.piku('secrets:set', APP, 'BAD-KEY', value=SECRET + '\n')
        self.assertNotEqual(result.returncode, 0)

    def test_refuses_existing_include_file(self):
        self.write('ENV', 'UWSGI_INCLUDE_FILE=custom.ini\n')
        result = self.piku('secrets:set', APP, 'API_KEY', value=SECRET + '\n')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('custom.ini', result.stdout + result.stderr)
        self.assertFalse(exists(join(self.env_dir, 'SECRETS')))

    def test_rejects_path_in_app_name(self):
        result = self.piku('secrets:set', '../' + APP, 'API_KEY', value=SECRET + '\n')
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(exists(join(self.env_dir, 'SECRETS')))

    def test_uwsgi_config_includes_secrets_without_values(self):
        self.set_secret()
        script = (
            "import runpy\n"
            "piku = runpy.run_path({!r}, run_name='piku')\n"
            "for d in [piku['UWSGI_AVAILABLE'], piku['UWSGI_ENABLED'], piku['LOG_ROOT']]:\n"
            "    __import__('os').makedirs(d, exist_ok=True)\n"
            "env = piku['parse_settings']({!r}, {{}})\n"
            "piku['spawn_worker']({!r}, 'worker', 'true', env)\n"
        ).format(PIKU_PY, join(self.env_dir, 'ENV'), APP)
        env = dict(environ, PIKU_ROOT=self.root, HOME=self.tmp.name)
        result = run([sys.executable, '-c', script], env=env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        with open(join(self.root, 'uwsgi-available', APP + '_worker.1.ini')) as h:
            ini = h.read()
        self.assertIn('ini = {}'.format(join(self.env_dir, 'SECRETS')), ini)
        self.assertNotIn(SECRET, ini)


if __name__ == '__main__':
    unittest.main()
