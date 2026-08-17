/* ============================================================
   SAMOURAIS — ANALYTICS
   ------------------------------------------------------------
   Trois principes tenus par ce fichier :

   1. UNE SEULE PÉRIODE. Le sélecteur du haut est la seule entrée de
      filtrage temporel ; tout bloc qui ne peut pas être filtré par le
      serveur l'est ici, côté client, et le dit dans sa note.

   2. UNE SEULE RÈGLE DE COMPARAISON, écrite à l'écran : chaque chiffre
      est comparé à la même mesure sur les N jours qui précèdent
      immédiatement la période affichée. Si les données ne couvrent pas
      réellement cette période antérieure, la variation est ABSENTE.
      Aucun « 0 % » n'est jamais affiché à la place d'une absence.

   3. AUCUNE COULEUR EN DUR. Chart.js dessine sur un canvas et ne
      connaît pas le CSS : les jetons sont lus sur :root au moment du
      rendu (readPalette) et les graphes sont redessinés à chaque
      changement de thème (watchTheme).
   ============================================================ */

(function () {
  'use strict';

  // ══════════════════════════════════════════════════════════
  // 0. Constantes
  // ══════════════════════════════════════════════════════════

  var MAX_DAYS = 365;                 // borne serveur de ?days=
  var DAY = 86400;
  var PRESETS = [7, 30, 90, 365];
  var VIEWER_PAGE = 200;              // borne serveur de ?per_page=
  var VIEWER_MAX_PAGES = 5;           // 1000 médias au plus : on borne le coût
  var HEAT_MIN_POSTS = 12;            // en-deçà, la heatmap se déclare insuffisante
  var STALE_AFTER = 36 * 3600;        // synchro « en retard » au-delà de 36 h
  var PERF_LIMITE = 500;              // posts au plus dans le classement

  // Seuil de fiabilité d'un agrégat de performance. En dessous, la
  // moyenne du groupe est affichée mais SIGNALÉE, et surtout le
  // « verdict » ne la cite pas : désigner un meilleur jour sur deux
  // posts, ce n'est pas une lecture, c'est du bruit.
  var AGG_MIN = 3;

  var JOURS = ['Lundi', 'Mardi', 'Mercredi', 'Jeudi', 'Vendredi', 'Samedi', 'Dimanche'];

  // Un type de média porte le MÊME nom partout : pastille du tableau,
  // légende de la ventilation, export CSV.
  var TYPES = { image: 'Photo', video: 'Vidéo', carousel: 'Carrousel' };

  var PLATEFORMES = {
    instagram: 'Instagram', twitter: 'X / Twitter',
    tiktok: 'TikTok', reddit: 'Reddit'
  };

  // Seule Instagram alimente `ig_like_count` / `ig_comment_count` /
  // `ig_view_count` : l'extracteur public (app/scraper/instagram.py) et
  // la Graph API (app/analytics/ig_collector.py). Les extracteurs
  // Twitter, TikTok et Reddit ne lisent aucun compteur — l'absence y
  // est définitive en l'état, et l'écran le dit plutôt que de proposer
  // un geste sans effet.
  function nomPlateforme(p) { return PLATEFORMES[p] || p || 'Autre'; }

  // Une série garde LA MÊME couleur dans tous les graphes de l'écran.
  // La valeur est l'index du jeton --chart-N (1..5).
  var SERIE = {
    followers: 1, posts: 2, likes: 1, comments: 4,
    reach: 1, impressions: 4, profile_views: 5, accounts_engaged: 2,
    image: 1, video: 4, carousel: 5, other: 3
  };

  var TRENDS = {
    followers:        { titre: 'Abonnés',            type: 'line' },
    posts:            { titre: 'Posts publiés',      type: 'bar'  },
    reach:            { titre: 'Couverture',         type: 'line' },
    impressions:      { titre: 'Impressions',        type: 'line' },
    profile_views:    { titre: 'Visites de profil',  type: 'line' },
    accounts_engaged: { titre: 'Comptes engagés',    type: 'line' }
  };

  // ══════════════════════════════════════════════════════════
  // 1. Formatage
  // ══════════════════════════════════════════════════════════

  var nfInt = new Intl.NumberFormat('fr-FR');
  var nfDec = new Intl.NumberFormat('fr-FR', { minimumFractionDigits: 1, maximumFractionDigits: 1 });
  var nfAbs = new Intl.DateTimeFormat('fr-FR', {
    day: 'numeric', month: 'long', year: 'numeric', hour: '2-digit', minute: '2-digit'
  });
  var nfJour = new Intl.DateTimeFormat('fr-FR', { day: 'numeric', month: 'long', year: 'numeric' });
  var nfCourt = new Intl.DateTimeFormat('fr-FR', { day: '2-digit', month: '2-digit' });

  function fmtInt(n) { return (n == null || !isFinite(n)) ? '—' : nfInt.format(Math.round(n)); }
  function fmtPct(n) { return (n == null || !isFinite(n)) ? '—' : nfDec.format(n) + ' %'; }
  function fmtCompact(n) {
    if (n == null || !isFinite(n)) return '—';
    var a = Math.abs(n);
    // Une décimale sur les paliers : sur une échelle serrée (abonnés),
    // arrondir au millier collerait deux graduations sur le même libellé.
    if (a >= 1e6) return nfDec.format(n / 1e6) + ' M';
    if (a >= 1e4) return nfDec.format(n / 1e3) + ' k';
    return nfInt.format(n);
  }
  function dateAbs(ts) { return (ts == null) ? null : nfAbs.format(new Date(ts * 1000)).replace(/\s+à\s+/, ', '); }
  function dateJour(ts) { return (ts == null) ? null : nfJour.format(new Date(ts * 1000)); }
  function dateCourte(ts) { return (ts == null) ? '—' : nfCourt.format(new Date(ts * 1000)); }
  function heure(ts) {
    if (ts == null) return '—';
    var d = new Date(ts * 1000);
    return String(d.getHours()).padStart(2, '0') + ':' + String(d.getMinutes()).padStart(2, '0');
  }
  /** Lundi = 0 … Dimanche = 6 (getDay() met dimanche à 0). */
  function jourIndex(ts) { return (new Date(ts * 1000).getDay() + 6) % 7; }

  function texte(el, s) { if (el) el.textContent = s; }

  // ══════════════════════════════════════════════════════════
  // 2. Palette — les jetons CSS lus au moment du rendu
  // ══════════════════════════════════════════════════════════

  /** Résout un jeton ; si sa valeur est encore un var(), suit le repli. */
  function jeton(cs, nom, repli) {
    var v = (cs.getPropertyValue(nom) || '').trim();
    if (!v || v.indexOf('var(') === 0) v = repli ? (cs.getPropertyValue(repli) || '').trim() : '';
    return v;
  }

  function readPalette() {
    var cs = getComputedStyle(document.documentElement);
    return {
      fg1:   jeton(cs, '--fg-1'),
      fg2:   jeton(cs, '--fg-2'),
      fg3:   jeton(cs, '--fg-3'),
      bg1:   jeton(cs, '--bg-1'),
      bord:  jeton(cs, '--border-2'),
      grid:  jeton(cs, '--chart-grid', '--border-1'),
      serie: [1, 2, 3, 4, 5].map(function (i) { return jeton(cs, '--chart-' + i); })
    };
  }

  /** Un jeton hexadécimal + un alpha → rgba(), seule forme que le canvas lit. */
  function alpha(couleur, a) {
    var m = /^#([0-9a-f]{3}|[0-9a-f]{6})$/i.exec((couleur || '').trim());
    if (!m) return couleur;
    var h = m[1];
    if (h.length === 3) h = h[0] + h[0] + h[1] + h[1] + h[2] + h[2];
    var n = parseInt(h, 16);
    return 'rgba(' + ((n >> 16) & 255) + ',' + ((n >> 8) & 255) + ',' + (n & 255) + ',' + a + ')';
  }

  function couleurSerie(pal, cle) {
    var i = (SERIE[cle] || 1) - 1;
    return pal.serie[i] || pal.serie[0];
  }

  // ══════════════════════════════════════════════════════════
  // 3. Réseau
  // ══════════════════════════════════════════════════════════

  function getJSON(chemin, params, methode) {
    var url = new URL(chemin, location.origin);
    Object.keys(params || {}).forEach(function (k) {
      if (params[k] != null) url.searchParams.set(k, params[k]);
    });
    return fetch(url.toString(), {
      method: methode || 'GET',
      headers: { Accept: 'application/json' }
    }).then(function (r) {
      return r.json().catch(function () { return null; }).then(function (corps) {
        if (!r.ok) {
          var e = new Error((corps && corps.error) || ('HTTP ' + r.status));
          e.status = r.status;
          throw e;
        }
        return corps;
      });
    });
  }

  // Tous les endpoints de l'écran passent par ici : ajouter le compte
  // à cet endroit suffit à ce que TOUS les blocs suivent le sélecteur,
  // exactement comme ils suivent déjà la période.
  function analytics(nom, days) {
    return getJSON('/api/analytics/' + nom, { days: days, profile_id: S.profileId });
  }

  // ══════════════════════════════════════════════════════════
  // 4. État
  // ══════════════════════════════════════════════════════════

  var S = {
    days: 30,
    profileId: null,     // null = le serveur choisit (premier Instagram actif)
    profiles: [],
    now: Math.floor(Date.now() / 1000),
    apercu: null,        // ig-api-status
    compte: null,        // account-overview
    compteErr: null,
    eng: null,           // { cur, prec, couvert }
    freq: null,          // { cur, prec, couvert, labels, data }
    abonnes: null,       // { labels, valeurs, precedent }
    insights: null,      // reach-impressions
    perf: null,          // media-performance : { items, counts, … } ou null
    perfErr: null,
    scope: 'profile',    // 'profile' | 'all'
    medias: null,        // source B : { liste, total, tronque } ou null
    tri: { cle: 'eng', sens: -1 },
    charts: {}
  };

  var chartsDispo = (typeof Chart !== 'undefined');

  // ══════════════════════════════════════════════════════════
  // 5. Blocs d'état — un seul gabarit, aucun HTML bricolé en JS
  // ══════════════════════════════════════════════════════════

  function bloc(id) { return document.getElementById(id).content.firstElementChild.cloneNode(true); }

  function boutons(conteneur, actions) {
    (actions || []).forEach(function (a) {
      var el;
      if (a.href) { el = document.createElement('a'); el.href = a.href; }
      else { el = document.createElement('button'); el.type = 'button'; if (a.onClick) el.addEventListener('click', a.onClick); }
      el.className = 'btn' + (a.primaire ? '' : ' btn--ghost');
      el.textContent = a.label;
      conteneur.appendChild(el);
    });
  }

  /** État vide composé : titre 20px + description ≤360px + ≤2 boutons. */
  function poserVide(slotId, titre, description, actions) {
    var slot = document.getElementById(slotId);
    if (!slot) return;
    slot.textContent = '';
    var n = bloc('tpl-empty');
    n.querySelector('.empty__title').textContent = titre;
    n.querySelector('.empty__text').textContent = description;
    boutons(n.querySelector('.empty__actions'), (actions || []).slice(0, 2));
    slot.appendChild(n);
  }

  function viderSlot(slotId) {
    var slot = document.getElementById(slotId);
    if (slot) slot.textContent = '';
  }

  function afficherCanvas(boxId, visible) {
    var box = document.getElementById(boxId);
    if (box) box.hidden = !visible;
  }

  /**
   * Le cas racine : sans compte en base, aucun bloc n'a de source.
   * Le premier bloc porte l'explication complète et les deux gestes ;
   * les suivants nomment seulement ce qui leur manque, pour ne pas
   * répéter quatre fois le même paragraphe.
   */
  function videSansCompte(slotId, boxId, manque) {
    if (boxId) afficherCanvas(boxId, false);
    if (manque) {
      poserVide(slotId, 'Aucun compte à analyser',
        'Le compte Instagram @samourais_ n’existe pas en base : ' + manque + '.',
        [{ label: 'Ajouter le profil', href: '/profiles', primaire: true }]);
      return;
    }
    poserVide(slotId, 'Aucun compte à analyser',
      'Cet écran calcule les statistiques du compte Instagram @samourais_, qui n’existe pas '
      + 'en base. Ajoute-le dans Profils, ou connecte l’API Graph pour qu’il soit créé '
      + 'automatiquement à la première collecte.',
      [{ label: 'Ajouter le profil', href: '/profiles', primaire: true },
       { label: 'Réglages', href: '/settings#ig-api' }]);
  }

  // ══════════════════════════════════════════════════════════
  // 6. La règle de comparaison — unique, et écrite à l'écran
  // ══════════════════════════════════════════════════════════

  function bornes() {
    var fin = S.now;
    var debut = fin - S.days * DAY;
    return { debut: debut, fin: fin, refDebut: debut - S.days * DAY, refFin: debut };
  }

  function libelleReference() {
    var b = bornes();
    return dateJour(b.refDebut) + ' – ' + dateJour(b.refFin);
  }

  /**
   * Écrit une variation, ou son absence.
   * @param cle   clé du KPI (attribut data-delta)
   * @param cur   valeur de la période
   * @param prec  valeur de la période de référence, ou null si indisponible
   * @param raison phrase expliquant l'absence (affichée au survol)
   * @param fmt   formateur des valeurs absolues
   */
  function poserDelta(cle, cur, prec, raison, fmt) {
    var el = document.querySelector('[data-delta="' + cle + '"]');
    if (!el) return;
    el.textContent = '';
    fmt = fmt || fmtInt;

    var indisponible =
      prec == null || !isFinite(prec) || cur == null || !isFinite(cur) || prec === 0;

    if (indisponible) {
      var span = document.createElement('span');
      span.className = 'delta delta--none';
      span.textContent = '—';
      span.title = raison || ('Aucune donnée sur la période de référence (' + libelleReference() + ').');
      el.appendChild(span);
      return;
    }

    var pct = (cur - prec) / Math.abs(prec) * 100;
    var sens = pct > 0.05 ? 'up' : (pct < -0.05 ? 'down' : 'flat');
    var fleche = sens === 'up' ? '▲' : (sens === 'down' ? '▼' : '=');

    var wrap = document.createElement('span');
    wrap.className = 'delta delta--' + sens;
    wrap.title = fmt(cur) + ' contre ' + fmt(prec) + ' ('
               + (cur - prec >= 0 ? '+' : '−') + fmt(Math.abs(cur - prec))
               + ') sur la période de référence : ' + libelleReference();

    var a = document.createElement('span');
    a.className = 'delta__arrow';
    a.setAttribute('aria-hidden', 'true');
    a.textContent = fleche;

    var t = document.createElement('span');
    t.textContent = (pct > 0 ? '+' : (pct < 0 ? '−' : '')) + nfDec.format(Math.abs(pct)) + ' %';

    wrap.appendChild(a);
    wrap.appendChild(t);
    el.appendChild(wrap);

    var vs = document.createElement('span');
    vs.textContent = 'vs période précédente';
    el.appendChild(vs);
  }

  function poserValeur(cle, texteValeur, absent) {
    var el = document.querySelector('[data-kpi="' + cle + '"]');
    if (!el) return;
    el.textContent = texteValeur;
    if (absent) el.setAttribute('data-absent', ''); else el.removeAttribute('data-absent');
  }

  // ══════════════════════════════════════════════════════════
  // 7. Chargement
  // ══════════════════════════════════════════════════════════

  function sommeSerie(d, champ) {
    var t = (d && d[champ]) || [];
    return t.reduce(function (a, b) { return a + (b || 0); }, 0);
  }

  /**
   * Le compte d'abord, et lui seul : s'il n'existe pas, aucune des
   * autres requêtes ne peut aboutir. On ne lance pas neuf appels
   * condamnés d'avance pour salir la console et l'attente.
   */
  function chargerCompte() {
    return Promise.all([
      getJSON('/api/analytics/ig-api-status', {}).catch(function () { return null; }),
      analytics('account-overview', S.days).catch(function (e) { S.compteErr = e; return null; })
    ]).then(function (r) {
      S.apercu = r[0];
      if (r[1] && !r[1].error) { S.compte = r[1]; S.compteErr = null; }
      rendreEntete();
      rendreBandeau();
    });
  }

  function chargerPeriode() {
    S.now = Math.floor(Date.now() / 1000);
    var N = S.days;
    var N2 = Math.min(N * 2, MAX_DAYS);
    var comparable = N * 2 <= MAX_DAYS;

    ecrirePeriode();

    if (!S.compte && S.scope !== 'all') {
      S.eng = null; S.freq = null; S.abonnes = null; S.insights = null;
      S.perf = null;
      rendreKPIs(); rendreTrend(); rendreSplit(); rendreHeatmap();
      rendreTable(); rendreAgregats();
      return Promise.resolve();
    }

    var doubleDispo = comparable;

    return Promise.all([
      Promise.resolve(S.compte),
      analytics('engagement', N).catch(function () { return null; }),
      doubleDispo ? analytics('engagement', N2).catch(function () { return null; }) : Promise.resolve(null),
      analytics('engagement', MAX_DAYS).catch(function () { return null; }),
      analytics('posting-frequency', N).catch(function () { return null; }),
      doubleDispo ? analytics('posting-frequency', N2).catch(function () { return null; }) : Promise.resolve(null),
      analytics('posting-frequency', MAX_DAYS).catch(function () { return null; }),
      analytics('follower-growth', doubleDispo ? N2 : N).catch(function () { return null; }),
      analytics('reach-impressions', N).catch(function () { return null; }),
      chargerPerformance()
    ]).then(function (r) {
      var eN = r[1], e2N = r[2], e365 = r[3];
      var fN = r[4], f2N = r[5], f365 = r[6], croissance = r[7], insights = r[8];

      // ── Engagement : la période de référence est la différence des
      //    deux fenêtres. Elle n'est retenue que si des posts existent
      //    AVANT son début (sondage sur 365 jours) : sinon on ne sait
      //    pas distinguer « zéro post » de « aucune donnée ».
      var nCur = eN ? (eN.labels || []).length : null;
      var n2 = e2N ? (e2N.labels || []).length : null;
      var n365 = e365 ? (e365.labels || []).length : null;
      var couvertEng = comparable && n2 != null && n365 != null && n365 > n2;
      S.eng = {
        n: nCur,
        likes: eN ? sommeSerie(eN, 'likes') : null,
        comments: eN ? sommeSerie(eN, 'comments') : null,
        serie: eN,
        prec: (couvertEng && nCur != null) ? {
          n: n2 - nCur,
          likes: sommeSerie(e2N, 'likes') - sommeSerie(eN, 'likes'),
          comments: sommeSerie(e2N, 'comments') - sommeSerie(eN, 'comments')
        } : null,
        couvert: couvertEng
      };

      var pCur = fN ? sommeSerie(fN, 'data') : null;
      var p2 = f2N ? sommeSerie(f2N, 'data') : null;
      var p365 = f365 ? sommeSerie(f365, 'data') : null;
      var couvertPosts = comparable && p2 != null && p365 != null && p365 > p2;
      S.freq = {
        n: pCur,
        serie: fN,
        prec: (couvertPosts && pCur != null) ? Math.max(0, p2 - pCur) : null,
        couvert: couvertPosts
      };

      // ── Abonnés : valeur d'aujourd'hui contre valeur à la fin de la
      //    période de référence, c'est-à-dire au début de la période
      //    affichée. Sans instantané antérieur à ce point, pas de
      //    comparaison — et on le dit.
      var b = bornes();
      var precAbo = null;
      if (croissance && croissance.labels && croissance.labels.length) {
        for (var i = croissance.labels.length - 1; i >= 0; i--) {
          var ts = Date.parse(croissance.labels[i] + 'T12:00:00') / 1000;
          if (ts <= b.debut) { precAbo = croissance.followers[i]; break; }
        }
      }
      S.abonnes = {
        labels: (croissance && croissance.labels) || [],
        valeurs: (croissance && croissance.followers) || [],
        precedent: precAbo
      };

      S.insights = insights;

      rendreKPIs();
      rendreTrend();
      rendreSplit();
      rendreHeatmap();
      rendreTable();
      rendreAgregats();
    });
  }

  /**
   * LOT B — le classement par performance réelle du post d'origine.
   *
   * Il dépend de la période (comme tout l'écran) ET de la portée : le
   * même endpoint sert le compte affiché ou l'ensemble des comptes
   * suivis. Le serveur regroupe les enfants d'un carrousel sur leur
   * post, et ne remplace JAMAIS un compteur absent par zéro : c'est
   * `measured` qui sépare les deux populations, pas la valeur.
   */
  function chargerPerformance() {
    if (!S.compte && S.scope !== 'all') {
      S.perf = null; S.perfErr = null;
      return Promise.resolve();
    }
    return getJSON('/api/analytics/media-performance', {
      days: S.days, profile_id: S.profileId, scope: S.scope, limit: PERF_LIMITE
    }).then(function (d) {
      S.perf = d; S.perfErr = null;
    }).catch(function (e) {
      S.perf = null; S.perfErr = e;
    });
  }

  /**
   * Source B — la médiathèque du compte, pour la distribution
   * jour × heure et les miniatures. Le serveur d'analytics n'expose ni
   * l'une ni les autres. Tout échec ici est absorbé : les blocs
   * concernés basculent sur leur état « source indisponible ».
   */
  function chargerMedias() {
    if (!S.compte || !S.compte.username) return Promise.resolve(null);

    return getJSON('/api/viewer/profiles', {}).then(function (profils) {
      var p = (profils || []).find(function (x) {
        return x.platform === 'instagram' && x.username === S.compte.username;
      });
      if (!p) return null;

      var liste = [];
      var limite = Math.floor(Date.now() / 1000) - MAX_DAYS * DAY;

      /** Résout `true` si la fenêtre de 365 jours est entièrement lue. */
      function page(n) {
        if (n > VIEWER_MAX_PAGES) return Promise.resolve(false);
        return getJSON('/api/viewer/media', {
          profile_id: p.id, per_page: VIEWER_PAGE, page: n, sort: 'date_desc'
        }).then(function (d) {
          var items = (d && d.items) || [];
          liste = liste.concat(items);
          var pages = (d && d.total_pages) || 1;
          var dernier = items.length ? items[items.length - 1].posted_at : null;
          if (!items.length || n >= pages) return true;
          if (dernier != null && dernier < limite) return true;
          return page(n + 1);
        });
      }

      return page(1).then(function (complet) {
        var vus = {};
        var propres = [];
        liste.forEach(function (m) {
          if (m.posted_at == null) return;
          var cle = m.post_url || ('id:' + m.id);
          if (vus[cle]) return;
          vus[cle] = 1;
          propres.push({ posted_at: m.posted_at, media_type: m.media_type });
        });
        return { liste: propres, complet: complet };
      });
    }).catch(function () { return null; });
  }

  // ══════════════════════════════════════════════════════════
  // 8. Rendu — en-tête, fraîcheur (A5), bandeau (A6)
  // ══════════════════════════════════════════════════════════

  function ecrirePeriode() {
    var b = bornes();
    var comparable = S.days * 2 <= MAX_DAYS;
    texte(document.getElementById('period-main'),
      'Du ' + dateJour(b.debut) + ' au ' + dateJour(b.fin) + ' (' + S.days + ' jours)');
    texte(document.getElementById('period-rule'),
      comparable
        ? '· Comparaison : les ' + S.days + ' jours précédents, soit ' + libelleReference()
          + '. Une variation n’est affichée que si les données couvrent réellement cette période ; sinon elle est absente.'
        : '· Aucune comparaison : la période de référence sortirait de la fenêtre de 365 jours servie par l’API.');
  }

  function rendreEntete() {
    var a = S.apercu || {};
    var compte = S.compte;
    var acct = document.getElementById('acct');
    var sep = document.getElementById('acct-sep');
    var av = document.getElementById('acct-avatar');

    if (compte && compte.username) {
      acct.hidden = false;
      sep.hidden = false;
      texte(document.getElementById('acct-handle'), '@' + compte.username);
      if (compte.avatar_url) { av.src = compte.avatar_url; av.hidden = false; }
      else { av.hidden = true; }
    } else {
      acct.hidden = true;
      sep.hidden = true;
    }

    var sync = document.getElementById('sync');
    if (a.last_snapshot) {
      var retard = (Math.floor(Date.now() / 1000) - a.last_snapshot) > STALE_AFTER;
      texte(sync, 'Dernière synchronisation réussie : ' + dateAbs(a.last_snapshot));
      sync.dataset.state = retard ? 'stale' : 'ok';
    } else {
      texte(sync, 'Aucune synchronisation réussie à ce jour');
      sync.dataset.state = 'none';
    }
  }

  function rendreBandeau() {
    var slot = document.getElementById('alert-slot');
    slot.textContent = '';
    var a = S.apercu || {};
    var niveau = null, titre = '', corps = '', actions = [];
    var derniere = a.last_snapshot ? dateAbs(a.last_snapshot) : null;

    if (!a.has_profile || S.compteErr) {
      niveau = 'danger';
      titre = 'Erreur — aucun compte Instagram analysable';
      corps = (S.compteErr && S.compteErr.message)
        || 'Le profil @samourais_ n’existe pas en base : aucun chiffre de cet écran ne peut être calculé.';
      corps += ' Dernière donnée valide : ' + (derniere || 'aucune');
      actions = [
        { label: 'Ajouter le profil', href: '/profiles', primaire: true },
        { label: 'Réglages', href: '/settings' }
      ];
    } else if (!a.configured) {
      niveau = 'danger';
      titre = 'Erreur — API Instagram non connectée'
            + (S.compte && S.compte.username ? ' (@' + S.compte.username + ')' : '');
      corps = 'Le jeton d’accès Graph API est absent ou expiré. Couverture, impressions, '
            + 'visites de profil et historique d’abonnés ne sont plus alimentés — ils resteront vides, '
            + 'et non à zéro. Dernière donnée valide : ' + (derniere || 'aucune') + '.';
      actions = [{ label: 'Reconnecter le compte', href: '/settings#ig-api', primaire: true }];
    } else if (!a.snapshot_count) {
      niveau = 'warning';
      titre = 'Avertissement — aucune donnée collectée';
      corps = 'L’API est connectée mais aucune collecte n’a encore abouti. '
            + 'La collecte tourne toutes les 6 heures ; tu peux la déclencher tout de suite.';
      actions = [
        { label: 'Collecter maintenant', primaire: true, onClick: collecterMaintenant },
        { label: 'Réglages', href: '/settings#ig-api' }
      ];
    } else if ((Math.floor(Date.now() / 1000) - a.last_snapshot) > STALE_AFTER) {
      niveau = 'warning';
      titre = 'Avertissement — synchronisation en retard';
      corps = 'Aucune collecte réussie depuis plus de 36 heures. Dernière donnée valide : '
            + derniere + '. Le jeton a pu expirer.';
      actions = [
        { label: 'Collecter maintenant', primaire: true, onClick: collecterMaintenant },
        { label: 'Vérifier le jeton', href: '/settings#ig-api' }
      ];
    }

    if (!niveau) return;   // tout va bien : la fraîcheur suffit, pas de bandeau

    var n = bloc('tpl-notice');
    n.classList.add('notice--' + niveau);
    n.querySelector('.notice__title').textContent = titre;
    n.querySelector('.notice__text').textContent = corps;
    boutons(n.querySelector('.notice__actions'), actions);
    slot.appendChild(n);
  }

  /** Message d'échec posé À CÔTÉ du bouton qui a échoué, jamais un toast. */
  function messageAction(bouton, texte_) {
    var actions = bouton.parentElement;
    var notice = actions.closest('.notice');
    var hote = notice ? notice.querySelector('.notice__body') : actions.parentElement;
    var p = hote.querySelector('.act-error');
    if (!p) {
      p = document.createElement('p');
      p.className = 'act-error';
      p.setAttribute('role', 'status');
      hote.appendChild(p);
    }
    p.textContent = texte_;
  }

  function collecterMaintenant(ev) {
    var b = ev.currentTarget;
    b.disabled = true;
    var avant = b.textContent;
    b.textContent = 'Collecte lancée…';         // retour < 100 ms, le réseau suit

    // Retour à l'état antérieur ET cause écrite : un échec ne laisse jamais
    // le bouton figé sur « Collecte lancée… » (G24).
    function echec(cause) {
      b.disabled = false;
      b.textContent = avant;
      messageAction(b, 'La collecte n’a pas pu être lancée. Cause : ' + cause
        + '. Réessaie ; si l’erreur persiste, vérifie le jeton dans les réglages.');
    }

    getJSON('/api/analytics/collect-now', null, 'POST')
      .then(function () {
        var p = document.querySelector('.act-error');
        if (p) p.remove();
        return new Promise(function (r) { setTimeout(r, 2500); });
      })
      .then(function () { return chargerCompte(); })
      .then(function () { return chargerPeriode(); })
      .catch(function (e) {
        // Une panne réseau rejette avec « Failed to fetch » : on ne recopie
        // pas ce texte brut à l'écran, on dit ce qui s'est passé (G27).
        echec((e && e.status) ? e.message : 'le serveur n’a pas répondu');
      });
  }

  // ══════════════════════════════════════════════════════════
  // 9. Rendu — les 5 agrégats
  // ══════════════════════════════════════════════════════════

  function rendreKPIs() {
    var c = S.compte, e = S.eng, f = S.freq;
    var b = bornes();

    // 1 — Abonnés
    var abo = c ? c.followers_count : null;
    poserValeur('followers', abo ? fmtInt(abo) : '—', !abo);
    poserDelta('followers', abo, S.abonnes && S.abonnes.precedent,
      'Aucun instantané d’abonnés antérieur au ' + dateJour(b.debut)
      + '. L’historique n’existe que si la collecte Graph API tourne.');

    // 2 — Posts publiés
    var np = f ? f.n : null;
    poserValeur('posts', np == null ? '—' : fmtInt(np), np == null);
    poserDelta('posts', np, f && f.prec,
      f && f.couvert === false
        ? 'Aucun post connu avant le ' + dateJour(b.refDebut)
          + ' : impossible de distinguer « aucune publication » de « aucune donnée ».'
        : 'Période de référence non couverte par les données.');

    // 3 et 4 — Likes et commentaires par post
    var moyL = (e && e.n) ? e.likes / e.n : null;
    var moyC = (e && e.n) ? e.comments / e.n : null;
    var precL = (e && e.prec && e.prec.n > 0) ? e.prec.likes / e.prec.n : null;
    var precC = (e && e.prec && e.prec.n > 0) ? e.prec.comments / e.prec.n : null;

    var raisonEng = (e && !e.couvert)
      ? 'Aucun post mesuré avant le ' + dateJour(b.refDebut) + ' : la période de référence n’est pas couverte.'
      : 'Aucun post mesuré sur la période de référence (' + libelleReference() + ').';

    poserValeur('likes', moyL == null ? '—' : fmtInt(moyL), moyL == null);
    poserDelta('likes', moyL, precL, raisonEng, fmtInt);

    poserValeur('comments', moyC == null ? '—' : fmtInt(moyC), moyC == null);
    poserDelta('comments', moyC, precC, raisonEng, fmtInt);

    // 5 — Taux d'engagement
    var taux = (moyL != null && abo) ? (moyL + moyC) / abo * 100 : null;
    var tauxPrec = (precL != null && abo) ? (precL + precC) / abo * 100 : null;
    poserValeur('rate', taux == null ? '—' : fmtPct(taux), taux == null);
    poserDelta('rate', taux, tauxPrec, !abo ? 'Nombre d’abonnés inconnu : le taux n’est pas calculable.' : raisonEng, fmtPct);

    // Sous-titre des cartes sans variation possible : on précise la mesure.
    var lignePosts = document.querySelector('[data-delta="posts"]');
    if (lignePosts && np === 0) lignePosts.title = 'Aucune publication sur la période affichée.';
  }

  // ══════════════════════════════════════════════════════════
  // 10. Rendu — graphes
  // ══════════════════════════════════════════════════════════

  function detruire(cle) {
    if (S.charts[cle]) { S.charts[cle].destroy(); delete S.charts[cle]; }
  }

  function optionsBase(pal, empile) {
    return {
      responsive: true,
      maintainAspectRatio: false,
      animation: { duration: 250 },
      interaction: { mode: 'index', intersect: false },
      plugins: {
        legend: {
          display: false,
          labels: { color: pal.fg2, boxWidth: 12, boxHeight: 12, padding: 12,
                    font: { family: 'Inter', size: 11 } }
        },
        tooltip: {
          backgroundColor: pal.fg1, titleColor: pal.bg1, bodyColor: pal.bg1,
          borderColor: pal.bord, borderWidth: 1, padding: 8, displayColors: true,
          titleFont: { family: 'Inter', size: 12 }, bodyFont: { family: 'Inter', size: 12 }
        }
      },
      scales: {
        x: {
          stacked: !!empile,
          grid: { display: false, drawBorder: false },
          border: { color: pal.grid },
          ticks: { color: pal.fg3, font: { family: 'Inter', size: 11 }, maxRotation: 0, autoSkipPadding: 16 }
        },
        y: {
          stacked: !!empile,
          beginAtZero: true,
          grid: { color: pal.grid, drawBorder: false },
          border: { display: false },
          ticks: { color: pal.fg3, font: { family: 'Inter', size: 11 }, callback: function (v) { return fmtCompact(v); } }
        }
      }
    };
  }

  function pasDeGraphes(slotId, boxId) {
    afficherCanvas(boxId, false);
    poserVide(slotId, 'Graphes indisponibles',
      'La bibliothèque de rendu (Chart.js) n’a pas pu être chargée depuis le CDN. '
      + 'Les agrégats, la heatmap et le tableau restent exacts ; seules les courbes manquent. '
      + 'Vérifie la connexion réseau du navigateur, puis recharge.',
      [{ label: 'Recharger', primaire: true, onClick: function () { location.reload(); } }]);
  }

  function rendreTrend() {
    var metrique = document.getElementById('trend-metric').value;
    var pal = readPalette();
    var note = document.getElementById('trend-note');
    detruire('trend');
    viderSlot('trend-slot');

    if (!chartsDispo) { texte(note, ''); return pasDeGraphes('trend-slot', 'trend-box'); }
    if (!S.compte) { texte(note, ''); return videSansCompte('trend-slot', 'trend-box'); }

    var labels = [], valeurs = [], type = TRENDS[metrique].type;

    if (metrique === 'followers') {
      labels = ((S.abonnes && S.abonnes.labels) || []).map(function (l) { return dateCourte(Date.parse(l + 'T12:00:00') / 1000); });
      valeurs = (S.abonnes && S.abonnes.valeurs) || [];
    } else if (metrique === 'posts') {
      labels = (S.freq && S.freq.serie && S.freq.serie.labels) || [];
      valeurs = (S.freq && S.freq.serie && S.freq.serie.data) || [];
    } else {
      var d = S.insights;
      labels = (d && d.labels || []).map(function (l) { return dateCourte(Date.parse(l + 'T12:00:00') / 1000); });
      valeurs = (d && d[metrique]) || [];
    }

    if (!valeurs.length) {
      afficherCanvas('trend-box', false);
      texte(note, '');
      videTrend(metrique);
      return;
    }

    afficherCanvas('trend-box', true);
    var couleur = couleurSerie(pal, metrique);
    var opts = optionsBase(pal, false);
    if (metrique === 'followers') opts.scales.y.beginAtZero = false;
    // Un compte de posts n'a pas de demi-unité : l'axe reste entier
    // plutôt que d'afficher 0,1 … 0,9 sous une barre qui vaut 1.
    if (metrique === 'posts') opts.scales.y.ticks.precision = 0;

    S.charts.trend = new Chart(document.getElementById('chart-trend'), {
      type: type,
      data: {
        labels: labels,
        datasets: [{
          label: TRENDS[metrique].titre,
          data: valeurs,
          borderColor: couleur,
          backgroundColor: type === 'bar' ? alpha(couleur, 0.75) : alpha(couleur, 0.12),
          pointBackgroundColor: couleur,
          pointBorderColor: pal.bg1,
          pointRadius: valeurs.length > 60 ? 0 : 3,
          pointHoverRadius: 4,
          borderWidth: 2,
          borderRadius: type === 'bar' ? 3 : 0,
          fill: type === 'line',
          tension: 0.25
        }]
      },
      options: opts
    });

    texte(note, TRENDS[metrique].titre + ' — ' + valeurs.length + ' point'
      + (valeurs.length > 1 ? 's' : '') + ' de mesure sur la période affichée.');
  }

  function videTrend(metrique) {
    if (metrique === 'followers') {
      poserVide('trend-slot', 'Aucun historique d’abonnés',
        'L’historique d’abonnés n’existe que si la collecte Graph API tourne : '
        + 'le scraping, lui, calcule les statistiques du profil sans les enregistrer. '
        + 'Connecte l’API puis lance une collecte pour créer le premier point.',
        [{ label: 'Ouvrir les réglages', href: '/settings#ig-api', primaire: true },
         { label: 'Collecter maintenant', onClick: collecterMaintenant }]);
    } else if (metrique === 'posts') {
      poserVide('trend-slot', 'Aucune publication sur la période',
        'Aucun post daté n’a été trouvé entre le ' + dateJour(bornes().debut)
        + ' et aujourd’hui. Élargis la période, ou lance un scraping du compte.',
        [{ label: 'Voir les profils', href: '/profiles', primaire: true }]);
    } else {
      poserVide('trend-slot', 'Aucun insight reçu',
        'Couverture, impressions, visites de profil et comptes engagés proviennent uniquement '
        + 'de l’API Graph d’Instagram. Sans jeton valide, aucune de ces valeurs n’existe — '
        + 'elles sont donc absentes, et non à zéro.',
        [{ label: 'Connecter l’API', href: '/settings#ig-api', primaire: true }]);
    }
  }

  function rendreSplit() {
    var quoi = document.getElementById('split-metric').value;
    var pal = readPalette();
    var note = document.getElementById('split-note');
    detruire('split');
    viderSlot('split-slot');

    if (!chartsDispo) { texte(note, ''); return pasDeGraphes('split-slot', 'split-box'); }
    if (!S.compte) { texte(note, ''); return videSansCompte('split-slot', 'split-box', 'cette ventilation n’a aucune source'); }

    var src = mediasPeriode();
    if (!src) {
      afficherCanvas('split-box', false);
      texte(note, '');
      poserVide('split-slot', 'Ventilation indisponible',
        'La ventilation est calculée à partir de la médiathèque du compte, qui n’a pas pu être lue. '
        + 'Recharge la page ; si le problème persiste, le compte n’a probablement aucun média en base.',
        [{ label: 'Recharger', primaire: true, onClick: function () { location.reload(); } }]);
      return;
    }
    if (!src.liste.length) {
      afficherCanvas('split-box', false);
      texte(note, '');
      poserVide('split-slot', 'Aucun média sur la période',
        'Aucun média daté entre le ' + dateJour(bornes().debut) + ' et aujourd’hui. '
        + 'Élargis la période ou lance un scraping du compte.',
        [{ label: 'Voir les profils', href: '/profiles', primaire: true }]);
      return;
    }

    afficherCanvas('split-box', true);
    var labels = [], valeurs = [], couleurs = [], type = 'bar', empile = false;

    if (quoi === 'type') {
      var parType = {};
      src.liste.forEach(function (m) {
        var t = m.media_type || 'other';
        parType[t] = (parType[t] || 0) + 1;
      });
      var noms = { image: 'Photos', video: 'Vidéos / Reels', carousel: 'Carrousels' };
      Object.keys(parType).sort().forEach(function (t) {
        labels.push(noms[t] || t);
        valeurs.push(parType[t]);
        couleurs.push(couleurSerie(pal, SERIE[t] ? t : 'other'));
      });
      type = 'doughnut';
    } else if (quoi === 'hour') {
      var h = new Array(24).fill(0);
      src.liste.forEach(function (m) { h[new Date(m.posted_at * 1000).getHours()]++; });
      labels = h.map(function (_, i) { return String(i).padStart(2, '0') + 'h'; });
      valeurs = h;
      couleurs = couleurSerie(pal, 'posts');
    } else {
      var j = new Array(7).fill(0);
      src.liste.forEach(function (m) { j[jourIndex(m.posted_at)]++; });
      labels = JOURS.map(function (x) { return x.slice(0, 3); });
      valeurs = j;
      couleurs = couleurSerie(pal, 'posts');
    }

    var opts = optionsBase(pal, empile);
    if (type !== 'doughnut') opts.scales.y.ticks.precision = 0;   // des posts, pas des fractions
    if (type === 'doughnut') {
      opts.scales = {};
      opts.cutout = '62%';
      opts.interaction = { mode: 'nearest', intersect: true };
      opts.plugins.legend.display = true;
      opts.plugins.legend.position = 'bottom';
    }

    S.charts.split = new Chart(document.getElementById('chart-split'), {
      type: type,
      data: {
        labels: labels,
        datasets: [{
          label: 'Posts',
          data: valeurs,
          backgroundColor: type === 'doughnut'
            ? couleurs
            : alpha(Array.isArray(couleurs) ? couleurs[0] : couleurs, 0.75),
          borderColor: type === 'doughnut' ? pal.bg1 : undefined,
          borderWidth: type === 'doughnut' ? 2 : 0,
          borderRadius: type === 'bar' ? 3 : 0
        }]
      },
      options: opts
    });

    texte(note, 'Source : ' + src.liste.length + ' post' + (src.liste.length > 1 ? 's' : '')
      + ' du compte présents en base entre le ' + dateJour(bornes().debut) + ' et aujourd’hui'
      + (src.complet ? '' : ' (médiathèque tronquée aux ' + (VIEWER_PAGE * VIEWER_MAX_PAGES) + ' médias les plus récents)') + '.');
  }

  /** Les posts de la source B restreints à la période affichée. */
  function mediasPeriode() {
    if (!S.medias) return null;
    var b = bornes();
    return {
      liste: S.medias.liste.filter(function (m) { return m.posted_at >= b.debut && m.posted_at <= b.fin; }),
      complet: S.medias.complet
    };
  }

  // ══════════════════════════════════════════════════════════
  // 11. Rendu — heatmap 7 × 24
  // ══════════════════════════════════════════════════════════

  function rendreHeatmap() {
    var table = document.getElementById('heat');
    var note = document.getElementById('heat-note');
    var src = mediasPeriode();
    viderSlot('heat-slot');

    var cellules = table.querySelectorAll('.heat__link');
    var grille = {};
    var total = 0, max = 0;

    if (src) {
      src.liste.forEach(function (m) {
        var d = new Date(m.posted_at * 1000);
        var cle = jourIndex(m.posted_at) + ':' + d.getHours();
        grille[cle] = (grille[cle] || 0) + 1;
        total++;
        if (grille[cle] > max) max = grille[cle];
      });
    }

    var insuffisant = !src || total < HEAT_MIN_POSTS;

    cellules.forEach(function (a) {
      var n = grille[a.dataset.d + ':' + a.dataset.h] || 0;
      var v = (!insuffisant && max) ? n / max : 0;
      a.dataset.n = String(n);
      a.parentNode.style.setProperty('--v', String(v));
      if (v > 0.55) a.dataset.strong = '1'; else delete a.dataset.strong;
      a.setAttribute('aria-label',
        JOURS[+a.dataset.d] + ' ' + String(a.dataset.h).padStart(2, '0') + 'h — '
        + (insuffisant ? 'données insuffisantes' : (n + ' post' + (n > 1 ? 's' : '')))
        + '. Ouvrir le calendrier sur ce créneau.');
    });

    texte(document.getElementById('heat-max'), insuffisant ? '—' : String(max));

    if (!S.compte) {
      table.setAttribute('data-insufficient', '');
      texte(note, '');
      texte(document.getElementById('heat-max'), '—');
      return videSansCompte('heat-slot', null, 'cette grille horaire n’a aucune source');
    }

    if (insuffisant) {
      table.setAttribute('data-insufficient', '');
      texte(note, '');
      poserVide('heat-slot', 'Données insuffisantes',
        (src ? total : 0) + ' post' + (total > 1 ? 's' : '') + ' daté' + (total > 1 ? 's' : '')
        + ' sur la période, pour ' + HEAT_MIN_POSTS + ' au minimum : la grille est neutralisée '
        + 'plutôt que d’afficher un motif qui ne veut rien dire. Élargis la période.',
        [{ label: 'Passer à 1 an', primaire: true, onClick: function () { poserPeriode(365); } }]);
    } else {
      table.removeAttribute('data-insufficient');
      texte(note, 'Volume de publication, ' + total + ' posts datés entre le '
        + dateJour(bornes().debut) + ' et aujourd’hui. Le maximum d’une case est de '
        + max + ' post' + (max > 1 ? 's' : '') + '. Cliquer une case ouvre le calendrier sur ce créneau. '
        + 'Cette grille compte les publications, pas leur engagement : l’API ne rattache pas '
        + 'les likes à l’heure de publication.');
    }
  }

  // ══════════════════════════════════════════════════════════
  // 12. Rendu — tableau
  // ══════════════════════════════════════════════════════════

  var COLONNES = ['account', 'type', 'likes', 'comments', 'views',
                  'eng', 'rate', 'date', 'hour', 'day'];

  /**
   * Une ligne du classement, telle que l'écran la manipule.
   *
   * Rien n'est comblé ici : `likes`, `comments`, `views`, `eng` et
   * `rate` restent `null` quand la mesure n'existe pas. Toute la suite
   * — tri, agrégats, export — distingue `null` de `0`.
   */
  function lignesTable() {
    var items = (S.perf && S.perf.items) || [];
    return items.map(function (p) {
      return {
        id: p.id,
        url: p.post_url,
        viewer: p.viewer_url,
        thumb: p.thumb_url,
        caption: p.caption || '',
        account: p.profile_username ? '@' + p.profile_username : '—',
        platform: p.platform,
        type: TYPES[p.media_type] || p.media_type || '—',
        typeCle: p.media_type || 'other',
        children: p.children || 1,
        likes: p.likes,
        comments: p.comments,
        views: p.views,
        viewsEtat: p.views_state,
        eng: p.engagement,
        engPartiel: !!p.engagement_partial,
        rate: p.rate,
        followers: p.followers,
        base: p.followers_basis,
        mesure: !!p.measured,
        mesurable: !!p.metrics_supported,
        manque: p.missing || [],
        date: p.posted_at,
        hour: p.posted_at,
        day: p.posted_at == null ? null : jourIndex(p.posted_at)
      };
    });
  }

  /** Pourquoi ce post n'a pas de chiffres — et ce qui les donnerait. */
  function raisonAbsence(l) {
    if (!l.mesurable) {
      return 'Aucun compteur n’est collecté sur ' + nomPlateforme(l.platform)
        + ' : seul l’extracteur Instagram lit les likes, les commentaires et les vues. '
        + 'Ce post est donc NON MESURÉ, pas à zéro.';
    }
    return 'Post Instagram enregistré sans ses compteurs : ils n’ont pas été relevés '
      + 'lors de la collecte. Un nouveau scraping du compte, ou une collecte Graph API, '
      + 'les renseignera. Non mesuré n’est pas zéro.';
  }

  /**
   * Tri HONNÊTE.
   *
   * Trois règles, dans cet ordre, et la première est la plus
   * importante :
   *   1. un post non mesuré n'est jamais classé parmi les mesurés —
   *      il ne peut donc ni gagner ni perdre le classement ;
   *   2. à l'intérieur d'un bloc, une valeur absente passe APRÈS les
   *      valeurs présentes quel que soit le sens du tri : croissant,
   *      un `null` ne remonte pas en tête comme le ferait un zéro ;
   *   3. à égalité, le plus récent d'abord — un ordre stable et lisible.
   */
  function trierLignes(lignes) {
    var cle = S.tri.cle, sens = S.tri.sens;
    return lignes.slice().sort(function (a, b) {
      if (a.mesure !== b.mesure) return a.mesure ? -1 : 1;
      var x = a[cle], y = b[cle];
      var ax = (x == null), ay = (y == null);
      if (ax !== ay) return ax ? 1 : -1;
      if (!ax) {
        if (typeof x === 'string') {
          var c = x.localeCompare(y, 'fr');
          if (c) return sens * c;
        } else if (x !== y) {
          return sens * (x - y);
        }
      }
      return (b.date || 0) - (a.date || 0);
    });
  }

  function chip(t, variante) {
    var s = document.createElement('span');
    s.className = 'chip' + (variante ? ' chip--' + variante : '');
    s.textContent = t;
    return s;
  }

  function cellule(tr, col, contenu, numerique, titre) {
    var td = document.createElement('td');
    td.className = 'col-' + col + (numerique ? ' num' : '');
    if (contenu instanceof Node) td.appendChild(contenu); else td.textContent = contenu;
    if (titre) td.title = titre;
    tr.appendChild(td);
  }

  /** Une cellule numérique : la valeur, ou l'absence ET sa cause. */
  function celluleMesure(tr, col, valeur, format, raison) {
    var td = document.createElement('td');
    td.className = 'col-' + col + ' num';
    if (valeur == null) {
      var s = document.createElement('span');
      s.className = 'void';
      s.textContent = '—';
      if (raison) s.title = raison;
      td.appendChild(s);
    } else {
      td.textContent = format(valeur);
    }
    tr.appendChild(td);
  }

  function titreTaux(l) {
    if (l.rate == null) {
      if (l.eng == null) return 'Engagement non mesuré : le taux ne peut pas être calculé.';
      return 'Nombre d’abonnés inconnu pour ce compte : le taux ne peut pas être calculé. '
        + 'L’historique d’abonnés vient de la collecte Graph API.';
    }
    if (l.base === 'snapshot') {
      return fmtInt(l.eng) + ' engagements pour ' + fmtInt(l.followers)
        + ' abonnés relevés AVANT la publication : taux exact à la date du post.';
    }
    return fmtInt(l.eng) + ' engagements pour ' + fmtInt(l.followers)
      + ' abonnés AUJOURD’HUI — aucun instantané d’abonnés n’existe avant ce post, '
      + 'le taux est donc approché.';
  }

  /** La ligne « ci-dessous, les non mesurés » — la frontière, écrite. */
  function ligneFrontiere(nb, colonnes) {
    var tr = document.createElement('tr');
    tr.className = 'tbl__cut';
    var td = document.createElement('td');
    td.colSpan = colonnes;
    td.textContent = nb + ' post' + (nb > 1 ? 's' : '') + ' sans aucun compteur relevé. '
      + 'Non classé' + (nb > 1 ? 's' : '') + ' : une absence de mesure ne vaut pas zéro, '
      + 'et un post non mesuré ne peut pas être dernier.';
    tr.appendChild(td);
    return tr;
  }

  function rendreTable() {
    var corps = document.getElementById('posts-body');
    var note = document.getElementById('posts-note');
    var table = document.getElementById('posts');
    var nbColonnes = table.querySelectorAll('thead th').length;
    viderSlot('posts-slot');

    if (S.perfErr) {
      corps.textContent = '';
      texte(note, '');
      poserVide('posts-slot', 'Classement indisponible',
        'Le classement n’a pas pu être calculé. Cause : '
        + (S.perfErr.message || 'le serveur n’a pas répondu')
        + '. Recharge la page ; si l’erreur persiste, consulte la file de traitements.',
        [{ label: 'Recharger', primaire: true, onClick: function () { location.reload(); } },
         { label: 'Voir les jobs', href: '/jobs' }]);
      return;
    }

    if (!S.perf) {
      corps.textContent = '';
      texte(note, '');
      return videSansCompte('posts-slot', null, 'il n’y a aucun post à classer');
    }

    var lignes = trierLignes(lignesTable());
    var c = S.perf.counts || {};

    if (!lignes.length) {
      corps.textContent = '';
      texte(note, '');
      return videClassement(c);
    }

    corps.textContent = '';
    var rang = 0;
    var frontierePosee = false;
    var nonMesures = lignes.filter(function (l) { return !l.mesure; }).length;

    lignes.forEach(function (l) {
      if (!l.mesure && !frontierePosee) {
        frontierePosee = true;
        corps.appendChild(ligneFrontiere(nonMesures, nbColonnes));
      }

      var tr = document.createElement('tr');
      tr.className = l.mesure ? 'tbl__row' : 'tbl__row tbl__row--void';
      if (l.viewer) {
        // Un clic n'importe où sur la ligne ouvre le média dans le
        // viewer, dont l'état de vue tient déjà dans l'URL. La légende
        // reste un vrai lien : le clavier garde le même chemin.
        tr.dataset.href = l.viewer;
      }

      var td = document.createElement('td');
      td.className = 'tbl__id';
      var box = document.createElement('div');
      box.className = 'post';

      var r = document.createElement('span');
      r.className = 'post__rank' + (l.mesure ? '' : ' post__rank--void');
      if (l.mesure) { rang += 1; r.textContent = String(rang); }
      else { r.textContent = '—'; r.title = 'Non classé : aucun compteur relevé.'; }
      box.appendChild(r);

      if (l.thumb) {
        var img = document.createElement('img');
        img.className = 'post__thumb';
        img.src = l.thumb;
        img.width = 44; img.height = 44;
        img.loading = 'lazy'; img.decoding = 'async';
        img.alt = '';
        img.addEventListener('error', function () {
          var ph = document.createElement('span');
          ph.className = 'post__thumb post__thumb--void';
          ph.textContent = '—';
          ph.title = 'Fichier local absent';
          img.replaceWith(ph);
        });
        box.appendChild(img);
      } else {
        var ph2 = document.createElement('span');
        ph2.className = 'post__thumb post__thumb--void';
        ph2.textContent = '—';
        ph2.title = 'Aucun fichier local pour ce post';
        box.appendChild(ph2);
      }

      var textes = document.createElement('span');
      textes.className = 'post__txt';

      var cap;
      if (l.viewer) { cap = document.createElement('a'); cap.href = l.viewer; }
      else { cap = document.createElement('span'); }
      cap.className = 'post__cap' + (l.caption ? '' : ' post__cap--void');
      cap.textContent = l.caption || 'Sans légende';
      cap.title = (l.caption || 'Sans légende') + ' — ouvrir dans le viewer';
      textes.appendChild(cap);

      if (l.children > 1) {
        var carr = document.createElement('span');
        carr.className = 'post__sub';
        carr.textContent = l.children + ' médias';
        carr.title = 'Carrousel : ' + l.children + ' médias partagent les compteurs '
          + 'de ce post. Ils comptent pour UNE ligne, pas ' + l.children + '.';
        textes.appendChild(carr);
      }
      box.appendChild(textes);

      if (l.url) {
        var ext = document.createElement('a');
        ext.className = 'post__ext';
        ext.href = l.url;
        ext.target = '_blank';
        ext.rel = 'noopener';
        ext.textContent = '↗';
        ext.title = 'Ouvrir le post d’origine sur ' + nomPlateforme(l.platform);
        ext.setAttribute('aria-label', 'Ouvrir le post d’origine sur ' + nomPlateforme(l.platform));
        box.appendChild(ext);
      }

      td.appendChild(box);
      tr.appendChild(td);

      cellule(tr, 'account', chip(l.account, 'plat'), false,
        nomPlateforme(l.platform));
      cellule(tr, 'type', chip(l.type));

      var raison = raisonAbsence(l);
      celluleMesure(tr, 'likes', l.likes, fmtInt, raison);
      celluleMesure(tr, 'comments', l.comments, fmtInt, raison);
      celluleMesure(tr, 'views', l.views, fmtInt,
        l.viewsEtat === 'na'
          ? 'Non applicable : Instagram ne publie pas de compteur de vues sur une photo.'
          : raison);

      if (l.engPartiel && l.eng != null) {
        var tdE = document.createElement('td');
        tdE.className = 'col-eng num';
        tdE.textContent = fmtInt(l.eng);
        var etoile = document.createElement('span');
        etoile.className = 'partiel';
        etoile.textContent = '*';
        etoile.title = 'Somme incomplète : '
          + (l.likes == null ? 'les likes' : 'les commentaires')
          + ' n’ont pas été relevés sur ce post.';
        tdE.appendChild(etoile);
        tr.appendChild(tdE);
      } else {
        celluleMesure(tr, 'eng', l.eng, fmtInt, raison);
      }

      var tdT = document.createElement('td');
      tdT.className = 'col-rate num';
      if (l.rate == null) {
        var sv = document.createElement('span');
        sv.className = 'void';
        sv.textContent = '—';
        sv.title = titreTaux(l);
        tdT.appendChild(sv);
      } else {
        tdT.textContent = fmtPct(l.rate);
        tdT.title = titreTaux(l);
        if (l.base !== 'snapshot') {
          var ap = document.createElement('span');
          ap.className = 'partiel';
          ap.textContent = '~';
          ap.title = titreTaux(l);
          tdT.appendChild(ap);
        }
      }
      tr.appendChild(tdT);

      cellule(tr, 'date', dateCourte(l.date), true);
      cellule(tr, 'hour', heure(l.hour), true);
      cellule(tr, 'day', l.day == null ? '—' : JOURS[l.day]);

      corps.appendChild(tr);
    });

    texte(note, noteClassement(c, lignes.length, nonMesures));
  }

  function noteClassement(c, affiches, nonMesures) {
    var mesures = affiches - nonMesures;
    var p = [];
    p.push(affiches + ' post' + (affiches > 1 ? 's' : '') + ' publié'
      + (affiches > 1 ? 's' : '') + ' sur la période, dont ' + mesures + ' mesuré'
      + (mesures > 1 ? 's' : '') + ' et ' + nonMesures + ' non mesuré'
      + (nonMesures > 1 ? 's' : '') + '.');
    p.push('Les enfants d’un carrousel comptent pour un seul post : ils partagent '
      + 'les compteurs du post d’origine.');

    if (S.scope === 'all') {
      var noms = ((S.perf && S.perf.profiles) || []).map(function (x) {
        return '@' + x.username + (x.followers_count ? ' (' + fmtInt(x.followers_count) + ' abonnés)' : ' (abonnés inconnus)');
      });
      p.push('Portée : tous les comptes suivis — ' + (noms.join(', ') || 'aucun') + '. '
        + 'Les colonnes Likes, Commentaires et Vues ne sont PAS comparables entre comptes '
        + 'd’audiences différentes ; seule la colonne Taux l’est, car elle normalise '
        + 'l’engagement par le nombre d’abonnés du compte au moment du post.');
    } else {
      p.push('Portée : le compte affiché. Le taux normalise l’engagement par le nombre '
        + 'd’abonnés du compte au moment du post.');
    }

    var n = (S.perf && S.perf.normalization) || {};
    if (c.rated) {
      p.push('Taux calculable sur ' + c.rated + ' post' + (c.rated > 1 ? 's' : '')
        + ' : ' + (n.snapshot_backed || 0) + ' sur un instantané d’abonnés antérieur au post '
        + '(exact) et ' + (n.current_fallback || 0) + ' sur le nombre d’abonnés '
        + 'd’aujourd’hui, faute d’instantané (approché, marqué ~).');
    }
    if (c.undated) {
      p.push(c.undated + ' post' + (c.undated > 1 ? 's' : '') + ' du périmètre '
        + (c.undated > 1 ? 'n’ont' : 'n’a') + ' aucune date de publication connue : '
        + 'impossible de le' + (c.undated > 1 ? 's' : '') + ' rattacher à une période, '
        + 'il' + (c.undated > 1 ? 's' : '') + ' reste' + (c.undated > 1 ? 'nt' : '')
        + ' donc hors classement.');
    }
    if (S.perf && S.perf.truncated) {
      p.push('Liste tronquée à ' + PERF_LIMITE + ' posts : réduis la période pour '
        + 'la voir en entier.');
    }
    return p.join(' ');
  }

  /**
   * Les gestes d'un bloc vide. « Passer à 1 an » n'est proposé que
   * s'il change quelque chose : sur une période déjà maximale, ce
   * bouton ne ferait que recharger le même vide.
   */
  function elargirOuProfils() {
    var actions = [];
    if (S.days < MAX_DAYS) {
      actions.push({ label: 'Passer à 1 an', primaire: true,
                     onClick: function () { poserPeriode(MAX_DAYS); } });
    }
    actions.push({ label: 'Voir les profils', href: '/profiles',
                   primaire: actions.length === 0 });
    return actions;
  }

  /** L'état vide du classement : la cause, puis LE geste qui la lève. */
  function videClassement(c) {
    if (c.undated) {
      poserVide('posts-slot', 'Aucun post daté sur la période',
        'Aucun post publié entre le ' + dateJour(bornes().debut) + ' et aujourd’hui. '
        + c.undated + ' post' + (c.undated > 1 ? 's du périmètre n’ont' : ' du périmètre n’a')
        + ' aucune date de publication en base : un média sans date ne peut être rattaché '
        + 'à aucune période, ni donc être classé. Relance un scraping du compte pour '
        + 'récupérer les dates de publication'
        + (S.days < MAX_DAYS ? ', ou élargis la période.' : '.'),
        elargirOuProfils());
      return;
    }
    poserVide('posts-slot', 'Aucun post sur la période',
      'Aucun média publié entre le ' + dateJour(bornes().debut) + ' et aujourd’hui '
      + 'n’est présent en base. '
      + (S.days < MAX_DAYS ? 'Élargis la période, ou lance' : 'Lance')
      + ' un scraping du compte.',
      elargirOuProfils());
  }

  // ══════════════════════════════════════════════════════════
  // 12 bis. Rendu — « Ce qui marche » (agrégats de performance)
  // ══════════════════════════════════════════════════════════

  var AXES = {
    platform: {
      titre: 'plateforme',
      cle: function (l) { return l.platform || 'other'; },
      nom: function (k) { return nomPlateforme(k); },
      ordre: null
    },
    type: {
      titre: 'type de média',
      cle: function (l) { return l.typeCle; },
      nom: function (k) { return TYPES[k] || k; },
      ordre: null
    },
    weekday: {
      titre: 'jour de publication',
      cle: function (l) { return l.day == null ? null : String(l.day); },
      nom: function (k) { return JOURS[+k]; },
      ordre: function (a, b) { return +a.cle - +b.cle; }
    },
    hour: {
      titre: 'heure de publication',
      cle: function (l) {
        if (l.date == null) return null;
        return String(new Date(l.date * 1000).getHours());
      },
      nom: function (k) { return String(k).padStart(2, '0') + 'h'; },
      ordre: function (a, b) { return +a.cle - +b.cle; }
    }
  };

  function mediane(t) {
    if (!t.length) return null;
    var u = t.slice().sort(function (a, b) { return a - b; });
    var m = Math.floor(u.length / 2);
    return u.length % 2 ? u[m] : (u[m - 1] + u[m]) / 2;
  }

  /** Groupe les lignes sur un axe, sans jamais compter une absence pour zéro. */
  function grouperPerf(axe) {
    var lignes = lignesTable();
    var par = {};
    lignes.forEach(function (l) {
      var k = axe.cle(l);
      if (k == null) return;
      var g = par[k] || (par[k] = {
        cle: k, eng: [], taux: [], nonMesures: 0, mesurables: 0
      });
      if (l.mesure && l.eng != null) g.eng.push(l.eng); else g.nonMesures += 1;
      if (l.mesurable) g.mesurables += 1;
      if (l.rate != null) g.taux.push(l.rate);
    });
    return Object.keys(par).map(function (k) {
      var g = par[k];
      var n = g.eng.length;
      return {
        cle: k,
        nom: axe.nom(k),
        n: n,
        nonMesures: g.nonMesures,
        // Aucun post du groupe ne PEUT porter de compteur : ce n'est
        // pas un manque de volume, c'est une plateforme non instrumentée.
        horsMesure: g.mesurables === 0,
        moyenne: n ? g.eng.reduce(function (a, b) { return a + b; }, 0) / n : null,
        mediane: mediane(g.eng),
        taux: g.taux.length
          ? g.taux.reduce(function (a, b) { return a + b; }, 0) / g.taux.length
          : null,
        nTaux: g.taux.length
      };
    });
  }

  function rendreAgregats() {
    var corps = document.getElementById('agg-body');
    var note = document.getElementById('agg-note');
    var verdict = document.getElementById('agg-verdict');
    var sel = document.getElementById('agg-axis');
    if (!corps || !sel) return;
    viderSlot('agg-slot');
    corps.textContent = '';
    texte(verdict, '');
    texte(note, '');

    if (S.perfErr) {
      // Le classement et les agrégats partagent leur source : quand
      // elle tombe, les deux blocs disent la MÊME cause. Sans ce test,
      // celui-ci accusait l'absence de compte, ce qui est faux.
      poserVide('agg-slot', 'Agrégats indisponibles',
        'Les agrégats sont calculés à partir du classement, qui n’a pas pu être chargé. '
        + 'Cause : ' + (S.perfErr.message || 'le serveur n’a pas répondu') + '.',
        [{ label: 'Recharger', primaire: true, onClick: function () { location.reload(); } }]);
      return;
    }

    if (!S.perf) {
      return videSansCompte('agg-slot', null, 'il n’y a rien à agréger');
    }

    var axe = AXES[sel.value] || AXES.platform;
    var groupes = grouperPerf(axe);
    var mesuresTotal = groupes.reduce(function (a, g) { return a + g.n; }, 0);

    if (!groupes.length || !mesuresTotal) {
      return videAgregats(S.perf.counts || {}, groupes.length);
    }

    // Les groupes fiables d'abord, du plus engageant au moins engageant ;
    // les groupes trop maigres ensuite. Sur un axe ordonné (jour, heure),
    // l'ordre naturel prime : on ne réordonne pas un calendrier.
    if (axe.ordre) {
      groupes.sort(axe.ordre);
    } else {
      groupes.sort(function (a, b) {
        var fa = a.n >= AGG_MIN, fb = b.n >= AGG_MIN;
        if (fa !== fb) return fa ? -1 : 1;
        return (b.moyenne || 0) - (a.moyenne || 0);
      });
    }

    var fiables = groupes.filter(function (g) { return g.n >= AGG_MIN; })
      .sort(function (a, b) { return (b.moyenne || 0) - (a.moyenne || 0); });

    groupes.forEach(function (g) {
      var tr = document.createElement('tr');
      if (g.n < AGG_MIN) tr.className = 'tbl__row--thin';

      var td = document.createElement('td');
      td.className = 'tbl__id';
      td.textContent = g.nom + ' ';
      if (fiables.length > 1 && fiables[0].cle === g.cle) {
        var b = document.createElement('span');
        b.className = 'chip chip--best';
        b.textContent = 'en tête';
        td.appendChild(b);
      }
      tr.appendChild(td);

      var raisonVide = g.horsMesure
        ? 'Aucun compteur n’est collecté sur cette plateforme : ces posts sont NON '
          + 'MESURÉS, ils ne valent pas zéro et n’entrent dans aucune moyenne.'
        : 'Aucun post mesuré dans ce groupe : la moyenne n’existe pas, elle ne vaut '
          + 'pas zéro.';

      var tdN = document.createElement('td');
      tdN.className = 'num';
      tdN.textContent = fmtInt(g.n);
      if (g.n < AGG_MIN) {
        var s = document.createElement('span');
        s.className = 'partiel';
        s.textContent = '!';
        s.title = g.horsMesure
          ? raisonVide
          : 'Moins de ' + AGG_MIN + ' posts mesurés : la moyenne est affichée '
            + 'mais elle ne suffit pas à conclure. Ce groupe est exclu de la lecture.';
        tdN.appendChild(s);
      }
      tr.appendChild(tdN);

      celluleMesure(tr, 'moy', g.moyenne, fmtInt, raisonVide);
      celluleMesure(tr, 'med', g.mediane, fmtInt, raisonVide);
      celluleMesure(tr, 'taux', g.taux, fmtPct,
        g.n ? 'Taux non calculable : nombre d’abonnés inconnu sur ce groupe.'
            : raisonVide);

      var tdV = document.createElement('td');
      tdV.className = 'num';
      tdV.textContent = fmtInt(g.nonMesures);
      if (g.nonMesures) {
        tdV.title = g.nonMesures + ' post' + (g.nonMesures > 1 ? 's' : '')
          + ' de ce groupe n’ont aucun compteur : ils sont exclus des moyennes, '
          + 'pas comptés pour zéro.';
      }
      tr.appendChild(tdV);

      corps.appendChild(tr);
    });

    texte(verdict, phraseVerdict(axe, fiables, mesuresTotal, groupes.length));
    texte(note, 'Moyennes calculées sur les ' + mesuresTotal + ' post'
      + (mesuresTotal > 1 ? 's' : '') + ' MESURÉ' + (mesuresTotal > 1 ? 'S' : '')
      + ' de la période. Un post sans compteur est exclu du calcul : le compter pour zéro '
      + 'tirerait chaque moyenne vers le bas. Un groupe de moins de ' + AGG_MIN
      + ' posts mesurés est affiché mais marqué « ! » et n’entre pas dans la lecture '
      + 'ci-dessus. Jours et heures sont lus dans le fuseau du navigateur.');
  }

  function phraseVerdict(axe, fiables, mesuresTotal, nbGroupes) {
    if (!fiables.length) {
      return 'Sur ' + mesuresTotal + ' post' + (mesuresTotal > 1 ? 's' : '') + ' mesuré'
        + (mesuresTotal > 1 ? 's' : '') + ' réparti' + (mesuresTotal > 1 ? 's' : '')
        + ' sur ' + nbGroupes + ' groupe' + (nbGroupes > 1 ? 's' : '')
        + ', aucun groupe n’atteint ' + AGG_MIN + ' posts. Les chiffres sont exacts, '
        + 'mais trop maigres pour désigner ce qui marche par ' + axe.titre + '.';
    }
    var t = fiables[0];
    // Un seul groupe assez fourni : il n'y a rien devant quoi il
    // « arrive en tête ». Le dire autrement, sinon la phrase promet une
    // comparaison qui n'a pas eu lieu.
    if (fiables.length === 1) {
      return 'Par ' + axe.titre + ' : ' + t.nom + ' est le seul groupe à atteindre '
        + AGG_MIN + ' posts mesurés — ' + fmtInt(t.moyenne) + ' engagements en moyenne '
        + 'sur ' + t.n + ' posts (médiane ' + fmtInt(t.mediane) + '). Aucun autre groupe '
        + 'n’est assez fourni pour être comparé à celui-là.';
    }
    var d = fiables[fiables.length - 1];
    return 'Par ' + axe.titre + ' : ' + t.nom + ' arrive en tête, ' + fmtInt(t.moyenne)
      + ' engagements en moyenne sur ' + t.n + ' post' + (t.n > 1 ? 's' : '') + ' mesuré'
      + (t.n > 1 ? 's' : '') + ' (médiane ' + fmtInt(t.mediane) + '), devant ' + d.nom
      + ' et ses ' + fmtInt(d.moyenne) + ' engagements moyens. Comparaison faite sur les '
      + fiables.length + ' groupes d’au moins ' + AGG_MIN + ' posts mesurés.';
  }

  /** Trois causes distinctes d'un bloc vide, trois messages distincts. */
  function videAgregats(c, nbGroupes) {
    if (!c.posts) {
      poserVide('agg-slot', 'Aucun post sur la période',
        'Rien n’a été publié — ou rien n’a été collecté — entre le '
        + dateJour(bornes().debut) + ' et aujourd’hui. '
        + (c.undated
            ? c.undated + ' post' + (c.undated > 1 ? 's du périmètre n’ont' : ' du périmètre n’a')
              + ' aucune date de publication en base et reste' + (c.undated > 1 ? 'nt' : '')
              + ' donc hors de toute période. '
            : '')
        + (S.days < MAX_DAYS ? 'Élargis la période, ou lance' : 'Lance')
        + ' un scraping du compte.',
        elargirOuProfils());
      return;
    }
    if (!c.measurable) {
      poserVide('agg-slot', 'Aucun compteur n’existe sur ce périmètre',
        'Les likes, commentaires et vues ne sont collectés que sur Instagram : les '
        + 'extracteurs des autres plateformes ne les lisent pas. Aucun des posts de la '
        + 'période ne vient d’Instagram, il n’y a donc rien à agréger — et rien qu’un '
        + 'nouveau scraping changerait sur ces comptes.',
        [{ label: 'Voir les profils', href: '/profiles', primaire: true }]);
      return;
    }
    poserVide('agg-slot', 'Aucun post mesuré à agréger',
      (c.posts || 0) + ' post' + ((c.posts || 0) > 1 ? 's' : '') + ' sur la période, '
      + 'aucun ne porte de compteur. Ces compteurs viennent du scraping Instagram, qui '
      + 'les enregistre depuis peu : les médias déjà en base ont été collectés avant. '
      + 'Relance un scraping du compte, ou connecte la Graph API et lance une collecte — '
      + 'les moyennes se rempliront d’elles-mêmes.',
      [{ label: 'Voir les profils', href: '/profiles', primaire: true },
       { label: 'Connecter l’API', href: '/settings#ig-api' }]);
  }

  // ══════════════════════════════════════════════════════════
  // 13. Interactions
  // ══════════════════════════════════════════════════════════

  function poserPeriode(days, sansHistorique) {
    days = Math.max(1, Math.min(MAX_DAYS, Math.round(days) || 30));
    S.days = days;

    document.querySelectorAll('.seg__btn').forEach(function (b) {
      b.setAttribute('aria-pressed', String(+b.dataset.days === days));
    });
    var champ = document.getElementById('custom-days');
    if (champ && document.activeElement !== champ) champ.value = PRESETS.indexOf(days) === -1 ? days : '';

    var u = new URL(location.href);
    u.searchParams.set('days', days);
    if (sansHistorique) history.replaceState(null, '', u); else history.pushState(null, '', u);

    chargerPeriode();
  }

  // Sélecteur de compte : même mécanique d'état que la période.
  //
  // Changer de compte ne peut PAS se contenter de chargerPeriode() :
  // l'en-tête, le bandeau de connexion et le top posts viennent de
  // chargerCompte(). Sans lui, l'écran afficherait
  // les chiffres du nouveau compte sous le nom de l'ancien.
  function poserCompte(id, sansHistorique) {
    S.profileId = id || null;
    var u = new URL(location.href);
    if (S.profileId) u.searchParams.set('profile_id', S.profileId);
    else u.searchParams.delete('profile_id');
    if (sansHistorique) history.replaceState(null, '', u); else history.pushState(null, '', u);
    return rechargerTout();
  }

  // La chaîne de chargement complète, partagée entre l'amorçage et
  // tout changement de compte.
  function rechargerTout() {
    var slot = document.getElementById('alert-slot');
    if (slot) slot.innerHTML = '';       // le bandeau vaut pour l'ancien compte
    S.medias = null;
    return chargerCompte()
      .then(chargerPeriode)
      .then(function () {
        // Une fois le compte connu, on va chercher la médiathèque, qui
        // alimente heatmap et ventilations. Le tableau, lui, ne dépend
        // plus d'elle : ses miniatures viennent désormais du même
        // endpoint que ses chiffres (lot B), donc une médiathèque
        // illisible ne peut plus vider ses vignettes.
        if (!S.compte) return;
        return chargerMedias().then(function (m) {
          S.medias = m;
          rendreSplit();
          rendreHeatmap();
        });
      });
  }

  function chargerComptes() {
    return getJSON('/api/analytics/profiles').then(function (d) {
      if (!d || !d.profiles) return;
      S.profiles = d.profiles;
      if (!S.profileId) S.profileId = d.currentId;

      var pick = document.getElementById('acct-pick');
      var sel = document.getElementById('acct-select');
      if (!pick || !sel) return;

      sel.innerHTML = '';
      d.profiles.forEach(function (p) {
        var o = document.createElement('option');
        o.value = p.id;
        // Le nom ET la plateforme : deux comptes peuvent porter le
        // même pseudo sur deux réseaux.
        o.textContent = '@' + p.username + ' · ' + p.platform;
        if (p.id === S.profileId) o.selected = true;
        sel.appendChild(o);
      });

      // Un seul compte : pas de choix à offrir.
      pick.hidden = d.profiles.length < 2;
      sel.addEventListener('change', function () { poserCompte(+sel.value); });
    }).catch(function () { /* le bandeau d'erreur de l'écran prend le relais */ });
  }

  function brancher() {
    document.querySelectorAll('.seg__btn').forEach(function (b) {
      b.addEventListener('click', function () { poserPeriode(+b.dataset.days); });
    });

    document.getElementById('custom-form').addEventListener('submit', function (e) {
      e.preventDefault();
      var v = parseInt(document.getElementById('custom-days').value, 10);
      if (isFinite(v)) poserPeriode(v);
    });

    document.getElementById('trend-metric').addEventListener('change', rendreTrend);
    document.getElementById('split-metric').addEventListener('change', rendreSplit);
    document.getElementById('agg-axis').addEventListener('change', rendreAgregats);

    // Portée du classement — même mécanique d'état que la période et le
    // compte : elle part dans l'URL, donc la vue entière est partageable.
    var scope = document.getElementById('perf-scope');
    scope.addEventListener('change', function () {
      S.scope = scope.value === 'all' ? 'all' : 'profile';
      var u = new URL(location.href);
      if (S.scope === 'all') u.searchParams.set('scope', 'all');
      else u.searchParams.delete('scope');
      history.pushState(null, '', u);
      chargerPerformance().then(function () { rendreTable(); rendreAgregats(); });
    });

    // Un clic sur la ligne ouvre le média dans le viewer. Les liens de
    // la ligne (légende, post d'origine) gardent leur propre cible : on
    // ne double pas leur navigation.
    document.getElementById('posts-body').addEventListener('click', function (e) {
      if (e.target.closest('a')) return;
      var tr = e.target.closest('tr[data-href]');
      if (!tr) return;
      location.href = tr.dataset.href;
    });

    document.getElementById('heat-values').addEventListener('change', function () {
      var t = document.getElementById('heat');
      if (this.checked) t.setAttribute('data-values', ''); else t.removeAttribute('data-values');
    });

    // Tri : 10 lignes en mémoire, effet immédiat, aucune requête (G18).
    document.querySelectorAll('#posts th[data-sort]').forEach(function (th) {
      th.setAttribute('tabindex', '0');
      th.setAttribute('role', 'button');
      function trier() {
        var cle = th.dataset.sort;
        S.tri = (S.tri.cle === cle) ? { cle: cle, sens: -S.tri.sens } : { cle: cle, sens: -1 };
        document.querySelectorAll('#posts th[data-sort]').forEach(function (o) { o.removeAttribute('aria-sort'); });
        th.setAttribute('aria-sort', S.tri.sens === 1 ? 'ascending' : 'descending');
        rendreTable();
      }
      th.addEventListener('click', trier);
      th.addEventListener('keydown', function (e) {
        if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); trier(); }
      });
    });
    var thDefaut = document.querySelector('#posts th[data-sort="eng"]');
    if (thDefaut) thDefaut.setAttribute('aria-sort', 'descending');

    // Colonnes masquables : bascule d'un attribut, état persisté.
    var table = document.getElementById('posts');
    var caches = [];
    try { caches = JSON.parse(localStorage.getItem('analytics.cols') || '[]'); } catch (e) { caches = []; }
    caches = caches.filter(function (c) { return COLONNES.indexOf(c) !== -1; });
    table.setAttribute('data-hide', caches.join(' '));
    document.querySelectorAll('.cols__row input[data-col]').forEach(function (cb) {
      cb.checked = caches.indexOf(cb.dataset.col) === -1;
      cb.addEventListener('change', function () {
        var set = (table.getAttribute('data-hide') || '').split(' ').filter(Boolean);
        var i = set.indexOf(cb.dataset.col);
        if (cb.checked && i !== -1) set.splice(i, 1);
        if (!cb.checked && i === -1) set.push(cb.dataset.col);
        table.setAttribute('data-hide', set.join(' '));
        try { localStorage.setItem('analytics.cols', JSON.stringify(set)); } catch (e) { /* stockage refusé */ }
      });
    });
    // Un clic hors du menu le referme.
    document.addEventListener('click', function (e) {
      var d = document.querySelector('.cols');
      if (d && d.open && !d.contains(e.target)) d.open = false;
    });

    document.getElementById('export-csv').addEventListener('click', exporterCSV);

    window.addEventListener('popstate', function () {
      var q = new URL(location.href).searchParams;
      var d = parseInt(q.get('days'), 10);
      var p = parseInt(q.get('profile_id'), 10);
      S.profileId = isFinite(p) ? p : null;
      S.scope = q.get('scope') === 'all' ? 'all' : 'profile';
      var sc = document.getElementById('perf-scope');
      if (sc) sc.value = S.scope;
      var sel = document.getElementById('acct-select');
      if (sel && S.profileId) sel.value = String(S.profileId);
      poserPeriode(isFinite(d) ? d : 30, true);
    });
  }

  // ══════════════════════════════════════════════════════════
  // 14. Export CSV
  // ══════════════════════════════════════════════════════════

  function construireCSV() {
    var b = bornes();
    var sep = ';';
    var l = [];
    function ligne(t) { l.push(t.map(function (c) {
      var s = (c == null) ? '' : String(c);
      return /[";\n]/.test(s) ? '"' + s.replace(/"/g, '""') + '"' : s;
    }).join(sep)); }

    ligne(['SAMOURAIS — Analytics']);
    ligne(['Période', dateJour(b.debut) + ' au ' + dateJour(b.fin), S.days + ' jours']);
    ligne(['Période de référence', libelleReference()]);
    ligne([]);
    ligne(['Agrégat', 'Valeur']);
    document.querySelectorAll('.kpi').forEach(function (k) {
      ligne([k.querySelector('.kpi__label').textContent.trim().replace(/\s*i$/, ''),
             k.querySelector('.kpi__value').textContent.trim()]);
    });
    ligne([]);
    ligne(['Portée', S.scope === 'all' ? 'Tous les comptes suivis' : 'Le compte affiché']);
    ligne(['Convention', 'Une cellule vide = mesure ABSENTE. Elle ne vaut pas zéro : '
           + 'la colonne « Mesuré » distingue les deux.']);
    ligne([]);
    ligne(['Rang', 'Mesuré', 'Compte', 'Plateforme', 'Légende', 'URL du post',
           'Ouvrir dans le viewer', 'Type', 'Médias du post', 'Likes', 'Commentaires',
           'Vues', 'Engagements', 'Engagement complet', 'Taux %', 'Abonnés retenus',
           'Base des abonnés', 'Date', 'Heure', 'Jour']);
    var rang = 0;
    trierLignes(lignesTable()).forEach(function (r) {
      if (r.mesure) rang += 1;
      ligne([r.mesure ? rang : '', r.mesure ? 'oui' : 'non', r.account,
             nomPlateforme(r.platform), r.caption, r.url,
             r.viewer ? location.origin + r.viewer : '',
             r.type, r.children,
             r.likes == null ? '' : r.likes,
             r.comments == null ? '' : r.comments,
             r.views == null ? '' : r.views,
             r.eng == null ? '' : r.eng,
             r.eng == null ? '' : (r.engPartiel ? 'non' : 'oui'),
             r.rate == null ? '' : nfDec.format(r.rate),
             // Aucun taux calculé : aucun nombre d'abonnés n'a été
             // « retenu ». Remplir ces deux colonnes laisserait croire
             // qu'un calcul a eu lieu.
             r.rate == null ? '' : r.followers,
             r.rate == null ? ''
               : (r.base === 'snapshot' ? 'instantané antérieur au post'
                                        : 'abonnés du jour (approché)'),
             dateCourte(r.date), heure(r.hour),
             r.day == null ? '' : JOURS[r.day]]);
    });
    return '﻿' + l.join('\r\n');   // BOM : Excel lit l'UTF-8 sans réglage
  }

  function exporterCSV() {
    var blob = new Blob([construireCSV()], { type: 'text/csv;charset=utf-8' });
    var a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = 'samourais-analytics-' + S.days + 'j-'
               + new Date().toISOString().slice(0, 10) + '.csv';
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(function () { URL.revokeObjectURL(a.href); }, 1000);
  }

  // ══════════════════════════════════════════════════════════
  // 15. Thème — Chart.js ne lit pas le CSS, on le lui redonne
  // ══════════════════════════════════════════════════════════

  function surThemeChange() {
    if (chartsDispo) {
      Chart.defaults.color = readPalette().fg3;
      Chart.defaults.font.family = 'Inter';
    }
    rendreTrend();
    rendreSplit();
    // La heatmap et le reste sont peints par le CSS : rien à refaire.
  }

  function surveillerTheme() {
    // 1. préférence système, quand aucun choix explicite n'est stocké
    var mq = window.matchMedia('(prefers-color-scheme: dark)');
    if (mq.addEventListener) mq.addEventListener('change', surThemeChange);
    else if (mq.addListener) mq.addListener(surThemeChange);

    // 2. choix explicite posé sur <html data-theme>
    new MutationObserver(surThemeChange).observe(document.documentElement, {
      attributes: true, attributeFilter: ['data-theme']
    });

    // 3. choix changé depuis un autre onglet
    window.addEventListener('storage', function (e) {
      if (e.key !== 'theme') return;
      if (e.newValue === 'dark' || e.newValue === 'light') document.documentElement.dataset.theme = e.newValue;
      else delete document.documentElement.dataset.theme;
    });
  }

  // ══════════════════════════════════════════════════════════
  // 16. Amorçage
  // ══════════════════════════════════════════════════════════

  function demarrer() {
    if (chartsDispo) {
      var pal = readPalette();
      Chart.defaults.color = pal.fg3;
      Chart.defaults.borderColor = pal.grid;
      Chart.defaults.font.family = 'Inter';
      Chart.defaults.font.size = 11;
    }

    brancher();
    surveillerTheme();

    var q = new URL(location.href).searchParams;
    var d = parseInt(q.get('days'), 10);
    S.days = (isFinite(d) && d >= 1 && d <= MAX_DAYS) ? d : 30;
    var p = parseInt(q.get('profile_id'), 10);
    S.profileId = isFinite(p) ? p : null;
    S.scope = q.get('scope') === 'all' ? 'all' : 'profile';
    var sc = document.getElementById('perf-scope');
    if (sc) sc.value = S.scope;
    document.querySelectorAll('.seg__btn').forEach(function (b) {
      b.setAttribute('aria-pressed', String(+b.dataset.days === S.days));
    });
    var champ = document.getElementById('custom-days');
    if (champ) champ.value = PRESETS.indexOf(S.days) === -1 ? S.days : '';

    // La liste des comptes d'abord : elle fixe S.profileId, que tous
    // les appels suivants portent.
    chargerComptes()
      .then(rechargerTout)
      .catch(function (e) {
        console.error('[analytics] amorçage', e);
        var slot = document.getElementById('alert-slot');
        if (slot && !slot.firstChild) {
          var n = bloc('tpl-notice');
          n.classList.add('notice--danger');
          n.querySelector('.notice__title').textContent = 'Erreur — l’écran n’a pas pu se charger';
          n.querySelector('.notice__text').textContent =
            'Cause : ' + (e && e.message ? e.message : 'inconnue')
            + '. Recharge la page ; si l’erreur persiste, consulte la file de traitements.';
          boutons(n.querySelector('.notice__actions'), [
            { label: 'Recharger', primaire: true, onClick: function () { location.reload(); } },
            { label: 'Voir les jobs', href: '/jobs' }
          ]);
          slot.appendChild(n);
        }
      });
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', demarrer);
  else demarrer();

})();
