# Werkzeugkasten fuer den Adressindex.
#
# osmium-tool filtert die 4,5-GB-Rohdatei in C++ vor (das in Python zu tun waere
# um Groessenordnungen langsamer), pyosmium liest die kleinen Reste dann bequem.
# Beides aus Debian-Paketen statt aus dem Netz nachgezogen — hier soll nichts
# laufen, was niemand geprueft hat.
#
# Das Bild wird NUR zum Bauen des Index gebraucht, nicht im Betrieb. Der
# Suchdienst selbst kommt ohne aus (nur SQLite).
FROM debian:trixie-slim

RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      osmium-tool python3 python3-pyosmium \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /w
