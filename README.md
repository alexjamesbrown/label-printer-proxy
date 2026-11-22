# label-printer-proxy

An AirPrint printer you can send **anything** to, which works out what the
interesting part of the page is, crops to it, and prints it sensibly on a label
roll.

Shipping labels from any carrier, tickets, receipts, screenshots, documents. No
"save the PDF, open it, zoom, crop, choose the right paper size" dance. Print to
it from an iPhone and a label comes out.

```
iPhone / Mac --AirPrint--> labelcrop queue --crop--> real queue --IPP--> label printer
```

Built for a Brother QL-1110NWB on a 103x164mm roll, but every size, margin and
threshold is config, so other QL models and other roll sizes are a config change
rather than a code change.

## Why

Carrier label PDFs are A4 pages with a label in one corner. Printing one on a
label roll normally means cropping it by hand. Browser-printed labels add a
header and a URL footer. Some carriers emit a blank second page. A receipt is
tiny and a document is full-bleed, and they want opposite treatment.

`labelcrop` is a CUPS backend that sits in front of your real label printer and
does the thinking: find the content, crop, rotate if it fits better, scale to the
roll, forward. It never drops a job, and it has no print path it can get wrong,
because the real printing is still done by CUPS.

## How it decides what to print

Per page, in order:

1. **Bordered box.** The outer extent of long continuous dark lines, which is how
   most carrier labels are drawn. Runs longer than 90% of the page are ignored,
   because those are page frames and cut guides rather than a label outline. The
   rectangle is only accepted if some single line actually *spans* it in each
   axis. That is what separates a real drawn box from four unrelated rules that
   happen to bound a region, and it is why a header rule above a label does not
   become the label's top edge. If the box turns out to enclose a whole *sheet*,
   with a cut line separating a page of instructions from the label, the label
   side is kept (see "A cut line inside a border" below).
2. **The side of a cut line with more solid ink.** Failing a border, if the page
   carries an explicit "cut here" rule it is split there and the denser side
   kept. This is the "instructions at the top, cut line, real label underneath"
   shape that InPost and others use.
3. **Content bounding box.** Ink bbox on a coarse grid, with connected
   components used to throw away isolated specks and thin strips hugging the
   top or bottom edge: browser print headers, URLs, date stamps, page numbers.
4. **None of the above.** If the detected region covers more than 85% of the
   page, the page *is* the content. No crop, just fit.

Then rotate to whichever orientation scales larger (but only if it wins by a
configurable margin, so near-square labels do not flip for a 0.2% gain), scale up
to fill the printable area, and centre.

A drawn border always wins over the density guess: it is an explicit statement
about where the label is, whereas block splitting is inference. Getting that
order wrong makes block splitting fire *inside* a bordered label and crop to
just its barcode. The cut-line check inside a border runs *after* the border
has fixed the width and outer extent, under much stricter rules, and only ever
trims the top or bottom off it.

Across pages, a page with no solid ink at all (no barcode, no filled block) is
an **instruction page** and is skipped, but only when another page in the job
does have solid ink. The two-page DPD return ("print this paper, cut out the
label below" and then the sheet) prints one label. A job that is nothing but
text still prints every page.

### Finding a label under a page of instructions

Some carriers send an A4 sheet of instructions with a dashed "cut here" line and
the real label below it. The ink bbox spans the whole sheet, so the label comes
out as a postage stamp in the middle of the roll.

⚠️ **Splitting happens only at an explicit cut rule, never on whitespace alone.**
That restriction is not fussiness. An earlier version split on whitespace, and a
borderless label whose address sits above its barcode with as little as a 40pt
gap was split in two, with the barcode winning on solid ink. The result is a
printed barcode with no addressee, which is worse than not cropping at all. The
ratio test gives no protection there, because text scores 0% solid and the ratio
is effectively infinite.

Three things make this detectable, and one obvious thing does not:

- **The cut line is usually dashed**, so longest-continuous-run line detection
  never sees it: it only ever finds one dash. Measuring the *span* of inked
  pixels across the row does.
- **A rule is a local spike in span, not just a wide thin band.** Defining it as
  the latter matched a bold centred heading at 81% of the page width, and missed
  the real line, which sat in a nine-row band with the text around it and so
  never looked thin. The test is a run of up to three rows reaching nearly the
  full width, with the rows outside the run reaching much less far. The run
  matters because a rule landing on a cell boundary straddles two grid rows, and
  each then looks like a wide neighbour of the other.
- **Ink coverage cannot tell text from barcodes.** On a real InPost label the
  instructions scored 46% coverage and the label 50%: useless. At this grid
  resolution a line of text inks nearly every cell it crosses.
- **Solid ink can.** Scoring cells whose *averaged* grey is genuinely dark,
  rather than merely non-white, gave 2.4% for the instructions against 18.5%
  for the label. Barcodes and filled blocks are dark; text is thin strokes on
  white.

The kept side must clear `BLOCK_SOLID_MIN` and beat the other side by
`BLOCK_SOLID_RATIO`. Within it, short bands of pure text are trimmed, which
removes a "YOU MUST ATTACH THIS LABEL" banner while leaving a multi-line address
block alone. If nothing stands out the page is left whole, which is the safe
outcome.

### A cut line inside a border

DPD Local returns draw one box round the whole sheet: a grey "Return Your
Parcel" banner, two columns of instructions and pickup shops, a dashed cut line,
and the label in the bottom third. The border detector is right that the box is
the content, and the result was the whole sheet on the roll at 59%, label
included, unreadable.

An earlier version never looked for a cut line inside a border at all, because a
label's own internal rules would be taken for one. That is still the danger, and
most bordered pages *are* plain labels, so the split inside a border is held to
stricter rules than on a bare page. Each came from something that went wrong:

- **Spans are measured against the border's interior, not the page.** Inside a
  border every row spans the border's width, because the border's own sides are
  ink at both ends, so on the page grid nothing is a spike. The border strokes
  are peeled off and the interior analysed on its own.
- **Cut lines are found at pixel level, not on the grid.** On the grid, two
  rows of two-column text (a sentence on the left, a shop address on the
  right) spanned the interior exactly like a rule and were picked, while the
  real cut (a light grey band, the dashes, and the label's own top rule
  directly under them) filled four cells on the original PDF and five once
  macOS had re-paginated it to A4, and was missed as "a block, not a line".
  At pixel level the structural cut is one dark row that reaches across the
  interior, text is five or more rows deep, and the grey band is not dark at
  all. A candidate is a run of wide dark pixel rows no taller than
  `CUTLINE_MAX_THICK_FRAC` of the page.
- **Sides are scored on striped ink, not solid ink.** The grey banner is as
  solid as a barcode on an averaged grid, and made the instructions side look
  like the label (ratio 1.2, split refused). Counting ink edges inside each
  solid cell on the line-detection pixmap tells them apart: a barcode cell
  measured 27-45 edges, the banner 0-3. `STRIPE_EDGES` is the cutoff.
- **The side cut away must hold most of the ink inside the border.** A label's
  header rule is also a wide line with text above and barcodes below, and so
  is a rule between an address block and a barcode. Splitting there loses the
  carrier name or the addressee. On the DPD sheet the instructions are 66% of
  the ink; a header and address are around 15%. `BORDER_CUT_MIN_INK` is the
  share required. When nothing clears it, the whole border prints, which is
  what happened before and is the safe failure.
- **With several cuts passing, the one that cuts away the least wins.** If a
  sheet's label has a rule of its own, that is the choice that keeps the whole
  label.

The DPD dashes themselves are light grey and vanish at the line-detection
threshold; the structural cut that gets found is the label's own solid top rule
directly under them. Test against what the printer actually receives, not just
the original file: macOS re-paginates a US Letter PDF to A4 on the way through
AirPrint, and the grid-level version of this check passed on the original and
failed on the job.

### What the preview shows is not what prints

iOS and macOS render the print preview from the **source document, on the
client**, before the job is sent. The cropping happens in this backend
afterwards, so the preview always shows the uncropped original. That is inherent
to AirPrint and cannot be changed from the printer side. Use the settings page's
**Re-crop** button to see what will actually come out.

**Every threshold is a fraction of the page, never millimetres.** That is
deliberate: a phone screenshot is nominally 413x893mm, so mm-based thresholds
silently stop working on exactly the inputs you most want handled.

Pages stay vector throughout (`show_pdf_page` with a clip). Only a bare image
input is rasterised, and only to wrap it in a page.

Multi-page input becomes one label per page worth printing, capped by
`MAX_PAGES`. Blank pages are skipped, which is what those two-page carrier PDFs
need. Instruction pages (below `PAGE_SOLID_MIN` solid ink) are skipped when
another page in the job has a label on it.

### Fail-safe

Three tiers, so a job is never silently dropped:

1. crop and fit (normal)
2. on any exception: fit the original pages to the label, no detection
3. if even that fails: send the original file through unchanged

All three log to the CUPS error log with an `ERROR: labelcrop:` prefix.

## Install

Debian 12 (bookworm) on anything: a VM, an LXC container, a Raspberry Pi, a spare
box. Needs CUPS, Avahi and PyMuPDF, all from apt.

```bash
git clone https://github.com/alexjamesbrown/label-printer-proxy
cd label-printer-proxy
sudo ./setup.sh <printer-ip> [proxy-queue-name] [target-queue-name]
```

`setup.sh` is idempotent. Re-run it after changing `labelcrop` or the PPD.

It installs the backend, creates a hidden queue pointing at your real printer and
a shared AirPrint queue in front of it, reads the loaded media straight off the
printer, enables the cutter, publishes the Bonjour record, and starts the
settings page.

> **Debian 12, not 13.** Trixie ships cups-filters 2.x, which moved PPD handling
> into `libppd` and dropped the classic `urftopdf`. This design is PPD-based, so
> bookworm is the well-trodden path. Patches welcome if you get it working on
> trixie.

## Settings page

`http://<host>:8080`, restricted by source IP.

Shows printer state and the loaded roll, warns if the configured media no longer
matches what is actually in the printer, and lists recent jobs as before/after
thumbnails with a **Re-crop with current settings** button that re-runs detection
and shows the result **without printing**. There is no print path in the UI at
all.

Zero extra dependencies: Python's stdlib `http.server` plus the PyMuPDF the
backend already needs. One systemd unit, about 15MB resident, runs as `lp`
rather than root.

Set the allowlist in `/etc/default/labelui`:

```
LABELUI_PORT=8080
LABELUI_ALLOW=192.168.0.0/16,127.0.0.1/32
```

The default is loopback only. **If your printers live on an untrusted IoT VLAN,
leave that VLAN out of the allowlist**, or every smart plug and TV sharing it can
reconfigure your printer.

## Config

`/etc/labelproxy.conf`, simple `KEY=VALUE`. The authoritative list with comments
is the `DEFAULTS` dict at the top of `labelcrop`. Any key can be overridden for a
single run with `LABELPROXY_<KEY>` in the environment, which is how the tests
work.

| Key | Default | |
|---|---|---|
| `MEDIA` | `om_brother-label-103x164mm_103x164mm` | **must match the loaded roll** |
| `FINISHINGS` | `60` | cutter: 3 none, 11 trim, 60 after every label, 63 end of job |
| `LABEL_W_MM` / `LABEL_H_MM` | 103 / 164 | |
| `MARGIN_*_MM` | 1.54 / 3.03 | the printer's own unprintable edges |
| `MAX_PAGES` | 4 | stops a 30-page doc eating the roll |
| `PAGE_SOLID_MIN` | 0.005 | below this much solid ink a page is an instruction page, skipped if another page has a label |
| `UPSCALE` / `UPSCALE_MAX` | 1 / 0 | scale small content up to fill, uncapped |
| `BLOCK_SOLID_RATIO` / `BLOCK_SOLID_MIN` | 2.0 / 0.05 | how clearly a block must stand out to be taken as the label |
| `KEEP_JOBS` | 5 | in/out PDF pairs retained for debugging |

Changing the config needs no restart. The backend rereads it on every job.

Find your own values with:

```bash
ipptool -tv ipp://<printer>/ipp/print get-printer-attributes.test \
  | grep -E "media-ready|finishings-supported|media-col-ready"
```

## Testing without a printer

```bash
pip install pymupdf
python3 test_crop.py /tmp/testout
```

Generates eight representative inputs (bordered label on A4, label with browser
header and footer, full-page text document, two-page PDF with a blank page 2,
landscape label, PNG screenshot, instructions-then-label, instruction page then
a bordered sheet), runs the real detection and placement code, and writes PNG
previews with the detected crop boxed in red beside the output label pages.

| case | expected | rotate |
|---|---|---|
| bordered label on A4 | border | 0 |
| browser header + URL footer | border | 0 |
| full-page text doc | content | 0 |
| 2 pages, page 2 blank | border, 1 skipped | 0 |
| landscape label | border | **90** |
| PNG screenshot | border | 0 |
| instructions + cut line, label below | block | **90** |
| text page, then bordered sheet with cut line | border-block, 1 skipped | **90** |

Run this before deploying any detection change. It is far cheaper than feeding
the roll. CI runs it on 3.11, 3.12 and 3.13 and uploads the previews as
artifacts.

## Things that took a while to work out

Most of these are not in any documentation, and all of them look like faults when
you hit them.

### The printer's "Auto Cut" setting does nothing for AirPrint

On the QL-1110NWB, Printer Settings -> Device Settings -> Auto Cut only applies
to **P-touch Template** command mode. AirPrint and IPP jobs ignore it.

Over IPP the cutter is the `finishings` attribute. The printer's own
`finishings-default` was already `trim-after-pages`, but **CUPS creates
driverless queues with `finishings=3` (none)** and that wins, so labels come out
joined together. Two things fix it, both in `setup.sh`:

```bash
lpadmin -p <target> -o CutMedia=EndOfPage     # the PPD maps IPP 60 onto this
# and the backend passes -o finishings=60 on every forwarded job
```

`lpoptions -p <queue>` will still report `finishings=3`. That is a separate IPP
attribute default and does **not** gate the cutter. Confirm with
`lpoptions -p <queue> -l | grep CutMedia`, which should show `*EndOfPage`.

### The printer reports the wrong media default

The QL reports `media-default = 29x90mm` **whatever roll is actually loaded**, so
the media name has to be stated explicitly on every job or output comes out the
wrong size. `media-ready` is the honest field. `setup.sh` reads it off the printer
and pins the queue automatically.

### The queue's icon comes from IPP, not Bonjour

macOS and iOS draw the printer icon from the IPP **`printer-icons`** attribute,
which is an actual PNG fetched over HTTP. None of `usb_MFG`, `usb_MDL`, `kind`,
`ty` or `product` in the Bonjour TXT record affect it.

cupsd always advertises `http://<host>:631/icons/<queue>.png` and serves
`DocumentRoot/images/generic.png` for it. **Debian does not ship that file**, so
the URL 404s and the OS falls back to a generic glyph. Dropping a PNG there fixes
it, and `setup.sh` pulls your printer's own icon straight off the device:

```bash
curl -o /usr/share/cups/doc-root/images/generic.png \
  http://<printer>/ipp/printer-icons-512.png
```

Two dead ends, recorded so you do not repeat them:

- **`*APPrinterIconPath` in a PPD does nothing on Debian.** It is a macOS-CUPS
  keyword; Debian's cupsd contains no reference to it, nor to `cupsIcon`, nor any
  per-printer icon handling at all. Even a driverless queue 404s its own icon.
- **Putting the PNG at `doc-root/icons/<queue>.png` does nothing.** cupsd
  intercepts `/icons/` before DocumentRoot is consulted.

Because `generic.png` is the only lever, the icon is server-wide. That is usually
fine on a box dedicated to this.

### CUPS will not let you name a shared queue

CUPS advertises a shared queue as `"<description> @ <hostname>"` and offers no
override. `lpadmin -o printer-dns-sd-name=...` is accepted silently and ignored.

So the Bonjour record here is **published by hand** from `labelproxy.service`,
with `Browsing Off` in `cupsd.conf` so CUPS does not publish a competing
duplicate. That does not affect remote IPP access, which is governed by
`printer-is-shared` plus `Listen`. It also lets us set the `usb_MFG` / `usb_MDL` /
`kind` keys CUPS will not emit for a network queue.

> An Avahi service file is **XML**: a raw `<` in a TXT value (for example
> `PaperMax=<legal-A4`) silently invalidates the whole file and nothing is
> advertised at all. Escape it as `&lt;` and check `journalctl -u avahi-daemon`.

### mDNS reflectors have finite capacity, and overrunning one takes OTHER devices offline

If your printers and clients are on different VLANs, something is relaying mDNS
between them. Those relays have limits.

Adding this service to a VLAN knocked two unrelated printers offline for clients
on another VLAN, twice, while both printers were completely healthy:
`printer-state = idle`, answering IPP on demand, and still advertising correctly
*on their own VLAN*. The relay was carrying only part of the record set.

Default Avahi publishes on **lo + eth0 + IPv6** across **`_ipp` and `_ipps`**:
six registrations for one queue. Cutting it to one brought both printers straight
back. `setup.sh` now sets `allow-interfaces` and `use-ipv6=no`, and publishes a
single `_ipp` record.

**Any new mDNS responder on a reflected VLAN spends shared budget.** Adding a Pi,
a speaker, a print server or a home-automation box can evict something that has
worked for months, and the symptom shows up on a completely unrelated device.

### Diagnosing "printer offline" when the printer is fine

In this order, because two failures that look identical can have different causes:

1. **Probe the device**:
   `ipptool -tv ipp://<ip>/ipp/print get-printer-attributes.test`. A healthy
   printer that reads offline is a **discovery** problem, full stop.
2. **Compare vantage points.** Browse mDNS from the printer's own VLAN
   (`avahi-browse -rtp _ipp._tcp`) against the client's VLAN
   (`dns-sd -B _ipp._tcp`). **The difference between those two lists is the
   diagnosis.**
3. **Separate browse from resolve.** `dns-sd -L "<name>" _ipp._tcp local` can
   succeed while `dns-sd -B _ipp._tcp` returns nothing. Testing only one will
   mislead you.
4. Only then suspect whatever you changed last.

> **macOS has no `timeout`.** `timeout dns-sd -B ...` silently runs nothing and
> reads exactly like "nothing is advertised". Background the browse and kill it:
> ```bash
> dns-sd -B _ipp._tcp > out.txt 2>&1 & P=$!; sleep 10; kill $P
> ```

> A macOS queue binds to one exact Bonjour record **including service type**. A
> queue on `..._ipps._tcp` goes offline while the same printer is still arriving
> fine on `_ipp._tcp`.

**If you keep hitting this, add the queue by IP and skip discovery entirely:**

```bash
lpadmin -p MyLabelPrinter -E -m everywhere \
  -v ipp://<proxy-host>:631/printers/<queue-name>
```

No sudo needed on macOS, and nothing in that path touches mDNS. iOS still needs
AirPrint, so this fixes desktops rather than removing the dependency.

A queue added this way is bound to that address for good, so give the proxy host
a DHCP reservation or a static IP first. If the lease ever moves, the proxy
queue and every desktop queue pointing at it go offline together.

### Wire the printer, then switch its radio off

If the printer moves from Wi-Fi to Ethernet, the Wi-Fi radio stays on unless you
disable it in the printer's settings. A live radio can drop back into Wi-Fi
Direct and the printer then shows up a second time, by a path that does not
work. One printer, one network path: a lit Wi-Fi lamp on a wired printer is a
fault waiting to happen.

### The settings page runs as `lp`, and `/etc` is `0755 root:root`

So it can write `/etc/labelproxy.conf` (which `setup.sh` makes `0664 root:lp`) but
**cannot create any new file in that directory**: no temp file, no sibling
`.bak`. That is why the save is an in-place rewrite rather than the usual
write-temp-then-rename, and why the backup goes to `/var/lib/labelproxy`.

Testing as your own user in a writable directory will not catch this. Test as the
service user.

## Debugging

```bash
lpstat -t                            # both queues
lpstat -W completed -o               # job history
ls -la /var/lib/labelproxy/          # last N in/out PDF pairs
grep -a labelcrop /var/log/cups/error_log
avahi-browse -rt _ipp._tcp           # Bonjour advertisement
journalctl -u labelui                # settings page
```

The CUPS error log only carries `ERROR` and `WARNING` by default. For the
per-page `DEBUG` lines showing which detector fired and what box it chose:

```bash
cupsctl --debug-logging     # remember to turn it back off
cupsctl --no-debug-logging
```

Compare `*-in.pdf` against `*-out.pdf` in `/var/lib/labelproxy/` before touching
any threshold, and reproduce it in `test_crop.py` rather than on the roll.

## Files

| | |
|---|---|
| `labelcrop` | the CUPS backend. Also importable as a module, which is how the tests drive it |
| `labelui` | the settings page, stdlib only |
| `labelproxy.ppd` | minimal PDF-passthrough PPD carrying the AirPrint `URF` advertisement |
| `labelproxy.conf` | installed to `/etc/labelproxy.conf` on first setup only |
| `labelproxy.service` | **Avahi** record template (XML), not a systemd unit |
| `labelui.systemd` | the systemd unit, named `.systemd` so it is not confused with the above |
| `labelui.default` | installed to `/etc/default/labelui` on first setup only |
| `setup.sh` | packages, queues, cutter, icon, Bonjour, service. Idempotent |
| `test_crop.py` | offline harness, generates inputs and PNG previews |

## Licence

MIT. See [LICENSE](LICENSE).

The printer icon is **not** included: it is pulled from your own printer at
install time, because the manufacturer's product image is theirs rather than
mine to redistribute.
