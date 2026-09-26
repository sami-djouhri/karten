#!/usr/bin/env python3
"""baue-adressindex.py — macht aus germany-latest.osm.pbf einen Adressindex
mit Hausnummern, als eine SQLite-Datei.

Warum kein Nominatim: Nominatim will fuer Deutschland grob 100 GB Datenbank und
16+ GB Arbeitsspeicher; auf den vorhandenen 5-7 GB laeuft allein der Import
tagelang. Was hier wirklich gebraucht wird — „Ort, dann Strasse, dann Nummer" —
sind drei Tabellen und zwei Indizes. Faktor zwanzig leichter, und es passt zum
Hausprinzip Eigen-System statt Fremd-Stack.

Was dabei fehlt, ist das freie Zerlegen beliebiger Suchtexte („Hauptstr 12 bei
Muenchen"). Mit gefuehrter Eingabe faellt das nicht ins Gewicht.

★ Der Ablauf ist in Stufen geteilt, jede mit eigener Ausgabedatei. Ein
  abgebrochener Lauf setzt dort wieder an, wo er stehengeblieben ist — bei
  einer Rohdatei dieser Groesse ist das kein Luxus, sondern Notwehr.

★★ Der Speicher ist der Engpass, nicht die Platte. Deshalb wandert NICHTS in
   grosse Python-Woerterbuecher: die Rohdaten gehen zeilenweise nach SQLite,
   und das Zusammenfuehren und Entdoppeln macht SQLite auf der Platte. Ein
   `dict` mit 20 Millionen Wegen waere gut 1,5 GB gewesen — mehr als frei ist.

Aufruf (im Container, siehe baue-index.sh):
    python3 baue-adressindex.py
"""
import os
import subprocess
import sqlite3
import sys
import time

import osmium

PBF = os.environ.get("PBF", "/w/germany-latest.osm.pbf")
ARB = os.environ.get("ARB", "/w/arbeit")          # Zwischenstaende
ZIEL = os.environ.get("ZIEL", "/w/adressen.sqlite")

NUR_KNOTEN = f"{ARB}/knoten-adr.osm.pbf"
NUR_WEGE = f"{ARB}/wege-adr.osm.pbf"
GESUCHTE_IDS = f"{ARB}/gesuchte-knoten.txt"
WEG_KNOTEN = f"{ARB}/weg-knoten.osm.pbf"
ROH = f"{ARB}/roh.sqlite"


def sag(text):
    print(f"[{time.strftime('%H:%M:%S')}] {text}", flush=True)


def lauf(*befehl):
    sag("   $ " + " ".join(befehl))
    subprocess.run(befehl, check=True)


# --- Schreibformen ----------------------------------------------------------
# Zwei Formen je Name, sonst faellt je eine Eingabeart durch — dieselbe Lehre
# wie in der Ortssuche im Frontend:
#   "Höseler Straße" -> hoeseler strasse  (deutsch umschrieben)  <- "Hoeseler"
#   "Höseler Straße" -> hoseler strasse   (nur Zeichen gestrippt) <- "Hoseler"
# Zusaetzlich wird "str." zu "strasse" ausgeschrieben: fast jeder tippt die
# Abkuerzung, in den Daten steht sie fast nie.
import unicodedata


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


def sortierschluessel(nummer):
    """„12a" -> 12, damit 2 vor 10 kommt und nicht dazwischen.

    ★ Nicht `c.isdigit()` benutzen: das ist auch fuer hochgestellte Ziffern
      wahr, `int()` kann sie aber nicht lesen. In den echten Daten steht die
      Hausnummer „21¹" — damit ist der ganze Lauf nach einer Stunde gescheitert.
    """
    ziffern = ""
    for c in nummer:
        if c in "0123456789":
            ziffern += c
        elif ziffern:
            break
    return int(ziffern) if ziffern else 0


# --- Stufe 1+2: vorfiltern (C++, schnell) ------------------------------------
def vorfiltern():
    # -R = referenzierte Objekte NICHT mitnehmen. Genau das haelt den
    # Speicherbedarf klein: osmium muesste sonst die Knoten-IDs aller Wege in
    # einem Bitfeld ueber den gesamten ID-Raum halten (~1,6 GB).
    if not os.path.exists(NUR_KNOTEN):
        sag("Stufe 1: Adressknoten herausfiltern")
        lauf("osmium", "tags-filter", "-R", "--overwrite",
             PBF, "n/addr:housenumber", "-o", NUR_KNOTEN)
    if not os.path.exists(NUR_WEGE):
        sag("Stufe 2: Adresswege (Gebaeude) herausfiltern")
        lauf("osmium", "tags-filter", "-R", "--overwrite",
             PBF, "w/addr:housenumber", "-o", NUR_WEGE)


# --- Stufe 3: Wege einlesen --------------------------------------------------
class WegLeser(osmium.SimpleHandler):
    """Sammelt Adresswege: Merkmale in die Datenbank, gebrauchte Knoten-ID
    in eine Textdatei fuer `osmium getid`."""

    def __init__(self, db, idatei):
        super().__init__()
        self.db = db
        self.idatei = idatei
        self.n = 0
        self.stapel = []

    def way(self, w):
        if not w.nodes:
            return
        t = w.tags
        hnr = t.get("addr:housenumber")
        if not hnr:
            return
        # ★ Der ERSTE Knoten des Gebaeudeumrisses reicht als Position. Der
        #   echte Schwerpunkt waere genauer, kostet aber alle Knoten des Wegs
        #   statt einem — bei einem Wohnhaus liegen beide wenige Meter
        #   auseinander, und zum Navigieren ist das ohne Belang.
        knoten = w.nodes[0].ref
        self.stapel.append((knoten, t.get("addr:street", ""), hnr,
                            t.get("addr:city") or t.get("addr:place") or "",
                            t.get("addr:postcode", "")))
        self.idatei.write(f"n{knoten}\n")
        self.n += 1
        if len(self.stapel) >= 50000:
            self.leeren()

    def leeren(self):
        self.db.executemany(
            "INSERT INTO weg_roh (knoten, strasse, hnr, ort, plz) "
            "VALUES (?, ?, ?, ?, ?)", self.stapel)
        self.db.commit()
        self.stapel.clear()


# --- Stufe 5+6: Knoten einlesen ----------------------------------------------
class KnotenPosLeser(osmium.SimpleHandler):
    """Nur Position — fuer die Knoten, die von Adresswegen gebraucht werden."""

    def __init__(self, db):
        super().__init__()
        self.db = db
        self.n = 0
        self.stapel = []

    def node(self, n):
        if not n.location.valid():
            return
        self.stapel.append((n.id, n.location.lat, n.location.lon))
        self.n += 1
        if len(self.stapel) >= 50000:
            self.leeren()

    def leeren(self):
        self.db.executemany(
            "INSERT OR REPLACE INTO knoten_pos (id, lat, lon) VALUES (?, ?, ?)",
            self.stapel)
        self.db.commit()
        self.stapel.clear()


class AdressKnotenLeser(osmium.SimpleHandler):
    """Adressen, die direkt als Punkt in den Daten stehen."""

    def __init__(self, db):
        super().__init__()
        self.db = db
        self.n = 0
        self.stapel = []

    def node(self, n):
        t = n.tags
        hnr = t.get("addr:housenumber")
        if not hnr or not n.location.valid():
            return
        self.stapel.append((t.get("addr:city") or t.get("addr:place") or "",
                            t.get("addr:postcode", ""),
                            t.get("addr:street", ""), hnr,
                            n.location.lat, n.location.lon))
        self.n += 1
        if len(self.stapel) >= 50000:
            self.leeren()

    def leeren(self):
        self.db.executemany(
            "INSERT INTO roh (ort, plz, strasse, hnr, lat, lon) "
            "VALUES (?, ?, ?, ?, ?, ?)", self.stapel)
        self.db.commit()
        self.stapel.clear()


def rohdatenbank():
    db = sqlite3.connect(ROH)
    db.execute("PRAGMA journal_mode=OFF")       # Zwischenstand, kein Journal noetig
    db.execute("PRAGMA synchronous=OFF")
    db.execute("PRAGMA cache_size=-200000")     # ~200 MB
    db.execute("CREATE TABLE IF NOT EXISTS roh (ort TEXT, plz TEXT, strasse TEXT,"
               " hnr TEXT, lat REAL, lon REAL)")
    db.execute("CREATE TABLE IF NOT EXISTS weg_roh (knoten INTEGER, strasse TEXT,"
               " hnr TEXT, ort TEXT, plz TEXT)")
    db.execute("CREATE TABLE IF NOT EXISTS knoten_pos (id INTEGER PRIMARY KEY,"
               " lat REAL, lon REAL)")
    # ★ Merkzettel der abgeschlossenen Stufen. Ohne ihn haette der zweite Lauf
    #   die 16 Millionen Wegadressen ein ZWEITES Mal in `roh` geschoben — die
    #   uebrigen Stufen erkennen ihren Zustand an ihrer Ausgabe, Stufe 7 kann
    #   das nicht, weil sie in eine bereits gefuellte Tabelle schreibt.
    db.execute("CREATE TABLE IF NOT EXISTS fertig (stufe TEXT PRIMARY KEY)")
    return db


def erledigt(db, stufe):
    return db.execute("SELECT 1 FROM fertig WHERE stufe = ?",
                      (stufe,)).fetchone() is not None


def abhaken(db, stufe):
    db.execute("INSERT OR IGNORE INTO fertig (stufe) VALUES (?)", (stufe,))
    db.commit()


def rohdaten_sammeln():
    db = rohdatenbank()

    if db.execute("SELECT COUNT(*) FROM weg_roh").fetchone()[0] == 0:
        sag("Stufe 3: Adresswege einlesen")
        with open(GESUCHTE_IDS, "w") as idatei:
            leser = WegLeser(db, idatei)
            leser.apply_file(NUR_WEGE)
            leser.leeren()
        sag(f"   {leser.n} Adresswege")

    if not os.path.exists(WEG_KNOTEN):
        sag("Stufe 4: Positionen dieser Knoten aus der Rohdatei holen")
        # osmium getid loest die IDs in einem Durchgang auf — in C++, mit einem
        # kompakten ID-Speicher. Derselbe Schritt in Python waere ein Abgleich
        # von 400 Millionen Knoten gegen eine Menge im Arbeitsspeicher.
        lauf("osmium", "getid", "--overwrite", "-i", GESUCHTE_IDS,
             PBF, "-o", WEG_KNOTEN)

    if db.execute("SELECT COUNT(*) FROM knoten_pos").fetchone()[0] == 0:
        sag("Stufe 5: Knotenpositionen einlesen")
        leser = KnotenPosLeser(db)
        leser.apply_file(WEG_KNOTEN)
        leser.leeren()
        sag(f"   {leser.n} Positionen")

    if db.execute("SELECT COUNT(*) FROM roh").fetchone()[0] == 0:
        sag("Stufe 6: Adressknoten einlesen")
        leser = AdressKnotenLeser(db)
        leser.apply_file(NUR_KNOTEN)
        leser.leeren()
        sag(f"   {leser.n} Adressknoten")

    if not erledigt(db, "zusammenfuehren"):
        sag("Stufe 7: Wege mit ihren Positionen zusammenfuehren")
        db.execute("INSERT INTO roh (ort, plz, strasse, hnr, lat, lon) "
                   "SELECT w.ort, w.plz, w.strasse, w.hnr, k.lat, k.lon "
                   "FROM weg_roh w JOIN knoten_pos k ON k.id = w.knoten")
        db.commit()
        abhaken(db, "zusammenfuehren")
    gesamt = db.execute("SELECT COUNT(*) FROM roh").fetchone()[0]
    sag(f"   {gesamt} Adressen insgesamt")
    return db, gesamt


# --- Stufe 8: den fertigen Index bauen ---------------------------------------
def index_bauen():
    sag("Stufe 8: Index bauen (Orte, Strassen, Hausnummern)")
    if os.path.exists(ZIEL):
        os.remove(ZIEL)
    db = sqlite3.connect(ZIEL)
    db.execute("PRAGMA journal_mode=OFF")
    db.execute("PRAGMA synchronous=OFF")
    db.execute("PRAGMA cache_size=-200000")
    db.execute("CREATE TABLE orte (id INTEGER PRIMARY KEY, name TEXT, plz TEXT,"
               " lat REAL, lon REAL, anzahl INTEGER, such1 TEXT, such2 TEXT)")
    db.execute("CREATE TABLE strassen (id INTEGER PRIMARY KEY, ort_id INTEGER,"
               " name TEXT, lat REAL, lon REAL, anzahl INTEGER,"
               " such1 TEXT, such2 TEXT)")
    db.execute("CREATE TABLE hausnummern (strasse_id INTEGER, nummer TEXT,"
               " lat REAL, lon REAL, sortier INTEGER)")

    # Orte: ein Eintrag je Ortsname. Die Lage ist der Mittelwert seiner
    # Adressen — das trifft den bewohnten Kern besser als der Verwaltungspunkt.
    sag("   Orte")
    db.execute("ATTACH DATABASE ? AS r", (ROH,))
    db.execute(
        "INSERT INTO orte (name, plz, lat, lon, anzahl) "
        # NULLIF: ohne das gewinnt der leere Text die MIN-Auswahl und der Ort
        # steht ohne Postleitzahl da, obwohl tausende Adressen eine haben.
        "SELECT ort, MIN(NULLIF(plz, '')), AVG(lat), AVG(lon), COUNT(*) "
        "FROM r.roh WHERE ort <> '' GROUP BY ort")
    db.commit()

    # ★ Ohne diesen Index sucht SQLite den Ort fuer JEDE der 30 Millionen
    #   Adresszeilen linear in der Ortstabelle. Mit ihm ist es ein Nachschlagen.
    db.execute("CREATE INDEX orte_name ON orte (name)")

    sag("   Strassen")
    db.execute(
        "INSERT INTO strassen (ort_id, name, lat, lon, anzahl) "
        "SELECT o.id, r.strasse, AVG(r.lat), AVG(r.lon), COUNT(*) "
        "FROM r.roh r JOIN orte o ON o.name = r.ort "
        "WHERE r.strasse <> '' GROUP BY o.id, r.strasse")
    db.commit()
    db.execute("CREATE INDEX strassen_name ON strassen (ort_id, name)")

    sag("   Hausnummern")
    db.execute(
        "INSERT INTO hausnummern (strasse_id, nummer, lat, lon, sortier) "
        "SELECT s.id, r.hnr, r.lat, r.lon, 0 "
        "FROM r.roh r JOIN orte o ON o.name = r.ort "
        "JOIN strassen s ON s.ort_id = o.id AND s.name = r.strasse")
    db.commit()
    db.execute("DETACH DATABASE r")

    # Suchformen und Sortierschluessel nachtragen — in Python, weil SQLite die
    # deutschen Umschreibungen nicht kennt.
    sag("   Suchformen")
    for tabelle in ("orte", "strassen"):
        zeilen = db.execute(f"SELECT id, name FROM {tabelle}").fetchall()
        db.executemany(f"UPDATE {tabelle} SET such1=?, such2=? WHERE id=?",
                       [(umschreiben(n), strippen(n), i) for i, n in zeilen])
        db.commit()
        sag(f"      {tabelle}: {len(zeilen)}")

    # ★ Nicht je Hausnummer ein UPDATE ... WHERE nummer=?: das waere je
    #   verschiedener Nummer ein voller Durchlauf durch 30 Millionen Zeilen.
    #   Stattdessen eine kleine Zuordnungstabelle und EIN Durchlauf.
    sag("   Sortierschluessel der Hausnummern")
    db.execute("CREATE TABLE nr_sort (nummer TEXT PRIMARY KEY, sortier INTEGER)")
    nummern = [z[0] for z in db.execute("SELECT DISTINCT nummer FROM hausnummern")]
    db.executemany("INSERT INTO nr_sort (nummer, sortier) VALUES (?, ?)",
                   [(n, sortierschluessel(n)) for n in nummern])
    db.commit()
    db.execute("UPDATE hausnummern SET sortier = "
               "(SELECT sortier FROM nr_sort WHERE nr_sort.nummer = hausnummern.nummer)")
    db.execute("DROP TABLE nr_sort")
    db.commit()
    sag(f"      {len(nummern)} verschiedene Hausnummern")

    sag("   Indizes")
    db.execute("CREATE INDEX orte_such1 ON orte (such1)")
    db.execute("CREATE INDEX orte_such2 ON orte (such2)")
    db.execute("CREATE INDEX strassen_ort ON strassen (ort_id, such1)")
    db.execute("CREATE INDEX strassen_ort2 ON strassen (ort_id, such2)")
    db.execute("CREATE INDEX strassen_such ON strassen (such1)")
    db.execute("CREATE INDEX hnr_strasse ON hausnummern (strasse_id, sortier)")
    db.commit()

    zahlen = {t: db.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
              for t in ("orte", "strassen", "hausnummern")}
    db.execute("VACUUM")
    db.close()
    return zahlen


def main():
    os.makedirs(ARB, exist_ok=True)
    start = time.monotonic()
    vorfiltern()
    rohdb, _ = rohdaten_sammeln()
    rohdb.close()
    zahlen = index_bauen()
    groesse = os.path.getsize(ZIEL) / 1e9
    sag(f"fertig in {(time.monotonic() - start) / 60:.0f} min — "
        f"{zahlen['orte']} Orte, {zahlen['strassen']} Strassen, "
        f"{zahlen['hausnummern']} Hausnummern, {groesse:.2f} GB")
    sag(f"Zwischenstaende in {ARB} koennen jetzt geloescht werden.")


if __name__ == "__main__":
    sys.exit(main())
