#!/usr/bin/env python3
"""ernte-s2.py — holt das wolkenfreie Sentinel-2-Mosaik fuer DE+NL als MBTILES.

Quelle: EOX „Sentinel-2 cloudless" (tiles.maps.eox.at), Lizenz CC BY-NC-SA 4.0.
Die Namensnennung steht im Kartenstil und MUSS dort bleiben — sie ist Bedingung
der Lizenz, nicht Hoeflichkeit.

★ Warum nur bis z13 und nicht z14:
  Sentinel-2 loest 10 m je Bildpunkt auf. z13 entspricht auf unserer Breite
  12 m/Bildpunkt, z14 waeren 6 m — also feiner als die Daten ueberhaupt hergeben.
  z14 kostet 340.000 zusaetzliche Abrufe und liefert dabei kein einziges neues
  Detail, nur weichgerechnete Zwischenwerte. Beim Hineinzoomen ueber z13 hinaus
  streckt MapLibre die vorhandene Kachel — optisch dasselbe Ergebnis, ohne einen
  fremden Gratisdienst mit dem Vierfachen zu belasten.
  Gesamt so: rund 114.000 Kacheln statt 453.000.

★ Ruecksicht auf die Quelle ist hier kein Beiwerk: das ist ein fremder
  Gratisdienst. Deshalb gedrosselte Rate, ehrliche Kennung im User-Agent,
  Rueckzug bei 429/5xx — und der Lauf ist wiederaufnehmbar, damit ein Abbruch
  nicht bedeutet, dass alles noch einmal geholt werden muss.

Aufruf:  setsid nohup ./ernte-s2.py > ernte.log 2>&1 < /dev/null &
"""
import math
import os
import queue
import sqlite3
import sys
import threading
import time
import urllib.error
import urllib.request

# --- Einstellungen -----------------------------------------------------------
JAHRGANG = os.environ.get("JAHRGANG", "2024")
VORLAGE = ("https://tiles.maps.eox.at/wmts/1.0.0/"
           f"s2cloudless-{JAHRGANG}_3857/default/g/{{z}}/{{y}}/{{x}}.jpg")
# ★ Reihenfolge im Pfad ist z/ZEILE/SPALTE, also z/y/x — nicht z/x/y.
#   Beide Varianten antworten mit HTTP 200, die vertauschte liefert nur eine
#   voellig andere Weltgegend. Gegenprobe war die Dateigroesse ueber Land.

BBOX = (3.2, 47.0, 15.5, 55.3)      # wie extract-maps.sh: DE + NL mit Rand
ZOOM_MIN, ZOOM_MAX = 0, 13
ZIEL = os.environ.get("ZIEL", "/home/user/knowledge-vault/maps/build/satellit.mbtiles")
ARBEITER = int(os.environ.get("ARBEITER", "5"))
RATE = float(os.environ.get("RATE", "15"))     # Abrufe je Sekunde, global
KENNUNG = ("homelab-offline-karten/1.0 (privater Offline-Kartenspeicher, "
           "nicht-kommerziell)")
ATTRIBUTION = ("Sentinel-2 cloudless by EOX IT Services GmbH — "
               "enthaelt modifizierte Copernicus-Sentinel-Daten, CC BY-NC-SA 4.0")


def kacheln(z):
    """Alle (x, y) der Zoomstufe z innerhalb der bbox."""
    n = 2 ** z
    minlon, minlat, maxlon, maxlat = BBOX

    def xy(lon, lat):
        x = int((lon + 180.0) / 360.0 * n)
        y = int((1.0 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2.0 * n)
        return max(0, min(n - 1, x)), max(0, min(n - 1, y))

    x0, y0 = xy(minlon, maxlat)     # oben links
    x1, y1 = xy(maxlon, minlat)     # unten rechts
    for x in range(x0, x1 + 1):
        for y in range(y0, y1 + 1):
            yield x, y


class Bremse:
    """Eimer mit Wertmarken — deckelt die Abrufe je Sekunde ueber alle Arbeiter."""

    def __init__(self, rate):
        self.rate = rate
        self.schloss = threading.Lock()
        self.naechster = time.monotonic()

    def warten(self):
        with self.schloss:
            jetzt = time.monotonic()
            if self.naechster < jetzt:
                self.naechster = jetzt
            wartezeit = self.naechster - jetzt
            self.naechster += 1.0 / self.rate
        if wartezeit > 0:
            time.sleep(wartezeit)


def hole(url, bremse, versuche=5):
    """Eine Kachel holen. Gibt bytes zurueck, None bei 404, wirft sonst."""
    for versuch in range(versuche):
        bremse.warten()
        anfrage = urllib.request.Request(url, headers={"User-Agent": KENNUNG})
        try:
            with urllib.request.urlopen(anfrage, timeout=30) as antwort:
                return antwort.read()
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None                      # Kachel gibt es nicht — in Ordnung
            if e.code in (429, 500, 502, 503, 504):
                # Rueckzug. Bei 429 deutlich laenger — der Dienst sagt uns
                # gerade, dass wir zu schnell sind.
                pause = (10 if e.code == 429 else 2) * (2 ** versuch)
                print(f"   HTTP {e.code}, warte {pause}s", flush=True)
                time.sleep(pause)
                continue
            raise
        except (urllib.error.URLError, TimeoutError, OSError):
            time.sleep(2 ** versuch)
    raise RuntimeError(f"aufgegeben nach {versuche} Versuchen: {url}")


def db_vorbereiten(pfad):
    os.makedirs(os.path.dirname(pfad), exist_ok=True)
    db = sqlite3.connect(pfad, check_same_thread=False)
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA synchronous=NORMAL")
    db.execute("CREATE TABLE IF NOT EXISTS tiles ("
               "zoom_level INTEGER, tile_column INTEGER, tile_row INTEGER,"
               "tile_data BLOB)")
    db.execute("CREATE UNIQUE INDEX IF NOT EXISTS tile_index ON tiles "
               "(zoom_level, tile_column, tile_row)")
    db.execute("CREATE TABLE IF NOT EXISTS metadata (name TEXT, value TEXT)")
    db.execute("CREATE UNIQUE INDEX IF NOT EXISTS metadata_index ON metadata (name)")
    for name, wert in [
        ("name", f"Sentinel-2 cloudless {JAHRGANG} — DE+NL"),
        ("format", "jpg"),
        ("type", "baselayer"),
        ("version", "1"),
        ("minzoom", str(ZOOM_MIN)),
        ("maxzoom", str(ZOOM_MAX)),
        ("bounds", ",".join(str(v) for v in BBOX)),
        ("attribution", ATTRIBUTION),
        ("description", "Wolkenfreies Sentinel-2-Mosaik, Quelle EOX IT Services GmbH"),
    ]:
        db.execute("INSERT OR REPLACE INTO metadata (name, value) VALUES (?, ?)",
                   (name, wert))
    db.commit()
    return db


def main():
    db = db_vorbereiten(ZIEL)

    # Was liegt schon da? Der Lauf ist damit wiederaufnehmbar.
    vorhanden = set(db.execute("SELECT zoom_level, tile_column, tile_row FROM tiles"))
    print(f"schon im Speicher: {len(vorhanden)} Kacheln", flush=True)

    offen = []
    for z in range(ZOOM_MIN, ZOOM_MAX + 1):
        for x, y in kacheln(z):
            zeile = (2 ** z - 1) - y          # MBTILES zaehlt die Zeilen von unten
            if (z, x, zeile) not in vorhanden:
                offen.append((z, x, y, zeile))
    gesamt = len(offen)
    print(f"zu holen: {gesamt} Kacheln (z{ZOOM_MIN}-z{ZOOM_MAX}, Jahrgang {JAHRGANG}), "
          f"{ARBEITER} Arbeiter, max {RATE}/s", flush=True)
    if not gesamt:
        print("nichts zu tun", flush=True)
        return

    bremse = Bremse(RATE)
    aufgaben = queue.Queue()
    for eintrag in offen:
        aufgaben.put(eintrag)

    schreibschloss = threading.Lock()
    zaehler = {"fertig": 0, "leer": 0, "fehler": 0}
    start = time.monotonic()
    abbruch = threading.Event()

    def arbeiter():
        while not abbruch.is_set():
            try:
                z, x, y, zeile = aufgaben.get_nowait()
            except queue.Empty:
                return
            try:
                daten = hole(VORLAGE.format(z=z, y=y, x=x), bremse)
            except Exception as e:                       # noqa: BLE001
                with schreibschloss:
                    zaehler["fehler"] += 1
                    if zaehler["fehler"] > 200:
                        print(f"zu viele Fehler, Abbruch: {e}", flush=True)
                        abbruch.set()
                continue
            finally:
                aufgaben.task_done()

            with schreibschloss:
                if daten is None:
                    zaehler["leer"] += 1
                else:
                    db.execute("INSERT OR REPLACE INTO tiles "
                               "(zoom_level, tile_column, tile_row, tile_data) "
                               "VALUES (?, ?, ?, ?)", (z, x, zeile, daten))
                zaehler["fertig"] += 1
                n = zaehler["fertig"]
                if n % 500 == 0:
                    db.commit()
                    verstrichen = time.monotonic() - start
                    tempo = n / verstrichen
                    rest = (gesamt - n) / tempo if tempo else 0
                    print(f"   {n}/{gesamt} ({100 * n / gesamt:.1f} %) "
                          f"{tempo:.1f}/s  noch ~{rest / 60:.0f} min  "
                          f"leer={zaehler['leer']} fehler={zaehler['fehler']}",
                          flush=True)

    fäden = [threading.Thread(target=arbeiter, daemon=True) for _ in range(ARBEITER)]
    for f in fäden:
        f.start()
    for f in fäden:
        f.join()

    db.commit()
    anzahl = db.execute("SELECT COUNT(*) FROM tiles").fetchone()[0]
    db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    db.close()
    dauer = (time.monotonic() - start) / 60
    print(f"fertig in {dauer:.0f} min — {anzahl} Kacheln im Speicher, "
          f"leer={zaehler['leer']} fehler={zaehler['fehler']}", flush=True)
    if zaehler["fehler"]:
        sys.exit(1)


if __name__ == "__main__":
    main()
