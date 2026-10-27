#!/usr/bin/env python3
"""Prepare only public feed files; a rejected build never replaces a deployment."""
import argparse
import math
import os
from pathlib import Path
import shutil
import ssl
import tempfile
import urllib.error
import urllib.request

import build_relatives_ua_adlist as builder

LICENSE_URL = 'https://raw.githubusercontent.com/ukrainianfilters/lists/main/LICENSE'
PUBLIC_FILES = {'relatives-ua-ads.txt', 'upstream-ads.txt', 'COPYING',
                'build_relatives_ua_adlist.py'}


def bounded_read(path, limit):
    with open(path, 'rb') as stream:
        data = stream.read(limit + 1)
    if len(data) > limit:
        raise ValueError('file exceeds size limit')
    return data


def fetch(url, limit):
    if not url.startswith('https://'):
        raise ValueError('only HTTPS is allowed')
    opener = urllib.request.build_opener(
        builder.HTTPSOnlyRedirect(),
        urllib.request.HTTPSHandler(context=ssl.create_default_context()))
    with opener.open(url, timeout=30) as stream:
        if stream.headers.get_content_type() not in ('text/plain', 'application/octet-stream'):
            raise ValueError('unexpected content type')
        data = stream.read(limit + 1)
    if len(data) > limit:
        raise ValueError('download exceeds size limit')
    return data


def feed_domains(raw, enforce_protection=True):
    if len(raw) > builder.MAX_OUTPUT:
        raise ValueError('previous feed exceeds size limit')
    domains = []
    for line in raw.decode('ascii').splitlines():
        if line.startswith('#'):
            continue
        if (not builder.valid_hostname(line) or line != line.lower()
                or (enforce_protection and any(builder.related(line, safe) for safe in builder.PROTECTED))):
            raise ValueError('invalid or protected domain in feed')
        domains.append(line)
    if not domains or len(domains) > builder.MAX_DOMAINS or len(set(domains)) != len(domains):
        raise ValueError('invalid feed count')
    return set(domains)


def prepare(output_dir, source_file=None, license_file=None, previous_file=None,
            previous_url=None, bootstrap=False):
    output = builder.checked_output(output_dir)
    if output.exists():
        raise ValueError('output directory must not already exist')
    if previous_file is not None and previous_url is not None:
        raise ValueError('use one previous feed source')
    previous = None
    if previous_file is not None:
        previous = feed_domains(bounded_read(previous_file, builder.MAX_OUTPUT), enforce_protection=False)
    elif previous_url is not None:
        try:
            previous = feed_domains(fetch(previous_url, builder.MAX_OUTPUT), enforce_protection=False)
        except urllib.error.HTTPError as error:
            if error.code != 404 or not bootstrap:
                raise
    if previous is None and not bootstrap:
        raise ValueError('a valid previous feed is required; bootstrap must be explicit')
    raw = builder.read_source(source_file)
    license_data = (bounded_read(license_file, 64 * 1024) if license_file is not None
                    else fetch(LICENSE_URL, 64 * 1024))
    if (b'GNU GENERAL PUBLIC LICENSE' not in license_data[:100]
            or b'Version 3, 29 June 2007' not in license_data[:300]):
        raise ValueError('expected upstream GPL-3.0 license')
    stage = Path(tempfile.mkdtemp(prefix='.ua-feed-', dir=output.parent))
    try:
        (stage/'upstream-ads.txt').write_bytes(raw)
        count = builder.build(stage/'relatives-ua-ads.txt', stage/'upstream-ads.txt')
        current = feed_domains((stage/'relatives-ua-ads.txt').read_bytes())
        if previous is not None:
            limit = max(5, math.ceil(len(previous) * 0.25))
            if len(current - previous) > limit or len(previous - current) > limit:
                raise ValueError('domain additions/removals exceed review threshold')
        (stage/'COPYING').write_bytes(license_data)
        shutil.copyfile(Path(builder.__file__), stage/'build_relatives_ua_adlist.py')
        if {x.name for x in stage.iterdir()} != PUBLIC_FILES or any(x.is_symlink() for x in stage.iterdir()):
            raise ValueError('unexpected publication contents')
        if output.exists() or output.is_symlink():
            raise ValueError('output appeared during build')
        os.rename(stage, output)
    finally:
        if stage.exists():
            shutil.rmtree(stage)
    return count


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--source-file', type=Path)
    parser.add_argument('--license-file', type=Path)
    group = parser.add_mutually_exclusive_group()
    group.add_argument('--previous-file', type=Path)
    group.add_argument('--previous-url')
    parser.add_argument('--bootstrap', action='store_true')
    args = parser.parse_args()
    try:
        count = prepare(**vars(args))
    except (OSError, ValueError) as error:
        parser.exit(1, 'Publication rejected: ' + str(error) + '\n')
    print('Validated public package: %d domains' % count)


if __name__ == '__main__':
    main()
