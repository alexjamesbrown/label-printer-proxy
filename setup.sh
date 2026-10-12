#!/usr/bin/env bash
# Label proxy: an AirPrint queue that accepts anything, crops it sensibly and
# forwards it to the real Brother label printer.
#
#   sudo ./setup.sh <brother-ip> [proxy-queue-name] [target-queue-name]
#
# Idempotent: safe to re-run after editing labelcrop or the PPD.
set -euo pipefail

BROTHER_IP="${1:?usage: sudo ./setup.sh <brother-ip> [proxy-queue] [target-queue]}"
PROXY="${2:-Label_Printer_Auto}"
TARGET="${3:-Brother_QL}"
PROXY_DESC="Label Printer (auto-crop)"
# Bonjour service name as iOS/macOS will show it. Kept free of the " @ host"
# suffix CUPS would otherwise append - see the Avahi section below.
# ⚠️ Avoid parentheses. "Label Printer (auto-crop)" escapes to
# Label\032Printer\032\040auto-crop\041 in DNS-SD, and an iPhone rendered that
# entry with a BLANK name while plainly-named printers on the same VLAN were
# fine. Spaces (\032) are harmless; brackets were not.
BONJOUR_NAME="${BONJOUR_NAME:-Auto-Crop Label Printer}"
DIR="$(cd "$(dirname "$0")" && pwd)"
CONF=/etc/labelproxy.conf

[ "$(id -u)" -eq 0 ] || { echo "run as root" >&2; exit 1; }

say() { printf '\n== %s\n' "$*"; }

say "packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y --no-install-recommends \
  cups cups-filters cups-ipp-utils avahi-daemon avahi-utils python3
# PyMuPDF is python3-fitz on bookworm, python3-pymupdf on newer.
apt-get install -y --no-install-recommends python3-pymupdf \
  || apt-get install -y --no-install-recommends python3-fitz

say "files"
install -m 0755 "$DIR/labelcrop" /usr/lib/cups/backend/labelcrop
install -m 0644 "$DIR/labelproxy.ppd" /usr/share/ppd/labelproxy.ppd
# The icon macOS/iOS actually draws comes from the IPP printer-icons attribute,
# not from any Bonjour TXT key. cupsd always advertises
# http://<host>:631/icons/<queue>.png and serves DocumentRoot/images/generic.png
# for it - a file Debian does not ship, so that URL 404s out of the box and the
# OS falls back to a generic printer glyph.
#
# Debian's cupsd has NO per-printer icon support (no APPrinterIconPath, no
# cupsIcon - the binary contains neither), so generic.png is the only lever.
# That makes it server-wide, which is fine here: the only other queue is the
# hidden Brother_QL, i.e. the same physical printer.
ICON=/usr/share/cups/doc-root/images/generic.png
if [ -f "$DIR/printer-icon.png" ]; then
  install -m 0644 "$DIR/printer-icon.png" "$ICON"
else
  # Fall back to pulling the printer's own icon straight off the device.
  curl -fsS -m 10 -o "$ICON" "http://$BROTHER_IP/ipp/printer-icons-512.png" \
    || echo "warning: no icon installed, macOS/iOS will show a generic printer"
fi
install -d -m 0755 -o lp -g lp /var/lib/labelproxy
if [ -f "$CONF" ]; then
  echo "keeping existing $CONF"
else
  install -m 0644 "$DIR/labelproxy.conf" "$CONF"
fi

# The backend runs as lp and shells out to lp(1); make sure it can.
python3 -c 'import pymupdf' 2>/dev/null || python3 -c 'import fitz' \
  || { echo "PyMuPDF not importable, aborting" >&2; exit 1; }

say "avahi"
# Only advertise on the LAN interface, IPv4 only. By default avahi publishes on
# lo + eth0 + IPv6, i.e. six registrations for one queue - and every one of them
# has to be carried across VLANs by the UniFi mDNS reflector, which evicts other
# devices' records when pushed too hard. That is not theoretical: it silently
# took both Brother printers offline on the Trusted VLAN until this was trimmed.
IFACE="$(ip -o -4 route show default | awk '{print $5}' | head -1)"
sed -i -E "s/^#?use-ipv6=.*/use-ipv6=no/" /etc/avahi/avahi-daemon.conf
sed -i -E "s/^#?allow-interfaces=.*/allow-interfaces=${IFACE:-eth0}/" /etc/avahi/avahi-daemon.conf
grep -q "^use-ipv6=" /etc/avahi/avahi-daemon.conf \
  || sed -i "/^\[server\]/a use-ipv6=no" /etc/avahi/avahi-daemon.conf
grep -q "^allow-interfaces=" /etc/avahi/avahi-daemon.conf \
  || sed -i "/^\[server\]/a allow-interfaces=${IFACE:-eth0}" /etc/avahi/avahi-daemon.conf

say "cups + avahi"
systemctl enable --now cups avahi-daemon
# Share queues on the LAN and answer to any hostname, so iOS can reach it by
# whatever name mDNS hands out.
cupsctl --share-printers --remote-any
grep -q '^ServerAlias \*' /etc/cups/cupsd.conf || echo 'ServerAlias *' >> /etc/cups/cupsd.conf
# CUPS advertises a shared queue as "<description> @ <hostname>" and offers no
# way to override that name, nor will it emit usb_MFG/usb_MDL/kind for a network
# queue. We publish the record ourselves instead (below), so stop CUPS doing it
# and creating a duplicate. This does NOT affect remote IPP access, which is
# governed by printer-is-shared plus Listen.
sed -i "s/^Browsing On/Browsing Off/" /etc/cups/cupsd.conf
grep -q "^Browsing" /etc/cups/cupsd.conf || echo "Browsing Off" >> /etc/cups/cupsd.conf
systemctl restart cups
sleep 2

say "target queue: $TARGET -> $BROTHER_IP"
lpadmin -p "$TARGET" -E -v "ipp://$BROTHER_IP/ipp/print" -m everywhere \
  || lpadmin -p "$TARGET" -E -v "ipp://$BROTHER_IP/ipp/print" \
             -m "driverless:ipp://$BROTHER_IP/ipp/print"
# Hidden: only the proxy should appear to clients.
lpadmin -p "$TARGET" -o printer-is-shared=false

# The QL reports media-default=29x90mm regardless of the loaded roll, so pin the
# queue default to whatever is actually in the printer right now.
READY="$(ipptool -tv "ipp://$BROTHER_IP/ipp/print" get-printer-attributes.test 2>/dev/null \
  | sed -n 's/.*media-ready (keyword) = \([^,]*\).*/\1/p' | head -1)"
if [ -n "$READY" ]; then
  echo "printer reports media-ready: $READY"
  lpoptions -p "$TARGET" -o "media=$READY" >/dev/null
  if ! grep -q "^MEDIA=$READY\$" "$CONF" 2>/dev/null; then
    echo "note: $CONF has MEDIA=$(sed -n 's/^MEDIA=//p' "$CONF" | head -1)"
    echo "      printer currently has $READY - update if they differ"
  fi
else
  echo "warning: could not read media-ready from $BROTHER_IP, leaving queue default"
fi

# Enable the cutter on the target queue. CUPS creates driverless queues with
# finishings=3 (none) even when the printer's own finishings-default is
# trim-after-pages, so labels print joined together until this is set. The PPD
# maps IPP 60/trim-after-pages onto the Brother's CutMedia=EndOfPage.
if lpoptions -p "$TARGET" -l 2>/dev/null | grep -q "^CutMedia"; then
  lpadmin -p "$TARGET" -o CutMedia=EndOfPage
  # Verify rather than assume: lpadmin reports success either way.
  if lpoptions -p "$TARGET" -l 2>/dev/null | grep -q "CutMedia.*\*EndOfPage"; then
    echo "cutter: CutMedia=EndOfPage - cuts after every label"
  else
    echo "WARNING: CutMedia did not take; check $TARGET's PPD"
  fi
else
  echo "note: $TARGET exposes no CutMedia option, cutter left to the printer"
fi

say "proxy queue: $PROXY"
lpadmin -p "$PROXY" -E -v "labelcrop:/$TARGET" -P /usr/share/ppd/labelproxy.ppd \
  -D "$PROXY_DESC" -L "" -o printer-is-shared=true
cupsenable "$PROXY"
cupsaccept "$PROXY"

say "bonjour advertisement"
# Hand-written so we control the service name and can carry the identity keys
# that make macOS/iOS draw the label-printer icon.
IP="$(ip -o -4 addr show "${IFACE:-eth0}" | awk '{print $4}' | cut -d/ -f1 | head -1)"
UUID="$(sed -n "/<Printer $PROXY>/,/<\/Printer>/s|^UUID urn:uuid:||p" /etc/cups/printers.conf | head -1)"
UUID="${UUID:-$(cat /proc/sys/kernel/random/uuid)}"
sed -e "s|@@NAME@@|$BONJOUR_NAME|g" \
    -e "s|@@QUEUE@@|$PROXY|g" \
    -e "s|@@HOST@@|$IP|g" \
    -e "s|@@UUID@@|$UUID|g" \
    "$DIR/labelproxy.service" > /etc/avahi/services/labelproxy.service
chmod 0644 /etc/avahi/services/labelproxy.service
systemctl restart avahi-daemon cups
sleep 5

say "settings page"
# Separate process from the print path: if it dies, printing is unaffected.
# Runs as lp (which already owns KEEP_DIR) and needs group-write on the config.
install -m 0755 "$DIR/labelui" /usr/local/bin/labelui
install -m 0644 "$DIR/labelui.systemd" /etc/systemd/system/labelui.service
if [ -f /etc/default/labelui ]; then
  echo "keeping existing /etc/default/labelui"
else
  install -m 0644 "$DIR/labelui.default" /etc/default/labelui
fi
chgrp lp "$CONF" && chmod 0664 "$CONF"
systemctl daemon-reload
systemctl enable --now labelui
sleep 2
if systemctl is-active --quiet labelui; then
  echo "settings page: http://${IP:-$(hostname -I | awk '{print $1}')}:$(
    . /etc/default/labelui 2>/dev/null; echo "${LABELUI_PORT:-8080}")"
  echo "  allowed from: $(. /etc/default/labelui 2>/dev/null; echo "$LABELUI_ALLOW")"
else
  echo "WARNING: labelui failed to start - journalctl -u labelui"
fi

say "result"
lpstat -t | sed -n '1,20p'
echo
echo "Bonjour advertisement (URF= is what makes iOS treat this as AirPrint):"
# No 2>/dev/null here: a missing avahi-browse must not masquerade as "not
# advertised yet". That exact silent failure cost a debugging round.
TXT="$(avahi-browse -rtp _ipp._tcp | grep -i "$PROXY" | grep '^=' | head -1)"
if [ -z "$TXT" ]; then
  echo "  WARNING: $PROXY is not being advertised. Check avahi-daemon and"
  echo "           'Browsing On' / 'BrowseLocalProtocols dnssd' in cupsd.conf."
else
  printf '%s\n' "$TXT" | sed 's/.*txt=//' | tr '"' '\n' \
    | grep -oE '^(URF|pdl|rp|mopria-certified|Copies)=.*' | sort | sed 's/^/  /'
  printf '%s' "$TXT" | grep -q 'URF=' \
    || echo "  WARNING: no URF= in the TXT record - iOS will not list this as AirPrint"
fi
echo
echo "Done."
echo "  print:  lp -d $PROXY file.pdf"
echo "  config: $CONF"
echo "  debug:  /var/lib/labelproxy/   and  journalctl -u cups  /  /var/log/cups/error_log"
