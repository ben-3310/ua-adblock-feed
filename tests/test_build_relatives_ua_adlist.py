"""Synthetic CLI regressions: never contact upstream or router."""
import hashlib
import importlib.util
import io
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'scripts/build_relatives_ua_adlist.py'


class BuilderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.source = self.directory / 'source.txt'
        self.output = self.directory / 'adlist.txt'

    def run_build(self, source, output=None):
        self.source.write_bytes(source if isinstance(source, bytes) else source.encode())
        return subprocess.run([sys.executable, str(SCRIPT), '--source-file', str(self.source),
                               '--output', str(output or self.output)], capture_output=True, text=True)

    def test_only_unconditional_dns_rules_survive(self):
        result = self.run_build('''! Ukrainian synthetic ads
[Adblock Plus 2.0]
||z.ads.example^
||a.ads.example^
||A.ads.example^
||options.example^$third-party
||path.example/path^
https://url.example
/example-regex/
cosmetic.example##.advert
!#if env_chromium
||conditional.example^
!#if nested
||nested.example^
!#endif
!#else
||else.example^
!#endif
||under_score.example^
||-bad.example^
||singlelabel^
||127.0.0.1^
||printer.local^
||localhost^
''')
        self.assertEqual(result.returncode, 0, result.stderr)
        domains = [line for line in self.output.read_text().splitlines() if not line.startswith('#')]
        self.assertEqual(domains, ['a.ads.example', 'z.ads.example'])

    def test_exceptions_protect_base_and_subdomains_even_with_options(self):
        result = self.run_build('''||keep.ads.example^
||ads.example.net^
||child.ads.example.net^
@@||ads.example.net^$domain=news.example
||parent.example.org^
@@||child.parent.example.org^
''')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual([x for x in self.output.read_text().splitlines() if not x.startswith('#')],
                         ['keep.ads.example'])

    def test_protected_services_cannot_be_blocked(self):
        names = ['youtube.com', 'youtu.be', 'googlevideo.com', 'ytimg.com', 'facebook.com',
                 'fbcdn.net', 'fbsbx.com', 'google.com', 'googleapis.com', 'gstatic.com',
                 'github.com', 'githubusercontent.com', 'pgl.yoyo.org', 'router.lan']
        result = self.run_build('||keep.ads.example^\n' + '\n'.join(
            '||' + name + '^\n||child.' + name + '^' for name in names))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual([x for x in self.output.read_text().splitlines() if not x.startswith('#')],
                         ['keep.ads.example'])

    def test_invalid_sources_preserve_old_output(self):
        for source in [b'', b'<!DOCTYPE html>\n||ads.example^', b'Error: upstream failed\n||ads.example^',
                       b'\xff\n||ads.example^', b'! comments only',
                       b'!#if unclosed\n||ads.example^', b'!#endif\n||ads.example^',
                       b'x' * (256 * 1024 + 1)]:
            with self.subTest(source=source[:40]):
                self.output.write_text('old\n')
                result = self.run_build(source)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(self.output.read_text(), 'old\n')

    def test_ukrainian_essential_services_are_not_imported(self):
        names = ['privatbank.ua', 'novaposhta.ua', 'ukr.net', 'gov.ua', 'monobank.ua']
        result = self.run_build('||keep.ads.example^\n' + '\n'.join(
            '||' + name + '^\n||child.' + name + '^' for name in names))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual([x for x in self.output.read_text().splitlines() if not x.startswith('#')],
                         ['keep.ads.example'])

    def test_count_and_output_size_bounds_preserve_old_output(self):
        sources = ['\n'.join('||ad%d.example^' % n for n in range(513)),
                   '\n'.join('||' + 'a' * 63 + '.' + 'b' * 63 + '.ad%d.example^' % n for n in range(250))]
        for source in sources:
            self.output.write_text('old\n')
            result = self.run_build(source)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(self.output.read_text(), 'old\n')

    def test_invalid_dns_lengths_are_skipped(self):
        result = self.run_build('||good.ads.example^\n||' + 'a' * 64 + '.example^\n||' +
                                '.'.join(['b' * 63] * 4) + '^')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual([x for x in self.output.read_text().splitlines() if not x.startswith('#')],
                         ['good.ads.example'])

    def test_output_in_repository_or_symlink_parent_is_refused(self):
        self.assertNotEqual(self.run_build('||ads.example^', ROOT / 'forbidden-generated.txt').returncode, 0)
        self.assertFalse((ROOT / 'forbidden-generated.txt').exists())
        (self.directory / 'repo').symlink_to(ROOT, target_is_directory=True)
        self.assertNotEqual(self.run_build('||ads.example^', self.directory / 'repo/forbidden-generated.txt').returncode, 0)
        self.assertFalse((ROOT / 'forbidden-generated.txt').exists())

    def test_symlink_and_special_output_are_refused(self):
        target = self.directory / 'target'
        target.write_text('untouched')
        self.output.symlink_to(target)
        self.assertNotEqual(self.run_build('||ads.example^').returncode, 0)
        self.assertEqual(target.read_text(), 'untouched')
        self.output.unlink()
        os.mkfifo(self.output)
        self.assertNotEqual(self.run_build('||ads.example^').returncode, 0)

    def load_builder(self):
        spec = importlib.util.spec_from_file_location('ua_adlist', SCRIPT)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_fetch_failure_preserves_old_output(self):
        builder = self.load_builder()
        self.output.write_text('old\n')
        # Network transport is external; preserve the real build/write path.
        with mock.patch.object(builder.urllib.request.OpenerDirector, 'open', side_effect=OSError('offline')):
            with self.assertRaises(OSError):
                builder.build(self.output)
        self.assertEqual(self.output.read_text(), 'old\n')

    def test_oversized_download_preserves_old_output(self):
        builder = self.load_builder()
        self.output.write_text('old\n')
        class Response(io.BytesIO):
            from email.message import Message
            headers = Message()
            headers['Content-Type'] = 'text/plain'
        with mock.patch.object(builder.urllib.request.OpenerDirector, 'open',
                               return_value=Response(b'x' * (256 * 1024 + 1))):
            with self.assertRaises(ValueError):
                builder.build(self.output)
        self.assertEqual(self.output.read_text(), 'old\n')

    def test_replace_failure_keeps_old_output_and_cleans_temporary_file(self):
        builder = self.load_builder()
        self.source.write_text('||ads.example^\n')
        self.output.write_text('old\n')
        with mock.patch.object(builder.os, 'replace', side_effect=OSError('disk error')):
            with self.assertRaises(OSError):
                builder.build(self.output, self.source)
        self.assertEqual(self.output.read_text(), 'old\n')
        self.assertEqual(sorted(x.name for x in self.directory.iterdir()), ['adlist.txt', 'source.txt'])

    def test_success_replaces_file_atomically_with_attribution(self):
        source = b'||ads.example^\n'
        self.output.write_text('old\n')
        old_inode = self.output.stat().st_ino
        result = self.run_build(source)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotEqual(self.output.stat().st_ino, old_inode)
        text = self.output.read_text()
        self.assertIn(hashlib.sha256(source).hexdigest(), text)
        self.assertIn('https://raw.githubusercontent.com/ukrainianfilters/lists/main/ads/ads.txt', text)
        self.assertIn('GPL-3.0', text)
        self.assertRegex(text, r'# Generated UTC: \d{4}-\d{2}-\d{2}T')
        self.assertEqual(sorted(x.name for x in self.directory.iterdir()), ['adlist.txt', 'source.txt'])


if __name__ == '__main__':
    unittest.main()
