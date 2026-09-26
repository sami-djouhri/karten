# Werkzeugkasten fuer den Adressindex.
#
# osmium-tool filtert die 4,5-GB-Rohdatei in C++ vor (das in Python zu tun waere
# um Groessenordnungen langsamer), pyosmium liest die kleinen Reste dann bequem.
# Beides aus Debian-Paketen statt aus dem Netz nachgezogen: hier soll nichts
# laufen, was niemand geprueft hat.
#
# Das Bild wird NUR zum Bauen des Index gebraucht, nicht im Betrieb. Der
# Suchdienst selbst kommt ohne aus (nur SQLite).
FROM debian:trixie-slim
# Sicherheitsstand des Basis-Image nachziehen. Ein Upstream-Image friert die Paketstaende
# vom Tag seines Baus ein, Debian-security ist regelmaessig weiter, und ein `--pull` holt
# nur ein neueres Bild derselben Verspaetung: gemessen am 2026-09-13 trug das aktuelle
# python:3.13-slim aus der Registry dieselben drei perl-CVEs wie das monatealte lokale.
# `upgrade`, nicht `dist-upgrade`: letzteres darf Pakete entfernen, um Konflikte zu loesen.
RUN apt-get update \
 && apt-get -y upgrade \
 && rm -rf /var/lib/apt/lists/*


RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      osmium-tool python3 python3-pyosmium \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /w
