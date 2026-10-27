# Ukrainian advertising DNS feed

A small, conservative DNS supplement derived from the advertising section of
[Ukrainian filters](https://github.com/ukrainianfilters/lists).

Feed: <https://ben-3310.github.io/ua-adblock-feed/relatives-ua-ads.txt>

The public repository contains only the builder, publication guard, synthetic
tests and this workflow. Generated lists are deployed as a GitHub Pages artifact,
never committed. No router configuration, router credentials, private keys or
network captures belong here.

## Updates and safeguards

- GitHub Actions builds daily at 02:23 UTC. Schedules may be delayed by GitHub.
- Whole-host unconditional advertising rules only; browser cosmetics, URL paths
  and conditional rules are skipped. Domain exceptions and protected essential
  service domains take precedence.
- Maximum source 256 KiB; output 512 names and 32 KiB.
- Additions and removals must each be no greater than the larger of five names
  and 25% of the previous published count, rounded up. Larger changes require
  review; there is no automatic override.
- A failed download, invalid input or rejected change stops publication. The
  previous successful Pages deployment stays available.
- Initial publication requires manual `bootstrap=true`. It only accepts a
  missing feed (404), not malformed existing content or other HTTP errors.
- After 30 days without a commit, the workflow makes an empty maintenance commit
  to avoid GitHub's 60-day public-schedule inactivity limit. No files change.
- Builds/deployments are serialized. Actions are pinned to commit SHAs.

This is domain filtering, not a guarantee that every advertisement disappears.
DNS cannot reliably separate YouTube video ads or Facebook sponsored content
from the wanted content on the same domains. False positives remain possible;
review the source and disable the feed if necessary.

## Run locally

```sh
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -v
python3 scripts/prepare_ua_adlist_feed.py \
  --output-dir /an/existing/external/parent/new-public-feed \
  --previous-url https://ben-3310.github.io/ua-adblock-feed/relatives-ua-ads.txt
```

The output directory must be new and outside the checkout. Its four files are
the DNS list, original upstream ads source, upstream GPL-3.0 license, and builder
source. TLS verification is enabled. The tool never connects to a router.

For the first deployment, enable Pages with GitHub Actions as the source, then
run `gh workflow run publish.yml -f bootstrap=true`. Subsequent runs must use the
default `bootstrap=false` and preserve a valid prior feed for comparison.

The upstream data and accompanying source are distributed under GPL-3.0; see
`COPYING` in the published artifact and the upstream repository. The public feed
repository's own code is provided under the accompanying GPL-3.0 license.
