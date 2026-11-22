# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A CUPS backend (`labelcrop`) that sits in front of a real label printer as an
AirPrint queue, detects the interesting region of each incoming page, crops and
fits it to the label roll, and forwards the result to the real queue with
`lp`. A stdlib-only settings page (`labelui`) edits the config and previews
crops. Target platform is Debian 12 with CUPS, Avahi and PyMuPDF from apt. The
README is thorough and is the authoritative explanation of the detection
algorithm and the many CUPS/Avahi/mDNS gotchas; read it before changing
anything in `setup.sh` or the detection thresholds.

## Commands

There is no build step, package manifest or linter config. The only dependency
is PyMuPDF (`pip install pymupdf`).

```bash
python3 test_crop.py /tmp/testout      # offline detection suite, writes PNG previews
bash -n setup.sh                        # what CI runs as "shellcheck"
```

CI (`.github/workflows/test.yml`) runs `test_crop.py` on Python 3.11, 3.12 and
3.13, requires at least 6 PNG previews, and uploads them as artifacts. 3.11 is
what Debian 12 ships and is the real target.

`test_crop.py` has no per-case assertions. It prints a table (result reason,
rotation, scale) and exits non-zero only if `build()` or the preview renderer
throws. The README's "Testing without a printer" table lists the expected
reason and rotation for each of the eight cases; compare the printed table
against it by eye, and open the PNGs (input with detected box in red on the
left, output label pages on the right). There is no way to run a single case
other than editing the `CASES` list. Run this before any detection change.

Deploying to a box: `sudo ./setup.sh <printer-ip> [proxy-queue] [target-queue]`.
It is idempotent and reinstalls `labelcrop`, the PPD and `labelui`.

## Architecture

```
iPhone/Mac --AirPrint--> proxy queue (labelcrop:/<target>) --crop--> hidden target queue --IPP--> printer
```

**`labelcrop` (no `.py` extension, it is a CUPS backend).** Nothing runs on
import. `test_crop.py` and `labelui` both load it by path with
`SourceFileLoader`, so any top-level side effect breaks both. Key entry points:

- `load_config()`: `DEFAULTS` dict overlaid by `/etc/labelproxy.conf`
  (`KEY=VALUE`), then `LABELPROXY_<KEY>` env vars. `DEFAULTS` is the single
  authority for which settings exist; `labelui` renders a field for every key
  in it, so a new setting appears in the UI automatically (under "Advanced"
  unless added to `FIELDS` in `labelui`).
- `detect_page(page, cfg, info=None)`: returns `(clip_rect | None, reason)`
  where reason is `border` / `border-block` / `block` / `content` /
  `full-page` / `blank`. Order is fixed and deliberate: drawn border box beats
  cut-line block splitting beats ink bbox. Do not reorder. Block splitting
  fires only when an explicit cut rule is found, never on whitespace (see
  README for the barcode-without-address failure that motivated this). Inside
  a border the cut-line split is held to stricter rules (thin at pixel level,
  striped-ink scoring, the cut-away side must hold most of the ink); see the
  README's "A cut line inside a border". `info["solid"]` gets the page's
  solid-ink fraction, which `build()` uses to skip instruction pages
  (`reason` `instructions` in previews) when another page has a label.
  Vocabulary for all of this is in `GLOSSARY.md`.
- `place()` chooses rotation and scale; `build()` assembles the output PDF and
  optionally fills a `previews` list the harness and UI consume;
  `build_passthrough()` is the fit-only fallback.
- `main()` is the CUPS entry: three-tier fail-safe (crop, then fit-only, then
  forward original unchanged), then shells out to `lp -d <target>` with
  `media=` and `finishings=` from config. Logs go to stderr with a
  `LEVEL: labelcrop:` prefix, which CUPS parses.

**Every threshold is a fraction of the page, never millimetres.** A phone
screenshot is nominally 413x893mm, so mm-based thresholds silently break on
exactly the inputs the project exists to handle. Keep new thresholds
fractional. Pages stay vector (`show_pdf_page` with a clip); only bare image
input is rasterised.

**`labelui`** imports `labelcrop` by path (`LABELUI_BACKEND`), serves on
`http.server` with a CIDR allowlist, and has no print path at all. Runs as the
`lp` user under a hardened systemd unit: it can rewrite `/etc/labelproxy.conf`
in place but cannot create files in `/etc`, so the save is an in-place rewrite
and the backup goes to `/var/lib/labelproxy`. Test config writes as the service
user, not as yourself.

**Installed files and their traps:**

- `labelproxy.service` is an **Avahi XML record template**, not a systemd unit.
  The systemd unit is `labelui.systemd`. A raw `<` in a TXT value silently
  invalidates the whole Avahi file. Avoid parentheses in the Bonjour name
  (iOS rendered it blank), and remember that iOS keeps the first name it saw
  for a printer in "Known Printers": a rename, UUID change or Avahi restart on
  the server cannot fix a stale entry on a phone. See the README section "iOS
  remembers the name a printer had the first time you used it" before
  debugging a blank or wrong name server-side.
- `labelproxy.ppd` is a minimal PDF-passthrough PPD carrying the AirPrint URF
  advertisement.
- `labelproxy.conf` and `labelui.default` are installed only on first setup;
  `setup.sh` keeps existing copies.
- `setup.sh` publishes the Bonjour record by hand with `Browsing Off` in CUPS,
  trims Avahi to one interface, IPv4, single `_ipp` record. This is load
  bearing: extra mDNS registrations overran a cross-VLAN reflector and knocked
  unrelated printers offline. Do not add Bonjour records (the settings page is
  deliberately not advertised).

## Conventions

- Comments and the README record *why* a decision was made and what failed
  before it. When changing a threshold or a detection rule, keep that record
  accurate: update the comment in `DEFAULTS`, the README section, and add or
  adjust a `test_crop.py` case that reproduces the input.
- Debug real jobs from the `*-in.pdf` / `*-out.pdf` pairs in
  `/var/lib/labelproxy/` and reproduce them in `test_crop.py` rather than on
  the roll.
