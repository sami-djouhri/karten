# nrw — gemeinsame Werkzeuge für die Landesdaten

Drei Datensätze des Landes NRW fließen ins Karten-Portal: Luftbilder (DOP),
Geländehöhen (DGM1) und Gebäudemodelle (LoD2). Sie kommen aus demselben
Katalogsystem, im selben Kachelraster, unter derselben Lizenz — deshalb liegen
Auswahl und Download hier gemeinsam statt dreimal fast gleich.

Auf dem Bau-LXC liegt das unter `/opt/nrw-werkzeug/`.

## nrw-auswahl.py — welche Kacheln

```bash
python3 nrw-auswahl.py dgm1 > dgm1-liste.tsv
python3 nrw-auswahl.py lod2 > lod2-liste.tsv
```

**Die Fläche steht hier nicht drin.** Sie wird aus `luftbilder/dop-liste.tsv`
abgeleitet — derselben Datei, aus der die Luftbilder gebaut werden. Jede weitere
Box-Definition wäre eine zweite Stelle, an der jemand die Fläche ändern müsste;
die zweite vergisst man, und dann liegt das Gelände neben dem Bild. So ist
Deckungsgleichheit strukturell garantiert statt beabsichtigt.

Die Fläche selbst ändert man in `luftbilder/dop-auswahl.py`.

Das Skript gibt die Downloadgröße aus dem Katalog aus — **diese Zahl nehmen**,
nicht aus einem Mittelwert hochrechnen. Bei LoD2 lag der NRW-Durchschnitt bei
6,27 MB je Kachel, unser dicht bebauter Ausschnitt bei 13,5.

Die Kachelnamen tragen Ost- und Nordwert in Kilometern, aber die Produkte sind
sich uneinig, ob Zone und Ostwert getrennt stehen:

```
dop10rgbi_32_361_5688_1_nw_2025.jp2  ->  (361, 5688)
dgm1_32_361_5688_1_nw_2022.tif       ->  (361, 5688)
LoD2_32_361_5688_1_NW.gml            ->  (361, 5688)
ndom50_32361_5688_1_nw_2023.tif      ->  (361, 5688)   <- ohne Trennzeichen
```

## hole-nrw.sh — herunterladen

```bash
./hole-nrw.sh dgm1 /opt/hoehendaten/dgm1-liste.tsv /opt/hoehendaten/quelle
```

Prüft jede Datei gegen die Sollgröße aus dem Katalog und verwirft
unvollständige sofort. **Eine halbe Datei ist schlimmer als keine** — der spätere
GDAL- oder Parser-Lauf bräche daran ab, und zwar erst nach Stunden. Der Lauf ist
dadurch beliebig wiederholbar.

## kette.sh — nacheinander bauen

Der LXC hat vier Kerne, und jeder Bau will drei davon. Gleichzeitig gestartet
werden alle langsamer als hintereinander, weil sie sich gegenseitig aus dem
Plattenpuffer drängen — und Valhalla, das im selben LXC bedient, wird dabei zäh.

`kette.sh` wartet auf einen laufenden Luftbild-Bau und startet dann Gelände und
Gebäude nacheinander. Ein fehlgeschlagener Schritt hält die späteren **nicht**
auf: die Datensätze hängen nicht voneinander ab, und ein Abbruch beim ersten soll
nicht die ganze Nacht kosten.

Als Dienst starten, damit es SSH-Trennungen übersteht:

```bash
systemd-run --unit=nrw-kette /opt/nrw-werkzeug/kette.sh
journalctl -u nrw-kette -f          # oder: tail -f /opt/nrw-werkzeug/kette.log
```

Nicht in der Kette: der Valhalla-Neubau mit Höhendaten. Der berührt als einziger
einen laufenden Dienst und liegt deshalb im Repo `homelab/routing-server`.

## ausliefern.sh — vom Bau-LXC ins Portal

```bash
# auf host
~/docker/karten/nrw/ausliefern.sh
```

Holt jedes fertige `.mbtiles`, wandelt es auf node1 nach `.pmtiles` und hängt es
atomar ins servierte Verzeichnis. Was noch nicht gebaut ist, wird übersprungen —
der Lauf ist damit beliebig wiederholbar, auch während die Kette noch arbeitet.

**Läuft auf host, nicht auf einem der Endpunkte.** node1 hat keinen Zugang zum
Bau-LXC und umgekehrt; host erreicht beide und leitet durch. Das kostet einen
zweiten Netzsprung — die Alternative wäre ein neuer Schlüssel zwischen zwei
Hosts, die sonst nichts miteinander zu tun haben.

Zwei Sicherungen, beide aus Schaden gelernt: die **Größenprüfung** nach der
Übertragung (ein abgebrochener Datenstrom liefert eine kürzere, aber völlig
gültig aussehende SQLite-Datei — die Umwandlung liefe durch und das Ergebnis
wäre eine Karte mit Löchern) und der **atomare Tausch** am Ende (ein gekillter
Lauf hinterließ 2026-08-16 einmal einen Null-Header, und die Karte war tot).

## Lizenz

Alle drei Datensätze stehen unter **Datenlizenz Deutschland Zero
(dl-de/zero-2-0)** — echtes Open Data mit ausdrücklich erlaubtem Massendownload.
Das ist kein Abernten fremder Kachelserver. Die Namensnennung im Frontend ist
trotzdem gesetzt.
