# CVE reproduction

This workload answers a defender's questions about one published vulnerability: is a given version actually affected, does the patched version close it, and what does the evidence look like. It runs entirely inside Corral's disposable, network-isolated containers, on infrastructure you control, one advisory at a time.

It is built for authorized work: confirming exposure, validating a patch, and producing a write-up for a team. It is not an exploit generator, and it holds no payloads of its own. The presence check is yours.

## How it works

You write a recipe. It names the CVE, a container image pinned to the affected version, an optional image carrying the fix, and a probe command. The probe is your own check that the vulnerable condition is present. By convention the probe exits zero when the condition is observed and non-zero when it is not, so the same probe against both images tells the whole story:

```
affected image  -> probe exits 0    => exposure CONFIRMED on that version
patched image   -> probe exits != 0 => the fix closes it
```

Corral fetches the public advisory for context (on the controller, where there is a network), runs the probe in each environment through the fleet (in isolated containers), compares the outcomes, and renders a Markdown report.

## A recipe

```json
{
  "cve_id": "CVE-2014-6271",
  "summary": "Shellshock. The probe is the standard detection check: a crafted environment variable whose value is a function definition followed by a command.",
  "affected_image": "bash:4.3",
  "patched_image": "bash:5.2",
  "probe": ["env x='() { :;}; echo VULNERABLE' bash -c 'exit 0' | grep -q VULNERABLE"],
  "probe_shell": true,
  "network": false,
  "select_labels": ["cve"],
  "timeout_s": 120
}
```

Fields: `cve_id`, `affected_image`, and `probe` are required. `patched_image` is optional; when present it is probed too. `network` defaults to false, so the probe runs with no network unless you opt in. `select_labels` and `select_os` choose which docker-capable node builds the environment. Example recipes live in [examples/](examples/).

Image availability and patch status vary over time, so treat the images in an example as a starting point and pin ones you have verified.

## Running it

```sh
corral cve --recipe docs/examples/shellshock.json --out report.md
corral cve --recipe recipe.json --no-fetch        # skip the advisory fetch, for an offline run
```

An agent can do the same through the `reproduce_cve` MCP tool, passing the recipe fields as arguments and receiving the verdict and the report.

## What the report contains

The verdict line, the advisory summary and CVSS when fetched, the environment (both images, the probe, whether a network was allowed), the observed probe outcomes with captured output, the exact `docker run` commands to reproduce it by hand, and the advisory references. It opens by stating it is for authorized, defensive use on infrastructure you control.
