#!/usr/bin/env bash
# Prueft, dass das grosse Inline-Skript in static/index.html ueberhaupt parst,
# und dass style.json gueltiges JSON ist.
#
# Warum das noetig ist: das Frontend hat bewusst kein Bausystem — eine Datei,
# 1800 Zeilen Skript, direkt ausgeliefert. Ein Tippfehler faellt damit erst im
# Browser auf, und zwar als komplett leere Karte. Diese zwei Sekunden fangen ihn
# vorher ab.
set -euo pipefail
cd "$(dirname "$0")/.."

tmp=$(mktemp -d); trap 'rm -rf "$tmp"' EXIT

python3 - "$tmp/inline.js" <<'PYEOF'
import re, sys
h = open("static/index.html").read()
teile = re.findall(r"<script>(.*?)</script>", h, re.S)
if not teile:
    print("kein Inline-Skript gefunden", file=sys.stderr); raise SystemExit(1)
js = max(teile, key=len)
open(sys.argv[1], "w").write(js)
print(f"Inline-Skript: {len(js)} Zeichen, {js.count(chr(10))} Zeilen")
PYEOF

node --check "$tmp/inline.js" && echo "  Skript parst"
python3 -c "import json; s=json.load(open('static/style.json')); print(f'  style.json gueltig, {len(s[\"layers\"])} Ebenen')"
python3 -c "import json; json.load(open('static/manifest.webmanifest')); print('  manifest gueltig')"

# Jede Ebene im Stil muss eine Quelle haben, die es auch gibt — ein Tippfehler
# im Quellnamen laesst MapLibre die Ebene stillschweigend weglassen.
python3 - <<'PYEOF'
import json, sys
s = json.load(open("static/style.json"))
quellen = set(s["sources"])
fehler = [l["id"] for l in s["layers"] if l.get("source") and l["source"] not in quellen]
if fehler:
    print("  Ebenen mit unbekannter Quelle: " + ", ".join(fehler), file=sys.stderr)
    sys.exit(1)
print(f"  alle Ebenen zeigen auf bekannte Quellen ({', '.join(sorted(quellen))})")
PYEOF
echo "ok"
