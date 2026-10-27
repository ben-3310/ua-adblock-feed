"""Publication boundary checks use synthetic files, never GitHub or a router."""
from pathlib import Path
import importlib.util
import io
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
import urllib.error

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'scripts/prepare_ua_adlist_feed.py'
LICENSE = 'GNU GENERAL PUBLIC LICENSE\nVersion 3, 29 June 2007\n'


class FeedTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.source = self.directory / 'source.txt'
        self.license = self.directory / 'license.txt'
        self.previous = self.directory / 'previous.txt'
        self.output = self.directory / 'public'
        self.license.write_text(LICENSE)

    def run_feed(self, names, previous=None, bootstrap=False):
        self.source.write_text('! synthetic advertising filter\n' + ''.join('||' + x + '^\n' for x in names))
        args = [sys.executable, str(SCRIPT), '--source-file', str(self.source),
                '--license-file', str(self.license), '--output-dir', str(self.output)]
        if previous is not None:
            self.previous.write_text(previous)
            args += ['--previous-file', str(self.previous)]
        if bootstrap:
            args += ['--bootstrap']
        return subprocess.run(args, text=True, capture_output=True)

    def test_initial_publication_requires_explicit_bootstrap(self):
        result = self.run_feed(['ads.example'])
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.output.exists())
        result = self.run_feed(['ads.example'], bootstrap=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_package_contains_only_public_allowlisted_files(self):
        (self.directory / 'credentials.txt').write_text('must never be published')
        result = self.run_feed(['a.ads.example', 'b.ads.example'], '# previous\na.ads.example\n')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(sorted(x.name for x in self.output.iterdir()),
                         ['COPYING', 'build_relatives_ua_adlist.py', 'relatives-ua-ads.txt', 'upstream-ads.txt'])
        self.assertEqual([x for x in (self.output/'relatives-ua-ads.txt').read_text().splitlines()
                          if not x.startswith('#')], ['a.ads.example', 'b.ads.example'])
        self.assertEqual((self.output/'COPYING').read_text(), LICENSE)

    def test_growth_or_removal_aborts_without_modifying_previous(self):
        old = ['ad%d.example' % i for i in range(20)]
        previous = '\n'.join(old) + '\n'
        for new in [old + ['new%d.example' % i for i in range(6)], old[:14]]:
            result = self.run_feed(new, previous)
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse(self.output.exists())
            self.assertEqual(self.previous.read_text(), previous)

    def test_allowed_change_at_boundary_is_published(self):
        old = ['ad%d.example' % i for i in range(20)]
        result = self.run_feed(old[5:] + ['new%d.example' % i for i in range(5)], '\n'.join(old))
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_invalid_previous_is_not_bypassed_by_bootstrap(self):
        for prior in ['', '<html>error</html>', '||ads.example^', 'x'*32769]:
            result = self.run_feed(['ads.example'], prior, bootstrap=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse(self.output.exists())

    def test_new_protection_can_remove_a_domain_in_the_previous_feed(self):
        result = self.run_feed(['ads.example', 'privatbank.ua'], 'ads.example\nprivatbank.ua\n')
        self.assertEqual(result.returncode, 0, result.stderr)
        names = [x for x in (self.output/'relatives-ua-ads.txt').read_text().splitlines()
                 if not x.startswith('#')]
        self.assertEqual(names, ['ads.example'])

    def load_publisher(self):
        sys.path.insert(0, str(ROOT/'scripts'))
        self.addCleanup(lambda: sys.path.remove(str(ROOT/'scripts')))
        spec = importlib.util.spec_from_file_location('feed_publisher', SCRIPT)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_network_failures_do_not_allow_bootstrap_except_404(self):
        publisher = self.load_publisher()
        self.source.write_text('||ads.example^\n')
        for code, bootstrap in [(404, False), (403, True), (500, True)]:
            with self.subTest(code=code, bootstrap=bootstrap):
                error = urllib.error.HTTPError('https://feed.example/list.txt', code, 'failed', {}, None)
                with mock.patch.object(publisher.urllib.request.OpenerDirector, 'open', side_effect=error):
                    with self.assertRaises(urllib.error.HTTPError):
                        publisher.prepare(self.output, self.source, self.license,
                                          previous_url='https://feed.example/list.txt', bootstrap=bootstrap)
                self.assertFalse(self.output.exists())
        error = urllib.error.HTTPError('https://feed.example/list.txt', 404, 'missing', {}, None)
        with mock.patch.object(publisher.urllib.request.OpenerDirector, 'open', side_effect=error):
            count = publisher.prepare(self.output, self.source, self.license,
                                      previous_url='https://feed.example/list.txt', bootstrap=True)
        self.assertEqual(count, 1)

    def test_oversized_previous_download_is_rejected(self):
        publisher = self.load_publisher()
        class Response(io.BytesIO):
            from email.message import Message
            headers = Message()
            headers['Content-Type'] = 'text/plain'
        with mock.patch.object(publisher.urllib.request.OpenerDirector, 'open',
                               return_value=Response(b'x' * 32769)):
            with self.assertRaises(ValueError):
                publisher.prepare(self.output, previous_url='https://feed.example/list.txt')
        self.assertFalse(self.output.exists())

    def test_invalid_license_prevents_publication(self):
        self.license.write_text('<html>upstream unavailable</html>')
        result = self.run_feed(['ads.example'], bootstrap=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.output.exists())

    def test_existing_directory_is_never_packaged_or_overwritten(self):
        self.output.mkdir()
        (self.output/'secret.txt').write_text('private')
        result = self.run_feed(['ads.example'], bootstrap=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual([x.name for x in self.output.iterdir()], ['secret.txt'])


if __name__ == '__main__':
    unittest.main()
