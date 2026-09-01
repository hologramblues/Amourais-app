/* ============================================================
   SAMOURAIS SCRAPPER — VIEWER
   ------------------------------------------------------------
   Règles tenues par ce fichier :
     - AUCUN prompt() / alert() / confirm(). Une boîte système est
       bloquante, non stylable, et un navigateur a le droit de la
       refuser : l'écran mourait alors avant d'avoir rien affiché.
       Tout passe par <dialog> et par le bandeau .banner.
     - Le pseudo n'est demandé QU'AU MOMENT de noter ou commenter.
       Le chargement de la grille ne demande jamais rien.
     - L'état complet de la vue vit dans l'URL (V14).
     - La mise en page est calculée AVANT l'arrivée des images (V5),
       à partir des dimensions renvoyées par le serveur.
   ============================================================ */

(function () {
  "use strict";

  var API = "/api/viewer";
  var PER_PAGE = 60;
  // Marge de montage : au-delà, une ligne est démontée. Elle garde sa
  // hauteur, donc l'ascenseur ne bouge pas et le DOM reste borné (V6).
  var MARGE_MONTAGE = "500px";
  // Plafond dur de vignettes présentes dans le DOM (V6 : < 200). La marge
  // de montage seule ne suffit pas : au palier le plus dense, une même
  // bande de 2000px contient trois fois plus de vignettes qu'au palier
  // large. C'est donc un budget en NOMBRE qui commande.
  var BUDGET_TUILES = 170;
  var PALIERS = ["--thumb-sm", "--thumb-md", "--thumb-lg"];
  var NOMS_PALIERS = ["dense", "moyenne", "large"];

  // ─── État ──────────────────────────────────────────────────
  var state = {
    tab: "media",
    filtres: {
      q: "", platform: "", profile_id: "", type: "", orientation: "",
      rating: "", source: "", used: "", caption: "", collection: "",
      from: "", to: "",
    },
    sort: "date_desc",
    group: "month",
    layout: "justified",
    density: 1,
    items: [],
    total: 0,
    page: 1,
    pages: 1,
    chargement: false,
    memes: { items: [], total: 0, page: 1, pages: 1, charge: false },
    selection: new Set(),
    curseur: -1,
    dernierClic: -1,
    lignes: [],
    apercu: -1,
    scrollAvantApercu: 0,
    // Par défaut on montre ce qui ne se devine pas d'un coup d'œil : l'usage
    // et la date. La plateforme et les dimensions restent à un clic (V25).
    props: { platform: false, date: true, rating: true, duration: true, usage: true, dims: false },

    // ─── Collections (V20) ───────────────────────────────────
    // La liste vient du serveur avec son compteur. `panneau` est le repli de
    // la colonne latérale, persisté ; il n'entre PAS dans l'URL — c'est une
    // préférence de poste de travail, pas un état de vue partageable.
    collections: { liste: [], panneau: true },

    // ─── Tri rapide (refonte « PAS-À-PAS », §3) ──────────────
    // `notes` et `phrases` du prototype se séparent ici : la PHRASE vit
    // sur l'item lui-même (`item.phrase`, servi par la liste), donc rien
    // à dupliquer ; seule MA note a besoin d'un cache, la liste ne
    // transportant que la moyenne du média.
    // `hist` est la pile des décisions — c'est elle, et rien d'autre,
    // que « Annuler » dépile.
    tri: {
      ouvert: false,
      index: 0,
      hist: [],        // [{id, action:"keep"|"pass"}]
      notes: {},       // {id: 1..5} — ma note, en attendant le serveur
      dx: 0,           // décalage du doigt, en px
      anim: 0,         // ±500 pendant la sortie animée, 0 sinon
      drag: null,      // origine du glissé, null hors geste
      minuterie: null,
      jeton: 0,        // annule la réponse d'une carte déjà quittée
    },

    // ─── Doublons (V27/V28/V29) ──────────────────────────────
    // `exact` et `similar` sont deux résultats de scan DISTINCTS, gardés
    // côte à côte : basculer de mode n'en jette aucun.
    dup: {
      ouvert: false,
      mode: "exact",
      distance: 6,      // seuil du curseur, appliqué APRÈS le scan
      exact: null,
      similar: null,
      chargement: false,
      empreintes: null, // {total, restants, md5, phash}
      calculEnCours: false,
      // Vrai quand une passe complète n'a plus rien fait avancer : les
      // médias restants ne PEUVENT pas recevoir d'empreinte (fichier
      // absent, ou contenu dont on ne sait pas tirer d'image). On cesse
      // alors de proposer un bouton qui ne changerait rien.
      empreintesBloquees: false,
    },
  };

  var user = null;
  try { user = localStorage.getItem("viewer_user") || null; } catch (e) { user = null; }

  // ─── Raccourcis DOM ────────────────────────────────────────
  function $(id) { return document.getElementById(id); }
  var grid = $("v-grid");
  var chrome = $("v-chrome");
  var notices = $("v-notices");
  var lightbox = $("lightbox");

  // ============================================================
  // 1. BANDEAUX ET DIALOGUES — les remplaçants de alert/confirm/prompt
  // ============================================================

  function notifier(texte, genre) {
    var el = document.createElement("div");
    el.className = "banner" + (genre ? " banner--" + genre : "");
    el.setAttribute("role", genre === "danger" ? "alert" : "status");
    var msg = document.createElement("span");
    msg.textContent = texte;
    el.appendChild(msg);

    var fermer = document.createElement("button");
    fermer.type = "button";
    fermer.className = "btn btn--sm";
    fermer.textContent = "OK";
    fermer.addEventListener("click", function () { el.remove(); });
    var actions = document.createElement("div");
    actions.className = "banner__actions";
    actions.appendChild(fermer);
    el.appendChild(actions);

    notices.appendChild(el);
    if (genre !== "danger") {
      setTimeout(function () { el.remove(); }, 6000);
    }
    return el;
  }

  /**
   * TOAST — accusé de réception, pas un message à acquitter.
   *
   * Bandeau encré au bas de l'écran, 1,6 s, sans bouton : il confirme un
   * geste qui a RÉUSSI (« Gardé ✓ », « Phrase enregistrée ✓ »). Le
   * conteneur porte role=status une fois pour toutes — le poser sur
   * chaque toast rejouerait l'annonce à chaque insertion.
   *
   * Un ÉCHEC ne passe jamais par ici : il va dans notifier(), où il reste
   * jusqu'à ce qu'on l'ait lu. Un message qui disparaît tout seul n'est
   * pas une façon d'annoncer que rien n'a été enregistré.
   */
  function toast(texte) {
    var hote = $("v-toasts");
    if (!hote) return;
    var el = document.createElement("div");
    el.className = "v-toast";
    el.textContent = texte;
    hote.appendChild(el);
    setTimeout(function () { el.remove(); }, 1600);
    return el;
  }

  /** Confirmation nommant explicitement ce qui va être touché (G16). */
  function confirmer(titre, texte, libelleOk) {
    return new Promise(function (resolve) {
      var dlg = $("dlg-confirm");
      $("dlg-confirm-title").textContent = titre;
      $("dlg-confirm-text").textContent = texte;
      var ok = $("dlg-confirm-ok");
      ok.textContent = libelleOk || "Confirmer";

      function terminer(valeur) {
        ok.removeEventListener("click", surOk);
        $("dlg-confirm-cancel").removeEventListener("click", surAnnuler);
        dlg.removeEventListener("close", surFermeture);
        if (dlg.open) dlg.close();
        resolve(valeur);
      }
      function surOk() { terminer(true); }
      function surAnnuler() { terminer(false); }
      function surFermeture() { terminer(false); }

      ok.addEventListener("click", surOk);
      $("dlg-confirm-cancel").addEventListener("click", surAnnuler);
      dlg.addEventListener("close", surFermeture);
      dlg.showModal();
      ok.focus();
    });
  }

  /**
   * Le pseudo, demandé AU POINT D'USAGE.
   * Jamais à l'initialisation : c'est exactement le défaut qu'on corrige.
   */
  function demanderPseudo() {
    return new Promise(function (resolve) {
      var dlg = $("dlg-user");
      var champ = $("dlg-user-input");
      champ.value = user || "";

      function terminer(valeur) {
        $("dlg-user-ok").removeEventListener("click", surOk);
        $("dlg-user-cancel").removeEventListener("click", surAnnuler);
        champ.removeEventListener("keydown", surTouche);
        dlg.removeEventListener("close", surFermeture);
        if (dlg.open) dlg.close();
        resolve(valeur);
      }
      function surOk() {
        var v = champ.value.trim();
        if (!v) { champ.focus(); return; }
        user = v;
        try { localStorage.setItem("viewer_user", v); } catch (e) { /* stockage refusé */ }
        terminer(v);
      }
      function surAnnuler() { terminer(null); }
      function surFermeture() { terminer(null); }
      function surTouche(e) { if (e.key === "Enter") { e.preventDefault(); surOk(); } }

      $("dlg-user-ok").addEventListener("click", surOk);
      $("dlg-user-cancel").addEventListener("click", surAnnuler);
      champ.addEventListener("keydown", surTouche);
      dlg.addEventListener("close", surFermeture);
      dlg.showModal();
      champ.focus();
      champ.select();
    });
  }

  function assurerPseudo() {
    if (user) return Promise.resolve(user);
    return demanderPseudo();
  }

  // ============================================================
  // 2. ÉTAT DANS L'URL (V14)
  // ============================================================

  // V14 : « collection » est un filtre comme les autres — il entre donc dans
  // l'URL, se retire d'un jeton, et se recompte dans les facettes.
  var CLES_FILTRE = ["q", "platform", "profile_id", "type", "orientation",
                     "rating", "source", "used", "caption", "collection",
                     "from", "to"];

  function lireURL() {
    var p = new URLSearchParams(location.search);
    CLES_FILTRE.forEach(function (k) {
      if (p.has(k)) state.filtres[k] = p.get(k);
    });
    if (p.has("sort")) state.sort = p.get("sort");
    if (p.has("group")) state.group = p.get("group");
    if (p.has("layout")) state.layout = p.get("layout") === "grid" ? "grid" : "justified";
    if (p.has("d")) {
      var d = parseInt(p.get("d"), 10);
      if (d >= 0 && d <= 2) state.density = d;
    }
    if (p.get("tab") === "memes") state.tab = "memes";
  }

  function ecrireURL() {
    var p = new URLSearchParams();
    CLES_FILTRE.forEach(function (k) {
      if (state.filtres[k]) p.set(k, state.filtres[k]);
    });
    if (state.sort !== "date_desc") p.set("sort", state.sort);
    if (state.group !== "month") p.set("group", state.group);
    if (state.layout !== "justified") p.set("layout", state.layout);
    if (state.density !== 1) p.set("d", String(state.density));
    if (state.tab !== "media") p.set("tab", state.tab);
    var qs = p.toString();
    history.replaceState(null, "", qs ? location.pathname + "?" + qs : location.pathname);
  }

  function paramsServeur(page) {
    var p = new URLSearchParams();
    CLES_FILTRE.forEach(function (k) {
      if (state.filtres[k]) p.set(k, state.filtres[k]);
    });
    p.set("sort", state.sort);
    p.set("page", String(page));
    p.set("per_page", String(PER_PAGE));
    return p;
  }

  // ============================================================
  // 3. MISE EN PAGE — lignes justifiées, ratios préservés (V3/V5)
  // ============================================================

  function paletteDensite() {
    var cs = getComputedStyle(document.body);
    return PALIERS.map(function (nom) {
      return parseFloat(cs.getPropertyValue(nom)) || 168;
    });
  }

  function hauteurCible() {
    return paletteDensite()[state.density];
  }

  function gouttiere() {
    return parseFloat(getComputedStyle(document.body).getPropertyValue("--v-gap")) || 4;
  }

  function ratio(item) {
    if (item.width > 0 && item.height > 0) {
      return Math.min(3, Math.max(0.4, item.width / item.height));
    }
    return 1; // ratio inconnu : carré, l'emplacement reste dimensionné
  }

  function itemsCourants() {
    return state.tab === "media" ? state.items : state.memes.items;
  }

  /** Découpe une suite d'index en lignes justifiées à la largeur utile. */
  function lignesJustifiees(indices, W, H, gap) {
    var items = itemsCourants();
    var lignes = [];
    var tampon = [];
    var sommeR = 0;
    indices.forEach(function (i) {
      var r = ratio(items[i]);
      tampon.push(i);
      sommeR += r;
      var largeur = sommeR * H + (tampon.length - 1) * gap;
      if (largeur >= W) {
        var h = (W - (tampon.length - 1) * gap) / sommeR;
        lignes.push({ indices: tampon, hauteur: h });
        tampon = [];
        sommeR = 0;
      }
    });
    if (tampon.length) {
      // Dernière ligne incomplète : hauteur cible, largeurs naturelles.
      lignes.push({ indices: tampon, hauteur: H });
    }
    return lignes;
  }

  /** Découpe en lignes de vignettes carrées, toutes de même taille. */
  function lignesCarrees(indices, W, H, gap) {
    var colonnes = Math.max(1, Math.floor((W + gap) / (H + gap)));
    var cote = (W - (colonnes - 1) * gap) / colonnes;
    var lignes = [];
    for (var i = 0; i < indices.length; i += colonnes) {
      lignes.push({ indices: indices.slice(i, i + colonnes), hauteur: cote, carre: cote });
    }
    return lignes;
  }

  // ─── Groupement ────────────────────────────────────────────
  function dateEffective(item) {
    return item.posted_at || item.discovered_at || item.created_at || 0;
  }

  function cleGroupe(item) {
    var ts = dateEffective(item);
    if (state.group === "profile") {
      return item.profile_username ? "@" + item.profile_username : "Source inconnue";
    }
    if (!ts) return "Sans date";
    var d = new Date(ts * 1000);
    if (state.group === "day") return d.toISOString().slice(0, 10);
    if (state.group === "month") return d.toISOString().slice(0, 7);
    if (state.group === "week") {
      var j = d.getDay();
      var lundi = new Date(d);
      lundi.setDate(d.getDate() - (j === 0 ? 6 : j - 1));
      lundi.setHours(0, 0, 0, 0);
      return "w" + lundi.toISOString().slice(0, 10);
    }
    return "";
  }

  function libelleGroupe(cle, item) {
    if (state.group === "profile" || cle === "Sans date") return cle;
    var ts = dateEffective(item);
    var d = new Date(ts * 1000);
    if (state.group === "day") {
      return d.toLocaleDateString("fr-FR", { weekday: "long", day: "numeric", month: "long", year: "numeric" });
    }
    if (state.group === "month") {
      return d.toLocaleDateString("fr-FR", { month: "long", year: "numeric" });
    }
    if (state.group === "week") {
      var lundi = new Date(cle.slice(1) + "T00:00:00");
      return "Semaine du " + lundi.toLocaleDateString("fr-FR", { day: "numeric", month: "long", year: "numeric" });
    }
    return cle;
  }

  function grouper() {
    var items = itemsCourants();
    if (state.group === "none" || state.tab === "memes") {
      return items.length ? [{ cle: "", libelle: "", indices: items.map(function (_, i) { return i; }) }] : [];
    }
    var groupes = [];
    var courant = null;
    items.forEach(function (item, i) {
      var cle = cleGroupe(item);
      if (!courant || courant.cle !== cle) {
        courant = { cle: cle, libelle: libelleGroupe(cle, item), indices: [] };
        groupes.push(courant);
      }
      courant.indices.push(i);
    });
    return fusionnerLesGroupesMaigres(groupes);
  }

  /**
   * Un en-tête pour UN média, c'est 28px de chrome pour 1 vignette : le
   * groupement se retourne alors contre la densité qu'il est censé servir.
   * Les groupes consécutifs qui ne remplissent même pas une ligne sont donc
   * réunis sous un seul en-tête d'intervalle.
   */
  function fusionnerLesGroupesMaigres(groupes) {
    var gap = gouttiere();
    var W = Math.max(1, grid.clientWidth - 2 * gap);
    var H = hauteurCible();
    // Capacité approchée d'une ligne, au ratio portrait dominant.
    var seuil = Math.max(3, Math.floor(W / (H * 0.8 + gap)));

    var sortie = [];
    var i = 0;
    while (i < groupes.length) {
      if (groupes[i].indices.length >= seuil) {
        sortie.push(groupes[i]);
        i++;
        continue;
      }
      var fusion = {
        cle: groupes[i].cle,
        libelle: groupes[i].libelle,
        indices: groupes[i].indices.slice(),
      };
      var j = i + 1;
      while (j < groupes.length
             && groupes[j].indices.length < seuil
             && fusion.indices.length + groupes[j].indices.length <= seuil) {
        fusion.indices = fusion.indices.concat(groupes[j].indices);
        fusion.libelle = groupes[i].libelle + " – " + groupes[j].libelle;
        j++;
      }
      sortie.push(fusion);
      i = j;
    }
    return sortie;
  }

  // ============================================================
  // 4. RENDU
  // ============================================================

  var observateur = null;
  var sentinelle = null;
  var obsSentinelle = null;

  /**
   * Remet la page où elle était après une reconstruction de la grille,
   * en restant dans les bornes du nouveau document.
   */
  function restaurerScroll(y) {
    var max = Math.max(0, document.documentElement.scrollHeight - window.innerHeight);
    var cible = Math.min(y, max);
    if (Math.abs(window.scrollY - cible) > 1) window.scrollTo(0, cible);
  }

  function mettreEnPage() {
    // Grille masquée (écran des doublons ouvert) : sa largeur vaut 0 et tout
    // calcul de ligne produirait une mise en page absurde, à refaire au
    // retour. On ne pose rien tant qu'elle n'est pas visible.
    if (grid.hidden) return;
    var items = itemsCourants();
    grid.dataset.layout = state.layout;
    majProps();

    // Vider la grille fait momentanément retomber la hauteur du document.
    // Le navigateur écrête alors scrollY, et l'utilisateur est renvoyé en
    // haut de la bibliothèque à CHAQUE page ajoutée par le défilement
    // continu — au bout de quatre écrans, la grille devient impraticable.
    // On cale donc la hauteur le temps de la reconstruction, puis on rend
    // sa position exacte à l'utilisateur.
    var yAvant = window.scrollY;
    var hAvant = grid.offsetHeight;
    if (hAvant > 0) grid.style.minHeight = hAvant + "px";

    if (observateur) observateur.disconnect();
    grid.replaceChildren();
    state.lignes = [];

    if (!items.length) {
      grid.style.minHeight = "";
      grid.appendChild(etatVide());
      return;
    }

    var gap = gouttiere();
    var W = grid.clientWidth - 2 * gap;
    if (W <= 0) { grid.style.minHeight = ""; return; }
    var H = hauteurCible();

    var groupes = grouper();
    var decoupe = state.layout === "grid" ? lignesCarrees : lignesJustifiees;

    groupes.forEach(function (g) {
      var section = document.createElement("section");
      section.className = "v-group";

      if (g.cle) section.appendChild(enTeteGroupe(g));

      var lignes = decoupe(g.indices, W, H, gap);
      lignes.forEach(function (ligne) {
        var el = document.createElement("div");
        el.className = "v-row v-row--parked";
        el.style.height = Math.round(ligne.hauteur) + "px";
        ligne.el = el;
        ligne.gap = gap;
        ligne.W = W;
        el.__ligne = ligne;
        state.lignes.push(ligne);
        section.appendChild(el);
      });

      grid.appendChild(section);
    });

    // Les hauteurs de ligne sont posées : le document a sa taille
    // définitive. On relâche le calage et on restaure la position AVANT de
    // monter les tuiles, sans quoi on monterait les lignes d'une position
    // de défilement qui n'est déjà plus la bonne.
    grid.style.minHeight = "";
    restaurerScroll(yAvant);

    // Premier montage SYNCHRONE : l'IntersectionObserver ne rend son
    // premier verdict qu'à la frame suivante — et pas du tout tant que
    // l'onglet est en arrière-plan. S'en remettre à lui seul laissait la
    // grille vide au premier rendu.
    monterVisibles();

    // Fenêtre de rendu : monte ce qui approche, démonte ce qui s'éloigne.
    observateur = new IntersectionObserver(function (entrees) {
      entrees.forEach(function (e) {
        if (e.isIntersecting) monterLigne(e.target.__ligne);
        else demonterLigne(e.target.__ligne);
      });
      respecterBudget();
    }, { rootMargin: MARGE_MONTAGE });
    state.lignes.forEach(function (l) { observateur.observe(l.el); });

    installerSentinelle();
  }

  /**
   * Chargement continu par sentinelle (V7).
   * L'écoute du scroll seule se bloquait en bas de page : arrivé au fond,
   * plus aucun événement de scroll n'est émis, donc plus rien ne se
   * chargeait. La sentinelle, elle, est réévaluée dès que le contenu
   * grandit — elle se redéclenche donc toute seule jusqu'à remplir l'écran.
   */
  function installerSentinelle() {
    if (obsSentinelle) obsSentinelle.disconnect();
    sentinelle = document.createElement("div");
    sentinelle.className = "v-sentinel";
    sentinelle.setAttribute("aria-hidden", "true");
    grid.appendChild(sentinelle);
    obsSentinelle = new IntersectionObserver(function (entrees) {
      if (entrees.some(function (e) { return e.isIntersecting; })) chargerSiBesoin();
    }, { rootMargin: "800px" });
    obsSentinelle.observe(sentinelle);
  }

  /** Monte les lignes déjà dans la fenêtre de rendu, sans attendre l'observateur. */
  function monterVisibles() {
    var marge = parseInt(MARGE_MONTAGE, 10);
    var haut = -marge;
    var bas = window.innerHeight + marge;
    state.lignes.forEach(function (l) {
      var r = l.el.getBoundingClientRect();
      if (r.bottom >= haut && r.top <= bas) monterLigne(l);
    });
    respecterBudget();
  }

  function tuilesMontees() {
    return state.lignes.reduce(function (n, l) {
      return n + (l.montee ? l.indices.length : 0);
    }, 0);
  }

  function eloignement(ligne) {
    var r = ligne.el.getBoundingClientRect();
    return Math.abs((r.top + r.bottom) / 2 - window.innerHeight / 2);
  }

  /** Démonte les lignes les plus éloignées jusqu'à repasser sous le budget. */
  function respecterBudget() {
    var n = tuilesMontees();
    if (n <= BUDGET_TUILES) return;
    var montees = state.lignes.filter(function (l) { return l.montee; });
    montees.sort(function (a, b) { return eloignement(b) - eloignement(a); });
    for (var i = 0; i < montees.length && n > BUDGET_TUILES; i++) {
      n -= montees[i].indices.length;
      demonterLigne(montees[i]);
    }
  }

  function enTeteGroupe(g) {
    var head = document.createElement("h2");
    head.className = "v-group__head";

    var label = document.createElement("span");
    label.className = "v-group__label";
    label.textContent = g.libelle;
    head.appendChild(label);

    var n = document.createElement("span");
    n.className = "v-group__n num";
    n.textContent = g.indices.length + (g.indices.length > 1 ? " médias" : " média");
    head.appendChild(n);

    /* Cohérence avec Analytics. `dateEffective` retombe sur
       `discovered_at` quand `posted_at` manque : sans mention, l'en-tête
       AFFIRME un mois de publication que la base ne connaît pas, alors
       que l'écran Analytics écarte ces mêmes médias de toute période
       (« un média sans date ne peut être rattaché à aucune période »).
       Le même fait doit se lire pareil des deux côtés. */
    if (state.group === "day" || state.group === "week" || state.group === "month") {
      var itemsGroupe = itemsCourants();
      var approx = 0;
      g.indices.forEach(function (i) {
        if (itemsGroupe[i] && !itemsGroupe[i].posted_at) approx++;
      });
      if (approx) {
        var marque = document.createElement("span");
        marque.className = "v-group__approx";
        marque.textContent = approx === g.indices.length
          ? (approx > 1 ? "dates de découverte" : "date de découverte")
          : approx + " à la date de découverte";
        marque.title = approx + (approx > 1 ? " médias n'ont" : " média n'a")
          + " pas de date de publication en base : "
          + (approx > 1 ? "ils sont classés" : "il est classé")
          + " à la date où le scraping "
          + (approx > 1 ? "les a découverts" : "l'a découvert")
          + ". L'écran Analytics, pour la même raison, "
          + (approx > 1 ? "les laisse" : "le laisse") + " hors période.";
        head.appendChild(marque);
      }
    }

    var pick = document.createElement("button");
    pick.type = "button";
    pick.className = "btn btn--sm v-group__pick";
    pick.textContent = "Sélectionner le groupe";
    pick.addEventListener("click", function () {
      var items = itemsCourants();
      g.indices.forEach(function (i) { if (items[i]) state.selection.add(items[i].id); });
      majSelection();
    });
    head.appendChild(pick);

    head.appendChild(Object.assign(document.createElement("span"), { className: "v-group__rule" }));
    return head;
  }

  function monterLigne(ligne) {
    if (!ligne || ligne.montee) return;
    ligne.montee = true;
    ligne.el.classList.remove("v-row--parked");
    var items = itemsCourants();
    var fragment = document.createDocumentFragment();
    ligne.indices.forEach(function (i) {
      var largeur = ligne.carre ? ligne.carre : ligne.hauteur * ratio(items[i]);
      fragment.appendChild(vignette(items[i], i, largeur));
    });
    ligne.el.replaceChildren(fragment);
  }

  function demonterLigne(ligne) {
    if (!ligne || !ligne.montee) return;
    ligne.montee = false;
    ligne.el.classList.add("v-row--parked");
    ligne.el.replaceChildren();
  }

  function vignette(item, index, largeur) {
    var fig = document.createElement("figure");
    fig.className = "v-tile is-loading";
    fig.style.width = Math.round(largeur) + "px";
    fig.dataset.index = String(index);
    fig.dataset.id = String(item.id);
    fig.tabIndex = -1;
    if (state.selection.has(item.id)) fig.classList.add("is-selected");
    if (state.curseur === index) fig.classList.add("is-cursor");

    var img = document.createElement("img");
    img.src = item.thumb_url || item.file_url || item.media_url || "";
    img.alt = item.caption || (item.media_type === "video" ? "Vidéo" : "Image");
    img.decoding = "async";
    img.addEventListener("load", function () { fig.classList.remove("is-loading"); });
    img.addEventListener("error", function () {
      fig.classList.remove("is-loading");
      fig.classList.add("is-broken");
    });
    fig.appendChild(img);

    var check = document.createElement("button");
    check.type = "button";
    check.className = "v-tile__check";
    check.dataset.role = "check";
    check.setAttribute("aria-label", "Sélectionner ce média");
    fig.appendChild(check);

    if (item.media_type === "video") {
      var play = document.createElement("span");
      play.className = "v-tile__play";
      play.textContent = "▶";
      fig.appendChild(play);
    }

    var meta = document.createElement("span");
    meta.className = "v-tile__meta";
    if (!item.isMeme) {
      var usage = span("v-tile__usage" + (item.used ? " v-tile__usage--used" : ""),
                       item.used ? "● Utilisé" : "○ Inédit");
      usage.title = item.used ? "Déjà programmé ou publié" : "Jamais utilisé";
      meta.appendChild(usage);
    }
    meta.appendChild(span("v-tile__platform", etiquettePlateforme(item.platform || item.template_format)));
    meta.appendChild(span("v-tile__date", dateCourte(dateEffective(item))));
    meta.appendChild(span("v-tile__duration", item.duration ? duree(item.duration) : ""));
    meta.appendChild(span("v-tile__dims", item.width && item.height ? item.width + "×" + item.height : ""));
    // La note EN DERNIER : la légende de la maquette est « date à gauche,
    // note ★ à droite », et c'est le `margin-left:auto` de .v-tile__rating
    // qui l'y pousse — encore faut-il qu'elle ferme la ligne.
    meta.appendChild(span("v-tile__rating", item.avg_rating > 0 ? "★ " + item.avg_rating : ""));
    fig.appendChild(meta);

    return fig;
  }

  function span(cls, texte) {
    var el = document.createElement("span");
    el.className = cls;
    el.textContent = texte || "";
    if (!texte) el.hidden = true;
    return el;
  }

  function etatVide() {
    var wrap = document.createElement("div");
    wrap.className = "v-empty-wrap";
    var vide = document.createElement("div");
    vide.className = "empty";
    var filtre = state.tab === "media" && filtresActifs().length > 0;
    var titre = document.createElement("p");
    titre.className = "empty__title";
    titre.textContent = filtre
      ? "Aucun média sous ces filtres"
      : (state.tab === "memes" ? "Aucun meme enregistré" : "La bibliothèque est vide");
    var texte = document.createElement("p");
    texte.className = "empty__text";
    texte.textContent = filtre
      ? "Retirez un jeton de filtre pour élargir la vue."
      : (state.tab === "memes"
        ? "Les montages enregistrés depuis l'Éditeur apparaîtront ici."
        : "Lancez un scrape depuis la page Profils pour remplir la bibliothèque.");
    vide.appendChild(titre);
    vide.appendChild(texte);
    if (filtre) {
      var actions = document.createElement("div");
      actions.className = "empty__actions";
      var b = document.createElement("button");
      b.type = "button";
      b.className = "btn btn--primary";
      b.textContent = "Effacer les filtres";
      b.addEventListener("click", effacerFiltres);
      actions.appendChild(b);
      vide.appendChild(actions);
    }
    wrap.appendChild(vide);
    return wrap;
  }

  function majProps() {
    Object.keys(state.props).forEach(function (k) {
      if (state.props[k]) grid.dataset["prop" + k.charAt(0).toUpperCase() + k.slice(1)] = "1";
      else delete grid.dataset["prop" + k.charAt(0).toUpperCase() + k.slice(1)];
    });
  }

  // ============================================================
  // 5. CHARGEMENT
  // ============================================================

  function squelette() {
    // Squelette aux dimensions exactes de la grille attendue (G21).
    var gap = gouttiere();
    var W = grid.clientWidth - 2 * gap;
    var H = hauteurCible();
    var colonnes = Math.max(1, Math.floor((W + gap) / (H + gap)));
    var lignes = Math.max(2, Math.ceil((window.innerHeight - grid.getBoundingClientRect().top) / (H + gap)));
    grid.replaceChildren();
    for (var r = 0; r < lignes; r++) {
      var row = document.createElement("div");
      row.className = "v-row";
      row.style.height = Math.round(H) + "px";
      for (var c = 0; c < colonnes; c++) {
        var t = document.createElement("figure");
        t.className = "v-tile is-loading";
        t.style.width = Math.round((W - (colonnes - 1) * gap) / colonnes) + "px";
        row.appendChild(t);
      }
      grid.appendChild(row);
    }
    // Une vue neuve (filtre, tri, recherche) commence en haut. C'est le seul
    // cas où la position précédente n'a plus de sens : elle désignait une
    // autre liste.
    window.scrollTo(0, 0);
  }

  function chargerMedias(suite) {
    if (state.chargement) return Promise.resolve();
    state.chargement = true;
    if (!suite) {
      state.page = 1;
      state.items = [];
      state.lignes = [];
      squelette();
    }
    // La page demandée n'est validée qu'au retour du serveur : un échec ne
    // doit pas faire sauter une page de la bibliothèque.
    var demandee = suite ? state.page + 1 : 1;
    return fetch(API + "/media?" + paramsServeur(demandee).toString())
      .then(function (r) { return r.json(); })
      .then(function (data) {
        if (data.error) throw new Error(data.error);
        state.page = demandee;
        state.pages = data.total_pages || 1;
        state.total = data.total || 0;
        state.items = suite ? state.items.concat(data.items) : data.items;
        majTotal();
        mettreEnPage();
      })
      .catch(function (e) {
        console.error("Chargement des médias", e);
        if (!suite) {
          grid.replaceChildren();
          var bloc = document.createElement("div");
          bloc.className = "v-empty-wrap";
          bloc.innerHTML = "";
          var vide = document.createElement("div");
          vide.className = "empty";
          var t = document.createElement("p"); t.className = "empty__title"; t.textContent = "La bibliothèque n'a pas répondu";
          var d = document.createElement("p"); d.className = "empty__text";
          d.textContent = "Le serveur a refusé la requête. Vérifiez que l'application tourne, puis réessayez.";
          var a = document.createElement("div"); a.className = "empty__actions";
          var b = document.createElement("button"); b.type = "button"; b.className = "btn btn--primary";
          b.textContent = "Réessayer";
          b.addEventListener("click", function () { chargerMedias(false); });
          a.appendChild(b); vide.appendChild(t); vide.appendChild(d); vide.appendChild(a);
          bloc.appendChild(vide);
          grid.appendChild(bloc);
        }
      })
      .then(function () {
        state.chargement = false;
        // setTimeout et non requestAnimationFrame : rAF est suspendu quand
        // l'onglet passe en arrière-plan, et la chaîne de chargement
        // s'arrêtait net au lieu de reprendre au retour.
        setTimeout(chargerSiBesoin, 0);
      });
  }

  function chargerMemes(suite) {
    var m = state.memes;
    if (m.charge) return Promise.resolve();
    m.charge = true;
    if (!suite) { m.page = 1; m.items = []; }
    return fetch(API + "/memes?page=" + m.page + "&per_page=" + PER_PAGE)
      .then(function (r) { return r.json(); })
      .then(function (data) {
        m.pages = data.total_pages || 1;
        m.total = data.total || 0;
        var normalises = (data.items || []).map(function (it) {
          var ratios = { square: [1, 1], portrait: [4, 5], story: [9, 16] };
          var r = ratios[it.template_format] || [1, 1];
          return {
            id: it.id, isMeme: true, thumb_url: it.thumbnail_url, file_url: it.file_url,
            media_type: it.media_type, caption: it.caption || it.title || "",
            template_format: it.template_format, created_at: it.created_at,
            file_size: it.file_size, width: r[0] * 100, height: r[1] * 100,
            avg_rating: 0, comment_count: 0,
          };
        });
        m.items = suite ? m.items.concat(normalises) : normalises;
        $("count-memes").textContent = m.total;
        if (state.tab === "memes") { majTotal(); mettreEnPage(); }
      })
      .catch(function (e) { console.error("Chargement des memes", e); })
      .then(function () { m.charge = false; });
  }

  function chargerSiBesoin() {
    // Écran des doublons ouvert : la grille est masquée, la hauteur du
    // document est celle des groupes, et rien ne doit déclencher la
    // pagination de la bibliothèque.
    if (state.dup.ouvert) return;
    var reste = document.documentElement.scrollHeight - window.innerHeight - window.scrollY;
    if (reste > 800) return;
    if (state.tab === "media" && !state.chargement && state.page < state.pages) {
      chargerMedias(true);
    } else if (state.tab === "memes" && !state.memes.charge && state.memes.page < state.memes.pages) {
      state.memes.page++;
      chargerMemes(true);
    }
  }

  function majTotal() {
    var n = state.tab === "media" ? state.total : state.memes.total;
    $("v-total-n").textContent = n;
    $("v-total-mot").textContent = state.tab === "media"
      ? (n > 1 ? "médias" : "média")
      : (n > 1 ? "memes" : "meme");
    if (state.tab === "media") $("count-media").textContent = state.total;
  }

  // ============================================================
  // 6. FACETTES ET JETONS (V12, V13)
  // ============================================================

  var LIBELLES = {
    platform: "Plateforme", type: "Type", profile_id: "Profil",
    orientation: "Orientation", rating: "Note", used: "Usage",
    caption: "Légende", source: "Source", q: "Recherche",
    collection: "Collection", from: "À partir du", to: "Jusqu'au",
  };
  var VALEURS = {
    type: { image: "Image", video: "Vidéo" },
    orientation: { portrait: "Portrait", paysage: "Paysage", carre: "Carré" },
    used: { oui: "Déjà utilisé", non: "Jamais utilisé" },
    caption: { oui: "Avec légende", non: "Sans légende" },
    source: { profiles: "Profils suivis", quicklink: "Quick Download" },
  };

  var nomsProfils = {};

  function libelleValeur(cle, valeur) {
    if (cle === "collection") return nomCollection(valeur);
    if (cle === "profile_id") return nomsProfils[valeur] || "profil " + valeur;
    if (cle === "rating") return valeur + "★ et plus";
    if (cle === "platform") return etiquettePlateforme(valeur);
    if (VALEURS[cle] && VALEURS[cle][valeur]) return VALEURS[cle][valeur];
    return String(valeur);
  }

  function filtresActifs() {
    return CLES_FILTRE.filter(function (k) { return state.filtres[k]; });
  }

  function chargerFacettes() {
    var p = new URLSearchParams();
    CLES_FILTRE.forEach(function (k) { if (state.filtres[k]) p.set(k, state.filtres[k]); });
    return fetch(API + "/facets?" + p.toString())
      .then(function (r) { return r.json(); })
      .then(function (data) {
        if (!data.facettes) return;
        (data.facettes.profile_id || []).forEach(function (f) {
          nomsProfils[String(f.valeur)] = f.libelle || String(f.valeur);
        });
        rendreFacettes(data.facettes);
        rendreJetons();
      })
      .catch(function (e) { console.error("Facettes", e); });
  }

  var ORDRE_FACETTES = ["type", "platform", "profile_id", "collection",
                        "orientation", "rating", "used", "caption", "source"];

  function rendreFacettes(facettes) {
    var hote = $("v-facets");
    hote.replaceChildren();

    ORDRE_FACETTES.forEach(function (cle) {
      var valeurs = (facettes[cle] || []).filter(function (f) { return f.compte > 0; });
      // V13 : une valeur qui ne ramène rien n'est pas proposée.
      if (!valeurs.length) return;

      var bloc = document.createElement("div");
      bloc.className = "v-facets__group";
      var titre = document.createElement("p");
      titre.className = "v-facets__title";
      titre.textContent = LIBELLES[cle];
      bloc.appendChild(titre);

      valeurs.forEach(function (f) {
        var v = String(f.valeur);
        var b = document.createElement("button");
        b.type = "button";
        b.className = "v-facet" + (String(state.filtres[cle]) === v ? " is-on" : "");
        b.appendChild(span("v-facet__tick", ""));
        b.lastChild.hidden = false;
        var lab = document.createElement("span");
        lab.className = "v-facet__label";
        lab.textContent = f.libelle || libelleValeur(cle, v);
        b.appendChild(lab);
        var n = document.createElement("span");
        n.className = "v-facet__n num";
        n.textContent = f.compte;
        b.appendChild(n);
        b.addEventListener("click", function () {
          poserFiltre(cle, String(state.filtres[cle]) === v ? "" : v);
        });
        bloc.appendChild(b);
      });

      hote.appendChild(bloc);
    });
  }

  function rendreJetons() {
    var barre = $("v-chips");
    var liste = $("v-chips-list");
    liste.replaceChildren();
    var actifs = filtresActifs();

    actifs.forEach(function (cle) {
      var jeton = document.createElement("span");
      jeton.className = "chip";
      var texte = LIBELLES[cle] + " : " + (cle === "q" ? "« " + state.filtres[cle] + " »" : libelleValeur(cle, state.filtres[cle]));
      jeton.appendChild(document.createTextNode(texte));
      var x = document.createElement("button");
      x.type = "button";
      x.className = "chip__remove";
      x.setAttribute("aria-label", "Retirer le filtre " + LIBELLES[cle]);
      x.addEventListener("click", function () { poserFiltre(cle, ""); });
      jeton.appendChild(x);
      liste.appendChild(jeton);
    });

    barre.hidden = actifs.length === 0;
    var badge = $("filters-badge");
    badge.textContent = actifs.length;
    badge.hidden = actifs.length === 0;
    mesurerChrome();
  }

  function poserFiltre(cle, valeur) {
    state.filtres[cle] = valeur;
    if (cle === "q") $("f-q").value = valeur;
    if (cle === "from") $("f-from").value = valeur;
    if (cle === "to") $("f-to").value = valeur;
    // Filtrer par collection revient à la grille : l'écran des doublons ne
    // connaît pas les filtres, le laisser ouvert mentirait sur le résultat.
    if (cle === "collection" && state.dup.ouvert) basculerDoublons(false);
    ecrireURL();
    rendreCollections();
    chargerFacettes();
    chargerMedias(false);
  }

  function effacerFiltres() {
    CLES_FILTRE.forEach(function (k) { state.filtres[k] = ""; });
    $("f-q").value = "";
    $("f-from").value = "";
    $("f-to").value = "";
    ecrireURL();
    rendreCollections();
    chargerFacettes();
    chargerMedias(false);
  }

  // ============================================================
  // 7. SÉLECTION (V9, V10, V11)
  // ============================================================

  function majSelection() {
    var n = state.selection.size;
    $("sel-count").textContent = n;
    $("sel-label").textContent = n > 1 ? "éléments sélectionnés" : "élément sélectionné";
    $("v-selbar").hidden = n === 0;
    // La barre est FIXE en bas d'écran : sans cette réserve sous la grille,
    // elle recouvrirait la dernière rangée de vignettes.
    document.body.classList.toggle("has-selbar", n > 0);
    grid.querySelectorAll(".v-tile").forEach(function (t) {
      t.classList.toggle("is-selected", state.selection.has(parseInt(t.dataset.id, 10)));
    });
    mesurerChrome();
  }

  function basculer(index) {
    var item = itemsCourants()[index];
    if (!item) return;
    if (state.selection.has(item.id)) state.selection.delete(item.id);
    else state.selection.add(item.id);
    state.dernierClic = index;
    majSelection();
  }

  function selectionnerPlage(jusqua) {
    var depart = state.dernierClic >= 0 ? state.dernierClic : jusqua;
    var a = Math.min(depart, jusqua), b = Math.max(depart, jusqua);
    var items = itemsCourants();
    for (var i = a; i <= b; i++) if (items[i]) state.selection.add(items[i].id);
    majSelection();
  }

  function toutSelectionner() {
    itemsCourants().forEach(function (it) { state.selection.add(it.id); });
    majSelection();
  }

  function viderSelection() {
    state.selection.clear();
    majSelection();
  }

  function telechargerSelection() {
    var items = itemsCourants().filter(function (it) { return state.selection.has(it.id); });
    if (!items.length) return;
    items.forEach(function (it, k) {
      if (!it.file_url) return;
      setTimeout(function () {
        var a = document.createElement("a");
        a.href = it.file_url + (it.file_url.indexOf("?") >= 0 ? "&" : "?") + "dl=1";
        a.download = "";
        document.body.appendChild(a);
        a.click();
        a.remove();
      }, k * 250);
    });
    notifier(items.length + (items.length > 1 ? " téléchargements lancés." : " téléchargement lancé."), "success");
  }

  function supprimerSelection() {
    var n = state.selection.size;
    if (!n) return;
    confirmer(
      "Supprimer " + n + (n > 1 ? " médias" : " média") + " ?",
      "Les fichiers et leurs commentaires seront effacés du disque et de la base. "
      + "Cette action est irréversible.",
      "Supprimer définitivement"
    ).then(function (ok) {
      if (!ok) return;
      var ids = Array.from(state.selection);
      // Mutation optimiste : la grille répond immédiatement (G19).
      var avant = state.items.slice();
      state.items = state.items.filter(function (it) { return ids.indexOf(it.id) < 0; });
      state.total = Math.max(0, state.total - ids.length);
      state.selection.clear();
      majSelection();
      majTotal();
      mettreEnPage();

      fetch(API + "/media/batch", {
        method: "DELETE",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ids: ids }),
      })
        .then(function (r) { return r.json(); })
        .then(function (data) {
          if (data.error) throw new Error(data.error);
          notifier(data.deleted + (data.deleted > 1 ? " médias supprimés." : " média supprimé."), "success");
          chargerFacettes();
        })
        .catch(function () {
          // Échec : retour exact à l'état antérieur + cause (G24).
          state.items = avant;
          state.total += ids.length;
          majTotal();
          mettreEnPage();
          notifier("La suppression a échoué : la bibliothèque est inchangée. Réessayez.", "danger");
        });
    });
  }

  // ============================================================
  // 8. APERÇU (V15)
  // ============================================================

  /**
   * Verrou de défilement de l'arrière-plan pendant l'aperçu.
   * `overflow: hidden` sur le corps remet scrollY à ZÉRO : la position à
   * restaurer était donc perdue à l'OUVERTURE, bien avant qu'Échap ne
   * tente de la rendre (V15). On fige le corps à sa place au lieu de lui
   * retirer son défilement.
   */
  function verrouillerDefilement(actif) {
    if (actif) {
      document.body.style.top = (-state.scrollAvantApercu) + "px";
      document.body.classList.add("is-locked");
    } else {
      document.body.classList.remove("is-locked");
      document.body.style.top = "";
    }
  }

  function ouvrirApercu(index) {
    var items = itemsCourants();
    if (!items[index]) return;
    state.scrollAvantApercu = window.scrollY;
    state.apercu = index;
    lightbox.hidden = false;
    verrouillerDefilement(true);
    rendreApercu();
    $("lb-close").focus();
  }

  function fermerApercu() {
    if (lightbox.hidden) return;
    lightbox.hidden = true;
    verrouillerDefilement(false);
    var v = $("lb-media").querySelector("video");
    if (v) v.pause();
    $("lb-media").replaceChildren();
    var i = state.apercu;
    state.apercu = -1;
    // Restauration EXACTE de la position de scroll (V15).
    window.scrollTo(0, state.scrollAvantApercu);
    if (i >= 0) poserCurseur(i, false);
  }

  function naviguerApercu(pas) {
    var items = itemsCourants();
    var n = state.apercu + pas;
    if (n < 0 || n >= items.length) return;
    state.apercu = n;
    rendreApercu();
  }

  function rendreApercu() {
    var item = itemsCourants()[state.apercu];
    if (!item) return;
    var media = $("lb-media");
    media.replaceChildren();
    var src = item.file_url || item.media_url || "";
    if (item.media_type === "video") {
      var v = document.createElement("video");
      v.src = src;
      if (item.thumb_url) v.poster = item.thumb_url;
      v.controls = true; v.autoplay = true; v.playsInline = true; v.preload = "metadata";
      media.appendChild(v);
    } else {
      var img = document.createElement("img");
      img.src = src;
      img.alt = item.caption || "";
      media.appendChild(img);
    }

    $("lb-info").textContent = [
      item.isMeme ? "Meme" : etiquettePlateforme(item.platform),
      item.profile_username ? "@" + item.profile_username : "",
      dateLongue(dateEffective(item)),
      item.width && item.height ? item.width + "×" + item.height : "",
    ].filter(Boolean).join("  ·  ");
    $("lb-caption").textContent = item.caption || "";

    var lien = $("lb-post-link");
    if (item.post_url) { lien.href = item.post_url; lien.hidden = false; }
    else lien.hidden = true;

    // IDÉE DE VANNE — même champ `phrase` que le Tri rapide, ici sur
    // n'importe quel média. On la remplit à l'ouverture de la fiche.
    var phraseInput = $("lb-phrase");
    var phraseBloc = phraseInput && phraseInput.parentElement;
    if (phraseInput) {
      phraseInput.value = item.phrase || "";
      // Un meme n'a pas d'idée de vanne à porter : il EST déjà composé.
      if (phraseBloc) phraseBloc.hidden = !!item.isMeme;
      phraseInput._item = item;
    }

    var edit = $("lb-edit-btn");
    if (item.isMeme) edit.hidden = true;
    else {
      edit.hidden = false;
      edit.href = "/editor?media_id=" + item.id;
      edit.textContent = "Envoyer à l'éditeur";
    }

    var dl = $("lb-download-btn");
    if (item.file_url) {
      dl.hidden = false;
      dl.href = item.file_url + (item.file_url.indexOf("?") >= 0 ? "&" : "?") + "dl=1";
      dl.setAttribute("download", "");
    } else dl.hidden = true;

    var noter = document.querySelector(".rating-section");
    var commenter = document.querySelector(".comments-section");
    noter.hidden = !!item.isMeme;
    commenter.hidden = !!item.isMeme;
    if (!item.isMeme) chargerFiche(item.id);
  }

  function chargerFiche(id) {
    return fetch(API + "/media/" + id)
      .then(function (r) { return r.json(); })
      .then(function (detail) {
        if (detail.error) return;
        rendreEtoiles(detail);
        rendreCommentaires(detail.comments || [], id);
        if (!$("v-inspector").hidden) rendreInspecteur(detail);
      })
      .catch(function (e) { console.error("Fiche", e); });
  }

  function rendreEtoiles(detail) {
    var hote = $("lb-stars");
    hote.replaceChildren();
    var mienne = (detail.ratings || []).find(function (r) { return r.user_name === user; });
    var valeur = mienne ? mienne.rating : 0;
    for (var i = 1; i <= 5; i++) {
      (function (n) {
        var b = document.createElement("button");
        b.type = "button";
        b.className = "star" + (n <= valeur ? " is-on" : "");
        b.textContent = "★";
        b.setAttribute("aria-label", n + " sur 5");
        b.addEventListener("click", function () { noter(detail.id, n); });
        hote.appendChild(b);
      })(i);
    }
    var info = $("lb-rating-info");
    info.replaceChildren();
    if (detail.avg_rating > 0) {
      var fort = document.createElement("strong");
      fort.textContent = detail.avg_rating;
      info.appendChild(fort);
      info.appendChild(document.createTextNode(" / 5  ("
        + (detail.rating_count || (detail.ratings || []).length) + " votes)"));
    } else {
      info.textContent = "—"; // donnée absente : un tiret, pas un zéro (G29)
    }
  }

  function noter(id, valeur) {
    assurerPseudo().then(function (pseudo) {
      if (!pseudo) return;
      return fetch(API + "/media/" + id + "/rate", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ user_name: pseudo, rating: valeur }),
      })
        .then(function (r) { return r.json(); })
        .then(function (data) {
          if (data.error) throw new Error(data.error);
          var item = itemsCourants()[state.apercu];
          if (item) { item.avg_rating = data.avg_rating; item.rating_count = data.rating_count; }
          return chargerFiche(id);
        })
        .catch(function () { notifier("La note n'a pas pu être enregistrée.", "danger"); });
    });
  }

  function rendreCommentaires(commentaires, id) {
    var hote = $("lb-comments");
    hote.replaceChildren();
    if (!commentaires.length) {
      var vide = document.createElement("p");
      vide.className = "comment-empty";
      vide.textContent = "Aucun commentaire.";
      hote.appendChild(vide);
    }
    commentaires.forEach(function (c) {
      var bloc = document.createElement("div");
      bloc.className = "comment-item";
      var tete = document.createElement("div");
      tete.className = "comment-header";
      var auteur = document.createElement("span");
      auteur.className = "comment-author";
      auteur.textContent = c.user_name;
      tete.appendChild(auteur);
      var droite = document.createElement("span");
      var date = document.createElement("span");
      date.className = "comment-date";
      date.textContent = dateCourte(c.created_at);
      droite.appendChild(date);
      if (c.user_name === user) {
        var sup = document.createElement("button");
        sup.type = "button";
        sup.className = "comment-delete";
        sup.textContent = "Supprimer";
        sup.addEventListener("click", function () { supprimerCommentaire(id, c.id); });
        droite.appendChild(sup);
      }
      tete.appendChild(droite);
      bloc.appendChild(tete);
      var texte = document.createElement("p");
      texte.className = "comment-text";
      texte.textContent = c.text;
      bloc.appendChild(texte);
      hote.appendChild(bloc);
    });

    $("lb-comment-btn").onclick = function () { envoyerCommentaire(id); };
    $("lb-comment-input").onkeydown = function (e) {
      if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); envoyerCommentaire(id); }
    };
  }

  function envoyerCommentaire(id) {
    var champ = $("lb-comment-input");
    var texte = champ.value.trim();
    if (!texte) return;
    assurerPseudo().then(function (pseudo) {
      if (!pseudo) return;
      return fetch(API + "/media/" + id + "/comment", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ user_name: pseudo, text: texte }),
      })
        .then(function (r) { return r.json(); })
        .then(function (data) {
          if (data.error) throw new Error(data.error);
          champ.value = "";
          return chargerFiche(id);
        })
        .catch(function () { notifier("Le commentaire n'a pas pu être envoyé.", "danger"); });
    });
  }

  function supprimerCommentaire(mediaId, commentId) {
    confirmer("Supprimer ce commentaire ?", "Il sera effacé pour tout le monde.", "Supprimer")
      .then(function (ok) {
        if (!ok) return;
        fetch(API + "/media/" + mediaId + "/comment/" + commentId
          + "?user_name=" + encodeURIComponent(user || ""), { method: "DELETE" })
          .then(function () { chargerFiche(mediaId); })
          .catch(function () { notifier("Suppression impossible.", "danger"); });
      });
  }

  // ============================================================
  // 9. INSPECTEUR (V17)
  // ============================================================

  function rendreInspecteur(detail) {
    var hote = $("v-inspector-body");
    hote.replaceChildren();
    if (!detail) {
      var p = document.createElement("p");
      p.className = "empty__text";
      p.textContent = "Surlignez ou ouvrez un média pour voir sa fiche.";
      hote.appendChild(p);
      return;
    }
    var titre = document.createElement("p");
    titre.className = "v-inspector__title";
    titre.textContent = detail.file_name || ("Média #" + detail.id);
    hote.appendChild(titre);

    if (detail.file_url) {
      var img = document.createElement("img");
      img.className = "v-inspector__thumb";
      img.src = detail.file_url.replace("/media/file/", "/media/thumb/");
      img.alt = "";
      hote.appendChild(img);
    }

    var dl = document.createElement("dl");
    dl.className = "v-def";
    [
      ["Dimensions", detail.width && detail.height ? detail.width + " × " + detail.height + " px" : null],
      ["Poids", poids(detail.file_size)],
      ["Format", detail.file_name ? (detail.file_name.split(".").pop() || "").toUpperCase() : null],
      ["Type", detail.media_type === "video" ? "Vidéo" : "Image"],
      ["Durée", detail.duration ? duree(detail.duration) : null],
      ["Publié le", detail.posted_at ? dateLongue(detail.posted_at) : null],
      ["Découvert le", detail.discovered_at ? dateLongue(detail.discovered_at) : null],
      ["Profil", detail.profile_username ? "@" + detail.profile_username : null],
      ["Plateforme", etiquettePlateforme(detail.platform)],
      ["Usage", detail.used ? "Déjà programmé" : "Jamais utilisé"],
      ["Note", detail.avg_rating > 0 ? detail.avg_rating + " / 5" : null],
      ["Tags", null],
    ].forEach(function (paire) {
      var dt = document.createElement("dt");
      dt.textContent = paire[0];
      var dd = document.createElement("dd");
      // Donnée absente : un tiret neutre, jamais un zéro (G29).
      dd.textContent = paire[1] == null || paire[1] === "" ? "—" : paire[1];
      dl.appendChild(dt);
      dl.appendChild(dd);
    });
    hote.appendChild(dl);

    // V20 : TOUTES les collections d'appartenance, pas une seule. Chacune
    // se retire d'ici — retirer n'efface que l'appartenance.
    var bloc = document.createElement("div");
    bloc.className = "v-inspector__collections";
    var titreCol = document.createElement("p");
    titreCol.className = "v-inspector__sous-titre";
    titreCol.textContent = "Collections";
    bloc.appendChild(titreCol);

    var collections = detail.collections || [];
    if (!collections.length) {
      var rien = document.createElement("p");
      rien.className = "empty__text";
      rien.textContent = "Dans aucune collection.";
      bloc.appendChild(rien);
    } else {
      var liste = document.createElement("div");
      liste.className = "v-inspector__chips";
      collections.forEach(function (c) {
        var jeton = document.createElement("span");
        jeton.className = "chip";
        var lien = document.createElement("button");
        lien.type = "button";
        lien.className = "chip__lien";
        lien.textContent = c.name;
        lien.title = "Filtrer la grille sur « " + c.name + " »";
        lien.addEventListener("click", function () {
          poserFiltre("collection", String(c.id));
        });
        jeton.appendChild(lien);
        var x = document.createElement("button");
        x.type = "button";
        x.className = "chip__remove";
        x.title = "Retirer de « " + c.name + " »";
        x.setAttribute("aria-label", "Retirer ce média de la collection " + c.name);
        x.addEventListener("click", function () {
          retirerDeLaCollection(c.id, detail.id, c.name);
        });
        jeton.appendChild(x);
        liste.appendChild(jeton);
      });
      bloc.appendChild(liste);
    }
    hote.appendChild(bloc);

    if (detail.post_url) {
      var a = document.createElement("a");
      a.className = "v-inspector__link";
      a.href = detail.post_url;
      a.target = "_blank";
      a.rel = "noopener";
      a.textContent = "URL source ↗";
      hote.appendChild(a);
    }
  }

  function rafraichirInspecteur() {
    if ($("v-inspector").hidden) return;
    var item = itemsCourants()[state.curseur >= 0 ? state.curseur : 0];
    if (!item || item.isMeme) { rendreInspecteur(null); return; }
    fetch(API + "/media/" + item.id)
      .then(function (r) { return r.json(); })
      .then(function (d) { if (!d.error) rendreInspecteur(d); })
      .catch(function () { rendreInspecteur(null); });
  }

  // ============================================================
  // 10. CURSEUR ET CLAVIER (V22)
  // ============================================================

  function poserCurseur(index, defiler) {
    var items = itemsCourants();
    if (index < 0 || index >= items.length) return;
    state.curseur = index;
    grid.querySelectorAll(".v-tile.is-cursor").forEach(function (t) { t.classList.remove("is-cursor"); });
    var tuile = grid.querySelector('.v-tile[data-index="' + index + '"]');
    if (tuile) {
      tuile.classList.add("is-cursor");
      if (defiler) tuile.scrollIntoView({ block: "nearest" });
      tuile.focus({ preventScroll: true });
    } else if (defiler) {
      // La ligne visée peut être démontée : on l'amène à l'écran, on la
      // monte, puis on surligne — sinon le curseur devient invisible.
      var ligne = null;
      for (var i = 0; i < state.lignes.length; i++) {
        if (state.lignes[i].indices.indexOf(index) >= 0) { ligne = state.lignes[i]; break; }
      }
      if (ligne) {
        ligne.el.scrollIntoView({ block: "center" });
        monterVisibles();
        var t2 = grid.querySelector('.v-tile[data-index="' + index + '"]');
        if (t2) { t2.classList.add("is-cursor"); t2.focus({ preventScroll: true }); }
      }
    }
    rafraichirInspecteur();
  }

  function ligneDe(index) {
    for (var i = 0; i < state.lignes.length; i++) {
      if (state.lignes[i].indices.indexOf(index) >= 0) return i;
    }
    return -1;
  }

  function deplacerCurseur(dx, dy) {
    var items = itemsCourants();
    if (!items.length) return;
    if (state.curseur < 0) { poserCurseur(0, true); return; }
    if (dx) { poserCurseur(Math.min(items.length - 1, Math.max(0, state.curseur + dx)), true); return; }

    var li = ligneDe(state.curseur);
    if (li < 0) { poserCurseur(Math.min(items.length - 1, Math.max(0, state.curseur + dy)), true); return; }
    var colonne = state.lignes[li].indices.indexOf(state.curseur);
    var cible = state.lignes[li + dy];
    if (!cible) return;
    poserCurseur(cible.indices[Math.min(colonne, cible.indices.length - 1)], true);
  }

  function dansUnChamp(e) {
    var t = e.target;
    return t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA" || t.isContentEditable);
  }

  function installerClavier() {
    document.addEventListener("keydown", function (e) {
      // Tri rapide ouvert : il recouvre tout, donc il capte tout. Les
      // flèches y valent les deux gestes, 1–5 valent les étoiles.
      if (!$("v-tri").hidden) {
        if (dansUnChamp(e)) {
          if (e.key === "Escape") e.target.blur();
          return;
        }
        if (e.key === "Escape") { e.preventDefault(); fermerTri(); }
        else if (e.key === "ArrowRight") { e.preventDefault(); deciderTri("keep"); }
        else if (e.key === "ArrowLeft") { e.preventDefault(); deciderTri("pass"); }
        else if (e.key >= "1" && e.key <= "5") {
          var courant = triCourant();
          if (courant) { e.preventDefault(); noterTri(courant, parseInt(e.key, 10)); }
        }
        return;
      }

      // Aperçu ouvert : il capte les flèches et Échap.
      if (!lightbox.hidden) {
        if (dansUnChamp(e)) {
          if (e.key === "Escape") { e.target.blur(); }
          return;
        }
        if (e.key === "Escape") { e.preventDefault(); fermerApercu(); }
        else if (e.key === "ArrowLeft") { e.preventDefault(); naviguerApercu(-1); }
        else if (e.key === "ArrowRight") { e.preventDefault(); naviguerApercu(1); }
        else if (e.key === " ") { e.preventDefault(); fermerApercu(); }
        else if (e.key >= "1" && e.key <= "5") {
          var it = itemsCourants()[state.apercu];
          if (it && !it.isMeme) { e.preventDefault(); noter(it.id, parseInt(e.key, 10)); }
        }
        return;
      }

      // G15 : aucun raccourci à une touche quand le focus est dans un champ.
      if (dansUnChamp(e)) {
        if (e.key === "Escape") e.target.blur();
        return;
      }
      if (e.metaKey || e.ctrlKey || e.altKey) return;

      switch (e.key) {
        case "/": e.preventDefault(); $("f-q").focus(); break;
        case "ArrowRight": case "l": e.preventDefault(); deplacerCurseur(1, 0); break;
        case "ArrowLeft": case "h": e.preventDefault(); deplacerCurseur(-1, 0); break;
        case "ArrowDown": case "j": e.preventDefault(); deplacerCurseur(0, 1); break;
        case "ArrowUp": case "k": e.preventDefault(); deplacerCurseur(0, -1); break;
        case " ":
          if (state.curseur >= 0) { e.preventDefault(); ouvrirApercu(state.curseur); }
          break;
        case "Enter":
          if (state.curseur >= 0) { e.preventDefault(); ouvrirApercu(state.curseur); }
          break;
        case "x": case "X":
          if (state.curseur >= 0) { e.preventDefault(); basculer(state.curseur); }
          break;
        case "d": case "D":
          if (state.curseur >= 0) {
            var it2 = itemsCourants()[state.curseur];
            if (it2 && it2.file_url) {
              e.preventDefault();
              var a = document.createElement("a");
              a.href = it2.file_url + "?dl=1";
              a.download = "";
              document.body.appendChild(a); a.click(); a.remove();
            }
          }
          break;
        case "i": case "I": e.preventDefault(); basculerInspecteur(); break;
        case "c": case "C": e.preventDefault(); basculerCollections(); break;
        case "Escape": viderSelection(); break;
        default:
          if (e.key >= "1" && e.key <= "5" && state.curseur >= 0) {
            var it3 = itemsCourants()[state.curseur];
            if (it3 && !it3.isMeme) { e.preventDefault(); noter(it3.id, parseInt(e.key, 10)); }
          }
      }
    });
  }

  // ============================================================
  // 10 bis. COLLECTIONS MANUELLES (V20)
  // ------------------------------------------------------------
  // Une collection regroupe des médias EN TRAVERS des profils. Un média
  // appartient à autant de collections qu'on veut : rien ici ne suppose
  // une appartenance unique.
  // ============================================================

  /** Petit constructeur d'élément — le DOM à la main, jamais d'innerHTML. */
  function el(balise, classe, texte) {
    var n = document.createElement(balise);
    if (classe) n.className = classe;
    if (texte != null) n.textContent = texte;
    return n;
  }

  /** Saisie de texte en <dialog>. Remplace prompt(), qui est bloquant. */
  function demanderTexte(titre, texte, valeur, libelleOk, indice) {
    return new Promise(function (resolve) {
      var dlg = $("dlg-saisie");
      var champ = $("dlg-saisie-input");
      var erreur = $("dlg-saisie-erreur");
      $("dlg-saisie-title").textContent = titre;
      $("dlg-saisie-text").textContent = texte || "";
      $("dlg-saisie-text").hidden = !texte;
      $("dlg-saisie-ok").textContent = libelleOk || "Valider";
      champ.value = valeur || "";
      champ.placeholder = indice || "";
      erreur.hidden = true;
      erreur.textContent = "";

      function terminer(v) {
        $("dlg-saisie-ok").removeEventListener("click", surOk);
        $("dlg-saisie-cancel").removeEventListener("click", surAnnuler);
        champ.removeEventListener("keydown", surTouche);
        dlg.removeEventListener("close", surFermeture);
        if (dlg.open) dlg.close();
        resolve(v);
      }
      function surOk() {
        var v = champ.value.trim();
        if (!v) {
          erreur.textContent = "Le nom ne peut pas être vide.";
          erreur.hidden = false;
          champ.focus();
          return;
        }
        terminer(v);
      }
      function surAnnuler() { terminer(null); }
      function surFermeture() { terminer(null); }
      function surTouche(e) { if (e.key === "Enter") { e.preventDefault(); surOk(); } }

      $("dlg-saisie-ok").addEventListener("click", surOk);
      $("dlg-saisie-cancel").addEventListener("click", surAnnuler);
      champ.addEventListener("keydown", surTouche);
      dlg.addEventListener("close", surFermeture);
      dlg.showModal();
      champ.focus();
      champ.select();
    });
  }

  /** fetch JSON qui distingue « le serveur a refusé » de « le réseau est mort ». */
  function envoyer(url, options) {
    return fetch(url, options).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (d) {
        if (!r.ok || d.error) {
          var err = new Error(d.error || "Le serveur a répondu " + r.status);
          err.statut = r.status;
          throw err;
        }
        return d;
      });
    });
  }

  function trouverCollection(id) {
    var cible = String(id);
    var liste = state.collections.liste;
    for (var i = 0; i < liste.length; i++) {
      if (String(liste[i].id) === cible) return liste[i];
    }
    return null;
  }

  function nomCollection(id) {
    var c = trouverCollection(id);
    return c ? c.name : "collection " + id;
  }

  function chargerCollections() {
    return fetch(API + "/collections")
      .then(function (r) { return r.json(); })
      .then(function (d) {
        state.collections.liste = d.collections || [];
        rendreCollections();
        rendreListeAjout();
      })
      .catch(function (e) { console.error("Collections", e); });
  }

  function rendreCollections() {
    var hote = $("collections-list");
    if (!hote) return;
    hote.replaceChildren();

    // « Toutes » : la sortie du filtre, toujours au même endroit.
    var toutes = el("button", "v-col-row__pick v-col-row__pick--all"
      + (state.filtres.collection ? "" : " is-on"));
    toutes.type = "button";
    toutes.appendChild(el("span", "v-col-row__name", "Toute la bibliothèque"));
    toutes.addEventListener("click", function () { poserFiltre("collection", ""); });
    hote.appendChild(toutes);

    if (!state.collections.liste.length) {
      var vide = el("p", "v-collections__vide",
        "Aucune collection. Une collection regroupe des médias de plusieurs "
        + "profils à la fois.");
      hote.appendChild(vide);
      return;
    }

    state.collections.liste.forEach(function (c) {
      var actif = String(state.filtres.collection) === String(c.id);
      var ligne = el("div", "v-col-row" + (actif ? " is-on" : ""));

      var pick = el("button", "v-col-row__pick");
      pick.type = "button";
      pick.setAttribute("aria-pressed", actif ? "true" : "false");
      pick.appendChild(el("span", "v-col-row__name", c.name));
      pick.appendChild(el("span", "v-col-row__n num", String(c.count)));
      pick.addEventListener("click", function () {
        poserFiltre("collection", actif ? "" : String(c.id));
      });
      ligne.appendChild(pick);

      var renommer = el("button", "v-col-row__act", "✎");
      renommer.type = "button";
      renommer.title = "Renommer « " + c.name + " »";
      renommer.setAttribute("aria-label", "Renommer la collection " + c.name);
      renommer.addEventListener("click", function () { renommerCollection(c); });
      ligne.appendChild(renommer);

      var supprimer = el("button", "v-col-row__act v-col-row__act--danger", "✕");
      supprimer.type = "button";
      supprimer.title = "Supprimer « " + c.name + " »";
      supprimer.setAttribute("aria-label", "Supprimer la collection " + c.name);
      supprimer.addEventListener("click", function () { supprimerCollection(c); });
      ligne.appendChild(supprimer);

      hote.appendChild(ligne);
    });
  }

  function creerCollection(ids, valeurInitiale) {
    var combien = ids && ids.length ? ids.length : 0;
    return demanderTexte(
      "Nouvelle collection",
      combien
        ? "Les " + combien + (combien > 1 ? " médias sélectionnés y seront ajoutés." : " média sélectionné y sera ajouté.")
        : "Une collection regroupe des médias de n'importe quel profil. Un même média peut appartenir à plusieurs collections.",
      valeurInitiale || "",
      "Créer",
      "ex. Références typo"
    ).then(function (nom) {
      if (!nom) return null;
      var corps = { name: nom };
      if (combien) corps.ids = ids;
      return envoyer(API + "/collections", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(corps),
      }).then(function (d) {
        notifier(
          "Collection « " + d.name + " » créée"
          + (d.ajoutes ? " avec " + d.ajoutes + (d.ajoutes > 1 ? " médias." : " média.") : "."),
          "success"
        );
        return chargerCollections().then(chargerFacettes).then(function () { return d; });
      }).catch(function (e) {
        notifier(e.message, "danger");
        // Nom déjà pris : on redonne la main avec la saisie intacte plutôt
        // que de la perdre.
        if (e.statut === 409) return creerCollection(ids, nom);
        return null;
      });
    });
  }

  function renommerCollection(c) {
    demanderTexte(
      "Renommer la collection",
      "Les médias qu'elle contient ne bougent pas.",
      c.name, "Renommer", "Nom de la collection"
    ).then(function (nom) {
      if (!nom || nom === c.name) return;
      envoyer(API + "/collections/" + c.id, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: nom }),
      }).then(function () {
        notifier("Collection renommée en « " + nom + " ».", "success");
        return chargerCollections().then(chargerFacettes);
      }).catch(function (e) { notifier(e.message, "danger"); });
    });
  }

  function supprimerCollection(c) {
    confirmer(
      "Supprimer la collection « " + c.name + " » ?",
      "Les " + c.count + (c.count > 1 ? " médias qu'elle contient ne sont PAS supprimés"
        : " média qu'elle contient n'est PAS supprimé")
      + " : ils restent dans la bibliothèque, dans leurs autres collections, et "
      + "leurs fichiers ne sont pas touchés. Seul le regroupement disparaît.",
      "Supprimer la collection"
    ).then(function (ok) {
      if (!ok) return;
      envoyer(API + "/collections/" + c.id, { method: "DELETE" })
        .then(function () {
          notifier("Collection « " + c.name + " » supprimée. Aucun média effacé.", "success");
          if (String(state.filtres.collection) === String(c.id)) {
            poserFiltre("collection", "");
          }
          return chargerCollections().then(chargerFacettes);
        })
        .catch(function (e) { notifier(e.message, "danger"); });
    });
  }

  /** Le panneau « Ajouter à… » de la barre de sélection. */
  function rendreListeAjout() {
    var hote = $("addto-list");
    if (!hote) return;
    hote.replaceChildren();
    if (!state.collections.liste.length) {
      hote.appendChild(el("p", "v-addto__vide", "Aucune collection pour l'instant."));
      return;
    }
    state.collections.liste.forEach(function (c) {
      var b = el("button", "v-facet");
      b.type = "button";
      b.appendChild(el("span", "v-facet__label", c.name));
      b.appendChild(el("span", "v-facet__n num", String(c.count)));
      b.addEventListener("click", function () { ajouterALaCollection(c); });
      hote.appendChild(b);
    });
  }

  function ajouterALaCollection(c) {
    var ids = Array.from(state.selection);
    if (!ids.length) { notifier("Sélectionnez d'abord des médias.", "danger"); return; }
    envoyer(API + "/collections/" + c.id + "/items", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ids: ids }),
    }).then(function (d) {
      var msg = d.ajoutes
        ? d.ajoutes + (d.ajoutes > 1 ? " médias ajoutés" : " média ajouté") + " à « " + c.name + " »."
        : "Rien à ajouter : tout était déjà dans « " + c.name + " ».";
      if (d.ajoutes && d.deja_presents) {
        msg += " " + d.deja_presents + (d.deja_presents > 1 ? " y étaient déjà." : " y était déjà.");
      }
      notifier(msg, "success");
      return chargerCollections().then(chargerFacettes).then(rafraichirInspecteur);
    }).catch(function (e) { notifier(e.message, "danger"); });
  }

  function retirerDeLaCollection(collectionId, mediaId, nom) {
    envoyer(API + "/collections/" + collectionId + "/items", {
      method: "DELETE",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ids: [mediaId] }),
    }).then(function () {
      notifier("Média retiré de « " + nom + " ». Le média lui-même est intact.", "success");
      return chargerCollections().then(chargerFacettes).then(rafraichirInspecteur);
    }).catch(function (e) { notifier(e.message, "danger"); });
  }

  function basculerCollections(force) {
    var ouvert = force === undefined ? !state.collections.panneau : !!force;
    state.collections.panneau = ouvert;
    $("v-collections").hidden = !ouvert;
    $("btn-collections").setAttribute("aria-pressed", ouvert ? "true" : "false");
    try { localStorage.setItem("viewer_collections", ouvert ? "1" : "0"); } catch (e) { /* ignoré */ }
    mettreEnPage();
  }

  // ============================================================
  // 10 ter. DOUBLONS ET QUASI-DOUBLONS (V27, V28, V29)
  // ------------------------------------------------------------
  // Deux modes NOMMÉS, deux endpoints séparés, deux résultats gardés côte
  // à côte. Le curseur de tolérance ne touche QUE l'affichage : les
  // distances sont calculées une fois, au scan, et filtrées ensuite (V28).
  // ============================================================

  function basculerDoublons(force) {
    var ouvert = force === undefined ? !state.dup.ouvert : !!force;
    state.dup.ouvert = ouvert;
    $("v-dup").hidden = !ouvert;
    grid.hidden = ouvert;
    $("btn-doublons").setAttribute("aria-pressed", ouvert ? "true" : "false");
    if (ouvert) {
      // Les jetons de filtre disparaissent le temps de l'écran des doublons :
      // le scan porte sur TOUTE la bibliothèque locale, pas sur la vue
      // filtrée. Les laisser à l'écran laisserait croire le contraire.
      $("v-chips").hidden = true;
      $("v-selbar").hidden = true;
      mesurerChrome();
      majEtatEmpreintes().then(chargerDoublons);
    } else {
      rendreJetons();
      majSelection();
      mettreEnPage();
    }
  }

  function majEtatEmpreintes() {
    return fetch(API + "/fingerprints/status")
      .then(function (r) { return r.json(); })
      .then(function (d) {
        if (d.error) throw new Error(d.error);
        state.dup.empreintes = d;
        rendreEmpreintes();
        return d;
      })
      .catch(function (e) {
        console.error("État des empreintes", e);
        state.dup.empreintes = null;
        rendreEmpreintes();
      });
  }

  function rendreEmpreintes() {
    var bloc = $("dup-empreintes");
    var texte = $("dup-empreintes-texte");
    var bouton = $("btn-empreintes");
    var e = state.dup.empreintes;
    if (!e) {
      bloc.hidden = false;
      texte.textContent = "L'état des empreintes n'a pas pu être lu.";
      bouton.hidden = true;
      return;
    }
    bloc.hidden = false;
    var detail = "Empreinte de fichier : " + e.md5 + " / " + e.total
      + " · empreinte visuelle : " + e.phash + " / " + e.total + ".";

    if (e.restants > 0 && state.dup.empreintesBloquees) {
      // Une passe complète n'a rien fait avancer : insister serait un
      // bouton qui ment. On dit pourquoi, et on s'arrête là.
      texte.textContent = detail + " " + e.restants
        + (e.restants > 1
          ? " médias n'ont pas pu recevoir toutes leurs empreintes"
          : " média n'a pas pu recevoir toutes ses empreintes")
        + " : fichier absent du disque, ou contenu dont aucune image ne peut "
        + "être extraite. " + (e.restants > 1 ? "Ils restent" : "Il reste")
        + " hors comparaison.";
      bouton.hidden = true;
    } else if (e.restants > 0) {
      texte.textContent = detail + " " + e.restants
        + (e.restants > 1
          ? " médias n'en ont pas et ne peuvent donc pas être comparés"
          : " média n'en a pas et ne peut donc pas être comparé")
        + " — les médias déjà en bibliothèque ont été téléchargés avant "
        + "cette fonctionnalité.";
      bouton.hidden = state.dup.calculEnCours;
      bouton.textContent = "Calculer les empreintes (" + e.restants + ")";
    } else {
      texte.textContent = detail + " Toute la bibliothèque locale est comparable.";
      bouton.hidden = true;
    }
  }

  function majJauge(faits, total) {
    var pct = total > 0 ? Math.round((faits / total) * 100) : 100;
    $("dup-jauge-barre").style.width = pct + "%";
    $("dup-jauge").setAttribute("aria-valuenow", String(pct));
  }

  function calculerEmpreintes() {
    if (state.dup.calculEnCours) return;
    var depart = state.dup.empreintes ? state.dup.empreintes.restants : 0;
    if (!depart) return;
    state.dup.calculEnCours = true;
    state.dup.empreintesBloquees = false;
    $("dup-jauge").hidden = false;
    majJauge(0, depart);
    rendreEmpreintes();

    function lot() {
      return envoyer(API + "/fingerprints/compute", { method: "POST" })
        .then(function (d) {
          majJauge(depart - d.restants, depart);
          if (state.dup.empreintes) state.dup.empreintes.restants = d.restants;
          rendreEmpreintes();
          // `termine` vaut vrai dès qu'une passe ne fait plus RECULER le
          // nombre de restants : c'est la seule condition d'arrêt qui ne
          // peut pas tourner en rond sur un fichier inexploitable.
          if (d.restants > 0 && !d.termine) return lot();
          return d;
        });
    }

    lot()
      .then(function (d) {
        var faits = depart - d.restants;
        state.dup.empreintesBloquees = d.restants > 0;
        notifier(
          (faits > 0
            ? faits + (faits > 1 ? " empreintes calculées." : " empreinte calculée.")
            : "Aucune empreinte n'a pu être calculée.")
          + (d.restants
            ? " " + d.restants + (d.restants > 1 ? " médias restent" : " média reste")
              + " sans empreinte : fichier absent ou contenu inexploitable."
            : ""),
          d.restants ? "danger" : "success"
        );
        state.dup.exact = null;
        state.dup.similar = null;
        return majEtatEmpreintes().then(chargerDoublons);
      })
      .catch(function (e) {
        notifier("Le calcul des empreintes a échoué : " + e.message, "danger");
      })
      .then(function () {
        state.dup.calculEnCours = false;
        $("dup-jauge").hidden = true;
        rendreEmpreintes();
      });
  }

  function chargerDoublons(force) {
    var mode = state.dup.mode;
    if (force) state.dup[mode] = null;
    if (state.dup[mode]) { rendreDoublons(); return Promise.resolve(); }

    state.dup.chargement = true;
    rendreDoublons();
    var url = mode === "exact" ? "/duplicates/exact" : "/duplicates/similar";
    return envoyer(API + url)
      .then(function (d) { state.dup[mode] = d; })
      .catch(function (e) { state.dup[mode] = { erreur: e.message }; })
      .then(function () {
        state.dup.chargement = false;
        rendreDoublons();
      });
  }

  /**
   * Regroupe les paires similaires par composantes connexes, au seuil courant.
   * AUCUN appel réseau : les distances viennent du scan déjà effectué (V28).
   */
  function groupesSimilaires(donnees, seuil) {
    var parent = {};
    var cartes = {};
    (donnees.items || []).forEach(function (it) {
      cartes[it.id] = it;
      parent[it.id] = it.id;
    });
    function racine(x) {
      while (parent[x] !== x) { parent[x] = parent[parent[x]]; x = parent[x]; }
      return x;
    }
    var retenues = (donnees.paires || []).filter(function (p) {
      return p.distance <= seuil && cartes[p.a] && cartes[p.b];
    });
    retenues.forEach(function (p) {
      var ra = racine(p.a), rb = racine(p.b);
      if (ra !== rb) parent[ra] = rb;
    });

    var paquets = {};
    retenues.forEach(function (p) {
      var r = racine(p.a);
      if (!paquets[r]) paquets[r] = { ids: {}, dmax: 0, dmin: 64 };
      paquets[r].ids[p.a] = true;
      paquets[r].ids[p.b] = true;
      paquets[r].dmax = Math.max(paquets[r].dmax, p.distance);
      paquets[r].dmin = Math.min(paquets[r].dmin, p.distance);
    });

    return Object.keys(paquets).map(function (r) {
      var membres = Object.keys(paquets[r].ids).map(function (id) { return cartes[id]; });
      return {
        cle: "sim-" + r,
        items: membres,
        distance_max: paquets[r].dmax,
        distance_min: paquets[r].dmin,
        octets_recuperables: membres
          .slice(1)
          .reduce(function (t, it) { return t + (it.file_size || 0); }, 0),
      };
    }).filter(function (g) {
      return g.items.length > 1;
    }).sort(function (a, b) {
      return (a.distance_max - b.distance_max) || (b.items.length - a.items.length);
    });
  }

  /** Le candidat proposé par défaut : la plus grande définition, puis le plus lourd. */
  function meilleurCandidat(items) {
    var meilleur = items[0];
    items.forEach(function (it) {
      var px = (it.width || 0) * (it.height || 0);
      var pxm = (meilleur.width || 0) * (meilleur.height || 0);
      if (px > pxm || (px === pxm && (it.file_size || 0) > (meilleur.file_size || 0))) {
        meilleur = it;
      }
    });
    return meilleur;
  }

  function rendreDoublons() {
    var hote = $("dup-groupes");
    var resume = $("dup-resume");
    hote.replaceChildren();

    var similaire = state.dup.mode === "similar";
    $("dup-seuil").hidden = !similaire;

    if (state.dup.chargement) {
      resume.textContent = "Analyse…";
      hote.appendChild(el("p", "v-dup__attente",
        similaire
          ? "Comparaison des empreintes perceptuelles…"
          : "Regroupement des fichiers identiques…"));
      return;
    }

    var donnees = state.dup[state.dup.mode];
    if (!donnees) { resume.textContent = ""; return; }

    if (donnees.erreur) {
      resume.textContent = "";
      var bloc = el("div", "empty");
      bloc.appendChild(el("p", "empty__title", "La recherche a échoué"));
      bloc.appendChild(el("p", "empty__text", donnees.erreur));
      var act = el("div", "empty__actions");
      var rb = el("button", "btn btn--primary", "Réessayer");
      rb.type = "button";
      rb.addEventListener("click", function () { chargerDoublons(true); });
      act.appendChild(rb);
      bloc.appendChild(act);
      hote.appendChild(bloc);
      return;
    }

    var groupes = similaire
      ? groupesSimilaires(donnees, state.dup.distance)
      : (donnees.groupes || []);

    var nbMedias = groupes.reduce(function (t, g) { return t + g.items.length; }, 0);
    var recuperables = groupes.reduce(function (t, g) { return t + (g.octets_recuperables || 0); }, 0);
    resume.textContent = groupes.length
      ? groupes.length + (groupes.length > 1 ? " groupes · " : " groupe · ") + nbMedias
        + " médias · " + (poids(recuperables) || "0 Ko") + " récupérables"
      : "Aucun groupe";

    // Ce qui n'a PAS pu être comparé — dit franchement, jamais tu.
    var reserves = [];
    if (donnees.sans_empreinte) {
      reserves.push(donnees.sans_empreinte + (donnees.sans_empreinte > 1
        ? " médias sans empreinte, non comparés"
        : " média sans empreinte, non comparé"));
    }
    if (similaire && donnees.uniformes) {
      reserves.push(donnees.uniformes + (donnees.uniformes > 1
        ? " images unies écartées"
        : " image unie écartée")
        + " : une empreinte uniforme ressemblerait à toutes les autres");
    }
    if (similaire && donnees.exhaustif === false) {
      reserves.push("bibliothèque volumineuse : comparaison par seaux de 16 bits, "
        + "exhaustive jusqu'à 3 bits d'écart, très majoritairement complète au-delà");
    }
    if (reserves.length) {
      hote.appendChild(el("p", "v-dup__reserve", "À savoir — " + reserves.join(" ; ") + "."));
    }

    if (!groupes.length) {
      var vide = el("div", "empty");
      vide.appendChild(el("p", "empty__title",
        similaire ? "Aucun média visuellement proche à ce seuil" : "Aucun fichier en double"));
      vide.appendChild(el("p", "empty__text",
        similaire
          ? "Élargissez la tolérance avec le curseur : le résultat est déjà calculé, "
            + "le curseur ne fait que le filtrer."
          : "Aucun média de la bibliothèque locale ne partage son empreinte de fichier "
            + "avec un autre."));
      var enveloppe = el("div", "v-empty-wrap");
      enveloppe.appendChild(vide);
      hote.appendChild(enveloppe);
      return;
    }

    groupes.forEach(function (groupe, rang) {
      hote.appendChild(rendreGroupeDoublon(groupe, rang, similaire));
    });
  }

  function rendreGroupeDoublon(groupe, rang, similaire) {
    var section = el("section", "dup-group");
    section.dataset.cle = groupe.cle;

    var tete = el("header", "dup-group__head");
    tete.appendChild(el("h3", "dup-group__title", "Groupe " + (rang + 1)));
    tete.appendChild(el("span", "dup-group__n",
      groupe.items.length + (similaire ? " médias proches" : " fichiers identiques")));
    if (similaire) {
      tete.appendChild(el("span", "dup-group__dist num",
        groupe.distance_min === groupe.distance_max
          ? "écart " + groupe.distance_max + " bits"
          : "écart " + groupe.distance_min + " à " + groupe.distance_max + " bits"));
    }
    tete.appendChild(el("span", "dup-group__gain",
      (poids(groupe.octets_recuperables) || "0 Ko") + " récupérables"));
    tete.appendChild(el("span", "toolbar__spacer"));

    var choix = { keep: meilleurCandidat(groupe.items).id };

    var bouton = el("button", "btn btn--sm btn--danger", "Dédupliquer…");
    bouton.type = "button";
    bouton.addEventListener("click", function () {
      ouvrirDialogueDedup(groupe, choix.keep, similaire);
    });
    tete.appendChild(bouton);
    section.appendChild(tete);

    var liste = el("div", "dup-group__items");
    groupe.items.forEach(function (item) {
      liste.appendChild(carteDoublon(item, groupe, choix));
    });
    section.appendChild(liste);
    return section;
  }

  /**
   * Un candidat. V27 : résolution, poids et format visibles, côte à côte,
   * sans survol ni clic — ce sont EXACTEMENT les trois critères qui
   * permettent de décider lequel garder.
   */
  function carteDoublon(item, groupe, choix) {
    var carte = el("figure", "dup-card" + (item.id === choix.keep ? " is-keep" : ""));
    carte.dataset.id = String(item.id);

    var vue = el("div", "dup-card__vue");
    if (item.thumb_url) {
      var img = document.createElement("img");
      img.src = item.thumb_url;
      img.alt = "";
      img.loading = "lazy";
      img.decoding = "async";
      vue.appendChild(img);
    }
    if (item.media_type === "video") {
      vue.appendChild(el("span", "dup-card__video", "vidéo"));
    }
    carte.appendChild(vue);

    var corps = el("figcaption", "dup-card__corps");

    var choisir = el("label", "dup-card__keep");
    var radio = document.createElement("input");
    radio.type = "radio";
    radio.name = "keep-" + groupe.cle;
    radio.value = String(item.id);
    radio.checked = item.id === choix.keep;
    radio.addEventListener("change", function () {
      choix.keep = item.id;
      var parent = carte.parentNode;
      if (parent) {
        parent.querySelectorAll(".dup-card").forEach(function (c) {
          c.classList.toggle("is-keep", c.dataset.id === String(item.id));
        });
      }
    });
    choisir.appendChild(radio);
    choisir.appendChild(el("span", null, "Garder celui-ci"));
    corps.appendChild(choisir);

    // Les trois critères de V27, chacun sur sa ligne, toujours présents.
    var faits = el("dl", "dup-card__faits");
    [
      ["Définition", item.width && item.height ? item.width + " × " + item.height + " px" : "inconnue"],
      ["Poids", poids(item.file_size) || "inconnu"],
      ["Format", item.format || "inconnu"],
    ].forEach(function (paire) {
      faits.appendChild(el("dt", null, paire[0]));
      faits.appendChild(el("dd", null, paire[1]));
    });
    corps.appendChild(faits);

    var second = el("p", "dup-card__second");
    var morceaux = [];
    if (item.profile_username) morceaux.push("@" + item.profile_username);
    if (item.platform) morceaux.push(etiquettePlateforme(item.platform));
    var quand = item.posted_at || item.discovered_at;
    if (quand) morceaux.push(dateCourte(quand));
    if (item.duration) morceaux.push(duree(item.duration));
    second.textContent = morceaux.join(" · ");
    corps.appendChild(second);

    // Ce que la suppression détruirait : montré AVANT, pas dans un regret.
    var attaches = [];
    if (item.comment_count) attaches.push(item.comment_count + " commentaire" + (item.comment_count > 1 ? "s" : ""));
    if (item.rating_count) attaches.push(item.rating_count + " note" + (item.rating_count > 1 ? "s" : ""));
    if (item.collections && item.collections.length) {
      attaches.push(item.collections.length + " collection" + (item.collections.length > 1 ? "s" : "")
        + " (" + item.collections.join(", ") + ")");
    }
    if (attaches.length) {
      corps.appendChild(el("p", "dup-card__attaches", "Porte : " + attaches.join(" · ")));
    }

    if (item.media_type === "video") {
      corps.appendChild(el("p", "dup-card__note",
        "Vidéo : comparée sur une image de référence extraite du fichier, pas sur la séquence."));
    }

    var liens = el("p", "dup-card__liens");
    if (item.file_url) {
      var voir = document.createElement("a");
      voir.className = "v-inspector__link";
      voir.href = item.file_url;
      voir.target = "_blank";
      voir.rel = "noopener";
      voir.textContent = "Ouvrir le fichier ↗";
      liens.appendChild(voir);
    }
    corps.appendChild(liens);

    carte.appendChild(corps);
    return carte;
  }

  /**
   * V29 : le dialogue qui exige DEUX réponses avant toute suppression —
   * quel exemplaire est gardé, et quelles métadonnées le rejoignent.
   */
  function ouvrirDialogueDedup(groupe, keepId, similaire) {
    var dlg = $("dlg-dedup");
    var garde = null;
    var perdus = [];
    groupe.items.forEach(function (it) {
      if (it.id === keepId) garde = it;
      else perdus.push(it);
    });
    if (!garde || !perdus.length) {
      notifier("Choisissez d'abord l'exemplaire à garder.", "danger");
      return;
    }

    // ---- Exemplaire gardé, nommé et décrit.
    var hoteGarde = $("dedup-garde");
    hoteGarde.replaceChildren();
    var resume = el("div", "dedup__carte");
    if (garde.thumb_url) {
      var img = document.createElement("img");
      img.src = garde.thumb_url;
      img.alt = "";
      resume.appendChild(img);
    }
    var txt = el("div", "dedup__carte-texte");
    txt.appendChild(el("strong", null, garde.file_name || ("Média #" + garde.id)));
    txt.appendChild(el("span", null,
      (garde.width && garde.height ? garde.width + " × " + garde.height + " px" : "définition inconnue")
      + " · " + (poids(garde.file_size) || "poids inconnu")
      + " · " + (garde.format || "format inconnu")));
    resume.appendChild(txt);
    hoteGarde.appendChild(resume);

    // ---- Métadonnées à reprendre : comptées, jamais supposées.
    var totalCommentaires = 0, totalNotes = 0;
    var nomsCollections = {};
    perdus.forEach(function (it) {
      totalCommentaires += it.comment_count || 0;
      totalNotes += it.rating_count || 0;
      (it.collections || []).forEach(function (n) { nomsCollections[n] = true; });
    });
    var listeCollections = Object.keys(nomsCollections);

    var hoteMeta = $("dedup-meta");
    hoteMeta.replaceChildren();
    var cases = {};
    [
      ["commentaires", "Commentaires", totalCommentaires,
        totalCommentaires + " commentaire(s) des exemplaires supprimés"],
      ["notes", "Notes", totalNotes,
        totalNotes + " note(s) — celles déjà posées sur l'exemplaire gardé sont conservées telles quelles"],
      ["collections", "Collections", listeCollections.length,
        listeCollections.length ? "appartenances à : " + listeCollections.join(", ") : ""],
    ].forEach(function (def) {
      var lab = el("label", "v-check dedup__case");
      var cb = document.createElement("input");
      cb.type = "checkbox";
      cb.checked = def[2] > 0;
      cb.disabled = def[2] === 0;
      cases[def[0]] = cb;
      lab.appendChild(cb);
      var bloc = el("span", "dedup__case-texte");
      bloc.appendChild(el("span", "dedup__case-titre",
        def[1] + (def[2] ? " (" + def[2] + ")" : " — aucun")));
      if (def[2]) bloc.appendChild(el("span", "dedup__case-detail", def[3]));
      lab.appendChild(bloc);
      hoteMeta.appendChild(lab);
    });

    // ---- Ce qui part, nommé fichier par fichier.
    var hotePerdus = $("dedup-perdus");
    hotePerdus.replaceChildren();
    perdus.forEach(function (it) {
      hotePerdus.appendChild(el("li", null,
        (it.file_name || ("Média #" + it.id))
        + " — " + (it.width && it.height ? it.width + "×" + it.height : "?")
        + ", " + (poids(it.file_size) || "?")));
    });

    var octets = perdus.reduce(function (t, it) { return t + (it.file_size || 0); }, 0);
    $("dedup-avertissement").textContent =
      (perdus.length > 1
        ? "Les " + perdus.length + " fichiers ci-dessus seront effacés"
        : "Le fichier ci-dessus sera effacé")
      + " du disque et de la base "
      + "(" + (poids(octets) || "0 Ko") + " récupérés). C'est irréversible. "
      + (similaire
        ? "Ces médias sont SEMBLABLES, pas identiques : vérifiez la définition avant de valider."
        : "Ces fichiers sont identiques au bit près à celui qui est gardé.");

    var ok = $("dlg-dedup-ok");
    ok.textContent = "Supprimer " + perdus.length + " média" + (perdus.length > 1 ? "s" : "");

    function terminer() {
      ok.removeEventListener("click", surOk);
      $("dlg-dedup-cancel").removeEventListener("click", surAnnuler);
      dlg.removeEventListener("close", terminer);
      if (dlg.open) dlg.close();
    }
    function surAnnuler() { terminer(); }
    function surOk() {
      var corps = {
        keep_id: garde.id,
        remove_ids: perdus.map(function (it) { return it.id; }),
        conserver: {
          commentaires: cases.commentaires.checked,
          notes: cases.notes.checked,
          collections: cases.collections.checked,
        },
      };
      terminer();
      envoyer(API + "/duplicates/resolve", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(corps),
      }).then(function (d) {
        var t = d.transferts || {};
        notifier(
          d.supprimes + (d.supprimes > 1 ? " médias supprimés" : " média supprimé")
          + ", exemplaire #" + d.garde + " conservé. Repris sur lui : "
          + (t.commentaires || 0) + " commentaire" + (t.commentaires > 1 ? "s" : "")
          + ", " + (t.notes || 0) + " note" + (t.notes > 1 ? "s" : "")
          + ", " + (t.collections || 0) + " appartenance" + (t.collections > 1 ? "s" : "") + ".",
          "success"
        );
        // Les deux scans sont périmés : un média a disparu.
        state.dup.exact = null;
        state.dup.similar = null;
        return majEtatEmpreintes()
          .then(function () { return chargerDoublons(); })
          .then(chargerCollections)
          .then(chargerFacettes)
          .then(function () { return chargerMedias(false); });
      }).catch(function (e) {
        notifier("La déduplication a échoué : " + e.message + " — rien n'a été supprimé.", "danger");
      });
    }

    ok.addEventListener("click", surOk);
    $("dlg-dedup-cancel").addEventListener("click", surAnnuler);
    dlg.addEventListener("close", terminer);
    dlg.showModal();
  }

  // ============================================================
  // 10 quater. TRI RAPIDE (refonte « PAS-À-PAS », §3)
  // ------------------------------------------------------------
  // Un écran plein, une carte à la fois. On note (5 étoiles), on écrit
  // la phrase du futur meme, puis on GARDE (glissé à droite) ou on
  // PASSE (glissé à gauche). L'éditeur relira la phrase à son étape
  // « Texte » : c'est tout l'intérêt du mode.
  //
  // DEUX PERSISTANCES, DEUX ROUTES, ET AUCUNE INVENTION :
  //   - la note passe par POST /api/viewer/media/<id>/rate, exactement
  //     comme les étoiles de l'aperçu (même corps, même pseudo demandé
  //     au point d'usage) ;
  //   - la phrase passe par POST /api/viewer/media/<id>/phrase, la
  //     route ajoutée au socle, calquée à la ligne près sur `rate`.
  // Les deux écrivent en base, donc les deux survivent au rechargement.
  //
  // LA PILE NE SORT PAS DE LA VUE COURANTE : elle est bâtie sur
  // `itemsCourants()`, c'est-à-dire les médias déjà chargés SOUS LES
  // FILTRES ACTIFS. Trier « les 30 derniers jours notés ≥ 3 » est donc
  // un filtre puis un tri, pas un mode de plus à régler.
  // ============================================================

  // Les trois constantes du geste. Le prototype fait autorité : au-delà
  // de 80px la décision est prise, la sortie parcourt 500px en 200ms.
  var TRI_SEUIL = 80;
  var TRI_SORTIE = 500;
  var TRI_DUREE = 200;
  // Pente de l'inclinaison : la carte tourne de dx/30 degrés, soit un peu
  // moins de 3° au seuil. Assez pour se sentir, trop peu pour gêner.
  var TRI_PENTE = 30;

  /** Les médias triables : la vue courante, dans son ordre affiché. */
  function triItems() {
    return state.tab === "media" ? state.items : [];
  }

  function triCourant() {
    return triItems()[state.tri.index] || null;
  }

  function ouvrirTri() {
    // La note et la phrase sont des colonnes de MEDIA_ITEMS. Un meme
    // enregistré n'en a pas : le dire plutôt que d'ouvrir un écran vide.
    if (state.tab !== "media") {
      toast("Le tri rapide ne trie que l'onglet Médias.");
      return;
    }
    var items = triItems();
    if (!items.length) {
      toast("Aucun média à trier sous ces filtres.");
      return;
    }
    state.tri.ouvert = true;
    // Le pied du Tri rapide occupe le bas de l'écran : les toasts doivent
    // se poser au-dessus de lui, pas dessus (règle CSS `.tri-ouvert`).
    document.body.classList.add("tri-ouvert");
    state.tri.index = 0;
    state.tri.hist = [];
    state.tri.dx = 0;
    state.tri.anim = 0;
    state.tri.drag = null;
    // Même mécanique de gel que l'aperçu (V15) : le corps est figé À SA
    // PLACE, jamais privé de son défilement — sinon iOS saute en haut.
    state.scrollAvantApercu = window.scrollY;
    $("v-tri").hidden = false;
    verrouillerDefilement(true);
    rendreTri();
    $("btn-tri-close").focus();
  }

  function fermerTri() {
    if ($("v-tri").hidden) return;
    // Une phrase tapée mais pas encore quittée du doigt serait perdue :
    // on la pousse avant de fermer.
    enregistrerPhraseCourante();
    clearTimeout(state.tri.minuterie);
    state.tri.ouvert = false;
    state.tri.drag = null;
    document.body.classList.remove("tri-ouvert");
    $("v-tri").hidden = true;
    verrouillerDefilement(false);
    window.scrollTo(0, state.scrollAvantApercu);
    // Les notes viennent de changer : les légendes ★ de la grille aussi.
    mettreEnPage();
    $("btn-tri").focus();
  }

  /**
   * Compteur et jauge. Le dénominateur est le TOTAL DE LA VUE tel que le
   * serveur l'annonce — pas le nombre de cartes déjà téléchargées. Dire
   * « 12 / 60 » quand la vue en compte 312 serait un chiffre faux.
   */
  function majCompteurTri() {
    var charges = triItems().length;
    var total = state.total || charges;
    var i = Math.min(state.tri.index, total);
    var pct = total ? Math.round(i / total * 100) : 0;
    $("tri-jauge-barre").style.width = pct + "%";
    $("tri-jauge").setAttribute("aria-valuenow", String(pct));
    $("tri-compteur").textContent = (i >= total ? total : i + 1) + " / " + total;
  }

  /**
   * Réapprovisionnement de la pile.
   * Le corps est gelé pendant le tri, donc le chargement continu de la
   * grille ne se déclenche plus : sans ça, le tri s'arrêterait à la
   * première page et annoncerait « terminé » au 60e média d'une vue qui
   * en compte 300. On va chercher la suite TROIS cartes avant la fin.
   */
  function reapprovisionnerTri() {
    var charges = triItems().length;
    if (state.chargement || state.page >= state.pages) return;
    if (state.tri.index < charges - 3) return;
    chargerMedias(true).then(function () {
      if (!state.tri.ouvert) return;
      // Si la pile était VIDE, on redessine : il n'y a pas de carte à
      // l'écran, donc aucune saisie en cours à écraser. Sinon on se
      // contente du compteur — repeindre la carte effacerait une phrase
      // en train d'être tapée.
      if (state.tri.index >= charges) rendreTri();
      else majCompteurTri();
    });
  }

  /** Rendu complet de l'écran : compteur, jauge, carte ou bilan. */
  function rendreTri() {
    var items = triItems();
    var total = items.length;
    var i = state.tri.index;
    var fini = i >= total;

    majCompteurTri();
    reapprovisionnerTri();

    $("tri-pile").hidden = fini;
    $("tri-pied").hidden = fini;
    $("tri-fin").hidden = !fini;

    if (fini) {
      var gardes = state.tri.hist.filter(function (h) { return h.action === "keep"; }).length;
      var passes = state.tri.hist.length - gardes;
      $("tri-bilan").textContent =
        gardes + " gardés · " + passes + " passés · notes et phrases enregistrées";
      return;
    }

    var item = items[i];
    // Plus rien derrière la dernière carte : le fantôme mentirait.
    $("tri-pile").classList.toggle("is-derniere", i >= total - 1);

    var vue = $("tri-vue");
    vue.replaceChildren();
    var img = document.createElement("img");
    img.src = item.thumb_url || item.file_url || item.media_url || "";
    img.alt = item.caption || (item.media_type === "video" ? "Vidéo" : "Image");
    img.decoding = "async";
    vue.appendChild(img);

    $("tri-source").textContent = sourceTri(item);
    $("tri-meta").textContent = metaTri(item);
    $("tri-phrase").value = item.phrase || "";
    rendreEtoilesTri(item);
    chargerMaNote(item);

    // La carte arrive EN PLACE : sans coupure de transition, elle
    // reviendrait en glissant depuis les ±500px de la carte précédente.
    var carte = $("tri-carte");
    state.tri.dx = 0;
    state.tri.anim = 0;
    carte.style.transition = "none";
    appliquerGesteTri();
    void carte.offsetWidth;
    carte.style.transition = "";

    $("btn-tri-undo").disabled = state.tri.hist.length === 0;
  }

  function sourceTri(item) {
    var p = etiquettePlateforme(item.platform);
    var u = item.profile_username;
    if (p && u) return p + " · @" + u;
    return p || (u ? "@" + u : "Média");
  }

  function metaTri(item) {
    var bouts = [];
    var d = dateCourte(dateEffective(item));
    if (d) bouts.push(d);
    if (item.width && item.height) bouts.push(item.width + "×" + item.height);
    bouts.push(item.used ? "déjà utilisé" : "jamais utilisé");
    return bouts.join(" · ");
  }

  /**
   * La note affichée sur la carte.
   * `state.tri.notes` retient MA note dès que je l'ai posée ; à défaut on
   * part de la moyenne du média, la seule note que la liste transporte.
   * chargerMaNote() vient corriger si ma note diffère de la moyenne.
   */
  function noteTri(item) {
    if (typeof state.tri.notes[item.id] === "number") return state.tri.notes[item.id];
    return Math.round(item.avg_rating || 0);
  }

  function etoileSvg(pleine) {
    var NS = "http://www.w3.org/2000/svg";
    var svg = document.createElementNS(NS, "svg");
    svg.setAttribute("viewBox", "0 0 24 24");
    svg.setAttribute("aria-hidden", "true");
    svg.setAttribute("fill", pleine ? "currentColor" : "none");
    svg.setAttribute("stroke", "currentColor");
    svg.setAttribute("stroke-width", "2");
    var p = document.createElementNS(NS, "path");
    p.setAttribute("d", "M12 2l3.09 6.26L22 9.27l-5 4.87 1.18 6.88L12 17.77 5.82 21.02 7 14.14 2 9.27l6.91-1.01z");
    svg.appendChild(p);
    return svg;
  }

  function rendreEtoilesTri(item) {
    var hote = $("tri-etoiles");
    hote.replaceChildren();
    var valeur = noteTri(item);
    for (var i = 1; i <= 5; i++) {
      (function (n) {
        var b = document.createElement("button");
        b.type = "button";
        b.className = "v-tri__etoile";
        b.setAttribute("aria-label", n + " sur 5");
        b.setAttribute("aria-pressed", n <= valeur ? "true" : "false");
        b.appendChild(etoileSvg(n <= valeur));
        b.addEventListener("click", function () { noterTri(item, n); });
        hote.appendChild(b);
      })(i);
    }
  }

  /**
   * Ma note exacte, quand elle diffère de la moyenne.
   * Une seule requête, pour la carte AFFICHÉE, et un jeton qui annule la
   * réponse si l'on a déjà glissé plus loin : sans lui, une réponse lente
   * repeindrait les étoiles de la carte SUIVANTE.
   */
  function chargerMaNote(item) {
    if (!user) return;
    if (typeof state.tri.notes[item.id] === "number") return;
    var jeton = ++state.tri.jeton;
    fetch(API + "/media/" + item.id)
      .then(function (r) { return r.json(); })
      .then(function (detail) {
        if (jeton !== state.tri.jeton || detail.error) return;
        var mienne = (detail.ratings || []).find(function (r) { return r.user_name === user; });
        if (!mienne) return;
        state.tri.notes[item.id] = mienne.rating;
        if (triCourant() === item) rendreEtoilesTri(item);
      })
      .catch(function () { /* la moyenne reste affichée : rien de faux */ });
  }

  function noterTri(item, valeur) {
    assurerPseudo().then(function (pseudo) {
      if (!pseudo) return;
      var avant = state.tri.notes[item.id];
      // Retour immédiat : au doigt, une étoile qui attend le réseau
      // passe pour un tap raté et se fait taper deux fois.
      state.tri.notes[item.id] = valeur;
      rendreEtoilesTri(item);
      return fetch(API + "/media/" + item.id + "/rate", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ user_name: pseudo, rating: valeur }),
      })
        .then(function (r) { return r.json(); })
        .then(function (data) {
          if (data.error) throw new Error(data.error);
          item.avg_rating = data.avg_rating;
          item.rating_count = data.rating_count;
          toast("Note enregistrée ✓");
        })
        .catch(function () {
          // L'échec REVIENT en arrière : une étoile allumée qui n'est pas
          // en base est un mensonge que l'éditeur paierait plus tard.
          if (typeof avant === "number") state.tri.notes[item.id] = avant;
          else delete state.tri.notes[item.id];
          if (triCourant() === item) rendreEtoilesTri(item);
          notifier("La note n'a pas pu être enregistrée.", "danger");
        });
    });
  }

  /** Écrit la phrase si — et seulement si — elle a changé. */
  /** Champ « idée de vanne » de la fiche : enregistrement et passage à l'éditeur.
   *
   *  LE POINT DÉLICAT est le clic sur « Envoyer à l'éditeur » : la navigation
   *  partirait AVANT que le POST de la phrase n'aboutisse, et la phrase tout
   *  juste tapée serait perdue — précisément celle qu'on veut retrouver. On
   *  retient donc la navigation le temps de l'enregistrement, puis on part. */
  function installerPhraseFiche() {
    var champ = $("lb-phrase");
    var etat = $("lb-phrase-etat");
    if (!champ) return;

    champ.addEventListener("change", function () {
      var item = champ._item;
      if (!item) return;
      if (etat) etat.textContent = "Enregistrement…";
      enregistrerPhrase(item, champ.value).then(function () {
        if (etat) etat.textContent = "";
      });
    });

    var bouton = $("lb-edit-btn");
    if (!bouton) return;
    bouton.addEventListener("click", function (e) {
      var item = champ._item;
      if (!item) return;
      var v = (champ.value || "").trim();
      if (v === (item.phrase || "")) return; // rien de neuf : on laisse partir
      e.preventDefault();
      var cible = bouton.href;
      if (etat) etat.textContent = "Enregistrement…";
      enregistrerPhrase(item, v).finally(function () {
        window.location.href = cible;
      });
    });
  }

  function enregistrerPhrase(item, valeur) {
    var v = (valeur || "").trim();
    var avant = item.phrase || "";
    if (v === avant) return Promise.resolve();
    item.phrase = v || null;
    return fetch(API + "/media/" + item.id + "/phrase", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ phrase: v }),
    })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        if (data.error) throw new Error(data.error);
        item.phrase = data.phrase;
        toast(data.phrase ? "Phrase enregistrée ✓" : "Phrase effacée");
      })
      .catch(function () {
        item.phrase = avant || null;
        notifier("La phrase n'a pas pu être enregistrée.", "danger");
      });
  }

  function enregistrerPhraseCourante() {
    var item = triCourant();
    if (!item) return Promise.resolve();
    return enregistrerPhrase(item, $("tri-phrase").value);
  }

  /**
   * L'unique endroit qui écrit la transformation de la carte.
   * `anim` (la sortie) prime sur `dx` (le doigt) : pendant les 200ms de
   * sortie, plus rien ne suit le pointeur.
   */
  function appliquerGesteTri() {
    var carte = $("tri-carte");
    var x = state.tri.anim || state.tri.dx;
    carte.style.transform = "translateX(" + x + "px) rotate(" + (x / TRI_PENTE) + "deg)";
    carte.style.opacity = state.tri.anim ? "0" : "1";
    $("tri-indice-keep").style.opacity = String(Math.min(1, Math.max(0, x / TRI_SEUIL)));
    $("tri-indice-pass").style.opacity = String(Math.min(1, Math.max(0, -x / TRI_SEUIL)));
  }

  function deciderTri(action) {
    var item = triCourant();
    if (!item || state.tri.anim) return;
    // La phrase part AVANT que la carte ne quitte l'écran.
    enregistrerPhraseCourante();
    state.tri.drag = null;
    $("tri-carte").classList.remove("is-drag");
    state.tri.anim = action === "keep" ? TRI_SORTIE : -TRI_SORTIE;
    appliquerGesteTri();
    toast(action === "keep" ? "Gardé ✓" : "Passé");
    clearTimeout(state.tri.minuterie);
    state.tri.minuterie = setTimeout(function () {
      state.tri.hist.push({ id: item.id, action: action });
      state.tri.index += 1;
      rendreTri();
    }, TRI_DUREE);
  }

  /** Annuler DÉPILE la dernière décision et revient sur sa carte. */
  function annulerTri() {
    if (!state.tri.hist.length) return;
    // Une phrase tapée mais pas encore quittée du doigt serait perdue :
    // « Annuler » revient sur la carte PRÉCÉDENTE, donc il quitte la carte
    // courante exactement comme le fait un glissé ou la croix. Les trois
    // sorties doivent pousser la saisie en cours — sinon la seule façon de
    // perdre une phrase dans cet écran serait de se raviser.
    enregistrerPhraseCourante();
    clearTimeout(state.tri.minuterie);
    state.tri.hist.pop();
    state.tri.index = Math.max(0, state.tri.index - 1);
    state.tri.anim = 0;
    state.tri.dx = 0;
    rendreTri();
    toast("Décision annulée");
  }

  function recommencerTri() {
    clearTimeout(state.tri.minuterie);
    state.tri.index = 0;
    state.tri.hist = [];
    state.tri.dx = 0;
    state.tri.anim = 0;
    rendreTri();
  }

  /**
   * Le geste. pointerdown sur la carte, move/up sur la FENÊTRE : un doigt
   * qui sort de la carte en cours de glissé ne doit pas figer le geste.
   */
  function installerGestesTri() {
    var carte = $("tri-carte");

    carte.addEventListener("pointerdown", function (e) {
      if (state.tri.anim) return;
      // Les étoiles et le champ sont des CIBLES, pas une poignée : un tap
      // dessus ne doit jamais devenir un glissé.
      if (e.target.closest(".v-tri__etoiles") || e.target.closest(".v-tri__phrase")) return;
      state.tri.drag = e.clientX - state.tri.dx;
      carte.classList.add("is-drag");
    });

    window.addEventListener("pointermove", function (e) {
      if (state.tri.drag === null) return;
      state.tri.dx = e.clientX - state.tri.drag;
      appliquerGesteTri();
    });

    function relacher() {
      if (state.tri.drag === null) return;
      state.tri.drag = null;
      carte.classList.remove("is-drag");
      var dx = state.tri.dx;
      if (dx > TRI_SEUIL) deciderTri("keep");
      else if (dx < -TRI_SEUIL) deciderTri("pass");
      else { state.tri.dx = 0; appliquerGesteTri(); }
    }
    window.addEventListener("pointerup", relacher);
    window.addEventListener("pointercancel", relacher);

    // La phrase part au `change` — donc à la sortie du champ ou sur
    // Entrée. Pas à chaque frappe : ce serait une requête par lettre.
    $("tri-phrase").addEventListener("change", function () {
      enregistrerPhraseCourante();
    });
    $("tri-phrase").addEventListener("keydown", function (e) {
      if (e.key === "Enter") { e.preventDefault(); $("tri-phrase").blur(); }
    });

    $("btn-tri").addEventListener("click", ouvrirTri);
    $("btn-tri-close").addEventListener("click", fermerTri);
    $("btn-tri-retour").addEventListener("click", fermerTri);
    $("btn-tri-restart").addEventListener("click", recommencerTri);
    $("btn-tri-undo").addEventListener("click", annulerTri);
    $("btn-tri-pass").addEventListener("click", function () { deciderTri("pass"); });
    $("btn-tri-keep").addEventListener("click", function () { deciderTri("keep"); });
  }

  // ============================================================
  // 11. HELPERS D'AFFICHAGE
  // ============================================================

  var PLATEFORMES = {
    instagram: "Instagram", tiktok: "TikTok", twitter: "Twitter",
    reddit: "Reddit", square: "Carré", portrait: "Portrait", story: "Story",
  };
  function etiquettePlateforme(p) { return PLATEFORMES[p] || (p || ""); }

  function dateCourte(ts) {
    if (!ts) return "";
    return new Date(ts * 1000).toLocaleDateString("fr-FR", { day: "2-digit", month: "2-digit", year: "2-digit" });
  }
  function dateLongue(ts) {
    if (!ts) return "";
    return new Date(ts * 1000).toLocaleDateString("fr-FR", { day: "numeric", month: "long", year: "numeric" });
  }
  function duree(s) {
    var m = Math.floor(s / 60), r = Math.round(s % 60);
    return m + ":" + (r < 10 ? "0" : "") + r;
  }
  function poids(octets) {
    if (!octets) return null;
    if (octets < 1024 * 1024) return Math.round(octets / 1024) + " Ko";
    return (octets / (1024 * 1024)).toFixed(1) + " Mo";
  }

  function mesurerChrome() {
    document.body.style.setProperty("--v-chrome-h", chrome.offsetHeight + "px");
  }

  // ============================================================
  // 12. CÂBLAGE
  // ============================================================

  function basculerInspecteur() {
    var insp = $("v-inspector");
    insp.hidden = !insp.hidden;
    $("btn-inspector").setAttribute("aria-pressed", insp.hidden ? "false" : "true");
    if (!insp.hidden) rafraichirInspecteur();
    mettreEnPage();
  }

  function installerPopovers() {
    document.querySelectorAll(".v-pop").forEach(function (pop) {
      var trigger = pop.querySelector(".v-pop__trigger");
      var panel = pop.querySelector(".v-pop__panel");
      trigger.addEventListener("click", function (e) {
        e.stopPropagation();
        var ouvert = !panel.hidden;
        document.querySelectorAll(".v-pop__panel").forEach(function (p) { p.hidden = true; });
        document.querySelectorAll(".v-pop__trigger").forEach(function (t) { t.setAttribute("aria-expanded", "false"); });
        panel.hidden = ouvert;
        trigger.setAttribute("aria-expanded", ouvert ? "false" : "true");
      });
      panel.addEventListener("click", function (e) { e.stopPropagation(); });
    });
    document.addEventListener("click", function () {
      document.querySelectorAll(".v-pop__panel").forEach(function (p) { p.hidden = true; });
      document.querySelectorAll(".v-pop__trigger").forEach(function (t) { t.setAttribute("aria-expanded", "false"); });
    });
    document.addEventListener("keydown", function (e) {
      if (e.key !== "Escape") return;
      document.querySelectorAll(".v-pop__panel").forEach(function (p) { p.hidden = true; });
    });
  }

  function installerGrille() {
    grid.addEventListener("click", function (e) {
      var tuile = e.target.closest(".v-tile");
      if (!tuile) return;
      var index = parseInt(tuile.dataset.index, 10);
      if (isNaN(index)) return;

      // V9 : la case sélectionne, l'image ouvre. Aucun mode préalable.
      if (e.target.closest('[data-role="check"]')) {
        e.preventDefault();
        // Maj+clic étend la plage, que le geste parte de la case ou de
        // l'image : deux cibles voisines ne peuvent pas obéir à deux règles
        // différentes pour le même modificateur.
        if (e.shiftKey) selectionnerPlage(index);
        else basculer(index);
        return;
      }
      if (e.shiftKey) { e.preventDefault(); selectionnerPlage(index); return; }
      if (e.metaKey || e.ctrlKey) { e.preventDefault(); basculer(index); return; }
      poserCurseur(index, false);
      ouvrirApercu(index);
    });
  }

  function installerBarre() {
    // Onglets
    document.querySelectorAll(".v-tab").forEach(function (b) {
      b.addEventListener("click", function () {
        state.tab = b.dataset.tab;
        document.querySelectorAll(".v-tab").forEach(function (o) {
          var actif = o === b;
          o.classList.toggle("is-active", actif);
          o.setAttribute("aria-selected", actif ? "true" : "false");
        });
        state.selection.clear();
        state.curseur = -1;
        majSelection();
        majTotal();
        ecrireURL();
        if (state.tab === "memes" && !state.memes.items.length) chargerMemes(false);
        else mettreEnPage();
      });
    });

    // Recherche — 250ms de latence, le temps d'une frappe.
    var minuterie;
    $("f-q").addEventListener("input", function () {
      clearTimeout(minuterie);
      minuterie = setTimeout(function () { poserFiltre("q", $("f-q").value.trim()); }, 250);
    });

    $("f-from").addEventListener("change", function () { poserFiltre("from", $("f-from").value); });
    $("f-to").addEventListener("change", function () { poserFiltre("to", $("f-to").value); });

    $("f-sort").addEventListener("change", function () {
      state.sort = $("f-sort").value;
      ecrireURL();
      chargerMedias(false);
    });

    $("f-group").addEventListener("change", function () {
      state.group = $("f-group").value;
      ecrireURL();
      mettreEnPage();
    });

    document.querySelectorAll(".v-seg__btn").forEach(function (b) {
      b.addEventListener("click", function () {
        state.layout = b.dataset.layout;
        document.querySelectorAll(".v-seg__btn").forEach(function (o) {
          o.classList.toggle("is-active", o === b);
        });
        ecrireURL();
        mettreEnPage();
      });
    });

    // V1 : densité. Effet immédiat, aucun aller-retour serveur (G18).
    $("f-density").addEventListener("input", function () {
      state.density = parseInt($("f-density").value, 10);
      $("f-density").setAttribute("aria-valuetext", NOMS_PALIERS[state.density]);
      ecrireURL();
      mettreEnPage();
      requestAnimationFrame(chargerSiBesoin);
    });

    // V25 : propriétés de vignette, sans bouton Appliquer, persistées.
    document.querySelectorAll('#pop-display input[type="checkbox"]').forEach(function (cb) {
      cb.addEventListener("change", function () {
        state.props[cb.dataset.prop] = cb.checked;
        try { localStorage.setItem("viewer_props", JSON.stringify(state.props)); } catch (e) { /* ignoré */ }
        majProps();
      });
    });

    // ─── Collections (V20) ───────────────────────────────────
    $("btn-collections").addEventListener("click", function () { basculerCollections(); });
    $("btn-collection-new").addEventListener("click", function () { creerCollection([]); });
    $("btn-addto-new").addEventListener("click", function () {
      creerCollection(Array.from(state.selection));
    });

    // ─── Doublons (V27/V28/V29) ──────────────────────────────
    $("btn-doublons").addEventListener("click", function () { basculerDoublons(); });
    $("btn-dup-close").addEventListener("click", function () { basculerDoublons(false); });
    $("btn-dup-rescan").addEventListener("click", function () {
      majEtatEmpreintes().then(function () { return chargerDoublons(true); });
    });
    $("btn-empreintes").addEventListener("click", calculerEmpreintes);

    document.querySelectorAll("[data-dupmode]").forEach(function (b) {
      b.addEventListener("click", function () {
        state.dup.mode = b.dataset.dupmode;
        document.querySelectorAll("[data-dupmode]").forEach(function (o) {
          o.classList.toggle("is-active", o === b);
        });
        chargerDoublons();
      });
    });

    // V28 : le curseur ne relance AUCUN calcul. Il filtre des distances
    // déjà calculées et déjà en mémoire — d'où le rendu immédiat.
    $("dup-distance").addEventListener("input", function () {
      state.dup.distance = parseInt($("dup-distance").value, 10);
      $("dup-distance-out").textContent = "≤ " + state.dup.distance + " bits";
      rendreDoublons();
    });

    $("btn-inspector").addEventListener("click", basculerInspecteur);
    $("btn-clear-filters").addEventListener("click", effacerFiltres);
    $("btn-sel-all").addEventListener("click", toutSelectionner);
    $("btn-sel-none").addEventListener("click", viderSelection);
    $("btn-sel-download").addEventListener("click", telechargerSelection);
    $("btn-sel-delete").addEventListener("click", supprimerSelection);

    $("lb-close").addEventListener("click", fermerApercu);
    $("lb-prev").addEventListener("click", function () { naviguerApercu(-1); });
    $("lb-next").addEventListener("click", function () { naviguerApercu(1); });
  }

  function appliquerEtatAuxControles() {
    $("f-q").value = state.filtres.q;
    $("f-from").value = state.filtres.from;
    $("f-to").value = state.filtres.to;
    $("f-sort").value = state.sort;
    $("f-group").value = state.group;
    $("f-density").value = String(state.density);
    $("f-density").setAttribute("aria-valuetext", NOMS_PALIERS[state.density]);
    document.querySelectorAll(".v-seg__btn").forEach(function (b) {
      b.classList.toggle("is-active", b.dataset.layout === state.layout);
    });
    document.querySelectorAll(".v-tab").forEach(function (b) {
      var actif = b.dataset.tab === state.tab;
      b.classList.toggle("is-active", actif);
      b.setAttribute("aria-selected", actif ? "true" : "false");
    });
    document.querySelectorAll('#pop-display input[type="checkbox"]').forEach(function (cb) {
      cb.checked = !!state.props[cb.dataset.prop];
    });
    $("dup-distance").value = String(state.dup.distance);
    $("dup-distance-out").textContent = "≤ " + state.dup.distance + " bits";
    $("v-collections").hidden = !state.collections.panneau;
    $("btn-collections").setAttribute("aria-pressed", state.collections.panneau ? "true" : "false");
  }

  // ─── Amorçage ──────────────────────────────────────────────
  function init() {
    try {
      var props = JSON.parse(localStorage.getItem("viewer_props") || "null");
      if (props) Object.keys(state.props).forEach(function (k) {
        if (typeof props[k] === "boolean") state.props[k] = props[k];
      });
    } catch (e) { /* stockage indisponible */ }

    // Colonne des collections : ouverte par défaut sur un grand écran, repliée
    // sous 1024px où elle mangerait la grille. Le choix explicite prime.
    try {
      var memo = localStorage.getItem("viewer_collections");
      state.collections.panneau = memo === null
        ? window.innerWidth >= 1024
        : memo === "1";
    } catch (e) { state.collections.panneau = window.innerWidth >= 1024; }

    lireURL();
    appliquerEtatAuxControles();
    mesurerChrome();
    majProps();

    installerBarre();
    installerPopovers();
    initQuickDownload();
    installerPhraseFiche();
    installerGrille();
    installerGestesTri();
    installerClavier();

    // AUCUNE demande de pseudo ici. L'écran affiche les médias, point.
    chargerFacettes();
    chargerCollections();
    chargerMedias(false);
    chargerMemes(false);

    window.addEventListener("scroll", chargerSiBesoin, { passive: true });
    // Un onglet en arrière-plan ne reçoit ni IntersectionObserver ni rAF :
    // on relance la chaîne de chargement à son retour au premier plan.
    document.addEventListener("visibilitychange", function () {
      if (!document.hidden) { monterVisibles(); chargerSiBesoin(); }
    });

    var redim;
    window.addEventListener("resize", function () {
      clearTimeout(redim);
      redim = setTimeout(function () { mesurerChrome(); mettreEnPage(); }, 120);
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }

  // Surface de contrôle exposée pour l'inspection au navigateur.
  window.__viewer = {
    state: state,
    mettreEnPage: mettreEnPage,
    chargerSiBesoin: chargerSiBesoin,
    monterVisibles: monterVisibles,
    basculerDoublons: basculerDoublons,
    basculerCollections: basculerCollections,
    chargerCollections: chargerCollections,
    chargerDoublons: chargerDoublons,
    groupesSimilaires: groupesSimilaires,
    ouvrirTri: ouvrirTri,
    fermerTri: fermerTri,
    deciderTri: deciderTri,
    annulerTri: annulerTri,
    rendreTri: rendreTri,
    toast: toast,
    initQuickDownload: initQuickDownload,
  };

  // ==========================================================
  // QUICK DOWNLOAD — déplacé du dashboard vers la bibliothèque
  // ----------------------------------------------------------
  // Coller un lien PRODUIT un média : sa place est là où ce média
  // atterrit. L'ancien bloc vivait sur le dashboard, avec son
  // propre <script> et un affichage d'état maison ; ici il passe
  // par les toasts de la refonte, comme le reste de l'écran.
  // ==========================================================
  function initQuickDownload() {
    var form = $("v-qdl");
    if (!form) return;
    var champ = $("quick-dl-url");
    var bouton = $("quick-dl-btn");
    var etat = $("quick-dl-status");

    function dire(texte, genre) {
      if (!etat) return;
      etat.textContent = texte;
      etat.dataset.ton = genre || "";
    }

    form.addEventListener("submit", function (e) {
      e.preventDefault();
      var url = (champ.value || "").trim();
      if (!url) { dire("Colle d'abord un lien.", "danger"); champ.focus(); return; }

      bouton.disabled = true;
      var libelle = bouton.textContent;
      bouton.textContent = "En cours…";
      dire("Envoi en cours…");

      fetch("/api/quick-download", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ url: url }),
      })
        .then(function (r) { return r.json().then(function (d) { return { ok: r.ok, d: d }; }); })
        .then(function (res) {
          if (!res.ok) {
            // L'échec reste VISIBLE et nommé : c'est la règle de tout ce
            // chantier, un téléchargement raté ne doit pas passer pour un
            // succès silencieux.
            dire(res.d.error || "Échec du téléchargement.", "danger");
            return;
          }
          champ.value = "";
          dire("Téléchargement lancé — le média arrivera dans la bibliothèque.");
          toast("Téléchargement lancé ✓");
        })
        .catch(function () {
          dire("Réseau indisponible — rien n'a été lancé.", "danger");
        })
        .finally(function () {
          bouton.disabled = false;
          bouton.textContent = libelle;
        });
    });
  }
})();
