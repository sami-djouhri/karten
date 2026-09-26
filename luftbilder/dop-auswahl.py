#!/usr/bin/env python3
"""Waehlt aus dem NRW-DOP-Katalog die Kacheln fuer das Karten-Portal aus.

Drei Bereiche, aus denen spaeter drei Kachelsaetze mit verschiedener Schaerfe
werden — grob ueber die Flaeche, scharf da, wo man wirklich hinschaut:

    aussen     26x35 km  Heiligenhaus/Velbert bis Solingen/Remscheid, bis z19
    innen       8x8  km  Heiligenhaus,   bis z20
    solingen    8x8  km  Solingen-Mitte, bis z20

Die scharfen Inseln liegen INNERHALB der Gesamtflaeche und werden zuerst
vergeben: eine Kachel traegt genau eine Markierung, und `aussen` ist der Rest.
Der Bau liest die Datei dreimal mit verschiedenen Filtern (`alle` fuer z19).

Kein pyproj auf host, deshalb die UTM-Vorwaertsprojektion von Hand (Karney/
Snyder, Standardreihen). Fuer die Kachelwahl reicht das mit Abstand: eine Kachel
ist 1000 m breit, die Reihen sind auf Zentimeter genau. ETRS89 gegen WGS84 macht
in NRW unter einem Meter aus — ebenfalls belanglos hier.

★ Der Katalog wird selbst geholt und neben dem Skript abgelegt. Frueher wurde er
  unter /tmp erwartet — nach einem Neustart des LXC war er weg und das Skript
  brach mit FileNotFoundError ab, obwohl nichts kaputt war.
"""
import json
import math
import os
import sys
import urllib.request

A = 6378137.0            # GRS80/WGS84 grosse Halbachse
F = 1 / 298.257222101    # GRS80-Abplattung (ETRS89); WGS84 weicht erst in der 9. Stelle ab
K0 = 0.9996
ZONE = 32
LON0 = math.radians(6 * ZONE - 183)   # Zone 32 -> 9 Grad Ost

KATALOG_URL = ("https://www.opengeodata.nrw.de/produkte/geobasis/lusat/akt/"
               "dop/dop_jp2_f10/index.json")
KATALOG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dop_index.json")


def wgs84_nach_utm32(lat_grad, lon_grad):
    lat, lon = math.radians(lat_grad), math.radians(lon_grad)
    e2 = F * (2 - F)
    ep2 = e2 / (1 - e2)
    N = A / math.sqrt(1 - e2 * math.sin(lat) ** 2)
    T = math.tan(lat) ** 2
    C = ep2 * math.cos(lat) ** 2
    Adist = math.cos(lat) * (lon - LON0)

    M = A * ((1 - e2 / 4 - 3 * e2**2 / 64 - 5 * e2**3 / 256) * lat
             - (3 * e2 / 8 + 3 * e2**2 / 32 + 45 * e2**3 / 1024) * math.sin(2 * lat)
             + (15 * e2**2 / 256 + 45 * e2**3 / 1024) * math.sin(4 * lat)
             - (35 * e2**3 / 3072) * math.sin(6 * lat))

    easting = K0 * N * (Adist + (1 - T + C) * Adist**3 / 6
                        + (5 - 18 * T + T**2 + 72 * C - 58 * ep2) * Adist**5 / 120) + 500000.0
    northing = K0 * (M + N * math.tan(lat) * (Adist**2 / 2
                     + (5 - T + 9 * C + 4 * C**2) * Adist**4 / 24
                     + (61 - 58 * T + T**2 + 600 * C - 330 * ep2) * Adist**6 / 720))
    return easting, northing


def kachel(lat, lon):
    """Kachelindex (Ost/Nord in Kilometern) — die Kachel deckt [km, km+1)."""
    e, n = wgs84_nach_utm32(lat, lon)
    return int(e // 1000), int(n // 1000)


# Kontrollpunkt: Koelner Dom. Amtlich rund 356_000 / 5_645_000 in UTM32.
pe, pn = wgs84_nach_utm32(50.94130, 6.95828)
print(f"Probe Koelner Dom: E {pe:,.0f}  N {pn:,.0f}", file=sys.stderr)
assert 355000 < pe < 357500 and 5644000 < pn < 5646500, "UTM-Rechnung stimmt nicht"

HEILIGENHAUS = (51.32690, 7.01690)
SOLINGEN = (51.17140, 7.08460)

# Gesamtflaeche als halboffener Kachelkasten [x0, x1) x [y0, y1) in UTM32-km.
# Reicht im Norden ueber Heiligenhaus/Velbert, im Sueden unter Solingen durch,
# im Osten bis Remscheid und Wuppertal-Sued, im Westen ueber Erkrath/Hilden
# hinaus bis westlich von Duesseldorf (Stadtgebiet E 339..351, Zentrum 345).
GESAMT = (338, 5663, 376, 5698)     # 38 x 35 km = 1330 Kacheln
SCHARF = 4                          # Halbe Kantenlaenge der Scharf-Inseln in km


def kasten_um(ort, radius):
    x, y = kachel(*ort)
    return (x - radius, y - radius, x + radius, y + radius)


# Reihenfolge ist bedeutsam: die scharfen Inseln zuerst, damit sie ihre
# Markierung behalten und nicht von `aussen` ueberschrieben werden.
BEREICHE = [
    ("innen", kasten_um(HEILIGENHAUS, SCHARF)),
    ("solingen", kasten_um(SOLINGEN, SCHARF)),
    ("aussen", GESAMT),
]

for name, (x0, y0, x1, y1) in BEREICHE:
    print(f"{name:>9}: E {x0}..{x1} N {y0}..{y1}  "
          f"({x1-x0} x {y1-y0} km = {(x1-x0)*(y1-y0)} Kacheln)", file=sys.stderr)

# --- Katalog besorgen --------------------------------------------------------
if not os.path.exists(KATALOG):
    print(f"Katalog fehlt, hole {KATALOG_URL}", file=sys.stderr)
    with urllib.request.urlopen(KATALOG_URL, timeout=120) as antwort:
        roh = antwort.read()
    # Erst vollstaendig lesen, dann schreiben: ein Abbruch mitten im Download
    # darf keine halbe Datei hinterlassen, die beim naechsten Lauf als gueltig gilt.
    with open(KATALOG + ".tmp", "wb") as fh:
        fh.write(roh)
    os.replace(KATALOG + ".tmp", KATALOG)
    print(f"   {len(roh)/1e6:.1f} MB nach {KATALOG}", file=sys.stderr)

with open(KATALOG) as fh:
    daten = json.load(fh)

katalog = {}
for satz in daten.get("datasets", []):
    for eintrag in satz.get("files", []):
        # dop10rgbi_32_361_5688_1_nw_2025.jp2
        teile = eintrag["name"].split("_")
        if len(teile) < 5 or not eintrag["name"].endswith(".jp2"):
            continue
        katalog[(int(teile[2]), int(teile[3]))] = (eintrag["name"], int(eintrag["size"]))

print(f"Katalog: {len(katalog):,} Kacheln", file=sys.stderr)
if not katalog:
    sys.exit("FEHLER: Katalog leer — hat sich das Format geaendert?")

# --- Auswahl -----------------------------------------------------------------
gewaehlt = {}
for name, (x0, y0, x1, y1) in BEREICHE:
    treffer, fehlend = [], 0
    for x in range(x0, x1):
        for y in range(y0, y1):
            eintrag = katalog.get((x, y))
            if eintrag is None:
                fehlend += 1          # ausserhalb NRW oder Luecke im Katalog
                continue
            treffer.append(eintrag)
            gewaehlt.setdefault((x, y), (eintrag, name))
    byte = sum(g for _, g in treffer)
    print(f"{name:>9}: {len(treffer):>4} Kacheln, {byte/1e9:6.1f} GB, fehlend {fehlend:>3}",
          file=sys.stderr)

gesamt = sum(g for (_, g), _ in gewaehlt.values())
verteilung = {}
for _, name in gewaehlt.values():
    verteilung[name] = verteilung.get(name, 0) + 1
print(f"\nzusammen: {len(gewaehlt):,} Kacheln, {gesamt/1e9:.1f} GB Quelldaten", file=sys.stderr)
print(f"  Markierungen: {verteilung}", file=sys.stderr)

for (x, y), ((name, groesse), bereich) in sorted(gewaehlt.items()):
    print(f"{bereich}\t{name}\t{groesse}")
