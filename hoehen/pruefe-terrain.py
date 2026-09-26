#!/usr/bin/env python3
"""Prüft einen fertigen Terrain-Kachelsatz gegen die DGM1-Rohdaten.

    docker run --rm -v /opt/hoehendaten:/w -w /w <gdal-image> \
        python3 /w/pruefe-terrain.py

Der eigentliche Test ist der Vergleich mit der Quelle: für bekannte Orte wird die
Höhe aus der fertigen Kachel dekodiert und gegen den DGM1-Rohwert an derselben
Stelle gehalten. Damit fallen genau die Fehler auf, die sonst niemand bemerkt —
falsche Kodierung, vertauschte Zeilenordnung (TMS gegen XYZ), Versatz um eine
Kachel. Alle drei erzeugen ein Ergebnis, das für sich betrachtet plausibel
aussieht: Zahlen in Metern, Relief an den richtigen Stellen, nur eben falsch.

Erwartbare Abweichung ist die Rasterung: bei z16 deckt ein Bildpunkt 2,4 m ab,
in steilem Gelände sind das ein paar Meter Unterschied zum 1-m-Rohwert.
"""
import math
import sqlite3
import sys

from osgeo import gdal, osr

gdal.UseExceptions()

MBTILES = sys.argv[1] if len(sys.argv) > 1 else "/w/aus/gelaende.mbtiles"
VRT = sys.argv[2] if len(sys.argv) > 2 else "/w/dgm.vrt"
ZOOM = 16
SCHWELLE = 6.0          # Meter — darüber stimmt etwas strukturell nicht

ORTE = {
    "Heiligenhaus": (7.0169, 51.3269),
    "Velbert": (7.0430, 51.3400),
    "Wuppertal-Mitte": (7.1508, 51.2562),
    "Solingen-Mitte": (7.0846, 51.1714),
    "Solingen-Burg (Wupper)": (7.1520, 51.1400),
    "Remscheid": (7.1930, 51.1790),
}


def kachel_xy(lon, lat, z):
    """WGS84 -> XYZ-Kachelkoordinate (mit Nachkommaanteil für die Pixellage)."""
    x = (lon + 180.0) / 360.0 * 2 ** z
    lat_rad = math.radians(lat)
    y = (1 - math.log(math.tan(lat_rad) + 1 / math.cos(lat_rad)) / math.pi) / 2 * 2 ** z
    return x, y


def main():
    db = sqlite3.connect(MBTILES)
    meta = dict(db.execute("SELECT name, value FROM metadata").fetchall())
    print(f"encoding: {meta.get('encoding')} | format: {meta.get('format')} | "
          f"zoom {meta.get('minzoom')}-{meta.get('maxzoom')}")
    print(f"bounds  : {meta.get('bounds')}")

    if meta.get("encoding") != "terrarium":
        print("WARNUNG: encoding ist nicht 'terrarium' — das Frontend rechnet dann falsch")

    roh = gdal.Open(VRT)
    gt = roh.GetGeoTransform()
    quelle = osr.SpatialReference(); quelle.ImportFromEPSG(4326)
    ziel = osr.SpatialReference(); ziel.ImportFromEPSG(25832)
    # Ohne das kommen (Breite, Länge) statt (Länge, Breite) — siehe lies-lod2.py.
    quelle.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    ziel.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    wandler = osr.CoordinateTransformation(quelle, ziel)

    print()
    print(f"{'Ort':<24}{'Kachel':>14}{'DGM1':>9}{'Kachel':>9}{'Diff':>8}")
    groesste = 0.0
    fehlend = 0

    for name, (lon, lat) in ORTE.items():
        fx, fy = kachel_xy(lon, lat, ZOOM)
        tx, ty = int(fx), int(fy)
        px, py = int((fx - tx) * 256), int((fy - ty) * 256)
        # ★ MBTILES zählt Zeilen von unten, XYZ von oben.
        tms_y = 2 ** ZOOM - 1 - ty

        zeile = db.execute(
            "SELECT tile_data FROM tiles WHERE zoom_level=? AND tile_column=? AND tile_row=?",
            (ZOOM, tx, tms_y)).fetchone()
        if not zeile:
            print(f"{name:<24}{'KACHEL FEHLT':>14}")
            fehlend += 1
            continue

        gdal.FileFromMemBuffer("/vsimem/k.png", zeile[0])
        bild = gdal.Open("/vsimem/k.png")
        r, g, b = (int(bild.GetRasterBand(i).ReadAsArray(px, py, 1, 1)[0][0])
                   for i in (1, 2, 3))
        bild = None
        gdal.Unlink("/vsimem/k.png")
        hoehe = (r * 256 + g + b / 256.0) - 32768.0

        ostwert, nordwert, _ = wandler.TransformPoint(lon, lat)
        ix = int((ostwert - gt[0]) / gt[1])
        iy = int((nordwert - gt[3]) / gt[5])
        dgm = float(roh.GetRasterBand(1).ReadAsArray(ix, iy, 1, 1)[0][0])

        diff = abs(hoehe - dgm)
        groesste = max(groesste, diff)
        print(f"{name:<24}{tx:>8}/{ty:<5}{dgm:>8.1f}m{hoehe:>8.1f}m{diff:>7.1f}m")

    print()
    if fehlend:
        print(f"FEHLER: {fehlend} Kacheln fehlen")
        return 1
    if groesste > SCHWELLE:
        print(f"FEHLER: groesste Abweichung {groesste:.1f} m — ueber der Schwelle "
              f"von {SCHWELLE} m. Kodierung, Zeilenordnung oder Versatz pruefen.")
        return 1
    print(f"OK: groesste Abweichung {groesste:.1f} m (Schwelle {SCHWELLE} m, "
          f"bei z16 deckt ein Bildpunkt 2,4 m ab)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
