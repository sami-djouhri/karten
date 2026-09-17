#!/usr/bin/env python3
"""Zaehlt die POI-Arten, die in den Kacheln WIRKLICH stehen, und haelt den Stil dagegen.

Warum das noetig ist: `style.json` filtert die POI-Ebenen ueber eine aufgezaehlte
Liste von `kind`-Werten. Eine solche Liste hat zwei stille Fehlerarten, und beide
sieht man der Karte nicht an:

  * ein Wert steht im Stil, kommt in den Daten aber gar nicht vor (Phantom):
    man glaubt, Universitaeten gefiltert zu haben, und filtert einen Tippfehler;
  * ein Wert kommt haeufig vor, steht aber in keiner Liste. Er faellt damit in
    die Restklasse und erscheint erst spaet, ohne dass das je entschieden wurde.

Das Werkzeug liest deshalb die Rohkacheln statt der Doku. Protomaps liefert
keine Werteliste in den Metadaten (geprueft 2026-09-06: `vector_layers` nennt
nur `kind: String`), also wird gezaehlt.

Aufruf (auf node1, dort liegt das Archiv):
    tests/poi-arten.py                      # Vorgabe-Staedte, Zoom 14
    tests/poi-arten.py --zoom 13 --umkreis 2
    tests/poi-arten.py --nur-abgleich       # nur Stil gegen Daten, kurze Ausgabe

Rueckgabewert 1, wenn der Stil auf Arten filtert, die in der Stichprobe nicht
vorkommen. Das ist ein Hinweis, kein Beweis: eine Stichprobe kann eine seltene
Art verfehlen. Deshalb nennt die Ausgabe immer, wie breit gemessen wurde.
"""
import argparse
import gzip
import json
import math
import re
import subprocess
import sys
from collections import Counter, defaultdict
from pathlib import Path

ARCHIV = "/home/user/knowledge-vault/maps/de-nl.pmtiles"
PMTILES = "/home/user/bin/pmtiles"
STIL = Path(__file__).resolve().parent.parent / "static" / "style.json"

# Stichprobe: Orte mit unterschiedlichem Charakter, damit die Zaehlung nicht die
# Eigenart einer einzigen Stadt abbildet. Heiligenhaus ist die Heimatregion,
# die uebrigen tragen Hochschulen, Kliniken und Innenstadtdichte bei.
ORTE = {
    "heiligenhaus": (51.3269, 7.0169),
    "wuppertal-uni": (51.2465, 7.1495),
    "essen-uni": (51.4680, 7.0060),
    "duesseldorf-uni": (51.1890, 6.7940),
    "koeln-innenstadt": (50.9375, 6.9603),
    "duisburg": (51.4344, 6.7623),
}


def kachel(lat, lon, z):
    """lat/lon zu (x, y) im Slippy-Map-Schema."""
    n = 2 ** z
    x = int((lon + 180.0) / 360.0 * n)
    rad = math.radians(lat)
    y = int((1.0 - math.asinh(math.tan(rad)) / math.pi) / 2.0 * n)
    return x, y


# --- minimaler Protobuf-Leser ------------------------------------------------
# Ein MVT ist Protobuf. Fuer diese Frage braucht es keinen Decoder mit Schema:
# Layername, Schluesseltabelle, Werttabelle und die tag-Paare je Objekt genuegen.
# Deshalb bewusst ohne Fremdmodul (`mapbox_vector_tile` ist auf node1 nicht da).

def _varint(b, i):
    r = s = 0
    while True:
        x = b[i]
        i += 1
        r |= (x & 0x7F) << s
        if not x & 0x80:
            return r, i
        s += 7


def _felder(b):
    """(feldnummer, wert) je Feld; Laengenfelder als bytes, Varints als int."""
    i, n = 0, len(b)
    while i < n:
        k, i = _varint(b, i)
        nr, art = k >> 3, k & 7
        if art == 0:
            v, i = _varint(b, i)
            yield nr, v
        elif art == 2:
            ln, i = _varint(b, i)
            yield nr, b[i:i + ln]
            i += ln
        elif art == 5:
            yield nr, b[i:i + 4]
            i += 4
        elif art == 1:
            yield nr, b[i:i + 8]
            i += 8
        else:
            raise ValueError(f"unbekannter Drahttyp {art}")


def _wert(roh):
    """Value-Botschaft zu Python-Wert (nur die Faelle, die Protomaps benutzt)."""
    for nr, v in _felder(roh):
        if nr == 1:
            return v.decode("utf-8", "replace")   # string
        if nr == 4:
            return v                              # int64
        if nr == 5:
            return v                              # uint64
        if nr == 6:
            return (v >> 1) ^ -(v & 1)            # sint64
        if nr == 7:
            return bool(v)
    return None


def lies_layer(kachel_bytes, layername):
    """Liste der Attribut-Woerterbuecher aller Objekte eines Layers."""
    if kachel_bytes[:2] == b"\x1f\x8b":
        kachel_bytes = gzip.decompress(kachel_bytes)
    ergebnis = []
    for nr, roh in _felder(kachel_bytes):
        if nr != 3:                      # Tile.layers
            continue
        name, keys, values, objekte = None, [], [], []
        for lnr, lv in _felder(roh):
            if lnr == 1:
                name = lv.decode("utf-8", "replace")
            elif lnr == 2:
                objekte.append(lv)
            elif lnr == 3:
                keys.append(lv.decode("utf-8", "replace"))
            elif lnr == 4:
                values.append(_wert(lv))
        if name != layername:
            continue
        for obj in objekte:
            tags = []
            for fnr, fv in _felder(obj):
                if fnr != 2:             # Feature.tags (gepackt)
                    continue
                i = 0
                while i < len(fv):
                    t, i = _varint(fv, i)
                    tags.append(t)
            attr = {}
            for a, b in zip(tags[0::2], tags[1::2]):
                if a < len(keys) and b < len(values):
                    attr[keys[a]] = values[b]
            ergebnis.append(attr)
    return ergebnis


# --- Stil lesen ---------------------------------------------------------------

def stil_arten():
    """{quelle: set(kind)} aus dem Stil.

    Zwei Fundorte, und beide muessen mit: die `match`-Ausdruecke der POI-Ebenen
    (die Grundliste, die ohne JavaScript gilt) und `metadata.poi-sichten` (die
    Zusatzsichten des Umschalters). Wer nur die Ebenen liest, prueft die Haelfte
    und uebersieht genau die Listen, die spaeter erweitert werden.
    """
    stil = json.loads(re.sub(r"^\s*//.*$", "", STIL.read_text(), flags=re.M))
    treffer = {}
    for ebene in stil["layers"]:
        if ebene.get("source-layer") != "pois":
            continue
        arten = set()

        def suche(knoten):
            if isinstance(knoten, list):
                if knoten and knoten[0] == "match" and len(knoten) > 2 \
                        and isinstance(knoten[2], list):
                    arten.update(x for x in knoten[2] if isinstance(x, str))
                for k in knoten:
                    suche(k)

        suche(ebene.get("filter"))
        treffer[ebene["id"]] = arten

    sichten = (stil.get("metadata", {}).get("poi-sichten", {}).get("sichten", []))
    for s in sichten:
        treffer[f"sicht:{s['id']}"] = set(s.get("arten") or [])
    return treffer


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--zoom", type=int, default=14)
    p.add_argument("--umkreis", type=int, default=1,
                   help="Kacheln je Richtung um den Ort (1 = 3x3)")
    p.add_argument("--archiv", default=ARCHIV)
    p.add_argument("--nur-abgleich", action="store_true")
    p.add_argument("--zeige", type=int, default=60, help="wie viele Arten listen")
    p.add_argument("--detail", metavar="ART", action="append", default=[],
                   help="fuer diese Art die kind_detail-Verteilung zeigen; "
                        "mehrfach angebbar")
    a = p.parse_args()

    arten = Counter()
    detail = defaultdict(Counter)
    minzoom = defaultdict(list)
    beispiel = {}
    kacheln = leer = 0

    for ort, (lat, lon) in ORTE.items():
        x0, y0 = kachel(lat, lon, a.zoom)
        for dx in range(-a.umkreis, a.umkreis + 1):
            for dy in range(-a.umkreis, a.umkreis + 1):
                r = subprocess.run(
                    [PMTILES, "tile", a.archiv, str(a.zoom), str(x0 + dx), str(y0 + dy)],
                    capture_output=True)
                if r.returncode != 0 or not r.stdout:
                    leer += 1
                    continue
                kacheln += 1
                for obj in lies_layer(r.stdout, "pois"):
                    k = obj.get("kind")
                    if not k:
                        continue
                    arten[k] += 1
                    if obj.get("kind_detail"):
                        detail[k][obj["kind_detail"]] += 1
                    if isinstance(obj.get("min_zoom"), (int, float)):
                        minzoom[k].append(obj["min_zoom"])
                    if k not in beispiel and obj.get("name"):
                        beispiel[k] = f"{obj['name']} ({ort})"

    im_stil = stil_arten()
    alle_im_stil = set().union(*im_stil.values()) if im_stil else set()

    print(f"Stichprobe: {kacheln} Kacheln auf Zoom {a.zoom} um {len(ORTE)} Orte "
          f"({leer} leer), {sum(arten.values())} POI-Objekte, {len(arten)} Arten.\n")

    if not a.nur_abgleich:
        print(f"{'Art':<26} {'Anzahl':>7}  {'min_zoom':>8}  im Stil  Beispiel")
        print("-" * 100)
        for k, n in arten.most_common(a.zeige):
            mz = minzoom[k]
            mzs = f"{min(mz):.0f}-{max(mz):.0f}" if mz else "-"
            print(f"{k:<26} {n:>7}  {mzs:>8}  "
                  f"{'ja' if k in alle_im_stil else '  ':>7}  {beispiel.get(k, '')[:44]}")
        if len(arten) > a.zeige:
            print(f"... und {len(arten) - a.zeige} weitere Arten "
                  f"(--zeige {len(arten)} zeigt alle)")
        print()

    # ★ Die Unterart entscheidet oft, ob eine Art brauchbar ist. Protomaps fasst
    #   etwa alle Haltestellen unter `platform` zusammen; ob Bus oder Bahn steht
    #   erst in `kind_detail`. Wer nur `kind` liest, haelt die Karte fuer
    #   haltestellenlos, obwohl hunderte drin sind.
    for art in a.detail:
        if art not in arten:
            print(f"{art}: in der Stichprobe nicht vorhanden\n")
            continue
        ohne = arten[art] - sum(detail[art].values())
        print(f"{art} ({arten[art]} Objekte), Unterarten:")
        for d, n in detail[art].most_common(20):
            print(f"  {d:<24} {n:>6}")
        if ohne:
            print(f"  {'(ohne kind_detail)':<24} {ohne:>6}")
        print()

    fehler = 0
    for ebene, gefiltert in sorted(im_stil.items()):
        phantome = sorted(k for k in gefiltert if k not in arten)
        print(f"{ebene}: {len(gefiltert)} Arten im Filter, "
              f"{len(gefiltert) - len(phantome)} davon in der Stichprobe belegt")
        if phantome:
            print(f"  ! nicht gefunden: {', '.join(phantome)}")
            fehler += len(phantome)

    if fehler:
        print(f"\n{fehler} gefilterte Art(en) kommen in dieser Stichprobe nicht vor. "
              f"Das kann Seltenheit sein oder ein Tippfehler. Mit groesserem "
              f"--umkreis gegenpruefen, bevor etwas geaendert wird.")
        return 1
    print("\nJede gefilterte Art ist in den Daten belegt.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
