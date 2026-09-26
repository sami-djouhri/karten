// Faehrt eine Route im Browser ab und protokolliert, was das Navi anzeigt.
//
// Warum ueberhaupt: der Folgemodus laesst sich nicht am Schreibtisch pruefen.
// Die Fehler, die weh tun, treten erst in Bewegung auf — die Position springt
// an einer gekreuzten Stelle auf den falschen Abschnitt, das Manoever wechselt
// erst NACH dem Abbiegen, oder eine Neuberechnung feuert, obwohl man korrekt
// faehrt. Alle drei sind an einer Zahlenreihe zu erkennen und an nichts sonst.
//
// Die Positionen kommen NICHT aus der Seite, sondern aus einem eigenen Aufruf
// derselben Routing-Schnittstelle. Sonst pruefte der Test die Seite gegen sich
// selbst und faende genau die Fehler nicht, die in ihrer Auswertung stecken.
//
// Braucht node >= 21 oder `node --experimental-websocket`.

import { writeFileSync } from 'node:fs';

const CDP = process.env.CDP || 'http://127.0.0.1:9222';
const PORTAL = process.env.PORTAL || 'https://karten.home.arpa';
const ROUTE_BASIS = process.env.ROUTE_BASIS || 'http://192.0.2.10:8144';
const ORDNER = process.argv[2] || '/tmp/navifahrt';
const START = (process.argv[3] || '51.3269,7.0169').split(',').map(Number);
const ZIEL = (process.argv[4] || '51.1714,7.0846').split(',').map(Number);
const SCHRITT_M = Number(process.env.SCHRITT_M || 120);   // Abstand der simulierten Messungen
const WARTE_MS = Number(process.env.WARTE_MS || 700);

const schlaf = (ms) => new Promise(r => setTimeout(r, ms));

// --- Valhalla-Polyline, sechs Nachkommastellen ------------------------------
// ★ Der verbreitete Google-Decoder rechnet mit fuenf und legt die Route
//   zehnfach verschoben ins Nichts. Dieselbe Falle wie im Frontend.
function decodePolyline6(str) {
  let index = 0, lat = 0, lng = 0;
  const punkte = [];
  while (index < str.length) {
    let b, shift = 0, result = 0;
    do { b = str.charCodeAt(index++) - 63; result |= (b & 0x1f) << shift; shift += 5; } while (b >= 0x20);
    lat += (result & 1) ? ~(result >> 1) : (result >> 1);
    shift = 0; result = 0;
    do { b = str.charCodeAt(index++) - 63; result |= (b & 0x1f) << shift; shift += 5; } while (b >= 0x20);
    lng += (result & 1) ? ~(result >> 1) : (result >> 1);
    punkte.push([lng / 1e6, lat / 1e6]);
  }
  return punkte;
}

const meterZwischen = (a, b) => {
  const mx = Math.cos(a[1] * Math.PI / 180) * 111320;
  return Math.hypot((b[0] - a[0]) * mx, (b[1] - a[1]) * 110540);
};

// --- Route holen und in gleichmaessige Messpunkte zerlegen -------------------
const antwort = await fetch(ROUTE_BASIS + '/route/route', {
  method: 'POST', headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({
    locations: [{ lat: START[0], lon: START[1] }, { lat: ZIEL[0], lon: ZIEL[1] }],
    costing: 'auto', units: 'kilometers',
    directions_options: { language: 'de-DE' },
  }),
});
const daten = await antwort.json();
if (!daten.trip) { console.error('keine Route:', JSON.stringify(daten).slice(0, 300)); process.exit(1); }
const linie = daten.trip.legs.flatMap(l => decodePolyline6(l.shape));
const manoever = daten.trip.legs.flatMap(l => l.maneuvers || []);
console.log(`Route ${daten.trip.summary.length.toFixed(1)} km, ${manoever.length} Manoever, ${linie.length} Punkte`);

// ★ Die Punktfolge fuer die Fahrt kommt spaeter aus der SEITE (kartenRoute).
//   Der eigene Abruf oben bleibt als unabhaengiger Gegenwert: weichen beide
//   Geometrien voneinander ab, stimmt etwas an der Polylinien-Auswertung der
//   Seite nicht — genau die Falle mit fuenf statt sechs Nachkommastellen.
function inMesspunkte(linie, schritt) {
  // ★★ Der ERSTE Punkt der Strecke muss mit. Ohne ihn beginnt die simulierte
  //   Fahrt eine Schrittweite hinter dem Start — bei 25 m Schrittweite also
  //   25 m spaeter. Ein Manoever 11 m hinter dem Start liegt dann schon
  //   hinter einem, und die Auswertung meldet es als "nie angezeigt". Der
  //   Fehler sass im Pruefwerkzeug und sah aus wie einer in der Anzeige;
  //   zwei Aenderungen an der Karte gingen darauf drauf, bevor er auffiel.
  const punkte = [linie[0]];
  let rest = 0;
  for (let i = 1; i < linie.length; i++) {
    let d = meterZwischen(linie[i - 1], linie[i]);
    let t0 = 0;
    while (rest + d >= schritt) {
      const noch = schritt - rest;
      t0 += noch / d * (1 - t0);
      punkte.push([
        linie[i - 1][0] + (linie[i][0] - linie[i - 1][0]) * t0,
        linie[i - 1][1] + (linie[i][1] - linie[i - 1][1]) * t0,
      ]);
      d -= noch; rest = 0;
    }
    rest += d;
  }
  return punkte;
}

// --- CDP --------------------------------------------------------------------
const ziele = await (await fetch(CDP + '/json/list')).json();
const seite = ziele.find(z => z.type === 'page');
const ws = new WebSocket(seite.webSocketDebuggerUrl);
await new Promise((ok, fehl) => { ws.onopen = ok; ws.onerror = fehl; });
let lfd = 0; const offen = new Map();
ws.onmessage = (e) => {
  const n = JSON.parse(e.data);
  if (n.id && offen.has(n.id)) {
    const { ok, fehl } = offen.get(n.id); offen.delete(n.id);
    n.error ? fehl(new Error(n.error.message)) : ok(n.result);
  }
};
const ruf = (method, params = {}) => new Promise((ok, fehl) => {
  const id = ++lfd; offen.set(id, { ok, fehl });
  ws.send(JSON.stringify({ id, method, params }));
});
const js = async (ausdruck) => (await ruf('Runtime.evaluate',
  { expression: ausdruck, returnByValue: true, awaitPromise: true })).result.value;

await ruf('Page.enable'); await ruf('Runtime.enable');
await ruf('Browser.grantPermissions', { origin: PORTAL, permissions: ['geolocation'] });
const ort = (p) => ruf('Emulation.setGeolocationOverride',
  { latitude: p[1], longitude: p[0], accuracy: 8 });

async function schuss(name) {
  const { data } = await ruf('Page.captureScreenshot', { format: 'png' });
  writeFileSync(`${ORDNER}/${name}.png`, Buffer.from(data, 'base64'));
}

// --- Die Seite ueber ihre eigene Bedienung fahren ---------------------------
// Bewusst KEIN Testhaken im Frontend: alles laeuft ueber dieselben Knoepfe,
// die ein Mensch drueckt. Ein Haken wuerde genau den Teil ueberspringen, in dem
// die Fehler sitzen.
await ort(linie[0]);
await ruf('Page.navigate', { url: `${PORTAL}/#14/${START[0]}/${START[1]}` });
await schlaf(4000);

const klick = (wahl) => js(`(() => { const e = document.querySelector(${JSON.stringify(wahl)});
  if (!e) return 'fehlt'; e.click(); return 'ok'; })()`);

console.log('  Start setzen:', await klick('#routebtn'));
await schlaf(400);
console.log('  Mitte -> Start:', await klick('button.hier[data-ziel="rstart"]'));
await schlaf(300);
await js(`location.hash = '#14/${ZIEL[0]}/${ZIEL[1]}'`);
await schlaf(1500);
console.log('  Mitte -> Ziel:', await klick('button.hier[data-ziel="rziel"]'));
await schlaf(300);
console.log('  Route berechnen:', await klick('#rgo'));

for (let i = 0; i < 40; i++) {
  await schlaf(500);
  if (await js(`document.getElementById('rnavi').style.display === 'block'`)) break;
}
// ★★ Die Manoeverliste der SEITE lesen, nicht die aus dem eigenen Aufruf.
//   Beide Routen sind fast gleich, aber die Seite nimmt Start und Ziel aus der
//   Kartenmitte und schnappt sie anders an den Strassengraph. Der Vergleich
//   gegen die eigene Liste meldete dadurch zwei "nie angezeigte Manoever", die
//   es auf der gefahrenen Route gar nicht gab.
const seitenRoute = await js('window.kartenRoute ? window.kartenRoute() : null');
if (!seitenRoute) { console.error('Die Seite hat keine Route gezeichnet.'); process.exit(1); }
// Abweichung der beiden unabhaengig gewonnenen Geometrien messen.
let maxAbw = 0;
for (let i = 0; i < Math.min(seitenRoute.length, linie.length); i++) {
  maxAbw = Math.max(maxAbw, meterZwischen(seitenRoute[i], linie[i]));
}
console.log(`  Route der Seite: ${seitenRoute.length} Punkte (eigener Abruf ${linie.length}), ` +
            `groesste Abweichung ${maxAbw.toFixed(1)} m`);
const messpunkte = inMesspunkte(seitenRoute, SCHRITT_M);
console.log(`  ${messpunkte.length} simulierte Standortmeldungen im Abstand von ${SCHRITT_M} m`);

const seitenManoever = await js(`[...document.querySelectorAll('#rerg ol li')]
  .map(li => { const k = li.cloneNode(true); const s = k.querySelector('.weg');
               if (s) s.remove(); return k.textContent.trim(); })`);
console.log(`  Manoever laut Seite: ${seitenManoever.length}`);

console.log('  Navigation starten:', await klick('#rnavi'));
await schlaf(1500);
await schuss('00_start');

// --- Fahrt ------------------------------------------------------------------
const lies = () => js(`(() => ({
  entfernung: document.getElementById('navientfernung').textContent,
  anweisung: document.getElementById('navianweisung').textContent,
  rest: document.getElementById('navirest').textContent,
  zustand: document.getElementById('navizustand').textContent,
  navimodus: document.body.classList.contains('navi'),
  karte: window.kartenZustand ? window.kartenZustand() : null,
}))()`);

const verlauf = [];
for (let i = 0; i < messpunkte.length; i++) {
  await ort(messpunkte[i]);
  await schlaf(WARTE_MS);
  const z = await lies();
  verlauf.push({ i, m: i * SCHRITT_M, ...z });
  if (i % 25 === 0) {
    const k = z.karte || {};
    console.log(`  ${(i * SCHRITT_M / 1000).toFixed(1)} km  ${z.entfernung.padStart(7)}  ` +
                `z${k.zoom} n${k.neigung} d${k.drehung}  ${z.anweisung.slice(0, 46)}`);
  }
  if (i % 40 === 0) await schuss(`fahrt_${String(i).padStart(3, '0')}`);
  // Nachtmodus einmal mitten in der Fahrt an- und wieder ausschalten. Er
  // faerbt Ebenen um, die es geben muss — faellt eine weg, sieht man es nur
  // im Bild, nie in einer Zahl.
  if (i === Math.floor(messpunkte.length / 2)) {
    await klick('#navinacht'); await schlaf(900); await schuss('nacht');
    await klick('#navinacht'); await schlaf(600); await schuss('nacht_zurueck');
  }
}
await schuss('99_ziel');
writeFileSync(`${ORDNER}/verlauf.json`, JSON.stringify(verlauf, null, 1));

// --- Auswertung -------------------------------------------------------------
console.log('\n=== Auswertung ===');
const neuberechnungen = verlauf.filter(v => /neu berechnet|Abseits/i.test(v.anweisung)).length;
const abseits = verlauf.filter(v => /abseits/i.test(v.zustand)).length;
const ohneModus = verlauf.filter(v => !v.navimodus).length;

// Restweg muss faellen. Ein Anstieg heisst: die Position wurde auf einen
// falschen Abschnitt gelegt.
const km = verlauf.map(v => {
  const m = /^([\d.,]+)\s*(km|m)/.exec(v.rest);
  if (!m) return null;
  const z = Number(m[1].replace(',', '.'));
  return m[2] === 'km' ? z : z / 1000;
}).filter(v => v !== null);
let anstiege = 0, groessterAnstieg = 0;
for (let i = 1; i < km.length; i++) {
  const d = km[i] - km[i - 1];
  if (d > 0.05) { anstiege++; groessterAnstieg = Math.max(groessterAnstieg, d); }
}
const wechsel = verlauf.filter((v, i) => i > 0 && v.anweisung !== verlauf[i - 1].anweisung).length;
// Neigung und Drehung muessen wirklich anliegen — im Code standen sie schon,
// als die Karte noch flach und nach Norden zeigte.
const neigungen = verlauf.map(v => v.karte && v.karte.neigung).filter(n => typeof n === 'number');
const flach = neigungen.filter(n => n < 40).length;
const drehungen = new Set(verlauf.map(v => v.karte && Math.round((v.karte.drehung || 0) / 10)));

console.log(`  Messungen                      ${verlauf.length}`);
console.log(`  Anweisungswechsel              ${wechsel} (Manoever laut Seite: ${seitenManoever.length})`);
console.log(`  falsche Neuberechnungen        ${neuberechnungen}   <- muss 0 sein`);
console.log(`  Messungen "abseits der Route"  ${abseits}   <- muss 0 sein`);
console.log(`  Navi-Modus verlassen           ${ohneModus}   <- muss 0 sein`);
console.log(`  Restweg steigt an              ${anstiege}   <- muss 0 sein (groesster Anstieg ${groessterAnstieg.toFixed(2)} km)`);
console.log(`  Restweg Anfang/Ende            ${km[0]?.toFixed(1)} km -> ${km[km.length - 1]?.toFixed(1)} km`);
console.log(`  Messungen mit Neigung < 40     ${flach}   <- sollte nur der Anfang sein`);
console.log(`  Neigung Median                 ${neigungen.sort((a,b)=>a-b)[Math.floor(neigungen.length/2)]}`);
console.log(`  verschiedene Drehrichtungen    ${drehungen.size}   <- 1 hiesse: die Karte dreht nicht mit`);

// ★ Die eigentliche Frage: wurde JEDES Manoever auch angezeigt? Ein Manoever,
//   das zwischen zwei Messungen liegt, wird nie sichtbar — im Test ein Artefakt
//   der Schrittweite, im Auto ein verpasstes Abbiegen. Deshalb wird es gezaehlt
//   und nicht aus der Zahl der Wechsel geraten.
const gezeigt = new Set(verlauf.map(v => v.anweisung));
// Das erste Manoever ist bewusst ausgenommen: es beschreibt, worauf man schon
// faehrt ("Auf X Richtung Sueden fahren"), nicht die naechste Handlung. Die
// Anzeige zeigt immer die naechste — das ist die Zahl, die im Auto zaehlt.
const fehlend = seitenManoever.slice(1).filter(t => t && !gezeigt.has(t));
console.log(`  nie angezeigte Manoever        ${fehlend.length} von ${Math.max(0, seitenManoever.length - 1)}` +
            (fehlend.length ? '   <- bei grober Schrittweite normal' : ''));
fehlend.slice(0, 6).forEach(t => console.log(`      ${t.slice(0, 70)}`));
console.log(`  Bilder + verlauf.json in       ${ORDNER}`);

ws.close();
process.exit(neuberechnungen || abseits || ohneModus || anstiege ? 1 : 0);
