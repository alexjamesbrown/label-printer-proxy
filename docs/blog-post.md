# I built an AirPrint printer that crops things for me

I sell a bit of stuff online. Every sale ends the same way: a carrier emails a
PDF, I open it, and there is a shipping label sitting in the corner of an A4
page. I have a Brother QL-1110NWB loaded with 103x164mm labels. Getting one onto
the other meant opening Preview, drawing a crop box by eye, picking the right
paper size, and printing. Every single time.

So I built a printer that does it for me. Not a script, not a watched folder: an
actual AirPrint printer that shows up on my iPhone. I send it anything, and a
sensibly cropped label comes out.

It is about 600 lines of Python and it has been reliable since the day it went
in. The interesting part was not the cropping. It was everything around it.

## The shape of it

A CUPS backend is just an executable. CUPS hands it a job on stdin or as a file
path, and whatever it does next is its business. So:

```
iPhone --AirPrint--> labelcrop queue --crop--> hidden queue --IPP--> printer
```

The proxy queue is shared and advertised. The real printer's queue is hidden, so
only one printer appears on the network. In between, a Python script opens the
PDF, decides what to keep, and forwards a new one.

## Deciding what to keep

This was the fun bit, and it is three rules deep.

**Most carrier labels are drawn inside a box.** So: render the page, scan for
long continuous runs of dark pixels, and take the outer extent of them. Easy.

Except a page frame is also a long dark line. And a cut guide. And the rule
under a heading. My first version happily cropped from a header rule downwards,
because the rule was the topmost "border line" on the page.

The fix was to stop treating lines as a set of coordinates and start asking
whether they form a rectangle. A box is only accepted if some single horizontal
line actually spans its full width, and some single vertical line spans its full
height. A header rule fails that immediately, because the label's left edge
starts well below it.

I got this wrong twice. My first attempt at validation was "does this box contain
most of the page's ink?", which sounds reasonable and is not: a genuine label
sitting in the corner of a busy page legitimately holds a minority of the ink. It
rejected a real label border on a screenshot. Geometry was the right test;
heuristics about ink volume were not.

**When there is no box, use the ink.** Bounding box of everything on the page.
Also easy, also wrong on first contact, because browser-printed labels come with
a page header and a URL footer and those are ink too.

The answer was a coarse grid and connected components. Downsample the page to
about 110 cells on the long edge, mark the inked ones, label the connected
regions, then throw away anything that is either tiny (a speck) or a thin strip
hugging the very top or bottom edge (a header, a URL, a page number). Bounding
box over what survives.

**When the content fills the page, do not crop at all.** If the detected region
covers more than 85% of the page, the page *is* the content. Fit it and move on.
That is what makes printing an actual document work.

### The bug that only shows up on screenshots

I wrote all those thresholds in millimetres, because the page is in millimetres.
Then I fed it a phone screenshot and it printed a postage stamp in the middle of
a label.

A 1170x2532 PNG, wrapped in a PDF at its natural size, is a page **413mm by
893mm**. My "ignore strips within 14mm of the edge" rule was looking at a sliver
roughly 1.5% of the way down a page three times taller than a person.

Everything is now a fraction of the page. Nothing in the detection code knows
what a millimetre is. The only millimetres left are the ones describing the
physical roll, which is the one place they mean something.

The lesson generalises: if your input can be an arbitrary synthetic page, any
absolute unit is a bug waiting for the right input.

## Then I met the printer

The cropping took an afternoon. The printing took considerably longer, because
the printer and CUPS each had opinions they had not written down.

### "Auto Cut" does not auto cut

Labels came out joined together in a long strip. The printer's web UI has an
Auto Cut setting. It was on.

It was on, and irrelevant: that setting lives under Command Mode: P-touch
Template, and applies only to P-touch Template jobs. AirPrint and IPP ignore it
completely.

Over IPP the cutter is the `finishings` attribute. And here is the part that took
a while: the printer's own `finishings-default` was *already* `trim-after-pages`.
It wanted to cut. But **CUPS creates driverless queues with `finishings=3`
(none)**, and an explicit attribute on the job beats the printer's default. CUPS
was actively telling it not to cut.

There is a lovely bit of misdirection in the fix, too. After setting it,
`lpoptions -p <queue>` still reports `finishings=3`, because that is a different
attribute default that does not gate the cutter. The honest check is
`lpoptions -p <queue> -l | grep CutMedia`. I briefly "fixed" it, saw
`finishings=3`, and concluded it had not worked.

### The printer lies about its own paper

The QL reports `media-default = 29x90mm` regardless of what roll is physically
loaded. If you trust it, everything prints at the wrong size. `media-ready` is
the field that tells the truth. The installer now reads that off the printer and
pins the queue to it.

### The icon

Cosmetic, and the single most time-consuming thing in the project.

My proxy showed up on the Mac as a generic laser printer, next to the real one
showing a proper product photo. I wanted them to match.

I set `usb_MFG` and `usb_MDL` in the Bonjour TXT record, copied verbatim from the
real printer. Nothing. I set `ty` and `product` to the exact same strings.
Nothing. I added `kind=label,roll`. Nothing. I added `*1284DeviceID` to the PPD
and recreated the queue so CUPS would re-read it. Nothing.

None of those are where the icon comes from. **macOS fetches an actual PNG over
HTTP**, from the IPP `printer-icons` attribute. The real printer serves its own
product photo out of its firmware. That is the whole mechanism.

And CUPS was already advertising an icon URL for my queue. It 404'd. cupsd serves
that URL from `DocumentRoot/images/generic.png`, and **Debian does not ship that
file**. One `curl` to put a PNG there and the icon appeared instantly.

Two dead ends worth saving someone: `*APPrinterIconPath` in a PPD is a macOS-CUPS
keyword that Debian's cupsd ignores entirely (the binary contains no reference to
it, nor any per-printer icon handling at all), and putting the file at
`doc-root/icons/<queue>.png` does nothing because cupsd intercepts `/icons/`
before DocumentRoot is consulted.

### CUPS will not let you name your own printer

The queue appeared as `Label Printer (auto-crop) @ labelproxy`. CUPS appends
`@ <hostname>` to every shared queue and provides no way to turn it off.
`lpadmin -o printer-dns-sd-name=...` is accepted silently and ignored.

The fix was to stop letting CUPS advertise at all (`Browsing Off`) and publish
the Bonjour record by hand as an Avahi service file. Which, pleasingly, also
solved the icon-adjacent problem of CUPS refusing to emit `usb_MFG` and friends
for a network queue, and let me cut the advertisement down to a single record.

That last part turned out to matter more than I expected.

## The part where I broke the printers

I put the container on the same VLAN as the printers. Shortly after, both
Brothers went **Offline** on my Mac. Not the new one. The two that had worked for
months.

They were fine. `ipptool` got `printer-state = idle, state-reasons = none` from
both. They were still advertising correctly on their own VLAN. Only the Mac, on a
different VLAN, could not see them.

My printers and my laptop are on different networks, so something has to relay
mDNS between them. In my case that is the router's mDNS reflector. It turns out
those have finite capacity, and when you overrun one it does not fail loudly: it
quietly stops relaying *some* records. It had evicted the two Brothers and kept
mine.

Default Avahi publishes on loopback, the LAN interface, and IPv6, across both
`_ipp` and `_ipps`. Six registrations for one queue. Cutting that to one, by
restricting the interface, disabling IPv6, and publishing a single hand-written
record, brought both printers straight back.

**This is the finding I would most want someone else to have.** Any new mDNS
responder on a reflected network spends a shared budget. Adding a Pi, a speaker,
a print server or a home-automation box can evict something that has worked for
months, and the symptom appears on a completely unrelated device. Nothing logs
it. Nothing warns you.

### Diagnosing it, and the two ways I fooled myself

It happened again two days later, and the cause was different, which is exactly
why a method matters more than a fix.

The method: **probe the device first** (a healthy printer that reads offline is a
discovery problem, full stop), then **compare vantage points** by browsing mDNS
from the printer's own network and from the client's, because the difference
between those two lists *is* the diagnosis.

The third step I only learned the second time: **separate browse from resolve.**
`dns-sd -L "Label Printer" _ipp._tcp local` returned the full record, complete
with TXT, while `dns-sd -B _ipp._tcp` returned nothing at all. The record was
present and resolvable. Only enumeration was broken. If I had tested either one
alone I would have drawn the opposite conclusion.

And the embarrassing one: macOS has no `timeout` command. I spent a round of
diagnosis confidently reporting "the Mac sees no printers at all", which was
true only in the sense that `timeout dns-sd -B _ipp._tcp` had run nothing
whatsoever. A command that does not exist and a network that advertises nothing
look identical if you only read stdout.

In the end I sidestepped the whole thing for my desktop by adding the queues by
IP (`ipp://192.168.x.x/ipp/print`) instead of by Bonjour. Nothing in that path
touches discovery, so a reflector problem cannot take them offline. iOS still
needs AirPrint, so it is a fix for desktops rather than a cure.

## A small web UI, and one more lesson

There are about 25 settings. Editing them meant SSH-ing in to a config file, and
seeing why something cropped oddly meant copying PDFs off the box.

So: a settings page. Python's stdlib `http.server`, no framework, no database, no
build step, about 15MB resident. It shows printer state, warns if the configured
media no longer matches the roll actually loaded, and lists recent jobs as
before/after thumbnails with a **Re-crop with current settings** button that
re-runs detection and shows you the result without printing anything. Tuning a
threshold against a real job that went wrong, without feeding the roll, is the
feature I use most.

It runs as `lp` rather than root, which produced the last good lesson. The save
silently failed on the first deploy. `/etc` is `0755 root:root`, so the service
could write `/etc/labelproxy.conf` (group-writable, deliberately) but could not
**create any new file in that directory**: no temp file, no sibling `.bak`. The
usual write-temp-then-rename is simply unavailable to a non-root user writing
into `/etc`.

My local test had run as me, in a writable directory, and passed perfectly.

## Was it worth it

The cropping is maybe 150 lines of the 600. The rest is the three-tier fail-safe
(crop, then fit-only, then pass the original through untouched, because a printer
that eats jobs is worse than one that prints them badly), the config plumbing,
and the accumulated scar tissue above.

What I actually got: I forward a carrier email to the printer from my phone and a
label comes out, cut, the right way up, filling the roll. No laptop. The
screenshot case gets used more than I expected, too: a receipt, a booking
reference, a QR code.

And six test cases that run offline in under a second and render PNG previews, so
I can change the detection and see what happens to a bordered label, a
browser-printed one, a full-page document, a blank second page, a landscape
label and a phone screenshot, all without feeding a single label through the
printer. Given how many labels the manual debugging ate, that harness paid for
itself several times over.

The code is at [github.com/alexjamesbrown/label-printer-proxy](https://github.com/alexjamesbrown/label-printer-proxy),
MIT licensed. The README carries all the gotchas above in a form you can grep,
which is really why I wrote any of this down.
