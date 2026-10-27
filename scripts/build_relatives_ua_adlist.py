#!/usr/bin/env python3
"""Build a deliberately small Ukrainian advertising DNS supplement outside Git.

Only unconditional whole-host rules are portable to DNS. Browser filters and
privacy lists are not imported. This command never connects to a router.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import ipaddress
import os
from pathlib import Path
import re
import ssl
import stat
import sys
import tempfile
import urllib.request

SOURCE_URL = 'https://raw.githubusercontent.com/ukrainianfilters/lists/main/ads/ads.txt'
MAX_SOURCE = 256 * 1024
MAX_DOMAINS = 512
MAX_OUTPUT = 32 * 1024
REPOSITORY = Path(__file__).resolve().parents[1]
PROTECTED = (
    'youtube.com', 'youtu.be', 'googlevideo.com', 'ytimg.com', 'facebook.com',
    'fbcdn.net', 'fbsbx.com', 'google.com', 'googleapis.com', 'gstatic.com',
    'github.com', 'githubusercontent.com', 'pgl.yoyo.org', 'router.lan',
    'localhost', 'local', 'lan', 'internal', 'home.arpa', 'in-addr.arpa', 'ip6.arpa',
    'privatbank.ua', 'novaposhta.ua', 'ukr.net', 'gov.ua', 'monobank.ua',
)
LABEL = re.compile(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', re.ASCII)


def related(left, right):
    """DNS blocking of either a parent or child could break an exception."""
    return left == right or left.endswith('.' + right) or right.endswith('.' + left)


def valid_hostname(name):
    if len(name) > 253 or '.' not in name:
        return False
    if not all(LABEL.fullmatch(label) for label in name.split('.')):
        return False
    if name.rsplit('.', 1)[1].isdigit():
        return False
    try:
        ipaddress.ip_address(name)
        return False
    except ValueError:
        return True


def parse_source(raw):
    if len(raw) > MAX_SOURCE:
        raise ValueError('source exceeds 256 KiB')
    text = raw.decode('utf-8', errors='strict')
    if not text.strip() or re.search(r'<(?:!doctype|/?html|/?body|/?head)\b', text, re.I):
        raise ValueError('empty or HTML source')
    domains, exceptions = set(), set()
    depth = 0
    for original in text.splitlines():
        line = original.strip()
        if re.match(r'(?:error\b|\d{3}\s+(?:not found|forbidden|bad gateway))', line, re.I):
            raise ValueError('upstream error response')
        # Exceptions remain effective even inside conditions: DNS cannot express
        # their browser scope, so prefer underblocking to breaking a website.
        if line.startswith('@@'):
            match = re.match(r'@@(?:\|\||\|?https?://)([A-Za-z0-9.-]+)(?=[^A-Za-z0-9.-]|$)', line)
            if match and valid_hostname(match[1].lower()):
                exceptions.add(match[1].lower())
            continue
        if line.startswith('!#if '):
            depth += 1
            continue
        if line == '!#endif':
            if not depth:
                raise ValueError('unbalanced conditional filter')
            depth -= 1
            continue
        if line.startswith('!#else'):
            if not depth:
                raise ValueError('unbalanced conditional filter')
            continue
        if line.startswith('!#'):
            raise ValueError('unsupported filter preprocessor directive')
        if depth:
            continue
        match = re.fullmatch(r'\|\|([A-Za-z0-9.-]+)\^', line)
        if match:
            name = match[1].lower()
            if valid_hostname(name) and not any(related(name, safe) for safe in PROTECTED):
                domains.add(name)
    if depth:
        raise ValueError('unclosed conditional filter')
    domains = sorted(name for name in domains if not any(related(name, safe) for safe in exceptions))
    if not domains:
        raise ValueError('source contains no safe unconditional advertising domains')
    if len(domains) > MAX_DOMAINS:
        raise ValueError('output exceeds 512 domains')
    return domains


class HTTPSOnlyRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not newurl.lower().startswith('https://'):
            raise ValueError('refusing non-HTTPS redirect')
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def read_source(source_file=None):
    if source_file is not None:
        with open(source_file, 'rb') as stream:
            raw = stream.read(MAX_SOURCE + 1)
    else:
        opener = urllib.request.build_opener(
            HTTPSOnlyRedirect(), urllib.request.HTTPSHandler(context=ssl.create_default_context()))
        request = urllib.request.Request(SOURCE_URL, headers={'User-Agent': 'relatives-ua-adlist/1'})
        with opener.open(request, timeout=30) as stream:
            if stream.headers.get_content_type() not in ('text/plain', 'application/octet-stream'):
                raise ValueError('unexpected source content type')
            raw = stream.read(MAX_SOURCE + 1)
    if len(raw) > MAX_SOURCE:
        raise ValueError('source exceeds 256 KiB')
    return raw


def checked_output(path):
    path = Path(path).absolute()
    # Resolve parents, but never follow the destination itself.
    path = path.parent.resolve(strict=True) / path.name
    if path == REPOSITORY or REPOSITORY in path.parents:
        raise ValueError('generated output must be outside the repository')
    try:
        mode = path.lstat().st_mode
    except FileNotFoundError:
        return path
    if not stat.S_ISREG(mode):
        raise ValueError('output must not be a symlink or special file')
    return path


def build(output, source_file=None):
    output = checked_output(output)
    raw = read_source(source_file)
    domains = parse_source(raw)
    content = ('# Ukrainian advertising DNS supplement; ukrainianfilters/lists\n'
               '# Source: ' + SOURCE_URL + '\n'
               '# Source SHA256: ' + hashlib.sha256(raw).hexdigest() + '\n'
               '# Generated UTC: ' + datetime.now(timezone.utc).isoformat() + '\n'
               '# Upstream data license: GPL-3.0; https://github.com/ukrainianfilters/lists\n' +
               '\n'.join(domains) + '\n').encode('ascii')
    if len(content) > MAX_OUTPUT:
        raise ValueError('output exceeds 32 KiB')
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='wb', prefix='.' + output.name + '.',
                                         dir=output.parent, delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        checked_output(output)
        os.replace(temporary, output)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return len(domains)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True, help='file outside repository (parent must exist)')
    parser.add_argument('--source-file', type=Path, help='local upstream ads source for offline validation')
    args = parser.parse_args()
    try:
        count = build(args.output, args.source_file)
    except (OSError, ValueError) as error:
        parser.exit(1, 'Build failed: ' + str(error) + '\n')
    print('Wrote %d advertising domains to %s' % (count, args.output))


if __name__ == '__main__':
    main()
