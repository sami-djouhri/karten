# luftbilder — NRW-Orthophotos für das Karten-Portal

Macht aus den offenen Luftbildern des Landes NRW drei Rasterebenen für die
Offline-Karte: die Fläche grob, zwei Stadtkerne scharf.

| | |
|---|---|
| Quelle | opengeodata.nrw.de, DOP 10 cm, JPEG2000, 1×1-km-Kacheln in UTM32 |
| Lizenz | Datenlizenz Deutschland Zero (dl-de/zero-2-0) — echtes Open Data mit ausdrücklichem Massendownload |
| Gebiet | 26×35 km, UTM32 E 350–376 / N 5663–5698: Heiligenhaus und Velbert im Norden bis Solingen im Süden, Erkrath und Hilden im Westen bis Remscheid und Wuppertal-Süd im Osten |
| Umfang | 910 Quellkacheln, 32 GB, Jahrgang 2024/2025 |
| Ergebnis | `umland.pmtiles` (bis z19 ≈ 18,7 cm) + `stadt.pmtiles` und `solingen.pmtiles` (je 8×8 km bis z20 ≈ 9,3 cm) |

**Wo das läuft:** nicht auf node1. Das Umprojizieren von Gigapunkten aus
JPEG2000 gehört nicht auf einen Pi — es läuft auf dem Routing-LXC
(`192.0.2.10`, x86, Node `.17`) unter `/opt/luftbilder`. Nur das fertige
Ergebnis wandert nach `node1:~/knowledge-vault/maps/`.

## Ablauf

```bash
# auf 192.0.2.10
cd /opt/luftbilder
python3 dop-auswahl.py > dop-liste.tsv    # Kachelauswahl (holt den Katalog selbst)
./fetch-dop.sh                            # 910 Kacheln holen (wiederholbar)
./build-dop-tiles.sh                      # auspacken + warpen + kacheln

# danach auf node1
/home/user/bin/pmtiles convert umland.mbtiles umland.pmtiles
```

Die Fläche steht **nur hier** in `dop-auswahl.py`. Höhenmodell und
Gebäudemodelle leiten ihre Kachelliste aus `dop-liste.tsv` ab (`nrw/nrw-auswahl.py`)
— eine zweite Box-Definition wäre eine zweite Stelle, an der jemand die Fläche
ändern müsste, und die zweite vergisst man.

## Bildqualität: was hausgemacht ist und was nicht

Die Auflösung ist am Ende. NRW liefert genau eine DOP-Variante mit 10 cm, und
z20 entspricht 9,3 cm je Bildpunkt — bereits leicht über der Quelle. Mehr Zoom
vergrößert nur Bildpunkte.

Die Kompression ist dagegen zum Teil selbstgemacht. Die Quelle ist JPEG2000 mit
10-facher Kompression, daran ist nichts zu ändern. Früher kamen **zwei weitere**
JPEG-Stufen obendrauf (Auspacken Q90, Kacheln Q82) — drei Generationen sieht man
an Dachkanten, Zäunen und Fahrzeugen. Deshalb jetzt zwei Auspack-Durchgänge:

| Ziel | Format | wofür |
|---|---|---|
| `tif/` | JPEG Q90 | die z19-Fläche — dort wird auf 18,7 cm heruntergerechnet, die Artefakte verschwinden im Resampling ohnehin |
| `tif-fein/` | DEFLATE, verlustfrei | die beiden z20-Inseln — zwischen Quelle und fertiger Kachel liegt so nur **eine** verlustbehaftete Stufe (Q92 beim Kacheln) |

Verlustfrei kostet rund 150 MB je Kachel statt 22. Deshalb nur für die 128
Kacheln der Inseln, nicht für alle 910.

## Fünf Dinge, die hier Zeit gekostet haben

**Das Auspacken ist der Flaschenhals, nicht das Rechnen.** Eine JPEG2000-Kachel
zu dekodieren kostet rund 28 s und läuft je Datei **einkernig**. Lässt man
`gdalwarp` direkt aus den `.jp2` lesen, hängt der gesamte Lauf an einem Kern:
gemessen 13 % in 79 Minuten, hochgerechnet rund 10 Stunden für `umland` allein —
bei drei brachliegenden Kernen. Aus einem bereits ausgepackten, gekachelten
GeoTIFF warpt dieselbe Kachel in **5 s**. Deshalb dreistufig: erst alle Kacheln
parallel auspacken, dann warpen, dann kacheln.

Nicht funktioniert hat der naheliegende Umweg `gdalwarp -ovr AUTO-1`: die `.jp2`
tragen zwar interne Auflösungsstufen (5000×5000, 2500×2500, …) und
`gdal_translate -outsize 50%` liest sie auch in 6,5 s statt 28 s — `gdalwarp`
holt sie über die VRT aber nicht heran. Mit und ohne Schalter exakt 30 s,
byte-gleiche Ausgabe.

**Der direkte Weg ist unbrauchbar langsam.** VRT über hunderte JPEG2000-Dateien
direkt in MBTILES schreiben: nach zehn Minuten war die Zieldatei 16 KB groß.
Der Kachelschreiber liest blockweise über die volle Breite und dekodiert die
Quelle dabei immer wieder neu. Deshalb das gekachelte GeoTIFF dazwischen.

**`-of GTiff` ist beim Zwischenschritt Pflicht.** Das Auspacken schreibt erst
nach `.tif.tmp` und benennt dann um (sonst gilt eine halbe Datei beim nächsten
Lauf als fertig). GDAL rät den Ausgabetreiber aber aus der Endung, kennt `.tmp`
nicht und bricht mit `Output driver not found.` ab — hunderte Male in Folge,
ohne eine einzige Datei zu schreiben.

**Die Zoomstufe kommt aus der Auflösung, nicht aus einer Option.** Der
MBTILES-Schreiber leitet die höchste Stufe aus dem Eingang ab. Gibt man ihm die
10-cm-Kacheln unverändert, baut er für die ganze Fläche z20 und das Ergebnis ist
dreimal so groß wie gewollt. In WebMercator ist die Auflösung breitenunabhängig:
`156543,033928 / 2^z` Meter je Bildpunkt.

**Die vierte Lage ist Infrarot**, nicht Alpha. Ohne `-b 1 -b 2 -b 3` wird sie
als Transparenz missdeutet und das Bild sieht falsch aus.

## Keine externe Abhängigkeit zur Laufzeit

`dop-auswahl.py` rechnet die UTM-Projektion selbst (Snyder-Reihen), weil auf
host kein pyproj liegt. Für die Kachelwahl reicht das mit großem Abstand: eine
Kachel ist 1000 m breit, die Reihen treffen auf Zentimeter. Kontrollpunkt im
Skript ist der Kölner Dom.
