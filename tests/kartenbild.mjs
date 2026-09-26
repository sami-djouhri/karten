// Fotografiert das Karten-Portal ueber das Chrome-DevTools-Protokoll.
//
// Warum nicht einfach `chromium --screenshot`: der Schuss faellt dann sofort
// nach dem Ladeereignis, lange bevor MapLibre Kacheln geholt und gezeichnet
// hat. Der uebliche Ausweg `--virtual-time-budget` ist hier eine Falle —
// gemessen am 2026-08-29 auf node1 (Chromium 124, SwiftShader): unter
// virtueller Zeit feuert `requestAnimationFrame` KEIN EINZIGES MAL. MapLibre
// malt aber ausschliesslich in rAF. Das Ergebnis ist ein Bild mit vollstaendiger
// Bedienoberflaeche und leerer Karte — es sieht exakt aus wie fehlende
// Kartendaten und nicht wie ein Fehler des Werkzeugs.
//
// Deshalb: echte Zeit, und die Fertigstellung wird gemessen statt geraten —
// es wird so lange fotografiert, bis zwei Aufnahmen in Folge Byte fuer Byte
// gleich sind. Dann ist nichts mehr in Bewegung.
//
// Neben dem Bild wird mitgeschnitten, was der Browser dabei gemeldet hat
// (JS-Ausnahmen, Konsolenfehler, gescheiterte Anfragen). Ein Foto belegt
// naemlich nur, dass etwas gemalt wurde, nicht dass es stimmt: ein 404 auf den
// Stil und eine leere Gegend sehen im Bild gleich aus. Gibt es Befunde, endet
// der Lauf mit Rueckgabewert 1.
//
// Braucht node >= 21 oder `node --experimental-websocket` (node1 hat v20).

import { writeFileSync } from 'node:fs';
import { createHash } from 'node:crypto';

const CDP = process.env.CDP || 'http://127.0.0.1:9222';
const PORTAL = process.env.PORTAL || 'http://192.0.2.10:8144';
const RUHE_MS = Number(process.env.RUHE_MS || 1500);   // Abstand zwischen zwei Vergleichsaufnahmen
const FRIST_MS = Number(process.env.FRIST_MS || 45000); // Obergrenze je Ansicht

const [ordner, ...ansichten] = process.argv.slice(2);
if (!ordner || !ansichten.length) {
  console.error('Aufruf: kartenbild.mjs <ordner> <name>=<hash>[:<Knopf>,<Knopf>] ...');
  console.error('Beispiel: kartenbild.mjs /tmp/bilder paris=12/48.86/2.35 luft=13/51.30/6.75:Luftbild');
  process.exit(2);
}

const schlaf = (ms) => new Promise(r => setTimeout(r, ms));

// --- CDP-Grundgeruest -------------------------------------------------------
const ziele = await (await fetch(CDP + '/json/list')).json();
const seite = ziele.find(z => z.type === 'page');
if (!seite) { console.error('kein Seiten-Ziel unter ' + CDP); process.exit(1); }

const ws = new WebSocket(seite.webSocketDebuggerUrl);
await new Promise((ok, fehl) => { ws.onopen = ok; ws.onerror = fehl; });

let lfd = 0;
const offen = new Map();
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

await ruf('Page.enable');
await ruf('Runtime.enable');
await ruf('Network.enable');

// --- was ein Bild nicht zeigt ------------------------------------------------
// Ein Foto belegt, dass etwas gemalt wurde, nicht dass es stimmt. Eine
// gescheiterte Kachelanfrage, eine JS-Ausnahme oder ein 404 auf den Stil sehen
// im Bild aus wie eine leere Gegend. Deshalb wird beides erfasst: das Bild und
// das, was der Browser dabei gemeldet hat.
let konsole = [], netz = [], ausnahmen = [], abgebrochen = 0;
const alt = ws.onmessage;
ws.onmessage = (e) => {
  const n = JSON.parse(e.data);
  if (n.method === 'Runtime.consoleAPICalled' && n.params.type === 'error') {
    konsole.push(n.params.args.map(a => a.value ?? a.description ?? a.type).join(' ').slice(0, 200));
  } else if (n.method === 'Runtime.exceptionThrown') {
    const d = n.params.exceptionDetails;
    ausnahmen.push((d.exception?.description || d.text || '').split('\n')[0].slice(0, 200));
  } else if (n.method === 'Network.loadingFailed') {
    // ERR_ABORTED zaehlt nicht als Fehler: MapLibre bricht beim Schwenken
    // laufende Kachelanfragen ab, sobald der Ausschnitt sie nicht mehr braucht.
    // Wer das meldet, erzeugt Rauschen, und ein Pruefer, dem man nicht glaubt,
    // ist so gut wie keiner.
    if (n.params.errorText === 'net::ERR_ABORTED') abgebrochen++;
    else netz.push(n.params.errorText + ' [' + n.params.type + ']');
  } else if (n.method === 'Network.responseReceived' && n.params.response.status >= 400) {
    netz.push('HTTP ' + n.params.response.status + ' ' + n.params.response.url.replace(PORTAL, '').slice(0, 120));
  }
  alt(e);
};
let maengel = 0;

// --- eine Ansicht -----------------------------------------------------------
async function fotografiere(name, hash, knoepfe) {
  const url = `${PORTAL}/#${hash}`;
  konsole = []; netz = []; ausnahmen = []; abgebrochen = 0;
  // ★ Ein reines Hash-Wechseln laedt die Seite NICHT neu, MapLibre wuerde dann
  //   nur hinschwenken und der vorige Bildmodus bliebe stehen. Also erst auf
  //   about:blank, dann die Zieladresse — jede Ansicht startet sauber.
  await ruf('Page.navigate', { url: 'about:blank' });
  await schlaf(200);
  await ruf('Page.navigate', { url });
  await schlaf(2500);

  for (const knopf of knoepfe) {
    // Die Umschalter entstehen zur Laufzeit, erst nachdem die Seite geprueft
    // hat, welche Regionen und Ebenen ueberhaupt vorliegen. Es wird deshalb auf
    // sie gewartet statt fest geschlafen.
    // ★ Ohne das Warten meldete dieser Pruefer sporadisch "(keine Knoepfe)" und
    //   erfand damit einen Mangel. Ein Pruefer, der falsch Alarm gibt, wird so
    //   schnell ignoriert wie einer, der nichts findet.
    let r;
    const gebFrist = Date.now() + 15000;
    do {
      r = await ruf('Runtime.evaluate', {
        expression: `(() => {
          const alle = [...document.querySelectorAll('#ebenen button')];
          if (!alle.length) return '(noch keine Knoepfe)';
          const b = alle.find(x => x.textContent.trim() === ${JSON.stringify(knopf)});
          if (!b) return alle.map(x => x.textContent.trim()).join(', ');
          b.click(); return 'geklickt';
        })()`,
        returnByValue: true,
      });
      if (r.result.value !== '(noch keine Knoepfe)') break;
      await schlaf(500);
    } while (Date.now() < gebFrist);
    if (r.result.value !== 'geklickt') {
      // ★ Kein blosser Hinweis, sondern ein Mangel: sonst entsteht ein Bild der
      //   Grundkarte unter dem Namen der Ebene, die geprueft werden sollte.
      //   Genau so ist "Gelaende" statt "Gelände" durchgerutscht, das Bild sah
      //   plausibel aus und zeigte die falsche Sache.
      console.error(`  ! FEHLER ${name}: Knopf "${knopf}" gibt es nicht. Vorhanden: ${r.result.value}`);
      maengel++;
    }
    await schlaf(1200);
  }

  // ★★ Maus in die Karte bewegen, bevor fotografiert wird. Ohne das bleibt die
  //    Koordinatenanzeige leer, und die Bilder zeigen eine Ecke, die es im
  //    Gebrauch so nie gibt. Genau daran ist der verdeckte Massstabsbalken
  //    vorbeigelaufen: die Anzeige lag mit hoeherem z-index darueber, aber auf
  //    jedem Pruefbild war sie leer und der Balken schien frei zu liegen.
  await ruf('Input.dispatchMouseEvent', { type: 'mouseMoved', x: 700, y: 400 });
  await schlaf(600);

  let vorher = null, stand = null;
  const ende = Date.now() + FRIST_MS;
  let runden = 0;
  while (Date.now() < ende) {
    const { data } = await ruf('Page.captureScreenshot', { format: 'png' });
    const summe = createHash('sha256').update(data).digest('hex');
    runden++;
    if (summe === vorher) { stand = data; break; }
    vorher = summe; stand = data;
    await schlaf(RUHE_MS);
  }
  const datei = `${ordner}/${name}.png`;
  writeFileSync(datei, Buffer.from(stand, 'base64'));
  const ruhig = runden > 1 && stand && createHash('sha256').update(stand).digest('hex') === vorher;
  if (!ruhig) maengel++;
  console.log(`${name}: ${datei} (${runden} Aufnahmen, ${ruhig ? 'zur Ruhe gekommen' : 'FRIST ABGELAUFEN, noch in Bewegung'}${abgebrochen ? `, ${abgebrochen} Anfragen verworfen` : ''})`);

  for (const [art, liste] of [['AUSNAHME', ausnahmen], ['KONSOLE', konsole], ['NETZ', netz]]) {
    for (const eintrag of [...new Set(liste)].slice(0, 6)) {
      console.error(`  ! ${art} ${name}: ${eintrag}`);
      maengel++;
    }
  }
}

for (const a of ansichten) {
  const [name, rest] = a.split('=');
  const [hash, knopfliste] = rest.split(':');
  await fotografiere(name, hash, knopfliste ? knopfliste.split(',') : []);
}
ws.close();

// ★ Rueckgabewert statt nur einer Zeile auf stderr: wer die Ausgabe ueberfliegt,
//   soll trotzdem stolpern. Ein Pruefwerkzeug, das immer 0 liefert, wird
//   frueher oder spaeter wie ein gruener Haken gelesen.
if (maengel) {
  console.error(`\n${maengel} Befund(e) waehrend der Aufnahmen.`);
  process.exit(1);
}
console.log('\nkeine Fehler waehrend der Aufnahmen');
