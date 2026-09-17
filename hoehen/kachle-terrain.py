#!/usr/bin/env python3
"""Schreibt aus einem Hoehenraster einen Terrain-Kachelsatz (MBTILES, Terrarium).

Aufruf im GDAL-Container:
    python3 kachle-terrain.py <dem_3857.tif> <ziel.mbtiles> <minzoom> <maxzoom>

★★★ WARUM DIESES SKRIPT UEBERHAUPT EXISTIERT — die Uebersichtsstufen:
    Bei Bildkacheln baut man die Pyramide hinterher mit `gdaladdo -r average`.
    Fuer Terrain waere das FALSCH und faellt nicht sofort auf: Terrarium kodiert
    eine Hoehe ueber drei Byte (R grob, G fein, B sehr fein). Mittelt man R, G
    und B einzeln, mittelt man drei voneinander unabhaengige Stellenwerte —
    genauso sinnvoll, wie den Mittelwert der Ziffern zweier Zahlen zu bilden.
    Ein Uebergang von 255 auf 256 Metern springt in R um 1 und in G um -255; der
    Mittelwert landet irgendwo, nur nicht dazwischen. Das Ergebnis sind Zacken
    und Loecher, die beim Herauszoomen erscheinen und beim Hineinzoomen wieder
    verschwinden — ein Fehlerbild, das man schwer einer Mittelung zuordnet.

    Deshalb: die Pyramide entsteht auf den FLOAT-Hoehen (`gdaladdo` auf dem
    Float32-Raster, dort ist Mitteln korrekt), und JEDE Zoomstufe wird einzeln
    aus der passenden Aufloesungsstufe kodiert.

★ MBTILES zaehlt die Zeilen von UNTEN (TMS), XYZ-Kacheln von oben. Ohne die
  Umrechnung steht die Welt auf dem Kopf — und zwar nur in der Hoehe, was in
  einer 3D-Ansicht erst auffaellt, wenn man sich wundert, warum Taeler Berge sind.
"""
import os
import sqlite3
import sys

import numpy as np
from osgeo import gdal

gdal.UseExceptions()

ERDUMFANG = 20037508.342789244      # halbe Kantenlaenge von WebMercator in Metern
KACHEL_PX = 256


def kachelgroesse(z):
    return 2 * ERDUMFANG / (2 ** z)


def terrarium(hoehe):
    """Float-Hoehen (m) -> drei Byte-Ebenen. height = (R*256 + G + B/256) - 32768."""
    v = np.clip(hoehe.astype(np.float64) + 32768.0, 0, 65535.996)
    ganz = np.floor(v)
    r = (ganz / 256.0).astype(np.uint8)
    g = (ganz % 256).astype(np.uint8)
    b = np.floor((v - ganz) * 256.0).astype(np.uint8)
    return r, g, b


def als_png(r, g, b):
    """256x256x3 -> PNG-Bytes. Ueber GDAL statt Pillow — das Image hat kein Pillow."""
    mem = gdal.GetDriverByName("MEM").Create("", KACHEL_PX, KACHEL_PX, 3, gdal.GDT_Byte)
    for nr, ebene in enumerate((r, g, b), start=1):
        mem.GetRasterBand(nr).WriteArray(ebene)
    pfad = "/vsimem/kachel.png"
    gdal.GetDriverByName("PNG").CreateCopy(pfad, mem)
    dateigroesse = gdal.VSIStatL(pfad).size
    kanal = gdal.VSIFOpenL(pfad, "rb")
    daten = gdal.VSIFReadL(1, dateigroesse, kanal)
    gdal.VSIFCloseL(kanal)
    gdal.Unlink(pfad)
    return daten


def mbtiles_anlegen(pfad, minz, maxz, grenzen):
    if os.path.exists(pfad):
        os.remove(pfad)
    db = sqlite3.connect(pfad)
    db.executescript("""
        PRAGMA journal_mode = OFF;
        PRAGMA synchronous = OFF;
        CREATE TABLE metadata (name text, value text);
        CREATE TABLE tiles (zoom_level integer, tile_column integer,
                            tile_row integer, tile_data blob);
        CREATE UNIQUE INDEX tile_index ON tiles (zoom_level, tile_column, tile_row);
    """)
    db.executemany("INSERT INTO metadata VALUES (?, ?)", [
        ("name", "gelaende"),
        ("format", "png"),
        ("type", "baselayer"),
        ("version", "1"),
        ("minzoom", str(minz)),
        ("maxzoom", str(maxz)),
        ("bounds", ",".join(f"{w:.6f}" for w in grenzen)),
        # Ohne diesen Hinweis raet ein Betrachter auf Mapbox-Kodierung und
        # rechnet jede Hoehe um Faktor 10 und einen Versatz daneben.
        ("encoding", "terrarium"),
        ("attribution", "Hoehendaten &copy; Land NRW (dl-de/zero-2-0)"),
    ])
    db.commit()
    return db


def mercator_nach_grad(x, y):
    import math
    lon = x / ERDUMFANG * 180.0
    lat = math.degrees(2 * math.atan(math.exp(y / ERDUMFANG * math.pi)) - math.pi / 2)
    return lon, lat


def main():
    quelle, ziel, minz, maxz = sys.argv[1], sys.argv[2], int(sys.argv[3]), int(sys.argv[4])

    ds = gdal.Open(quelle)
    gt = ds.GetGeoTransform()
    band = ds.GetRasterBand(1)
    nodata = band.GetNoDataValue()
    links, oben = gt[0], gt[3]
    rechts = links + gt[1] * ds.RasterXSize
    unten = oben + gt[5] * ds.RasterYSize
    print(f"Quelle {ds.RasterXSize}x{ds.RasterYSize}, "
          f"X {links:.0f}..{rechts:.0f}  Y {unten:.0f}..{oben:.0f}, "
          f"{band.GetOverviewCount()} Uebersichtsstufen")

    lo, la_u = mercator_nach_grad(links, unten)
    lo2, la_o = mercator_nach_grad(rechts, oben)
    db = mbtiles_anlegen(ziel, minz, maxz, (lo, la_u, lo2, la_o))

    gesamt = 0
    for z in range(maxz, minz - 1, -1):
        ks = kachelgroesse(z)
        aufl = ks / KACHEL_PX
        x0 = int((links + ERDUMFANG) // ks)
        x1 = int((rechts + ERDUMFANG - 1e-6) // ks)
        y0 = int((ERDUMFANG - oben) // ks)
        y1 = int((ERDUMFANG - unten - 1e-6) // ks)

        # Ein Raster je Stufe, exakt auf die Kachelgrenzen ausgerichtet — dann
        # sind die Kacheln reine 256er-Bloecke und nichts muss verschoben werden.
        te = [x0 * ks - ERDUMFANG, ERDUMFANG - (y1 + 1) * ks,
              (x1 + 1) * ks - ERDUMFANG, ERDUMFANG - y0 * ks]
        stufe = f"/vsimem/stufe_{z}.tif"
        # Die Float-Pyramide der Quelle wird hier automatisch herangezogen:
        # gdalwarp hat `-ovr AUTO` als Vorgabe und waehlt die passende
        # Uebersichtsstufe selbst. Ein explizites `warpOptions=["USE_OVERVIEWS=YES"]`
        # ist KEINE gueltige Warp-Option — GDAL quittiert es mit
        # "warp options does not support option USE_OVERVIEWS" und macht
        # ansonsten genau dasselbe.
        gdal.Warp(stufe, ds, xRes=aufl, yRes=aufl, outputBounds=te,
                  resampleAlg="average", format="GTiff",
                  outputType=gdal.GDT_Float32)
        sds = gdal.Open(stufe)
        sband = sds.GetRasterBand(1)
        snodata = sband.GetNoDataValue()

        geschrieben = 0
        for ty in range(y0, y1 + 1):
            for tx in range(x0, x1 + 1):
                px = (tx - x0) * KACHEL_PX
                py = (ty - y0) * KACHEL_PX
                if px + KACHEL_PX > sds.RasterXSize or py + KACHEL_PX > sds.RasterYSize:
                    continue
                feld = sband.ReadAsArray(px, py, KACHEL_PX, KACHEL_PX)
                if feld is None:
                    continue
                feld = feld.astype(np.float64)

                # Fehlstellen entstehen an den vier Raendern, weil das UTM-Rechteck
                # in Mercator leicht gedreht liegt. Mit 0 zu fuellen gaebe dort eine
                # senkrechte Klippe von ueber hundert Metern; der Kachelmittelwert
                # ist die unauffaelligste Fortsetzung.
                luecke = np.isnan(feld)
                for nd in (nodata, snodata):
                    if nd is not None:
                        luecke |= (feld == nd)
                    # DGM1 markiert Fehlstellen zusaetzlich mit -9999.
                luecke |= feld < -1000
                if luecke.all():
                    continue                      # reine Randkachel -> gar nicht erst schreiben
                if luecke.any():
                    feld[luecke] = feld[~luecke].mean()

                r, g, b = terrarium(feld)
                # ★ MBTILES zaehlt Zeilen von unten (TMS), XYZ von oben.
                tms_y = (2 ** z) - 1 - ty
                db.execute("INSERT OR REPLACE INTO tiles VALUES (?,?,?,?)",
                           (z, tx, tms_y, sqlite3.Binary(als_png(r, g, b))))
                geschrieben += 1

        db.commit()
        sds = None
        gdal.Unlink(stufe)
        gesamt += geschrieben
        print(f"  z{z}: {geschrieben} Kacheln  ({x1-x0+1} x {y1-y0+1} Raster, "
              f"{aufl:.2f} m/px)", flush=True)

    db.execute("VACUUM")
    db.close()
    print(f"fertig: {gesamt} Kacheln, {os.path.getsize(ziel)/1e6:.1f} MB -> {ziel}")


if __name__ == "__main__":
    main()
