#!/usr/bin/env python3
"""Liest NRW-LoD2-Gebaeudemodelle (CityGML) und schreibt Grundriss + Hoehe.

    python3 lies-lod2.py <quellverzeichnis> <ziel.geojsonl> [prozesse]

Ausgabe ist zeilenweises GeoJSON (eine Feature je Zeile) — bei ueber einer
halben Million Gebaeuden passt keine FeatureCollection sinnvoll in den Speicher,
und `ogr2ogr` liest das Format direkt.

Je Gebaeude: Grundriss als Polygon (WGS84) + `hoehe` in Metern. Mehr braucht
MapLibres `fill-extrusion` nicht — und mehr kann es auch nicht darstellen.

★ WAS DABEI VERLOREN GEHT, damit sich spaeter niemand wundert:
  LoD2 beschreibt echte Dachformen (Sattel, Walm, Pult) als 3D-Koerper.
  `fill-extrusion` kann nur senkrechte Klotze mit flachem Deckel. Wir nehmen
  daher den Grundriss und ziehen ihn auf Firsthoehe hoch — die Silhouette der
  Stadt stimmt, das einzelne Dach nicht. Echte Dachformen braeuchten 3D-Tiles
  oder glTF, was MapLibre nicht nativ kann.

★★ DIE ACHSENREIHENFOLGE-FALLE: GDAL 3 haelt sich bei EPSG:4326 an die
   offizielle Definition, und die sagt (Breite, Laenge) — nicht (Laenge, Breite).
   Ohne OAMS_TRADITIONAL_GIS_ORDER landen alle Gebaeude vertauscht irgendwo im
   Indischen Ozean. Der Fehler ist gross und offensichtlich, sobald man hinsieht,
   aber die Transformation selbst meldet nichts.

★★★ NAMENSRAEUME WERDEN IGNORIERT: NRW liefert je nach Jahrgang CityGML 1.0
    oder 2.0, und die Namensraum-URLs unterscheiden sich. Ein Parser, der auf
    '{http://www.opengis.net/citygml/building/2.0}Building' prueft, findet in
    aelteren Kacheln schlicht nichts — und meldet keinen Fehler, sondern null
    Gebaeude. Deshalb wird ueberall nur der lokale Elementname verglichen.
"""
import json
import os
import sys
from multiprocessing import Pool
from xml.etree import ElementTree

from osgeo import osr

osr.UseExceptions()


def ohne_namensraum(tag):
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def _wandler():
    """UTM32 (ETRS89) -> WGS84. Je Prozess einmal, nicht je Gebaeude."""
    quelle = osr.SpatialReference()
    quelle.ImportFromEPSG(25832)
    ziel = osr.SpatialReference()
    ziel.ImportFromEPSG(4326)
    # ★★ Siehe Kopf: ohne diese Zeile kommen (Breite, Laenge) heraus.
    quelle.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    ziel.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    return osr.CoordinateTransformation(quelle, ziel)


WANDLER = None


def punkte_aus(element):
    """Sammelt die Koordinatentripel eines gml:posList oder mehrerer gml:pos."""
    roh = []
    for kind in element.iter():
        name = ohne_namensraum(kind.tag)
        if name in ("posList", "pos") and kind.text:
            roh.extend(kind.text.split())
    if len(roh) < 12:                     # < 4 Punkte -> kein geschlossener Ring
        return None
    try:
        zahlen = [float(w) for w in roh]
    except ValueError:
        return None
    # CityGML fuehrt hier immer 3D-Koordinaten. Bei einer anderen Dimension
    # lieber nichts liefern als stillschweigend falsch gruppieren.
    if len(zahlen) % 3:
        return None
    return [(zahlen[i], zahlen[i + 1], zahlen[i + 2]) for i in range(0, len(zahlen), 3)]


def aussenring(polygon_element):
    """Nur der aeussere Ring. Innenhoefe laesst fill-extrusion ohnehin fallen."""
    for kind in polygon_element:
        if ohne_namensraum(kind.tag) == "exterior":
            return punkte_aus(kind)
    return punkte_aus(polygon_element)


def gebaeude_lesen(bau):
    """Ein <bldg:Building> -> (ring, hoehe) oder None."""
    hoehe = None
    grund = None
    alle_z = []
    flaechen = []          # (mittleres z, ring) fuer den Notfall-Grundriss

    for element in bau.iter():
        name = ohne_namensraum(element.tag)

        if name == "measuredHeight" and element.text:
            try:
                hoehe = float(element.text)
            except ValueError:
                pass

        elif name == "Polygon":
            ring = aussenring(element)
            if not ring:
                continue
            zs = [p[2] for p in ring]
            alle_z.extend(zs)
            flaechen.append((sum(zs) / len(zs), ring))
            # Liegt dieses Polygon in einer GroundSurface? Der Elternpfad ist
            # ueber iter() nicht sichtbar, deshalb wird die GroundSurface unten
            # getrennt gesucht.

    # Bevorzugt die ausgewiesene Grundflaeche.
    for element in bau.iter():
        if ohne_namensraum(element.tag) == "GroundSurface":
            for kind in element.iter():
                if ohne_namensraum(kind.tag) == "Polygon":
                    grund = aussenring(kind)
                    if grund:
                        break
            if grund:
                break

    # Ohne ausgewiesene Grundflaeche: die tiefstliegende Flaeche nehmen.
    if not grund and flaechen:
        grund = min(flaechen, key=lambda f: f[0])[1]
    if not grund or len(grund) < 4:
        return None

    # Ohne measuredHeight aus der Koerpergeometrie ableiten.
    if hoehe is None and alle_z:
        hoehe = max(alle_z) - min(alle_z)
    if hoehe is None or not (0.5 <= hoehe <= 300):
        # Ausreisser sind fast immer Datenfehler (Hoehe 0, oder Antennen als
        # Gebaeudeteil). Ein 300-m-Klotz mitten in Solingen faellt mehr auf als
        # ein fehlendes Haus.
        return None
    return grund, round(hoehe, 1)


def kachel_lesen(pfad):
    global WANDLER
    if WANDLER is None:
        WANDLER = _wandler()

    zeilen = []
    try:
        # iterparse + clear(): 12 GB XML passen sonst nicht in den Speicher.
        for ereignis, element in ElementTree.iterparse(pfad, events=("end",)):
            if ohne_namensraum(element.tag) != "Building":
                continue
            ergebnis = gebaeude_lesen(element)
            if ergebnis:
                ring, hoehe = ergebnis
                punkte = [WANDLER.TransformPoint(x, y)[:2] for x, y, _ in ring]
                # TransformPoint liefert (x, y, z) = (lon, lat, hoehe) dank
                # TRADITIONAL_GIS_ORDER.
                koordinaten = [[round(lon, 7), round(lat, 7)] for lon, lat in punkte]
                if koordinaten[0] != koordinaten[-1]:
                    koordinaten.append(koordinaten[0])
                zeilen.append(json.dumps({
                    "type": "Feature",
                    "properties": {"hoehe": hoehe},
                    "geometry": {"type": "Polygon", "coordinates": [koordinaten]},
                }, separators=(",", ":")))
            element.clear()
    except ElementTree.ParseError as fehler:
        return pfad, [], f"XML kaputt: {fehler}"
    return pfad, zeilen, None


def main():
    quelle, ziel = sys.argv[1], sys.argv[2]
    prozesse = int(sys.argv[3]) if len(sys.argv) > 3 else 3

    dateien = sorted(os.path.join(quelle, n) for n in os.listdir(quelle)
                     if n.lower().endswith(".gml"))
    print(f"{len(dateien)} LoD2-Kacheln, {prozesse} Prozesse", flush=True)

    gesamt = 0
    fehler = 0
    with open(ziel, "w") as aus, Pool(prozesse) as pool:
        for nr, (pfad, zeilen, problem) in enumerate(
                pool.imap_unordered(kachel_lesen, dateien, chunksize=4), start=1):
            if problem:
                fehler += 1
                print(f"  WARNUNG {os.path.basename(pfad)}: {problem}", flush=True)
            if zeilen:
                aus.write("\n".join(zeilen) + "\n")
                gesamt += len(zeilen)
            if nr % 100 == 0 or nr == len(dateien):
                print(f"  {nr}/{len(dateien)} Kacheln, {gesamt:,} Gebaeude", flush=True)

    print(f"fertig: {gesamt:,} Gebaeude, {fehler} kaputte Kacheln, "
          f"{os.path.getsize(ziel)/1e6:.1f} MB -> {ziel}")


if __name__ == "__main__":
    main()
