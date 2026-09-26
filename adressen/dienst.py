#!/usr/bin/env python3
"""dienst.py — Adresssuche fuer das Karten-Portal. Liest nur, schreibt nie.

Bewusst ohne Fremdbibliothek: der Dienst schlaegt in einer SQLite-Datei nach und
gibt JSON zurueck — dafuer reicht die Standardbibliothek. Kein FastAPI, kein
uvicorn, keine Abhaengigkeitskette, die gepflegt werden muss. Das Ding soll in
fuenf Jahren noch starten, auch wenn es niemand angefasst hat. Passt zum Rest
des Offline-Wissens-Stapels: was offline retten soll, darf selbst nichts
brauchen.

Drei Stufen, wie die Eingabe im Frontend:
    /orte?q=heiligen              -> Orte
    /strassen?ort=<id>&q=haupt    -> Strassen in diesem Ort (ort weglassbar)
    /nummern?strasse=<id>&q=12    -> Hausnummern dieser Strasse
    /health
"""
import json
import os
import sqlite3
import threading
import unicodedata
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

DATEI = os.environ.get("INDEX", "/daten/adressen.sqlite")
PORT = int(os.environ.get("PORT", "8148"))
MAX = 25

# ★ Jeder Faden bekommt seine eigene Verbindung. Eine gemeinsame waere ohne
#   Sperre unsicher und mit Sperre ein Nadeloehr — bei reinem Lesen ist die
#   eigene Verbindung je Faden das Einfachste, was funktioniert.
_lokal = threading.local()


def db():
    if not hasattr(_lokal, "verbindung"):
        _lokal.verbindung = sqlite3.connect(
            f"file:{DATEI}?mode=ro", uri=True, check_same_thread=False)
    return _lokal.verbindung


# Dieselben zwei Schreibformen wie beim Bauen des Index und in der Ortssuche
# des Frontends — sonst findet die Eingabeart, die gerade nicht gemeint war,
# nichts.
def _grund(s):
    s = s.lower().replace("ß", "ss")
    s = s.replace("str.", "strasse").replace("str ", "strasse ")
    if s.endswith("str"):
        s = s[:-3] + "strasse"
    return s


def umschreiben(s):
    s = _grund(s)
    for a, b in (("ä", "ae"), ("ö", "oe"), ("ü", "ue")):
        s = s.replace(a, b)
    s = unicodedata.normalize("NFKD", s)
    return "".join(c for c in s if not unicodedata.combining(c)).strip()


def strippen(s):
    s = unicodedata.normalize("NFKD", _grund(s))
    return "".join(c for c in s if not unicodedata.combining(c)).strip()


def formen(s):
    return list(dict.fromkeys([umschreiben(s), strippen(s)]))


def suche_namen(tabelle, text, ort_id=None):
    """Erst was vorne passt, dann was irgendwo passt — Treffer am Wortanfang
    sind fast immer die gemeinten."""
    if len(text.strip()) < 2:
        return []
    if tabelle == "orte":
        spalten = "o.id, o.name, o.lat, o.lon, o.anzahl, o.plz"
        quelle, kurz = "orte o", "o"
    else:
        # Die Strasse allein sagt wenig — „Hauptstrasse" gibt es tausendfach.
        # Deshalb kommt der Ort immer mit, sonst ist die Trefferliste wertlos.
        spalten = "s.id, s.name, s.lat, s.lon, s.anzahl, o.name"
        quelle, kurz = "strassen s JOIN orte o ON o.id = s.ort_id", "s"

    wo = f"{kurz}.ort_id = ? AND " if ort_id else ""
    args_vorn, args_drin = [], []
    bed_vorn, bed_drin = [], []
    for f in formen(text):
        bed_vorn.append(f"{kurz}.such1 LIKE ? OR {kurz}.such2 LIKE ?")
        args_vorn += [f + "%", f + "%"]
        bed_drin.append(f"{kurz}.such1 LIKE ? OR {kurz}.such2 LIKE ?")
        args_drin += ["%" + f + "%", "%" + f + "%"]

    vorher = [ort_id] if ort_id else []
    treffer, gesehen = [], set()
    for runde, (bed, args) in enumerate(((bed_vorn, args_vorn),
                                         (bed_drin, args_drin))):
        # ★ Die zweite Runde („enthaelt") kann den Index NICHT nutzen und liest
        #   die ganze Tabelle. Bei den Orten ist das belanglos (rund 50.000
        #   Zeilen), bei den Strassen sind es zwei Millionen — und die Suche
        #   laeuft bei JEDEM Tastendruck. Deshalb dort nur, wenn der Anfang
        #   kaum etwas gebracht hat und die Eingabe lang genug ist, um es wert
        #   zu sein. „Bahnhof" soll ja auch „Zum Alten Bahnhof" finden.
        if runde == 1:
            if len(treffer) >= MAX:
                break
            if tabelle != "orte" and (len(treffer) >= 5 or len(text.strip()) < 4):
                break
        sql = (f"SELECT {spalten} FROM {quelle} WHERE {wo}("
               + " OR ".join(bed) + f") ORDER BY {kurz}.anzahl DESC LIMIT ?")
        for z in db().execute(sql, vorher + args + [MAX]):
            if z[0] in gesehen:
                continue
            gesehen.add(z[0])
            eintrag = {"id": z[0], "name": z[1], "lat": z[2], "lon": z[3],
                       "anzahl": z[4]}
            if tabelle == "orte":
                eintrag["plz"] = z[5] or ""
            else:
                eintrag["ort"] = z[5]
            treffer.append(eintrag)
            if len(treffer) >= MAX:
                break
    return treffer


def nummern(strasse_id, text):
    sql = ("SELECT nummer, lat, lon FROM hausnummern WHERE strasse_id = ? "
           "ORDER BY sortier, nummer")
    zeilen = db().execute(sql, (strasse_id,)).fetchall()
    if text:
        n = text.strip().lower()
        # Erst genau, dann was so anfaengt — wer „12" tippt, meint 12 und
        # nicht 120, will aber 12a angeboten bekommen.
        genau = [z for z in zeilen if z[0].lower() == n]
        beginnt = [z for z in zeilen if z[0].lower().startswith(n) and z not in genau]
        zeilen = genau + beginnt
    return [{"nummer": z[0], "lat": z[1], "lon": z[2]} for z in zeilen[:MAX]]


class Griff(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def antworte(self, inhalt, code=200):
        roh = json.dumps(inhalt, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(roh)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(roh)

    def do_GET(self):
        zerlegt = urlparse(self.path)
        p = parse_qs(zerlegt.query)
        eins = lambda k, s=None: (p.get(k) or [s])[0]      # noqa: E731
        try:
            if zerlegt.path == "/health":
                anzahl = db().execute(
                    "SELECT COUNT(*) FROM orte").fetchone()[0]
                return self.antworte({"status": "ok", "orte": anzahl})
            if zerlegt.path == "/orte":
                return self.antworte(suche_namen("orte", eins("q", "")))
            if zerlegt.path == "/strassen":
                ort = eins("ort")
                return self.antworte(suche_namen(
                    "strassen", eins("q", ""), int(ort) if ort else None))
            if zerlegt.path == "/nummern":
                strasse = eins("strasse")
                if not strasse:
                    return self.antworte({"fehler": "strasse fehlt"}, 400)
                return self.antworte(nummern(int(strasse), eins("q", "")))
            self.antworte({"fehler": "unbekannt"}, 404)
        except (ValueError, sqlite3.Error) as e:
            self.antworte({"fehler": str(e)}, 400)

    def log_message(self, *_):
        pass            # nginx protokolliert bereits; doppelt hilft niemandem


if __name__ == "__main__":
    print(f"Adresssuche auf :{PORT}, Index {DATEI}", flush=True)
    ThreadingHTTPServer(("0.0.0.0", PORT), Griff).serve_forever()
