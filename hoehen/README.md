# hoehen — Geländemodell für 3D-Ansicht und Schummerung

Macht aus dem offenen Höhenmodell des Landes NRW einen Terrain-Kachelsatz. Damit
lässt sich die Karte kippen, das Luftbild legt sich über echtes Gelände, und die
Schummerung zeigt die Täler des Bergischen auch in der flachen Ansicht.

| | |
|---|---|
| Quelle | opengeodata.nrw.de, **DGM1** — Geländehöhe, 1 m Raster, GeoTIFF Float32, 1×1-km-Kacheln in UTM32 |
| Lizenz | Datenlizenz Deutschland Zero (dl-de/zero-2-0) |
| Gebiet | dieselben 910 Kacheln wie die Luftbilder — abgeleitet aus `luftbilder/dop-liste.tsv` |
| Umfang | 910 Kacheln, 2,1 GB Quelldaten |
| Ergebnis | `gelaende.pmtiles`, Terrarium-kodiert, z8–z16 (2,4 m je Bildpunkt) |

**Geländemodell, nicht Oberflächenmodell:** DGM1 ist der nackte Boden. Bäume und
Häuser stehen nicht darin — die kämen aus DOM1 und würden eine 3D-Ansicht
unbrauchbar machen, weil Wälder als massive Plateaus erschienen. Gebäude kommen
aus dem sauber getrennten LoD2-Modell (siehe `gebaeude/`).

## Ablauf

```bash
# auf 192.0.2.10
python3 /opt/nrw-werkzeug/nrw-auswahl.py dgm1 > /opt/hoehendaten/dgm1-liste.tsv
/opt/nrw-werkzeug/hole-nrw.sh dgm1 /opt/hoehendaten/dgm1-liste.tsv /opt/hoehendaten/quelle
/opt/hoehendaten/build-terrain.sh
```

## Prüfen

Der einzige Test, der etwas beweist, ist der gegen die Quelle:

```bash
docker run --rm -v /opt/hoehendaten:/w -w /w \
    ghcr.io/osgeo/gdal:ubuntu-small-latest python3 /w/pruefe-terrain.py
```

Für sechs bekannte Orte wird die Höhe aus der fertigen Kachel dekodiert und
gegen den DGM1-Rohwert an derselben Stelle gehalten. Damit fallen genau die
Fehler auf, die sonst niemand bemerkt — falsche Kodierung, vertauschte
Zeilenordnung, Versatz um eine Kachel. Alle drei erzeugen ein Ergebnis, das für
sich betrachtet plausibel aussieht: Zahlen in Metern, Relief an den richtigen
Stellen, nur eben falsch.

Messung vom 2026-08-23 (größte Abweichung 0,6 m, an einem Steilhang im
Wuppertal — bei z16 deckt ein Bildpunkt 2,4 m ab):

| Ort | DGM1 | Kachel |
|---|---|---|
| Heiligenhaus | 234,5 m | 234,4 m |
| Velbert | 243,6 m | 243,6 m |
| Wuppertal-Mitte | 147,7 m | 147,7 m |
| Solingen-Mitte | 223,2 m | 223,2 m |
| Solingen-Burg (Wupper) | 145,1 m | 145,7 m |
| Remscheid | 335,9 m | 335,9 m |

## Die Falle, wegen der `kachle-terrain.py` überhaupt existiert

Bei Bildkacheln baut man die Übersichtsstufen am Ende mit `gdaladdo -r average`.
Für Terrain wäre das **falsch**, und der Fehler fällt nicht sofort auf.

Terrarium kodiert eine Höhe über drei Byte: R grob, G fein, B sehr fein. Mittelt
man R, G und B einzeln, mittelt man drei voneinander unabhängige Stellenwerte —
so sinnvoll, wie den Mittelwert der Ziffern zweier Zahlen zu bilden. Ein Übergang
von 255 auf 256 Meter springt in R um +1 und in G um −255; der Mittelwert landet
irgendwo, nur nicht dazwischen. Das Ergebnis sind Zacken und Löcher, die beim
Herauszoomen erscheinen und beim Hineinzoomen wieder verschwinden — ein
Fehlerbild, das man nur schwer einer Mittelung zuordnet.

Deshalb entsteht die Pyramide auf den **Float-Höhen** (`gdaladdo` auf dem
Float32-Raster, dort ist Mitteln korrekt), und jede Zoomstufe wird einzeln aus
der passenden Auflösungsstufe kodiert.

## Weitere Stolpersteine

**`-r average`, nicht `bilinear`.** Beim Verkleinern eines Höhenmodells soll
jeder Quellwert eingehen. Bilinear tastet vier Nachbarn ab und lässt bei Faktor 8
den Rest liegen — einzelne Felsnasen und Bachtäler verschwinden dann je nach
Zoomstufe.

**MBTILES zählt Zeilen von unten** (TMS), XYZ-Kacheln von oben. Ohne die
Umrechnung steht die Welt auf dem Kopf, und zwar nur in der Höhe — was in einer
3D-Ansicht erst auffällt, wenn man sich wundert, warum Täler Berge sind.

**`encoding: 'terrarium'` muss im Frontend stehen.** MapLibre liest es nicht aus
den Metadaten der Datei; seine Vorgabe ist die Mapbox-Kodierung, die dieselben
Bytes völlig anders umrechnet. Aus 200 m würden rund 1,3 Millionen — das Ergebnis
ist keine leichte Verzerrung, sondern eine senkrechte Nadelwand.

**Fehlstellen am Rand.** Das UTM-Rechteck liegt in WebMercator leicht gedreht,
deshalb bleiben an den vier Rändern Pixel ohne Daten. Mit 0 gefüllt gäbe das eine
senkrechte Klippe von über hundert Metern; `kachle-terrain.py` setzt stattdessen
den Kachelmittelwert ein und lässt reine Randkacheln ganz weg.

## Warum z16 und nicht mehr

z16 sind 2,4 m je Bildpunkt. Die Quelle hat 1 m Raster — die Stufe ist also noch
ehrlich aufgelöst und nicht hochgerechnet. Für die Silhouette von Hängen würde
z14 reichen; z16 kostet wenig zusätzlich und macht die Höhenabfrage am Cursor
brauchbar genau.
