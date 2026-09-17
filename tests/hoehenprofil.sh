#!/usr/bin/env bash
# hoehenprofil.sh — prüft die Höhenprofil-Auswertung des Karten-Frontends.
#
# Die Funktion `hoehenAbschnitt` steckt im großen Inline-Skript von index.html.
# Sie braucht kein DOM (sie baut nur Zeichenketten), lässt sich also
# herausschneiden und in node prüfen. Das Herausschneiden statt Kopieren ist der
# Punkt: eine kopierte Fassung im Test würde irgendwann von der echten abweichen
# und der Test bliebe grün, während das Frontend etwas anderes tut.
#
#   ./tests/hoehenprofil.sh
set -euo pipefail

WURZEL="$(cd "$(dirname "$0")/.." && pwd)"
QUELLE="$WURZEL/static/index.html"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

command -v node >/dev/null || { echo "node fehlt" >&2; exit 1; }

python3 - "$QUELLE" "$TMP/funktion.js" <<'PY'
import re, sys
quelle, ziel = sys.argv[1], sys.argv[2]
text = open(quelle).read()
treffer = re.search(r'(      function hoehenAbschnitt\(beine\) \{.*?\n      \})', text, re.S)
if not treffer:
    sys.exit("hoehenAbschnitt() nicht in index.html gefunden — umbenannt oder umgebaut?")
open(ziel, "w").write(treffer.group(1))
PY

cat "$TMP/funktion.js" > "$TMP/pruef.js"
cat >> "$TMP/pruef.js" <<'JS'

let alle = true;
function pruefe(name, bedingung, zusatz) {
  console.log(`${bedingung ? 'OK  ' : 'FEHL'} ${name}${zusatz ? ': ' + zusatz : ''}`);
  alle = alle && bedingung;
}
const werte = (s) => s === '' ? null
  : s.match(/Anstieg <b>(\d+) m<\/b> . Abstieg <b>(\d+) m/).slice(1, 3).map(Number);
const ySpanne = (s) => {
  const ys = s.match(/polyline points="([^"]+)"/)[1]
    .split(' ').map(p => parseFloat(p.split(',')[1]));
  return Math.max(...ys) - Math.min(...ys);
};

// Testprofil wie eine Fahrt Heiligenhaus -> Tal -> Solingen, mit Messrauschen.
// Analytisch nachgerechnet aus h(t) = 170 - 70*sin(pi*t) + 50*t:
// Tiefpunkt bei t=0,427 -> 123,2 m, also 46,8 m Abstieg und 96,8 m Anstieg.
const echt = [];
for (let i = 0; i < 350; i++) {
  const t = i / 349;
  echt.push(170 - 70 * Math.sin(t * Math.PI) + 50 * t + Math.sin(i * 2.7) * 0.3);
}
const [auf, ab] = werte(hoehenAbschnitt([{ elevation: echt }]));
pruefe('Anstieg trifft die Rechnung (96,8 m)', Math.abs(auf - 96.8) < 5, `${auf} m`);
pruefe('Abstieg trifft die Rechnung (46,8 m)', Math.abs(ab - 46.8) < 5, `${ab} m`);

let rohAuf = 0;
for (let i = 1; i < echt.length; i++) if (echt[i] > echt[i-1]) rohAuf += echt[i] - echt[i-1];
pruefe('Glaettung nimmt die Zappelei raus', rohAuf > auf * 1.1,
       `roh ${Math.round(rohAuf)} m -> geglaettet ${auf} m`);

// ★ Der wichtigste Fall: ohne Hoehendaten antwortet Valhalla mit lauter Nullen,
//   nicht mit einem fehlenden Feld. Ein flaches Nullprofil waere eine
//   Falschaussage, keine fehlende Angabe.
pruefe('lauter Nullen zeigen nichts', hoehenAbschnitt([{ elevation: new Array(350).fill(0) }]) === '');
pruefe('fehlendes Feld zeigt nichts', hoehenAbschnitt([{ shape: 'x' }]) === '');
pruefe('zu kurze Reihe zeigt nichts', hoehenAbschnitt([{ elevation: [100, 105] }]) === '');
pruefe('NaN und null werden verworfen',
       hoehenAbschnitt([{ elevation: [100, NaN, 120, null, 140, 160] }]) !== '');

const lang = [];
for (let i = 0; i < 2600; i++) lang.push(100 + 80 * Math.sin(i / 200));
const punkte = hoehenAbschnitt([{ elevation: lang }])
  .match(/polyline points="([^"]+)"/)[1].split(' ').length;
pruefe('2600 Rohwerte -> Deckel 400 Stuetzpunkte', punkte <= 400, `${punkte} Punkte`);

// ★ Die Steigungssumme darf NICHT an der Streckenlaenge haengen. Sie wird
//   deshalb ueber alle Punkte gerechnet und erst danach fuers Bild ausgeduennt.
//   Andersherum kaeme eine lange Tour mit weniger Hoehenmetern je Kilometer
//   heraus als dieselbe Strecke einzeln berechnet — gemessen 295 statt 343 m.
const welle = [];
for (let i = 0; i < 100; i++) welle.push(200 + 40 * Math.sin(i / 16));
const kurz = welle;                                    // 100 Punkte
const zehnfach = [].concat(...Array.from({ length: 10 }, () => welle));  // 1000
const [aufKurz] = werte(hoehenAbschnitt([{ elevation: kurz }]));
const [aufLang] = werte(hoehenAbschnitt([{ elevation: zehnfach }]));
const verhaeltnis = aufLang / aufKurz;
pruefe('zehnfache Strecke -> rund zehnfache Steigung', verhaeltnis > 8.5,
       `${aufKurz} m -> ${aufLang} m (Faktor ${verhaeltnis.toFixed(1)})`);

// Die 10-m-Mindestskala darf aus einer Bodenwelle kein Gebirge machen …
const flach = [];
for (let i = 0; i < 100; i++) flach.push(50 + Math.sin(i / 10) * 1.2);
pruefe('2,4-m-Welle bleibt flach im Bild',
       ySpanne(hoehenAbschnitt([{ elevation: flach }])) < 16);
// … ein echtes Profil soll das Bild dagegen ausnutzen.
pruefe('97-m-Profil nutzt das Bild aus',
       ySpanne(hoehenAbschnitt([{ elevation: echt }])) > 30);

process.exit(alle ? 0 : 1);
JS

node "$TMP/pruef.js"
echo
echo "== alle Testfaelle bestanden =="
