/* ===========================================================================
   Banc d'essai du service worker.
   ===========================================================================

   POURQUOI CE FICHIER EXISTE

   Le service worker est le seul code de l'application capable de la casser
   durablement : il survit au rechargement et au redéploiement. Ses règles ne
   peuvent donc pas être vérifiées « à l'œil » dans un navigateur — il faut
   pouvoir les rejouer à chaque exécution de la suite.

   Ce fichier recrée le strict nécessaire d'un ServiceWorkerGlobalScope
   (`self`, `caches`, `fetch`, `Request`, `Response`), charge le VRAI
   `app/web/static/sw.js`, puis lui envoie des requêtes et rapporte ce qu'il
   en a fait : parti au réseau, servi depuis le cache, ou pas intercepté du
   tout. Le résultat sort en JSON sur la sortie standard ; c'est
   `tests/test_pwa.py` qui l'exécute et qui juge.

   Il n'imite PAS un navigateur : ni cycle install/activate complet, ni
   priorités de cache réelles. Il vérifie les décisions d'aiguillage, qui
   sont exactement là où une erreur coûterait cher.
   =========================================================================== */

import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const ICI = dirname(fileURLToPath(import.meta.url));
const SOURCE = join(ICI, "..", "app", "web", "static", "sw.js");

const ORIGINE = "https://exemple.test";

// --- Faux environnement -----------------------------------------------------

const journal = { reseau: [], cachePut: [], cacheMatch: [], cachesSupprimes: [] };

class FauxReponse {
  constructor(corps, { status = 200, provenance = "reseau" } = {}) {
    this.corps = corps;
    this.status = status;
    this.ok = status >= 200 && status < 300;
    this.provenance = provenance;
  }
  clone() {
    return new FauxReponse(this.corps, { status: this.status, provenance: this.provenance });
  }
}

class FausseRequete {
  constructor(url, { method = "GET", mode = "no-cors" } = {}) {
    this.url = url;
    this.method = method;
    this.mode = mode;
  }
}

// Le cache commence VIDE ; chaque scénario y dépose ce dont il a besoin.
const contenuCache = new Map();

const faussesCaches = {
  async open() {
    return {
      async addAll(urls) {
        urls.forEach((u) => contenuCache.set(new URL(u, ORIGINE).href,
          new FauxReponse("coquille", { provenance: "cache" })));
      },
      async put(req, rep) {
        journal.cachePut.push(typeof req === "string" ? req : req.url);
        contenuCache.set(typeof req === "string" ? new URL(req, ORIGINE).href : req.url, rep);
      },
      async keys() {
        return [...contenuCache.keys()].map((u) => new FausseRequete(u));
      },
    };
  },
  async match(req) {
    const url = typeof req === "string" ? new URL(req, ORIGINE).href : req.url;
    journal.cacheMatch.push(url);
    return contenuCache.get(url) || undefined;
  },
  async keys() {
    return [...anciensCaches];
  },
  async delete(nom) {
    journal.cachesSupprimes.push(nom);
    anciensCaches.delete(nom);
    return true;
  },
};

let anciensCaches = new Set();
let reseauEnPanne = false;

async function fauxFetch(req) {
  const url = typeof req === "string" ? req : req.url;
  journal.reseau.push(url);
  if (reseauEnPanne) throw new Error("hors ligne");
  return new FauxReponse("du réseau", { provenance: "reseau" });
}

const ecouteurs = {};
const self_ = {
  location: { origin: ORIGINE },
  addEventListener(nom, fn) {
    (ecouteurs[nom] = ecouteurs[nom] || []).push(fn);
  },
  skipWaiting() {
    journal.skipWaiting = true;
  },
  clients: {
    claim() {
      journal.claim = true;
      return Promise.resolve();
    },
  },
};

// --- Chargement du vrai fichier --------------------------------------------

const code = readFileSync(SOURCE, "utf8");
const fabrique = new Function(
  "self", "caches", "fetch", "URL", "Request", "Response", "Promise",
  code + "\n;return { CACHE: typeof CACHE !== 'undefined' ? CACHE : null };"
);
const expose = fabrique(self_, faussesCaches, fauxFetch, URL, FausseRequete, FauxReponse, Promise);

// --- Scénarios --------------------------------------------------------------

/** Rejoue un `fetch` et dit ce que le worker en a fait. */
async function passer(url, options = {}) {
  const avant = journal.reseau.length;
  const req = new FausseRequete(new URL(url, ORIGINE).href, options);
  let repondu = null;
  const evt = {
    request: req,
    respondWith(p) {
      repondu = p;
    },
    waitUntil() {},
  };
  for (const fn of ecouteurs.fetch || []) fn(evt);

  if (repondu === null) {
    return { intercepte: false, reseau: journal.reseau.length > avant };
  }
  let rep;
  try {
    rep = await repondu;
  } catch (e) {
    return { intercepte: true, erreur: String(e) };
  }
  return {
    intercepte: true,
    provenance: rep ? rep.provenance : null,
    corps: rep ? rep.corps : null,
    reseauAppele: journal.reseau.length > avant,
  };
}

const resultats = {};

// 1. Installation : la coquille hors ligne est mise en cache, et le worker
//    prend la main sans attendre.
for (const fn of ecouteurs.install || []) fn({ waitUntil: (p) => (resultats._install = p) });
await resultats._install;
delete resultats._install;
resultats.coquilleEnCache = contenuCache.has(new URL("/static/offline.html", ORIGINE).href);
resultats.skipWaitingALInstall = journal.skipWaiting === true;

// 2. Activation : les caches des versions précédentes sont purgés.
anciensCaches = new Set(["samourais-ancien", expose.CACHE]);
for (const fn of ecouteurs.activate || []) fn({ waitUntil: (p) => (resultats._act = p) });
await resultats._act;
delete resultats._act;
resultats.cachesSupprimes = journal.cachesSupprimes;
resultats.claim = journal.claim === true;

// 3. Une navigation part au réseau, même si la page est déjà en cache.
contenuCache.set(new URL("/viewer", ORIGINE).href,
  new FauxReponse("VIEILLE PAGE", { provenance: "cache" }));
resultats.navigation = await passer("/viewer", { mode: "navigate" });

// 4. Réseau coupé : la même navigation retombe sur le cache.
//    On REPOSE une empreinte reconnaissable : le passage précédent vient d'y
//    ranger sa propre copie réseau, et sans ça on ne saurait pas distinguer
//    « servi depuis le cache » de « servi par le réseau ».
contenuCache.set(new URL("/viewer", ORIGINE).href,
  new FauxReponse("VIEILLE PAGE", { provenance: "cache" }));
reseauEnPanne = true;
resultats.navigationHorsLigne = await passer("/viewer", { mode: "navigate" });

// 5. Réseau coupé, page jamais visitée : page « hors ligne ».
resultats.navigationInconnueHorsLigne = await passer("/jamais-vue", { mode: "navigate" });
reseauEnPanne = false;

// 6. L'API n'est jamais interceptée.
resultats.api = await passer("/api/viewer/corbeille");

// 7. Les médias non plus.
resultats.media = await passer("/media/thumb/x.jpg");

// 8. Une écriture n'est jamais interceptée, même sur une URL statique.
resultats.ecriture = await passer("/static/tokens.css", { method: "POST" });

// 9. Un statique déjà en cache est servi depuis le cache.
contenuCache.set(new URL("/static/tokens.css?v=42", ORIGINE).href,
  new FauxReponse("CSS EN CACHE", { provenance: "cache" }));
resultats.statiqueEnCache = await passer("/static/tokens.css?v=42");

// 10. Une autre origine n'est jamais touchée.
resultats.autreOrigine = await passer("https://ailleurs.test/x.js");

process.stdout.write(JSON.stringify(resultats, null, 2));
