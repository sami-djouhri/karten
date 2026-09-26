#!/usr/bin/env python3
"""Ergaenzt das Protomaps-Sprite um die Symbole, die hier fehlen.

    docker run --rm -v <static>:/w -w /w <gdal-image> python3 sprites/baue-symbole.py

Die Vektorkarte fuehrt 286 POI-Arten, das mitgelieferte Sprite deckt davon 17
ab. Gemessen an der Kartenregion sind das 1552 von 3767 Objekten der
Alltagskategorien — Parken, Kindergarten, Kirche, Tankstelle, Bank und
Apotheke fehlten komplett.

★ Gezeichnet wird hier, nicht heruntergeladen. Fertige Icon-Saetze (Maki,
  Temaki) liegen als SVG vor und braeuchten einen SVG-Renderer, den weder das
  GDAL-Abbild noch node1 hat — und eine neue Laufzeit-Abhaengigkeit widerspricht
  dem Offline-Anspruch des Portals. Geometrische Primitive mit Supersampling
  kommen ohne alles aus und treffen den Stil genauer, weil das Farbschema
  direkt aus dem vorhandenen Sheet uebernommen wird.

★★ Das Sheet wird NEBEN dem Original aufgebaut und erst am Ende getauscht.
   Ein abgebrochener Lauf darf kein halbes Sprite hinterlassen — dann faenden
   auch die 53 bestehenden Symbole ihren Platz nicht mehr und die Karte waere
   ohne Strassenschilder.
"""
import json
import os
import shutil

import numpy as np
from osgeo import gdal

gdal.UseExceptions()

KANTE = 19          # Kantenlaenge wie die vorhandenen POI-Symbole
UEBER = 8           # Supersampling-Faktor; darunter franst der Kreisrand aus
G = KANTE * UEBER

# Farbschema aus dem vorhandenen Sheet ausgelesen (Flaeche, Symbol)
FARBEN = {
    "einkauf":   ("#dcf0f8", "#4ca6cc"),
    "essen":     ("#fcecd8", "#fa9d3e"),
    "natur":     ("#d6f2e3", "#4fa074"),
    "bildung":   ("#dcebfa", "#8780ab"),
    "kultur":    ("#f9e5fb", "#f17bcb"),
    "verkehr":   ("#d7e4f2", "#5c7ed8"),
    # Neu: fuer Gesundheit und Notfall gab es keine Farbe. Rot passt ins
    # Schema (helle Flaeche, kraeftiges Symbol) und deckt sich mit dem
    # Rot, das der Style fuer wichtige POI-Beschriftungen schon verwendet.
    "gesundheit": ("#fbe2df", "#d9534f"),
}


def rgb(h):
    return np.array([int(h[i:i + 2], 16) for i in (1, 3, 5)], dtype=np.float64)


# --- Zeichenprimitive auf dem hochaufgeloesten Raster ------------------------
def leer():
    return np.zeros((G, G), dtype=np.float64)


def kreis(maske, cx, cy, r, wert=1.0):
    y, x = np.ogrid[:G, :G]
    maske[((x - cx * UEBER) ** 2 + (y - cy * UEBER) ** 2) <= (r * UEBER) ** 2] = wert
    return maske


def plakette(einzug=0.0, radius=3.4):
    """Die Grundform der Protomaps-Symbole: abgerundetes Quadrat ueber fast das
    ganze Feld — KEIN Kreis. Aus dem vorhandenen Sheet abgemessen: 19x19 voll
    gedeckt, nur die vier Ecken mit Radius ~3,4 gerundet, aussen ein Rand von
    einem Bildpunkt in der Symbolfarbe."""
    m = leer()
    y, x = np.mgrid[:G, :G]
    xf, yf = x / UEBER, y / UEBER
    a, b = einzug, KANTE - einzug
    innen_x = np.clip(np.maximum(a + radius - xf, xf - (b - radius)), 0, None)
    innen_y = np.clip(np.maximum(a + radius - yf, yf - (b - radius)), 0, None)
    m[(np.hypot(innen_x, innen_y) <= radius) & (xf >= a) & (xf <= b)
      & (yf >= a) & (yf <= b)] = 1.0
    return m


def stern(maske, cx, cy, r_aussen, r_innen, zacken=5, wert=1.0):
    punkte = []
    for i in range(zacken * 2):
        w = np.radians(-90 + i * 180.0 / zacken)
        r = r_aussen if i % 2 == 0 else r_innen
        punkte.append((cx + r * np.cos(w), cy + r * np.sin(w)))
    return polygon(maske, punkte, wert)


def rechteck(maske, x0, y0, x1, y1, wert=1.0):
    maske[int(y0 * UEBER):int(y1 * UEBER), int(x0 * UEBER):int(x1 * UEBER)] = wert
    return maske


def polygon(maske, punkte, wert=1.0):
    """Punkt-in-Polygon ueber Strahlensatz — reicht fuer konvexe Formen."""
    pts = [(x * UEBER, y * UEBER) for x, y in punkte]
    y, x = np.mgrid[:G, :G]
    drin = np.zeros((G, G), dtype=bool)
    n = len(pts)
    for i in range(n):
        x0, y0 = pts[i]
        x1, y1 = pts[(i + 1) % n]
        schnitt = ((y0 > y) != (y1 > y))
        with np.errstate(divide="ignore", invalid="ignore"):
            xs = (x1 - x0) * (y - y0) / np.where(y1 != y0, y1 - y0, 1) + x0
        drin ^= schnitt & (x < xs)
    maske[drin] = wert
    return maske


def linie(maske, x0, y0, x1, y1, dicke, wert=1.0):
    y, x = np.mgrid[:G, :G]
    ax, ay = x0 * UEBER, y0 * UEBER
    bx, by = x1 * UEBER, y1 * UEBER
    dx, dy = bx - ax, by - ay
    laenge2 = dx * dx + dy * dy or 1
    t = np.clip(((x - ax) * dx + (y - ay) * dy) / laenge2, 0, 1)
    abstand = np.hypot(x - (ax + t * dx), y - (ay + t * dy))
    maske[abstand <= dicke * UEBER / 2] = wert
    return maske


# --- Die Symbole ------------------------------------------------------------
# Koordinaten in Bildpunkten des 19x19-Feldes; Mittelpunkt ist 9,5.
def sym_parken(m):
    # Grossbuchstabe P aus Stamm und Bogen
    rechteck(m, 7.0, 5.0, 8.6, 14.5)
    kreis(m, 10.4, 7.6, 2.9)
    kreis(m, 10.4, 7.6, 1.3, 0.0)
    rechteck(m, 5.5, 5.0, 7.0, 10.4, 0.0)
    rechteck(m, 7.0, 5.0, 8.6, 14.5)
    return m


def sym_tankstelle(m):
    rechteck(m, 5.2, 5.0, 10.4, 14.6)          # Saeule
    rechteck(m, 6.4, 6.4, 9.2, 8.6, 0.0)       # Anzeigefeld
    linie(m, 10.4, 7.2, 12.6, 7.2, 1.1)        # Schlaucharm
    linie(m, 12.6, 7.2, 12.6, 11.4, 1.1)
    kreis(m, 12.6, 12.0, 1.2)                  # Zapfhahn
    return m


def sym_bank(m):
    polygon(m, [(9.5, 4.4), (15.2, 8.0), (3.8, 8.0)])   # Giebel
    rechteck(m, 4.2, 8.4, 14.8, 9.4)                    # Architrav
    for x in (5.2, 8.7, 12.2):                          # drei Saeulen
        rechteck(m, x, 9.4, x + 1.6, 13.4)
    rechteck(m, 3.6, 13.4, 15.4, 14.6)                  # Sockel
    return m


def sym_kreuz(m):
    rechteck(m, 8.2, 4.4, 10.8, 14.6)
    rechteck(m, 4.4, 8.2, 14.6, 10.8)
    return m


def sym_kirche(m):
    rechteck(m, 8.9, 2.6, 10.1, 6.2)                    # Kreuz oben
    rechteck(m, 7.9, 3.6, 11.1, 4.8)
    polygon(m, [(9.5, 6.4), (14.4, 10.6), (4.6, 10.6)])  # Dach
    rechteck(m, 6.0, 10.6, 13.0, 15.0)                  # Schiff
    rechteck(m, 8.5, 12.2, 10.5, 15.0, 0.0)             # Portal
    return m


def sym_sport(m):
    # Ball: Umriss plus Fuenfeck in der Mitte. Ein durchgehendes Kreuz sah aus
    # wie ein Fadenkreuz — deshalb die Naehte als Fussballmuster.
    kreis(m, 9.5, 9.5, 6.2)
    kreis(m, 9.5, 9.5, 4.9, 0.0)
    ecken = [(9.5 + 3.0 * np.cos(np.radians(-90 + i * 72)),
              9.5 + 3.0 * np.sin(np.radians(-90 + i * 72))) for i in range(5)]
    polygon(m, ecken)
    for i in range(5):
        ex, ey = ecken[i]
        w = np.arctan2(ey - 9.5, ex - 9.5)
        linie(m, ex, ey, 9.5 + 6.0 * np.cos(w), 9.5 + 6.0 * np.sin(w), 0.9)
    return m


def sym_hotel(m):
    rechteck(m, 3.6, 7.0, 5.0, 14.4)                    # Kopfteil
    kreis(m, 7.6, 9.4, 1.9)                             # Kissen
    rechteck(m, 5.0, 11.0, 15.4, 12.6)                  # Matratze
    rechteck(m, 9.4, 9.6, 15.4, 11.0)                   # Decke
    rechteck(m, 14.0, 12.6, 15.4, 14.4)                 # Bettfuss
    return m


def sym_werkstatt(m):
    linie(m, 5.4, 13.8, 12.4, 6.4, 2.4)                 # Schaft
    kreis(m, 13.4, 5.4, 3.1)                            # Maulkopf
    kreis(m, 13.4, 5.4, 1.5, 0.0)
    polygon(m, [(13.4, 2.3), (16.5, 3.6), (14.6, 5.4)], 0.0)
    return m


def sym_feuerwehr(m):
    polygon(m, [(9.5, 2.8), (12.6, 7.4), (11.4, 8.4), (12.8, 11.0),
                (11.0, 15.4), (8.0, 15.4), (6.2, 11.0), (7.8, 7.6)])
    kreis(m, 9.6, 12.0, 2.0, 0.0)
    return m


def sym_rathaus(m):
    rechteck(m, 8.9, 2.4, 10.1, 5.0)                    # Fahnenmast
    polygon(m, [(9.5, 5.0), (14.6, 8.6), (4.4, 8.6)])   # Dach
    rechteck(m, 5.2, 8.6, 13.8, 15.0)                   # Baukoerper
    for x in (6.4, 8.9, 11.4):                          # Fenster
        rechteck(m, x, 10.0, x + 1.4, 12.0, 0.0)
    rechteck(m, 8.6, 12.8, 10.4, 15.0, 0.0)             # Tuer
    return m


def sym_kindergarten(m):
    # Bauklotz-Turm. Der erste Entwurf war ein Ballon — der sah aus wie ein Baum.
    rechteck(m, 4.6, 11.0, 9.0, 15.2)                   # unten links
    rechteck(m, 9.6, 11.0, 14.0, 15.2)                  # unten rechts
    rechteck(m, 7.1, 6.4, 11.5, 10.4)                   # oben
    for x0, y0 in ((4.6, 11.0), (9.6, 11.0), (7.1, 6.4)):
        rechteck(m, x0 + 1.2, y0 + 1.2, x0 + 3.2, y0 + 2.6, 0.0)   # Loch im Klotz
    return m


def sym_baecker(m):
    # Baguette schraeg mit vier Einschnitten. Der erste Entwurf war ein runder
    # Laib und sah aus wie ein Ball mit Muster.
    linie(m, 4.8, 14.0, 14.2, 5.2, 5.0)
    for i in range(4):
        t = 0.20 + i * 0.20
        mx = 4.8 + (14.2 - 4.8) * t
        my = 14.0 + (5.2 - 14.0) * t
        linie(m, mx - 1.5, my - 1.1, mx + 1.1, my + 1.5, 0.85, 0.0)
    return m


def sym_getraenke(m):
    rechteck(m, 8.4, 3.2, 10.6, 6.0)                    # Hals
    polygon(m, [(8.4, 6.0), (10.6, 6.0), (12.2, 8.6), (12.2, 15.2),
                (6.8, 15.2), (6.8, 8.6)])               # Rumpf
    rechteck(m, 6.8, 10.0, 12.2, 12.2, 0.0)             # Etikett
    return m


def sym_polizei(m):
    # ★ Wappen mit STERN, nicht mit Kreuz: der erste Entwurf war vom
    #   Krankenhaus-Symbol nicht zu unterscheiden.
    polygon(m, [(9.5, 2.8), (15.2, 5.2), (15.2, 10.0), (9.5, 15.6),
                (3.8, 10.0), (3.8, 5.2)])
    stern(m, 9.5, 8.6, 4.0, 1.7, 5, 0.0)
    return m


def sym_recycling(m):
    # Drei Pfeile im Ring. Der erste Entwurf setzte die Dreiecke frei im Raum
    # und wirkte zerstreut — jetzt bilden Balken einen geschlossenen Ring, an
    # dessen Enden die Spitzen sitzen.
    r = 5.2
    ecken = [(9.5 + r * np.cos(np.radians(-90 + i * 120)),
              9.5 + r * np.sin(np.radians(-90 + i * 120))) for i in range(3)]
    for i in range(3):
        ax, ay = ecken[i]
        bx, by = ecken[(i + 1) % 3]
        # Balken nur bis 60 % der Kante, dann die Spitze
        mx, my = ax + (bx - ax) * 0.58, ay + (by - ay) * 0.58
        linie(m, ax, ay, mx, my, 2.0)
        w = np.arctan2(by - ay, bx - ax)
        spitze = [(mx + 2.6 * np.cos(w), my + 2.6 * np.sin(w)),
                  (mx + 2.0 * np.cos(w + 2.3), my + 2.0 * np.sin(w + 2.3)),
                  (mx + 2.0 * np.cos(w - 2.3), my + 2.0 * np.sin(w - 2.3))]
        polygon(m, spitze)
    return m


def sym_schwimmen(m):
    kreis(m, 6.0, 5.6, 1.8)                             # Kopf
    linie(m, 7.4, 7.4, 11.0, 9.4, 1.4)                  # Arm/Rumpf
    linie(m, 11.0, 9.4, 15.0, 8.0, 1.2)
    for y in (12.0, 14.6):                              # Wellen
        for x0 in (2.6, 7.4, 12.2):
            linie(m, x0, y, x0 + 2.4, y - 1.0, 1.0)
            linie(m, x0 + 2.4, y - 1.0, x0 + 4.8, y, 1.0)
    return m


SYMBOLE = [
    ("parking",          "verkehr",    sym_parken),
    ("fuel",             "verkehr",    sym_tankstelle),
    ("car_repair",       "verkehr",    sym_werkstatt),
    ("hotel",            "verkehr",    sym_hotel),
    ("bank",             "einkauf",    sym_bank),
    ("bakery",           "einkauf",    sym_baecker),
    ("beverages",        "einkauf",    sym_getraenke),
    ("hospital",         "gesundheit", sym_kreuz),
    ("pharmacy",         "gesundheit", sym_kreuz),
    ("doctors",          "gesundheit", sym_kreuz),
    ("clinic",           "gesundheit", sym_kreuz),
    ("fire_station",     "gesundheit", sym_feuerwehr),
    ("police",           "gesundheit", sym_polizei),
    ("place_of_worship", "bildung",    sym_kirche),
    ("kindergarten",     "bildung",    sym_kindergarten),
    ("townhall",         "bildung",    sym_rathaus),
    ("sports_centre",    "natur",      sym_sport),
    ("swimming_pool",    "natur",      sym_schwimmen),
    ("recycling",        "natur",      sym_recycling),
]


def zeichne(art, form):
    """Ein Symbol als RGBA-Feld in Zielgroesse — im Stil der vorhandenen.

    Aufbau wie im Original-Sheet abgemessen: aussen die Plakette in
    Symbolfarbe, darin um einen Bildpunkt eingerueckt die helle Flaeche, darauf
    das Zeichen. Der erste Entwurf nahm eine randlose Kreisscheibe und stand
    zwischen den Originalen wie ein Fremdkoerper.
    """
    flaeche_hex, symbol_hex = FARBEN[art]
    aussen = plakette(einzug=0.0, radius=3.4)
    innen = plakette(einzug=1.0, radius=2.6)
    zeichen = form(leer()) * innen        # nichts darf ueber den Rand ragen

    def mittel(feld):
        return feld.reshape(KANTE, UEBER, KANTE, UEBER).mean(axis=(1, 3))

    a_aussen, a_innen, a_zeichen = mittel(aussen), mittel(innen), mittel(zeichen)

    bild = np.zeros((KANTE, KANTE, 4), dtype=np.float64)
    farbe_flaeche, farbe_symbol = rgb(flaeche_hex), rgb(symbol_hex)
    for k in range(3):
        # Rand (= aussen ohne innen) traegt die Symbolfarbe
        lage = farbe_symbol[k] * (1 - a_innen) + farbe_flaeche[k] * a_innen
        bild[..., k] = lage * (1 - a_zeichen) + farbe_symbol[k] * a_zeichen
    bild[..., 3] = a_aussen * 255
    return np.clip(bild, 0, 255).astype(np.uint8)


def main():
    # Werkzeug liegt in sprites/, das ausgelieferte Sheet in static/sprites/.
    ordner = os.environ.get("SPRITE_DIR", "static/sprites")
    if not os.path.isdir(ordner):
        raise SystemExit("Sprite-Ordner nicht gefunden: %s (aus %s)" %
                         (ordner, os.getcwd()))
    d = json.load(open(os.path.join(ordner, "light.json")))
    neu = [(n, a, f) for n, a, f in SYMBOLE if n not in d]
    if not neu:
        print("nichts zu tun — alle Symbole schon im Sheet")
        return
    print("ergaenze %d Symbole: %s" % (len(neu), ", ".join(n for n, _, _ in neu)))

    alt = gdal.Open(os.path.join(ordner, "light.png"))
    breite, hoehe = alt.RasterXSize, alt.RasterYSize
    je_zeile = breite // KANTE
    zeilen = (len(neu) + je_zeile - 1) // je_zeile
    neue_hoehe = hoehe + zeilen * KANTE
    print("Sheet %dx%d -> %dx%d (%d je Zeile)" % (breite, hoehe, breite, neue_hoehe, je_zeile))

    feld = np.zeros((neue_hoehe, breite, 4), dtype=np.uint8)
    for k in range(4):
        feld[:hoehe, :, k] = alt.GetRasterBand(k + 1).ReadAsArray()
    alt = None

    for i, (name, art, form) in enumerate(neu):
        x = (i % je_zeile) * KANTE
        y = hoehe + (i // je_zeile) * KANTE
        feld[y:y + KANTE, x:x + KANTE] = zeichne(art, form)
        d[name] = {"x": x, "y": y, "width": KANTE, "height": KANTE, "pixelRatio": 1}

    # ★ Erst neben das Original schreiben, dann tauschen.
    treiber = gdal.GetDriverByName("MEM")
    ds = treiber.Create("", breite, neue_hoehe, 4, gdal.GDT_Byte)
    for k in range(4):
        ds.GetRasterBand(k + 1).WriteArray(feld[..., k])
    gdal.GetDriverByName("PNG").CreateCopy(os.path.join(ordner, "light.png.neu"), ds)

    # @2x: dieselben Daten verdoppelt. Piktogramme aus Flaechen vertragen das;
    # ein zweiter Zeichendurchgang in doppelter Aufloesung waere genauer, aber
    # die Vorlage-Symbole sind ebenfalls nur skaliert.
    gross = np.repeat(np.repeat(feld, 2, axis=0), 2, axis=1)
    ds2 = treiber.Create("", breite * 2, neue_hoehe * 2, 4, gdal.GDT_Byte)
    for k in range(4):
        ds2.GetRasterBand(k + 1).WriteArray(gross[..., k])
    gdal.GetDriverByName("PNG").CreateCopy(os.path.join(ordner, "light@2x.png.neu"), ds2)

    d2 = json.load(open(os.path.join(ordner, "light@2x.json")))
    for name, _, _ in neu:
        e = d[name]
        d2[name] = {"x": e["x"] * 2, "y": e["y"] * 2,
                    "width": KANTE * 2, "height": KANTE * 2, "pixelRatio": 2}

    for datei, inhalt in (("light.json", d), ("light@2x.json", d2)):
        with open(os.path.join(ordner, datei + ".neu"), "w") as fh:
            json.dump(inhalt, fh, indent=1, sort_keys=True)

    for datei in ("light.png", "light@2x.png", "light.json", "light@2x.json"):
        ziel = os.path.join(ordner, datei)
        if not os.path.exists(ziel + ".alt"):
            shutil.copy2(ziel, ziel + ".alt")
        os.replace(ziel + ".neu", ziel)
    print("fertig: %d Symbole im Sheet" % len(d))


if __name__ == "__main__":
    main()
