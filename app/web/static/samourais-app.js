/* ============================================================
   SAMOURAIS SCRAPPER — couche applicative partagée
   ------------------------------------------------------------
   Chargée par partials/nav.html, donc présente sur LES 8 ÉCRANS
   (dashboard, profils, médias, éditeur, calendrier, analytics,
   jobs, réglages) — y compris les 4 écrans autonomes qui
   n'étendent pas layout.html.

   Elle porte trois choses qui n'appartenaient à aucun lot d'écran
   et qui étaient donc absentes partout :

     1. La palette de commandes ⌘K (critère G13, bloquant). Le
        déclencheur .s-cmdk existait dans la nav depuis le début
        mais restait masqué : aucun écran n'enregistrait de
        palette. Il est désormais toujours alimenté, avec les
        commandes de l'écran courant en tête.
     2. Le panneau des raccourcis, touche « ? » (critère G14).
     3. L'indicateur d'état système de la nav (data-system-status),
        qui était figé sur data-state="idle" sur les 8 écrans faute
        de quoi que ce soit pour le nourrir.

   Plus le sélecteur de thème : les 8 écrans LISENT localStorage
   'theme' (script anti-flash) mais AUCUN ne l'écrivait — le thème
   clair n'était atteignable qu'en changeant la préférence système.

   Aucune dépendance. Aucune boîte de dialogue native.
   ============================================================ */
(function () {
  'use strict';

  var DOC = document;
  var ROOT = DOC.documentElement;

  /* ---------------------------------------------------------
     Garde de saisie (critère G15)
     Un raccourci à une touche ne doit JAMAIS se déclencher quand
     l'utilisateur est en train d'écrire. Vaut aussi pour les
     <dialog> ouverts par un écran : on ne veut pas voler ⌘K à un
     champ de recherche modal.
     --------------------------------------------------------- */
  function isTyping(target) {
    var el = target || DOC.activeElement;
    if (!el) return false;
    if (el.isContentEditable) return true;
    var tag = (el.tagName || '').toLowerCase();
    return tag === 'input' || tag === 'textarea' || tag === 'select';
  }

  /* =========================================================
     1. THÈME
     ========================================================= */
  function currentTheme() {
    var stored = null;
    try { stored = localStorage.getItem('theme'); } catch (e) { /* privé */ }
    if (stored === 'dark' || stored === 'light') return stored;
    return window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches
      ? 'dark' : 'light';
  }

  function applyTheme(theme) {
    ROOT.dataset.theme = theme;
    try { localStorage.setItem('theme', theme); } catch (e) { /* privé */ }
    // Les écrans qui redessinent des couleurs en JS (Chart.js dans
    // analytics, Fabric dans l'éditeur) observent déjà data-theme ;
    // on émet en plus un évènement nommé pour ceux qui préfèrent.
    DOC.dispatchEvent(new CustomEvent('samourais:themechange', { detail: { theme: theme } }));
    syncThemeButton();
  }

  function toggleTheme() {
    applyTheme(currentTheme() === 'dark' ? 'light' : 'dark');
  }

  function syncThemeButton() {
    var btn = DOC.querySelector('.s-theme');
    if (!btn) return;
    var dark = currentTheme() === 'dark';
    // Glyphe ET libellé accessible : jamais la couleur ni la seule
    // icône pour porter le sens (critère G11).
    btn.querySelector('.s-theme__glyph').textContent = dark ? '☀' : '☾';
    var label = dark ? 'Passer au thème clair' : 'Passer au thème sombre';
    btn.setAttribute('aria-label', label);
    btn.setAttribute('title', label);
  }

  /* =========================================================
     2. REGISTRE DE COMMANDES
     Les écrans ajoutent les leurs via
     window.samourais.registerCommands([...]) ; elles remontent en
     tête de palette, avant les commandes globales (G13 : « les
     commandes de l'écran courant en premier »).
     ========================================================= */
  var screenCommands = [];

  /* Commandes propres à l'écran courant.
     Elles pilotent les contrôles RÉELS déjà présents dans le DOM
     (on clique le vrai bouton) : la palette ne peut donc pas
     diverger de ce que fait la barre d'outils, et une commande
     n'apparaît que si son contrôle existe sur la page. */
  function detectScreenCommands() {
    var path = location.pathname.replace(/\/$/, '') || '/';
    var out = [];

    function click(sel, label, keys) {
      var el = DOC.querySelector(sel);
      if (!el) return;
      out.push({
        label: label, group: 'Cet écran', keys: keys,
        run: function () { el.click(); el.focus && el.focus(); }
      });
    }
    function focus(sel, label, keys) {
      var el = DOC.querySelector(sel);
      if (!el) return;
      out.push({
        label: label, group: 'Cet écran', keys: keys,
        run: function () { el.focus(); el.select && el.select(); }
      });
    }

    if (path === '/viewer') {
      focus('#v-search, .v-search input, input[type="search"]', 'Rechercher dans les médias', '/');
      click('#v-select-all, [data-select-all]', 'Tout sélectionner');
    } else if (path === '/calendar') {
      click('#new-post, [data-new-post], .cal-new', 'Nouveau post');
      click('.cal-today, [data-today]', "Aller à aujourd'hui");
    } else if (path === '/analytics') {
      click('#export-csv', 'Exporter le tableau en CSV');
    } else if (path === '/profiles') {
      focus('input[name="username"]', 'Ajouter un profil');
    } else if (path === '/jobs') {
      out.push({
        label: 'Rafraîchir la liste des jobs', group: 'Cet écran',
        run: function () { location.reload(); }
      });
    } else if (path === '/settings') {
      focus('#ig-access-token, input[type="password"]', 'Modifier un jeton d\'API');
    }
    return out;
  }

  function globalCommands() {
    return [
      { id: 'go-dashboard',  label: 'Aller au Dashboard',   group: 'Navigation', href: '/' },
      { id: 'go-profiles',   label: 'Aller aux Profils',    group: 'Navigation', href: '/profiles' },
      { id: 'go-viewer',     label: 'Aller aux Médias',     group: 'Navigation', href: '/viewer' },
      { id: 'go-editor',     label: "Aller à l'Éditeur",    group: 'Navigation', href: '/editor' },
      { id: 'go-calendar',   label: 'Aller au Calendrier',  group: 'Navigation', href: '/calendar' },
      { id: 'go-analytics',  label: 'Aller aux Analytics',  group: 'Navigation', href: '/analytics' },
      { id: 'go-jobs',       label: 'Aller aux Jobs',       group: 'Navigation', href: '/jobs' },
      { id: 'go-settings',   label: 'Aller aux Réglages',   group: 'Navigation', href: '/settings' },
      {
        id: 'toggle-theme',
        label: 'Basculer le thème clair / sombre',
        group: 'Affichage',
        keys: 'T',
        run: toggleTheme
      },
      {
        id: 'show-shortcuts',
        label: 'Afficher les raccourcis clavier',
        group: 'Aide',
        keys: '?',
        run: function () { openShortcuts(); }
      }
    ];
  }

  // Écran courant d'abord, global ensuite (exigence G13).
  function allCommands() {
    return screenCommands.concat(detectScreenCommands()).concat(globalCommands());
  }

  /* =========================================================
     3. PALETTE ⌘K
     ========================================================= */
  var pal, palInput, palList, palEmpty, palItems = [], palIndex = 0, lastFocus = null;

  function buildPalette() {
    if (pal) return pal;
    pal = DOC.createElement('dialog');
    pal.className = 's-pal';
    pal.setAttribute('aria-label', 'Palette de commandes');
    pal.innerHTML =
      '<div class="s-pal__box">' +
        '<input class="s-pal__input" type="text" autocomplete="off" spellcheck="false"' +
        ' placeholder="Rechercher une commande…" aria-label="Rechercher une commande"' +
        ' role="combobox" aria-expanded="true" aria-controls="s-pal-list" aria-autocomplete="list">' +
        '<ul class="s-pal__list" id="s-pal-list" role="listbox"></ul>' +
        '<p class="s-pal__empty" hidden>Aucune commande ne correspond.</p>' +
        '<div class="s-pal__foot">' +
          '<span><kbd>↑</kbd><kbd>↓</kbd> naviguer</span>' +
          '<span><kbd>↵</kbd> exécuter</span>' +
          '<span><kbd>Échap</kbd> fermer</span>' +
        '</div>' +
      '</div>';
    DOC.body.appendChild(pal);

    palInput = pal.querySelector('.s-pal__input');
    palList = pal.querySelector('.s-pal__list');
    palEmpty = pal.querySelector('.s-pal__empty');

    palInput.addEventListener('input', function () { renderPalette(palInput.value); });

    palInput.addEventListener('keydown', function (e) {
      if (e.key === 'ArrowDown') { e.preventDefault(); move(1); }
      else if (e.key === 'ArrowUp') { e.preventDefault(); move(-1); }
      else if (e.key === 'Enter') { e.preventDefault(); runIndex(palIndex); }
    });

    // Clic hors de la boîte : on referme, comme tout popover de l'app.
    pal.addEventListener('click', function (e) {
      if (e.target === pal) closePalette();
    });
    pal.addEventListener('close', function () {
      if (lastFocus && lastFocus.focus) lastFocus.focus();
    });
    return pal;
  }

  function move(delta) {
    if (!palItems.length) return;
    palIndex = (palIndex + delta + palItems.length) % palItems.length;
    highlight();
  }

  function highlight() {
    var nodes = palList.querySelectorAll('.s-pal__item');
    for (var i = 0; i < nodes.length; i++) {
      var on = i === palIndex;
      nodes[i].setAttribute('aria-selected', on ? 'true' : 'false');
      if (on) {
        palInput.setAttribute('aria-activedescendant', nodes[i].id);
        nodes[i].scrollIntoView({ block: 'nearest' });
      }
    }
  }

  // Filtrage par sous-séquence : « acal » trouve « Aller au Calendrier ».
  function matches(label, query) {
    if (!query) return true;
    var l = label.toLowerCase(), q = query.toLowerCase(), i = 0;
    for (var c = 0; c < l.length && i < q.length; c++) if (l[c] === q[i]) i++;
    return i === q.length;
  }

  function renderPalette(query) {
    palItems = allCommands().filter(function (c) { return matches(c.label, query); });
    palIndex = 0;
    palList.innerHTML = '';
    palEmpty.hidden = palItems.length > 0;

    var lastGroup = null;
    palItems.forEach(function (cmd, i) {
      if (cmd.group && cmd.group !== lastGroup) {
        var g = DOC.createElement('li');
        g.className = 's-pal__group';
        g.setAttribute('role', 'presentation');
        g.textContent = cmd.group;
        palList.appendChild(g);
        lastGroup = cmd.group;
      }
      var li = DOC.createElement('li');
      li.className = 's-pal__item';
      li.id = 's-pal-opt-' + i;
      li.setAttribute('role', 'option');
      li.setAttribute('aria-selected', i === 0 ? 'true' : 'false');

      var name = DOC.createElement('span');
      name.className = 's-pal__label';
      name.textContent = cmd.label;
      li.appendChild(name);

      // Le raccourci s'affiche à droite de la ligne (exigence G13).
      if (cmd.keys) {
        var kbd = DOC.createElement('kbd');
        kbd.className = 's-pal__keys';
        kbd.textContent = cmd.keys;
        li.appendChild(kbd);
      }

      li.addEventListener('click', function () { runIndex(i); });
      li.addEventListener('mousemove', function () { palIndex = i; highlight(); });
      palList.appendChild(li);
    });
    highlight();
  }

  function runIndex(i) {
    var cmd = palItems[i];
    if (!cmd) return;
    closePalette();
    if (typeof cmd.run === 'function') cmd.run();
    else if (cmd.href) window.location.href = cmd.href;
  }

  function openPalette() {
    buildPalette();
    lastFocus = DOC.activeElement;
    renderPalette('');
    palInput.value = '';
    if (!pal.open) pal.showModal();
    palInput.focus();
  }

  function closePalette() {
    if (pal && pal.open) pal.close();
  }

  /* =========================================================
     4. PANNEAU DES RACCOURCIS — touche « ? » (G14)
     ========================================================= */
  var sc;

  function openShortcuts() {
    if (!sc) {
      sc = DOC.createElement('dialog');
      sc.className = 's-sc';
      sc.setAttribute('aria-label', 'Raccourcis clavier');
      sc.addEventListener('click', function (e) { if (e.target === sc) sc.close(); });
      DOC.body.appendChild(sc);
    }
    var rows = allCommands().filter(function (c) { return c.keys; });
    sc.innerHTML =
      '<div class="s-sc__box">' +
        '<h2 class="s-sc__title">Raccourcis clavier</h2>' +
        '<dl class="s-sc__list">' +
          '<dt>Palette de commandes</dt><dd><kbd>⌘K</kbd></dd>' +
          rows.map(function (c) {
            return '<dt>' + esc(c.label) + '</dt><dd><kbd>' + esc(c.keys) + '</kbd></dd>';
          }).join('') +
        '</dl>' +
        (rows.length ? '' : '<p class="s-sc__none">Cet écran n\'expose aucun raccourci propre.</p>') +
        '<form method="dialog"><button class="s-sc__close">Fermer</button></form>' +
      '</div>';
    if (!sc.open) sc.showModal();
  }

  function esc(s) {
    return String(s).replace(/[&<>"]/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c];
    });
  }

  /* =========================================================
     4bis. CONFIRMATION ET MESSAGES — sans boîte système
     ------------------------------------------------------------
     Le Viewer et le Calendrier ont chacun remplacé leurs
     alert()/confirm() par un <dialog> local. L'Éditeur (8 alert()
     dans editor.js) et l'écran Profils (hx-confirm, qui appelle
     window.confirm) étaient restés en arrière. Plutôt qu'une
     cinquième copie, on expose ici l'implémentation partagée.

     La confirmation NOMME l'élément concerné (critère G16) et le
     bouton destructif porte le verbe, jamais « OK ».
     ========================================================= */
  var cf;

  function confirmDialog(opts) {
    opts = opts || {};
    return new Promise(function (resolve) {
      if (!cf) {
        cf = DOC.createElement('dialog');
        cf.className = 's-cf';
        DOC.body.appendChild(cf);
      }
      cf.innerHTML =
        '<div class="s-cf__box">' +
          '<h2 class="s-cf__title">' + esc(opts.title || 'Confirmer') + '</h2>' +
          '<p class="s-cf__text">' + esc(opts.message || '') + '</p>' +
          '<div class="s-cf__actions">' +
            '<button class="s-cf__cancel" value="cancel">' + esc(opts.cancel || 'Annuler') + '</button>' +
            '<button class="s-cf__ok' + (opts.danger ? ' s-cf__ok--danger' : '') + '" value="ok">' +
              esc(opts.confirm || 'Confirmer') + '</button>' +
          '</div>' +
        '</div>';

      var done = function (v) {
        if (cf.open) cf.close();
        resolve(v);
      };
      cf.querySelector('.s-cf__cancel').addEventListener('click', function () { done(false); });
      cf.querySelector('.s-cf__ok').addEventListener('click', function () { done(true); });
      cf.addEventListener('cancel', function () { resolve(false); });   // Échap
      cf.showModal();
      cf.querySelector('.s-cf__cancel').focus();   // défaut non destructif
    });
  }

  // Message non bloquant. role=status : annoncé aux lecteurs d'écran
  // sans voler le focus, contrairement à alert().
  var noteHost;

  function notify(message, kind) {
    if (!noteHost) {
      noteHost = DOC.createElement('div');
      noteHost.className = 's-notes';
      noteHost.setAttribute('role', 'status');
      noteHost.setAttribute('aria-live', 'polite');
      DOC.body.appendChild(noteHost);
    }
    var n = DOC.createElement('div');
    n.className = 's-note' + (kind ? ' s-note--' + kind : '');
    n.textContent = message;
    noteHost.appendChild(n);
    setTimeout(function () { n.remove(); }, 6000);
    return n;
  }

  /* =========================================================
     5. CLAVIER GLOBAL
     ========================================================= */
  DOC.addEventListener('keydown', function (e) {
    // ⌘K / Ctrl+K : autorisé même depuis un champ (c'est la
    // convention de toutes les palettes) — mais pas quand une
    // palette est déjà ouverte.
    if ((e.metaKey || e.ctrlKey) && (e.key === 'k' || e.key === 'K')) {
      e.preventDefault();
      if (pal && pal.open) closePalette(); else openPalette();
      return;
    }
    if (e.metaKey || e.ctrlKey || e.altKey) return;
    if (isTyping(e.target)) return;          // G15
    if (pal && pal.open) return;

    if (e.key === '?') { e.preventDefault(); openShortcuts(); }
    else if (e.key === 't' || e.key === 'T') { e.preventDefault(); toggleTheme(); }
  });

  /* =========================================================
     6. INDICATEUR D'ÉTAT SYSTÈME DE LA NAV
     ========================================================= */
  function refreshStatus() {
    var el = DOC.querySelector('[data-system-status]');
    if (!el) return;
    fetch('/api/system/status', { headers: { Accept: 'application/json' } })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (d) {
        if (!d) return;
        el.dataset.state = d.state;
        var label = el.querySelector('.s-sys-label');
        if (label) label.textContent = d.label;   // le mot, pas seulement la pastille
        el.setAttribute('title', d.label);
      })
      .catch(function () { /* hors ligne : on garde le dernier état connu */ });
  }

  /* =========================================================
     7. AMORÇAGE
     ========================================================= */
  function init() {
    // Déverrouille le déclencheur ⌘K de la nav, qui reste masqué
    // tant qu'aucune palette n'est enregistrée.
    ROOT.dataset.cmdk = 'on';

    var trigger = DOC.querySelector('[data-command-palette]');
    if (trigger) trigger.addEventListener('click', openPalette);

    var themeBtn = DOC.querySelector('.s-theme');
    if (themeBtn) themeBtn.addEventListener('click', toggleTheme);
    syncThemeButton();

    refreshStatus();
    setInterval(refreshStatus, 15000);
  }

  if (DOC.readyState === 'loading') DOC.addEventListener('DOMContentLoaded', init);
  else init();

  /* API publique pour les écrans. */
  window.samourais = window.samourais || {};
  window.samourais.registerCommands = function (cmds) {
    if (Array.isArray(cmds)) screenCommands = screenCommands.concat(cmds);
  };
  window.samourais.openPalette = openPalette;
  window.samourais.openShortcuts = openShortcuts;
  window.samourais.toggleTheme = toggleTheme;
  window.samourais.currentTheme = currentTheme;
  window.samourais.confirm = confirmDialog;
  window.samourais.notify = notify;

  /* ---------------------------------------------------------
     HTMX : hx-confirm passe par window.confirm, la dernière
     boîte système de l'application (écran Profils). On détourne
     l'évènement pour rendre notre propre dialogue à la place.
     --------------------------------------------------------- */
  DOC.addEventListener('htmx:confirm', function (e) {
    if (!e.detail.question) return;    // pas de hx-confirm sur cet élément
    e.preventDefault();
    var el = e.detail.elt;
    confirmDialog({
      title: el.getAttribute('data-confirm-title') || 'Confirmer',
      message: e.detail.question,
      confirm: el.getAttribute('data-confirm-label') || 'Confirmer',
      danger: el.hasAttribute('data-confirm-danger')
    }).then(function (ok) { if (ok) e.detail.issueRequest(true); });
  });
})();
