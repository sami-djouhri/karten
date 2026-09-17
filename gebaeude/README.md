# gebaeude — Gebäudehöhen aus dem LoD2-Landesmodell

Macht aus den offenen 3D-Gebäudemodellen des Landes NRW Vektorkacheln mit
Grundriss und Höhe. Zusammen mit dem Geländemodell (`hoehen/`) ergibt das die
3D-Ansicht des Karten-Portals.

| | |
|---|---|
| Quelle | opengeodata.nrw.de, **LoD2** — CityGML, 1×1-km-Kacheln in UTM32 |
| Lizenz | Datenlizenz Deutschland Zero (dl-de/zero-2-0) |
| Gebiet | dieselben 910 Kacheln wie die Luftbilder — abgeleitet aus `luftbilder/dop-liste.tsv` |
| Umfang | 910 Kacheln, **12 GB** Quelldaten |
| Ergebnis | `gebaeude.pmtiles`, Vektorkacheln z13–z16, Feld `hoehe` in Metern |

**12 GB, nicht 5,7.** Der NRW-Durchschnitt liegt bei 6,27 MB je Kachel, aber der
gilt fürs ganze Land samt dünn besiedelter Flächen. Unser Ausschnitt ist dichter
bebaut: 13,5 MB je Kachel. Wer die Fläche erweitert, sollte die Größe aus dem
Katalog rechnen lassen (`nrw-auswahl.py` gibt sie aus) statt aus einem Mittelwert
hochzurechnen.

## Ablauf

```bash
# auf 192.0.2.10
python3 /opt/nrw-werkzeug/nrw-auswahl.py lod2 > /opt/gebaeude/lod2-liste.tsv
/opt/nrw-werkzeug/hole-nrw.sh lod2 /opt/gebaeude/lod2-liste.tsv /opt/gebaeude/quelle
/opt/gebaeude/build-gebaeude.sh
```

## Was dabei verloren geht

LoD2 beschreibt **echte Dachformen** — Sattel, Walm, Pult — als 3D-Körper.
MapLibres `fill-extrusion` kann nur senkrechte Klötze mit flachem Deckel. Wir
nehmen daher den Grundriss und ziehen ihn auf Firsthöhe hoch: die Silhouette der
Stadt stimmt, das einzelne Dach nicht.

Echte Dachformen bräuchten 3D-Tiles oder glTF, was MapLibre nicht nativ kann. Das
wäre ein eigenes Vorhaben, kein Zusatzschalter.

## Stolpersteine

**Namensräume ignorieren, nicht prüfen.** NRW liefert je nach Jahrgang CityGML
1.0 **oder** 2.0, und die Namensraum-URLs unterscheiden sich. Ein Parser, der auf
`{…/building/2.0}Building` prüft, findet in älteren Kacheln schlicht nichts — und
meldet keinen Fehler, sondern null Gebäude. Der Solingen-Testfall ist CityGML
**1.0**. Deshalb wird überall nur der lokale Elementname verglichen.

**Die Achsenreihenfolge-Falle.** GDAL 3 hält sich bei EPSG:4326 an die offizielle
Definition, und die sagt (Breite, Länge) — nicht (Länge, Breite). Ohne
`OAMS_TRADITIONAL_GIS_ORDER` landen alle Gebäude vertauscht im Indischen Ozean.
Der Fehler ist riesig und offensichtlich, sobald man hinsieht, aber die
Transformation selbst meldet nichts.

**Ausreißer verwerfen.** Höhen unter 0,5 m oder über 300 m sind fast immer
Datenfehler — eine Antenne als Gebäudeteil, oder eine Höhe 0. Ein 300-m-Klotz
mitten in Solingen fällt mehr auf als ein fehlendes Haus.

**Kein tippecanoe nötig.** GDAL schreibt MVT selbst (`ogr2ogr -f MVT`). Ein
Werkzeug weniger, das gepflegt werden muss — und das GDAL-Image liegt für die
Bildkacheln ohnehin auf der Platte.

**`iterparse` mit `clear()`.** 12 GB XML passen nicht in den Speicher. Der Parser
liest ereignisweise und gibt jedes fertige Gebäude sofort frei.

## Prüfwert

Die Kachel `LoD2_32_366_5670_1_NW.gml` (Solingen-Mitte) enthält **1947 Gebäude**,
Höhen-Median 7,9 m, Maximum 26,8 m, Koordinaten zwischen 7,083–7,098 O und
51,166–51,175 N. Wer am Parser schraubt, prüft dagegen.
