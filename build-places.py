#!/usr/bin/env python3
"""Offline-Ortsindex fuer den karten-Dienst (GeoNames, CC-BY).

★ Warum Alternativnamen noetig sind: GeoNames fuehrt Orte unter ihrem
internationalen Namen — die Stadt Muenchen heisst dort "Munich", Kopenhagen
"Copenhagen". Eine Suche nur ueber die name-Spalte findet auf Deutsch nichts
(getestet 2026-08-16: "München" lieferte nur Münchenstein/Münchenbuchsee).
Deshalb: deutschen Namen aus alternateNamesV2 ziehen, als ANZEIGENAME nehmen und
den Originalnamen als zusaetzlichen Suchbegriff behalten.

Ergebnis /maps/places.json, nach Einwohnerzahl sortiert:
  [anzeigename, lat, lon, einwohner, land, adm1, [weitere Suchnamen]]
"""
import csv, json, os, sys, unicodedata

GEO = "/tmp/geo"
ZIEL = "/home/user/knowledge-vault/maps/places.json"
LON_MIN, LAT_MIN, LON_MAX, LAT_MAX = -11.0, 34.0, 40.0, 72.0

csv.field_size_limit(10 ** 7)


def lies_orte():
    """(geonameid -> Datensatz) fuer alle Orte im Europa-Ausschnitt."""
    orte = {}
    for pfad, nur_de in ((f"{GEO}/DE.txt", True), (f"{GEO}/cities1000.txt", False)):
        with open(pfad, encoding="utf-8") as fh:
            for z in csv.reader(fh, delimiter="\t", quoting=csv.QUOTE_NONE):
                if len(z) < 15 or z[6] != "P":
                    continue
                try:
                    lat, lon, pop = float(z[4]), float(z[5]), int(z[14] or 0)
                except ValueError:
                    continue
                if not (LON_MIN <= lon <= LON_MAX and LAT_MIN <= lat <= LAT_MAX):
                    continue
                if nur_de and z[8] != "DE":
                    continue
                gid = int(z[0])
                vorh = orte.get(gid)
                if vorh is None or pop > vorh["pop"]:
                    orte[gid] = {"name": z[1], "ascii": z[2], "lat": round(lat, 5),
                                 "lon": round(lon, 5), "pop": pop, "land": z[8], "adm1": z[10]}
    return orte


def lies_deutsche_namen(gids):
    """Deutsche Namen je geonameid; bevorzugter Name gewinnt."""
    de = {}
    with open(f"{GEO}/alternateNamesV2.txt", encoding="utf-8") as fh:
        for z in csv.reader(fh, delimiter="\t", quoting=csv.QUOTE_NONE):
            if len(z) < 5 or z[2] != "de":
                continue
            try:
                gid = int(z[1])
            except ValueError:
                continue
            if gid not in gids:
                continue
            # Spalte 7 = isHistoric -> historische Namen ueberspringen
            if len(z) > 7 and z[7] == "1":
                continue
            bevorzugt = len(z) > 4 and z[4] == "1"
            vorh = de.get(gid)
            if vorh is None or (bevorzugt and not vorh[1]):
                de[gid] = (z[3], bevorzugt)
    return {g: n for g, (n, _) in de.items()}


orte = lies_orte()
print(f"Orte im Ausschnitt: {len(orte)}", flush=True)
deutsch = lies_deutsche_namen(set(orte))
print(f"davon mit deutschem Namen: {len(deutsch)}", flush=True)


def norm(s):
    s = s.lower()
    # Deutsche Umschreibungen VOR dem Diakritika-Strippen: "muenchen" == "münchen"
    for a, b in (("ä", "ae"), ("ö", "oe"), ("ü", "ue"), ("ß", "ss")):
        s = s.replace(a, b)
    return unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()


eintraege = []
for gid, o in orte.items():
    anzeige = deutsch.get(gid, o["name"])
    # Alles, was auf denselben Ort zeigt, als Suchbegriff behalten
    aliase = {o["name"], o["ascii"], deutsch.get(gid, "")} - {"", anzeige}
    eintraege.append([anzeige, o["lat"], o["lon"], o["pop"], o["land"], o["adm1"],
                      sorted(a for a in aliase if a)])

# Dubletten (gleicher Anzeigename, ~gleiche Position) zusammenfassen
gesehen = {}
for e in eintraege:
    key = (norm(e[0]), round(e[1], 1), round(e[2], 1))
    if key not in gesehen or e[3] > gesehen[key][3]:
        gesehen[key] = e

final = sorted(gesehen.values(), key=lambda e: -e[3])
with open(ZIEL, "w", encoding="utf-8") as fh:
    json.dump(final, fh, ensure_ascii=False, separators=(",", ":"))

print(f"Index: {len(final)} Orte, {os.path.getsize(ZIEL)/2**20:.1f} MB")
for probe in ("München", "Kopenhagen", "Berlin", "Zürich", "Mailand"):
    tr = [e for e in final if norm(e[0]) == norm(probe)]
    print(f"  {probe:12s} -> {tr[0][:5] if tr else 'FEHLT'}")
