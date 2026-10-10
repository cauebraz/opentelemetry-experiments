# L4-04 What does it take to run a processor you wrote yourself?

Level 4, theme collector. 3 repetitions per variant on otelcol-contrib 0.162.0 and Prometheus v3.15.0.

Results version 2, recorded with Python 3.14.8 on Darwin arm64 and Docker Compose 5.5.1.

Rendered from this question's `results.json` by `just render L4-04`. Do not edit by hand.

## Variants

| Variant | What it sends |
| --- | --- |
| `stock-pass-through` | otelcol-contrib, batch only |
| `custom-pass-through` | custom distribution, batch only |
| `custom-processor` | custom distribution with the experiment processor |

## Measured

| Variant | HTTP | Delivered | Stamped | Kept | Startup s | Idle RSS | Heap | Post s | CPU ns | Stock reject | Custom accept | Binary B | Image B | Build s |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `stock-pass-through` | 200 | 1000 | 0 | 1000 | 0.001 (0.001 to 0.002) | 44145050 (42068869 to 50520392) | 25088680 (23182208 to 27413336) | 0.006 (0.005 to 0.006) | 5538000 (5146000 to 6622000) | 1 | 0 | 31719586 | 42580242 | 87.9 |
| `custom-pass-through` | 200 | 1000 | 0 | 1000 | 0.002 (0.001 to 0.003) | 12949914 (11974738 to 14795407) | 3385080 (3254032 to 3386352) | 0.005 (0.005 to 0.006) | 8843000 (6620000 to 9891000) | 1 | 0 | 31719586 | 42580242 | 87.9 |
| `custom-processor` | 200 | 1000 | 1000 | 1000 | 0.002 (0.002 to 0.003) | 11911823 (11408507 to 14963180) | 2853032 (2805672 to 3242416) | 0.005 (0.005 to 0.006) | 9263000 (7448000 to 10020000) | 1 | 0 | 31719586 | 42580242 | 87.9 |

Numbers are the median across the valid repetitions, with the range in parentheses when the repetitions disagreed. Text columns show the value when every repetition agreed.

## Interpretation

The official binary and the custom one did not disagree about delivery.
Every variant accepted the POST, the sink printed 1000 records, and all
1000 still carried `keep=yes`. The stamp appeared only when the
`experiment` processor was in the pipeline: 1000 of 1000 there, 0 on both
pass-throughs. `otelcol-contrib validate` exited 1 on that config; the
custom image exited 0.

The cost that shows up in the numbers is the artifact, not the burst. The
custom binary is 31719586 bytes, the image 42580242, and the image build
took 87.9 s. Idle RSS on the stock contrib image was about 44 MB; the
custom image, which contains only the components in the OCB manifest, sat
near 12 MB. CPU and post time on 1000 logs overlap across the three
variants. Owning the processor means owning that binary: a new image, a
module graph (`go version -m`), and a rebuild whenever core, contrib, or
OCB moves. This run does not turn that ownership into a deploy-time number.

## What this does not say

Measured on Darwin arm64 with Docker Desktop, one custom image
(`ocb v0.162.0`, Go 1.26.0) and `otelcol-contrib 0.162.0`. The first
session was invalid only because a Prometheus query ran before the sink
counter was scraped; the sink log already showed 1000 records. That
session is kept in `results/invalid-prometheus-query/`. The reader now
uses the sink's own `:8888` text. Multi-arch build, an SBOM, and the
calendar time to ship a custom distribution are outside the run. CPU on
this burst is noisy.
