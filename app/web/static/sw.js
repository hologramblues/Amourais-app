/* ===========================================================================
   Service worker — SAMOURAIS SCRAPPER
   ===========================================================================

   CE QU'IL FAIT, ET SURTOUT CE QU'IL NE FAIT PAS

   Un service worker est le seul morceau de code de cette application capable
   de la casser DURABLEMENT : il survit au rechargement, il survit au
   redéploiement, et un mauvais cache peut servir une version morte du site
   à un téléphone pendant des semaines sans qu'aucun bouton « actualiser »
   n'y change quoi que ce soit. Le propriétaire a déjà cru deux fois que
   Railway lui servait l'ancienne version ; ici, ce serait vrai et sans
   recours. Toutes les décisions ci-dessous découlent de ce risque-là.

   RÈGLE 1 — Les PAGES ne sont jamais servies depuis le cache en premier.
   Une navigation part TOUJOURS au réseau. Le cache n'est qu'un filet en cas
   de coupure. C'est ce qui garantit qu'un déploiement est visible au
   rechargement suivant, comme sans service worker.

   RÈGLE 2 — L'API et les médias ne sont pas mis en cache du tout.
   `/api/…` renvoie l'état de la médiathèque : un compteur, une corbeille,
   un job en cours. Une réponse servie depuis un cache serait un mensonge.
   `/media/…` peut disparaître (vidage de la corbeille) : une vignette
   ressortie du cache montrerait un média supprimé.

   RÈGLE 3 — Les fichiers statiques sont versionnés par l'URL.
   `asset()` colle `?v=<mtime>` derrière chaque CSS/JS (layout.html). Une URL
   ne désigne donc jamais deux contenus différents : le cache-first est sûr,
   et un fichier corrigé change d'URL, donc de clé de cache.

   RÈGLE 4 — Le worker se remplace lui-même sans attendre.
   `skipWaiting` + `clients.claim` : la version suivante prend la main au
   premier chargement, au lieu d'attendre que tous les onglets soient fermés.
   Combiné à la règle 1, un déploiement ne peut pas rester coincé.

   La ligne `BUILD` est réécrite à la volée par la route `/sw.js` (routes.py)
   avec l'empreinte des fichiers statiques : dès qu'un asset bouge, les
   octets de ce fichier changent, le navigateur voit un worker différent et
   l'installe. Aucun numéro de version à penser à incrémenter à la main.
   =========================================================================== */

const BUILD = "dev";
const CACHE = "samourais-" + BUILD;
const HORS_LIGNE = "/static/offline.html";

// Le strict minimum pour afficher quelque chose sans réseau. Volontairement
// court : plus la liste est longue, plus une install peut échouer en entier
// (une seule 404 fait rejeter addAll) et laisser l'app sans worker.
const COQUILLE = [HORS_LIGNE];

self.addEventListener("install", (e) => {
  e.waitUntil(
    caches.open(CACHE).then((c) => c.addAll(COQUILLE)).then(() => self.skipWaiting())
  );
});

self.addEventListener("activate", (e) => {
  e.waitUntil(
    caches
      .keys()
      .then((noms) =>
        Promise.all(noms.filter((n) => n !== CACHE).map((n) => caches.delete(n)))
      )
      .then(() => self.clients.claim())
  );
});

// Permet de forcer la relève depuis la page (bouton « mettre à jour »).
self.addEventListener("message", (e) => {
  if (e.data === "skip-waiting") self.skipWaiting();
});

function estStatique(url) {
  return url.pathname.startsWith("/static/");
}

self.addEventListener("fetch", (e) => {
  const req = e.request;

  // Tout ce qui n'est pas une lecture simple passe DROIT au réseau : une
  // suppression, un enregistrement de phrase ou un vidage de corbeille ne
  // doivent jamais rencontrer ce fichier.
  if (req.method !== "GET") return;

  const url = new URL(req.url);
  if (url.origin !== self.location.origin) return;

  // Règle 2 : l'état de l'application ne se met pas en conserve.
  if (url.pathname.startsWith("/api/") || url.pathname.startsWith("/media/")) return;

  // Règle 1 : les pages d'abord au réseau, le cache seulement en secours.
  if (req.mode === "navigate") {
    e.respondWith(
      fetch(req)
        .then((rep) => {
          // On garde une copie de la dernière page vue pour la coupure
          // suivante — mais on ne s'en sert JAMAIS tant que le réseau répond.
          const copie = rep.clone();
          caches.open(CACHE).then((c) => c.put(req, copie)).catch(() => {});
          return rep;
        })
        .catch(() =>
          caches.match(req).then((c) => c || caches.match(HORS_LIGNE))
        )
    );
    return;
  }

  // Règle 3 : statiques versionnés — on sert le cache et on rafraîchit
  // derrière. L'URL portant le mtime, « périmé » n'existe pas ici.
  if (estStatique(url)) {
    e.respondWith(
      caches.match(req).then((enCache) => {
        const reseau = fetch(req)
          .then((rep) => {
            if (rep && rep.ok) {
              const copie = rep.clone();
              caches.open(CACHE).then((c) => c.put(req, copie)).catch(() => {});
            }
            return rep;
          })
          .catch(() => enCache);
        return enCache || reseau;
      })
    );
  }
});
