/* ============================================================
   SAMOURAIS SCRAPPER — ÉCRAN CALENDRIER
   ------------------------------------------------------------
   Références : Buffer (cycle de vie, échecs, file) + Later (grille).

   Trois partis pris qui expliquent la structure du fichier :

   1. UNE SEULE CARTE. `postCard()` est le seul endroit où l'état
      d'un post devient visible. La grille, la file, le tiroir et
      l'inspecteur l'appellent tous : la signalétique ne PEUT pas
      diverger d'une vue à l'autre.

   2. UNE SEULE REQUÊTE, SANS FENÊTRE. `GET /api/calendar/posts`
      sans start/end renvoie TOUT, brouillons non datés compris.
      Le filtre serveur `scheduled_at >= start` est un `NULL` en
      SQL pour ces posts-là : passer une fenêtre les efface de
      l'écran (AUDIT.md §3 « Affichage des brouillons »). On filtre
      donc côté client, et on rend les non-datés dans un tiroir.

   3. AUCUNE BOÎTE SYSTÈME. Ni alert(), ni confirm(), ni prompt() :
      un navigateur a le droit de les refuser, et une modale
      bloquante ne peut pas nommer ce qu'elle détruit.
   ============================================================ */

(function () {
    'use strict';

    // ─────────────────────────────────────────────────────────
    // Table des états. Chaque état = une couleur, UNE FORME et UN
    // LIBELLÉ. La couleur seule ne porte jamais l'information.
    // `rank` = ordre « problèmes d'abord » (C14).
    // ─────────────────────────────────────────────────────────
    const STATUS = {
        failed:    { label: 'Échoué',     glyph: '!', rank: 0, movable: true  },
        draft:     { label: 'Brouillon',  glyph: '✎', rank: 1, movable: true  },
        ready:     { label: 'En attente', glyph: '⋯', rank: 2, movable: true  },
        scheduled: { label: 'Programmé',  glyph: '◷', rank: 3, movable: true  },
        published: { label: 'Publié',     glyph: '✓', rank: 4, movable: false },
    };

    const UNKNOWN_STATUS = { label: 'État inconnu', glyph: '?', rank: 2, movable: true };

    function statusOf(post) {
        return STATUS[post.status] || UNKNOWN_STATUS;
    }

    // ─────────────────────────────────────────────────────────
    // Plateformes : monogramme (pas de logo en aplat de marque),
    // limite de légende réelle, et lien de publication manuelle.
    // Les limites servent au diagnostic d'échec (C8).
    // ─────────────────────────────────────────────────────────
    const PLATFORMS = {
        instagram: { mono: 'IG', label: 'Instagram',   caption: 2200,  url: 'https://www.instagram.com/' },
        tiktok:    { mono: 'TT', label: 'TikTok',      caption: 2200,  url: 'https://www.tiktok.com/upload' },
        twitter:   { mono: 'X',  label: 'X (Twitter)', caption: 280,   url: 'https://x.com/compose/post' },
        reddit:    { mono: 'RD', label: 'Reddit',      caption: 40000, url: 'https://www.reddit.com/submit' },
    };

    function platformOf(key) {
        return PLATFORMS[key] || { mono: (key || '?').slice(0, 2).toUpperCase(), label: key, caption: 2200, url: '#' };
    }

    // Créneaux de publication par défaut, en heure locale. Ils
    // matérialisent la grille hebdomadaire (amorce de C11) ET
    // servent de destination à « Remettre en file ».
    const SLOTS = [[9, 0], [12, 30], [18, 0]];

    // ─────────────────────────────────────────────────────────
    // Traduction des codes d'API en cause + geste (C8).
    // Le serveur ne renvoie pas encore de code d'erreur : cette
    // table est le contrat qu'il devra remplir (champ
    // `extendedProps.error_code`). Tant qu'il ne le fait pas, le
    // diagnostic est calculé à partir du post lui-même — ce qui
    // couvre les causes réellement fréquentes, avec leurs seuils.
    // ─────────────────────────────────────────────────────────
    const ERROR_MAP = {
        media_too_large: {
            cause: 'Instagram refuse les images de plus de 8 Mo.',
            fix: 'Ré-exporte le média sous 8 Mo depuis l’Éditeur, puis « Réessayer maintenant ».',
        },
        aspect_ratio: {
            cause: 'Le ratio du média sort de la plage acceptée (4:5 à 1.91:1).',
            fix: 'Recadre en 4:5 ou 1:1 dans l’Éditeur, puis relance la publication.',
        },
        rate_limit: {
            cause: 'Quota de publication atteint (25 posts par 24 h sur Instagram).',
            fix: 'Choisis « Remettre en file » : le post repartira au prochain créneau libre.',
        },
        token_expired: {
            cause: 'L’autorisation du compte a expiré.',
            fix: 'Reconnecte le compte dans Réglages, puis « Réessayer maintenant ».',
        },
    };

    // ─────────────────────────────────────────────────────────
    // État de l'écran
    // ─────────────────────────────────────────────────────────
    let calendar = null;
    let posts = [];
    let selectedId = null;
    let loading = false;

    const filters = { status: 'all', platform: '', q: '' };
    let view = 'dayGridMonth';

    // Bibliothèque latérale (C1/C2/C10). `armed` porte le chemin de
    // secours sans drag : une vignette cliquée attend une case.
    const library = { items: [], unusedOnly: true, armed: null, open: true, loaded: false };

    // ─────────────────────────────────────────────────────────
    // Références DOM
    // ─────────────────────────────────────────────────────────
    const $ = (id) => document.getElementById(id);
    const elGridWrap = $('cal-grid');
    const elCalendar = $('calendar');
    const elQueue = $('cal-queue');
    const elTray = $('cal-tray');
    const elTrayList = $('cal-tray-list');
    const elTrayCount = $('cal-tray-count');
    const elSide = $('cal-side');
    const elSideBody = $('side-body');
    const elSideFoot = $('side-foot');
    const elSideTitle = $('side-title');
    const elAlert = $('cal-alert');
    const elFilterStatus = $('filter-status');
    const elDialog = $('cal-dialog');
    const elToasts = $('cal-toasts');
    const elPeriod = $('cal-period');
    const elLib = $('cal-lib');
    const elLibGrid = $('lib-grid');
    const elLibCount = $('lib-count');

    // ─────────────────────────────────────────────────────────
    // Utilitaires
    // ─────────────────────────────────────────────────────────
    function esc(str) {
        return String(str == null ? '' : str)
            .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
    }

    const fmtTime = new Intl.DateTimeFormat('fr-FR', { hour: '2-digit', minute: '2-digit' });
    const fmtDay = new Intl.DateTimeFormat('fr-FR', { weekday: 'long', day: 'numeric', month: 'long' });
    const fmtFull = new Intl.DateTimeFormat('fr-FR', {
        weekday: 'long', day: 'numeric', month: 'long', year: 'numeric',
        hour: '2-digit', minute: '2-digit',
    });

    function startOf(post) {
        return post.start ? new Date(post.start) : null;
    }

    function toLocalInput(date) {
        const d = new Date(date);
        return new Date(d.getTime() - d.getTimezoneOffset() * 60000).toISOString().slice(0, 16);
    }

    /** Durée humaine : « 3 h 12 », « 2 jours », « 14 min ». */
    function humanDelta(ms) {
        const min = Math.round(Math.abs(ms) / 60000);
        if (min < 60) return min + ' min';
        const h = Math.floor(min / 60);
        if (h < 24) return h + ' h ' + String(min % 60).padStart(2, '0');
        const d = Math.floor(h / 24);
        return d + (d > 1 ? ' jours' : ' jour');
    }

    // ─────────────────────────────────────────────────────────
    // Fuseau horaire affiché en clair (C12)
    // ─────────────────────────────────────────────────────────
    function renderTimezone() {
        const zone = Intl.DateTimeFormat().resolvedOptions().timeZone || 'local';
        const offMin = -new Date().getTimezoneOffset();
        const sign = offMin >= 0 ? '+' : '−';
        const abs = Math.abs(offMin);
        const label = zone + ' · UTC' + sign
            + String(Math.floor(abs / 60)).padStart(2, '0') + ':' + String(abs % 60).padStart(2, '0');
        const node = $('cal-tz-text');
        if (node) node.textContent = label;
    }

    // ─────────────────────────────────────────────────────────
    // Diagnostic d'un échec : cause précise + geste correctif,
    // avec des seuils chiffrés. Jamais un code d'API brut. (C8)
    // ─────────────────────────────────────────────────────────
    function diagnose(post) {
        const p = post.extendedProps || {};

        // 1) Le serveur a transmis un code connu : on le traduit.
        if (p.error_code && ERROR_MAP[p.error_code]) return ERROR_MAP[p.error_code];

        const platforms = p.platforms || [];
        const caption = p.caption || '';

        // 2) Aucune cible : la cause la plus fréquente, et la plus muette.
        if (!platforms.length) {
            return {
                cause: 'Aucune plateforme cochée : le post n’a pas de destination.',
                fix: 'Coche Instagram, TikTok, X ou Reddit dans les détails.',
            };
        }

        // 3) Légende trop longue pour l'une des cibles : on NOMME la
        //    plateforme fautive et le nombre exact de caractères en trop.
        for (const key of platforms) {
            const meta = platformOf(key);
            if (caption.length > meta.caption) {
                const n = (v) => v.toLocaleString('fr-FR');
                return {
                    cause: meta.label + ' : légende de ' + n(caption.length) + ' caractères, soit '
                        + n(caption.length - meta.caption) + ' de trop (limite ' + n(meta.caption) + ').',
                    fix: 'Retire ' + n(caption.length - meta.caption) + ' caractères dans les détails.',
                };
            }
        }

        // 4) Créneau dépassé.
        const at = startOf(post);
        if (at && at.getTime() < Date.now() - 5 * 60000) {
            return {
                cause: 'Créneau du ' + fmtTime.format(at) + ' dépassé de ' + humanDelta(Date.now() - at.getTime()) + '.',
                fix: 'Remets en file : prochain créneau libre ' + nextSlotLabel() + '.',
            };
        }

        // 5) Dernier recours : on dit ce qu'on ne sait pas, et quoi faire.
        return {
            cause: 'Échec sans cause transmise par la plateforme.',
            fix: 'Réessaie ; si l’échec se répète, vérifie le média et la légende.',
        };
    }

    // ─────────────────────────────────────────────────────────
    // Créneaux : fonction pure, recalculée à chaque appel.
    // ─────────────────────────────────────────────────────────
    function nextFreeSlot() {
        const taken = posts
            .map(startOf)
            .filter(Boolean)
            .map((d) => d.getTime());

        for (let day = 0; day < 30; day++) {
            for (const [h, m] of SLOTS) {
                const d = new Date();
                d.setDate(d.getDate() + day);
                d.setHours(h, m, 0, 0);
                if (d.getTime() <= Date.now()) continue;
                const busy = taken.some((t) => Math.abs(t - d.getTime()) < 30 * 60000);
                if (!busy) return d;
            }
        }
        const fallback = new Date(Date.now() + 86400000);
        fallback.setHours(SLOTS[0][0], SLOTS[0][1], 0, 0);
        return fallback;
    }

    function nextSlotLabel() {
        const d = nextFreeSlot();
        return fmtDay.format(d) + ' à ' + fmtTime.format(d);
    }

    // ─────────────────────────────────────────────────────────
    // LA CARTE — seul endroit où un état devient visible.
    // C4 : miniature + heure + plateforme, simultanément.
    // C5 : liseré + pastille + libellé, sans survol ni ouverture.
    // C7/C8 : deux reprises nommées et le diagnostic, DANS la carte.
    // ─────────────────────────────────────────────────────────
    function postCard(post, opts) {
        opts = opts || {};
        const p = post.extendedProps || {};
        const st = statusOf(p);
        const at = startOf(post);
        const isFailed = p.status === 'failed';

        const card = document.createElement(opts.tag || 'div');
        card.className = 'pcard pcard--' + (STATUS[p.status] ? p.status : 'draft')
            + (opts.row ? ' pcard--row' : '') + (opts.tile ? ' pcard--tile' : '');
        card.dataset.postId = post.id;

        // ---- Miniature (emplacement dimensionné même sans média) ----
        const thumb = document.createElement('span');
        thumb.className = 'pcard__thumb' + (p.media_type === 'video' ? ' pcard__thumb--video' : '');
        const src = '/api/calendar/posts/' + encodeURIComponent(post.id) + '/media';
        if (p.media_type === 'video') {
            const v = document.createElement('video');
            v.src = src + '#t=0.1';
            v.muted = true;
            v.preload = 'metadata';
            v.addEventListener('error', () => fallbackThumb(thumb, 'Vidéo'));
            thumb.appendChild(v);
        } else {
            const img = document.createElement('img');
            img.src = src;
            img.alt = '';
            img.loading = 'lazy';
            img.decoding = 'async';
            img.addEventListener('error', () => fallbackThumb(thumb, 'Sans média'));
            thumb.appendChild(img);
        }
        card.appendChild(thumb);

        // ---- Corps ----
        const body = document.createElement('span');
        body.className = 'pcard__body';

        const meta = document.createElement('span');
        meta.className = 'pcard__meta';

        const time = document.createElement('time');
        time.className = 'pcard__time';
        if (at) {
            time.dateTime = at.toISOString();
            time.textContent = opts.withDate ? (fmtDay.format(at) + ' · ' + fmtTime.format(at)) : fmtTime.format(at);
        } else {
            time.textContent = 'Sans date';
        }
        meta.appendChild(time);

        if (!st.movable) {
            const lock = document.createElement('span');
            lock.className = 'pcard__lock';
            lock.textContent = '🔒';
            lock.title = 'Post publié : sa date est verrouillée, il ne peut plus être déplacé.';
            meta.appendChild(lock);
        }

        const plats = document.createElement('span');
        plats.className = 'pcard__plats';
        const list = p.platforms || [];
        if (list.length) {
            list.forEach((key) => {
                const meta2 = platformOf(key);
                const chip = document.createElement('span');
                chip.className = 'plat';
                chip.textContent = meta2.mono;
                chip.title = meta2.label;
                plats.appendChild(chip);
            });
        } else {
            const chip = document.createElement('span');
            chip.className = 'plat plat--none';
            chip.textContent = '—';
            chip.title = 'Aucune plateforme cible';
            plats.appendChild(chip);
        }
        meta.appendChild(plats);
        body.appendChild(meta);

        const title = document.createElement('span');
        title.className = 'pcard__title';
        title.textContent = post.title || 'Sans titre';
        title.title = post.title || 'Sans titre';
        body.appendChild(title);

        const state = document.createElement('span');
        state.className = 'pcard__state';
        state.innerHTML = '<i class="dot" aria-hidden="true">' + esc(st.glyph) + '</i>';
        state.appendChild(document.createTextNode(st.label));
        body.appendChild(state);

        card.appendChild(body);

        // ---- Reprise, dans la carte elle-même (C7 + C8) ----
        if (isFailed && opts.withFix !== false) {
            const d = diagnose(post);
            const fix = document.createElement('div');
            fix.className = 'pfix';
            fix.innerHTML =
                '<p class="pfix__cause">' + esc(d.cause) + '</p>' +
                '<p class="pfix__do">' + esc(d.fix) + '</p>';

            const actions = document.createElement('div');
            actions.className = 'pfix__actions';

            const retry = document.createElement('button');
            retry.type = 'button';
            retry.className = 'btn btn--mini';
            retry.textContent = 'Réessayer maintenant';
            retry.addEventListener('click', (e) => { e.stopPropagation(); retryNow(post); });

            const requeue = document.createElement('button');
            requeue.type = 'button';
            requeue.className = 'btn btn--mini';
            requeue.textContent = 'Remettre en file';
            requeue.addEventListener('click', (e) => { e.stopPropagation(); requeuePost(post); });

            actions.appendChild(retry);
            actions.appendChild(requeue);
            fix.appendChild(actions);
            card.appendChild(fix);
        }

        return card;
    }

    function fallbackThumb(thumb, label) {
        thumb.classList.add('pcard__thumb--empty');
        thumb.classList.remove('pcard__thumb--video');
        thumb.textContent = label === 'Vidéo' ? '▶' : '—';
        thumb.title = label;
    }

    // ─────────────────────────────────────────────────────────
    // Reprises nommées (C7)
    // ─────────────────────────────────────────────────────────
    async function retryNow(post) {
        const when = new Date(Date.now() + 60000);
        try {
            await patchPost(post.id, { status: 'scheduled', scheduled_at: Math.floor(when.getTime() / 1000) });
            await reload();
            toast('Nouvelle tentative programmée à ' + fmtTime.format(when) + '.', 'success');
        } catch (e) {
            toast('La reprise n’a pas pu être enregistrée : le serveur a refusé la mise à jour.', 'error');
        }
    }

    async function requeuePost(post) {
        const when = nextFreeSlot();
        try {
            await patchPost(post.id, { status: 'scheduled', scheduled_at: Math.floor(when.getTime() / 1000) });
            await reload();
            toast('Remis en file : ' + fmtDay.format(when) + ' à ' + fmtTime.format(when) + '.', 'success');
        } catch (e) {
            toast('La remise en file n’a pas pu être enregistrée : le serveur a refusé la mise à jour.', 'error');
        }
    }

    // ─────────────────────────────────────────────────────────
    // Accès API
    // ─────────────────────────────────────────────────────────
    async function fetchPosts() {
        // SANS start/end : cf. parti pris n°2 en tête de fichier.
        const resp = await fetch('/api/calendar/posts');
        if (!resp.ok) throw new Error('HTTP ' + resp.status);
        return resp.json();
    }

    /**
     * Le serveur, quand il refuse, DIT pourquoi (champ `error`). Jeter
     * ce texte pour lui substituer « HTTP 409 » revenait à transformer
     * une cause nommée en code brut : c'est exactement ce que G24 et
     * G27 interdisent. On le remonte donc jusqu'au toast.
     */
    async function refus(resp) {
        let msg = '';
        try {
            const body = await resp.json();
            msg = String(body.error || body.message || '').trim();
        } catch (e) { /* réponse non-JSON : on garde le message vide */ }
        const err = new Error(msg || ('HTTP ' + resp.status));
        err.serverMessage = msg;
        err.status = resp.status;
        return err;
    }

    /** Phrase prête à afficher : cause du serveur, ou repli honnête. */
    function causeDe(err, repli) {
        const msg = err && err.serverMessage;
        if (!msg) return repli;
        return /[.!?]$/.test(msg) ? msg : msg + '.';
    }

    async function patchPost(id, data) {
        const resp = await fetch('/api/calendar/posts/' + encodeURIComponent(id), {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(data),
        });
        if (!resp.ok) throw await refus(resp);
        return resp.json();
    }

    async function createPost(data) {
        const resp = await fetch('/api/calendar/posts', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(data),
        });
        if (!resp.ok) throw await refus(resp);
        return resp.json();
    }

    /** Création AVEC média : multipart, pour que le fichier suive le post. */
    async function createPostWithMedia(data, media) {
        const form = new FormData();
        Object.keys(data).forEach((k) => form.append(k, data[k]));

        const fichier = await fetch(media.file_url);
        if (!fichier.ok) {
            const err = new Error('media_fetch');
            err.serverMessage = 'le fichier du média n’a pas pu être relu depuis la bibliothèque';
            throw err;
        }
        const blob = await fichier.blob();
        const nom = (media.file_url || 'media').split('/').pop() || 'media';
        form.append('media', blob, nom);

        const resp = await fetch('/api/calendar/posts', { method: 'POST', body: form });
        if (!resp.ok) throw await refus(resp);
        return resp.json();
    }

    async function destroyPost(id) {
        const resp = await fetch('/api/calendar/posts/' + encodeURIComponent(id), { method: 'DELETE' });
        if (!resp.ok) throw await refus(resp);
        return resp.json();
    }

    async function publishPost(id) {
        const resp = await fetch('/api/calendar/posts/' + encodeURIComponent(id) + '/publish', { method: 'POST' });
        if (!resp.ok) throw new Error('HTTP ' + resp.status);
        return resp.json();
    }

    // ─────────────────────────────────────────────────────────
    // Filtres — état persistant (localStorage + URL), C13
    // ─────────────────────────────────────────────────────────
    const STORE_KEY = 'samourais_calendar_view';

    function restoreState() {
        let saved = {};
        try { saved = JSON.parse(localStorage.getItem(STORE_KEY) || '{}'); } catch (e) { saved = {}; }
        const url = new URLSearchParams(location.search);

        filters.status = url.get('status') || saved.status || 'all';
        filters.platform = url.get('platform') || saved.platform || '';
        filters.q = url.get('q') || saved.q || '';
        view = url.get('view') || saved.view || 'dayGridMonth';

        // Lien profond depuis la heatmap d'Analytics :
        // /calendar?jour=0&heure=19 (jour : lundi = 0 … dimanche = 6).
        // Le aria-label de la heatmap promet « Ouvrir le calendrier sur
        // ce créneau » ; jusqu'ici les deux paramètres n'étaient jamais
        // lus, et persistState() les effaçait de l'URL juste après.
        // On les capture AVANT cette réécriture.
        const j = parseInt(url.get('jour'), 10);
        const h = parseInt(url.get('heure'), 10);
        pendingSlot = (j >= 0 && j <= 6 && h >= 0 && h <= 23) ? { jour: j, heure: h } : null;

        $('filter-platform').value = filters.platform;
        $('filter-q').value = filters.q;
    }

    // Créneau demandé par l'URL, consommé une fois le calendrier prêt.
    let pendingSlot = null;

    /** Prochaine occurrence de ce jour de semaine à cette heure. */
    function nextSlotDate(jour, heure) {
        const d = new Date();
        d.setHours(heure, 0, 0, 0);
        // getDay() : dimanche = 0 ; notre indexation : lundi = 0.
        const courant = (d.getDay() + 6) % 7;
        let delta = (jour - courant + 7) % 7;
        if (delta === 0 && d.getTime() <= Date.now()) delta = 7;
        d.setDate(d.getDate() + delta);
        return d;
    }

    function consumePendingSlot() {
        if (!pendingSlot) return;
        const cible = nextSlotDate(pendingSlot.jour, pendingSlot.heure);
        pendingSlot = null;
        if (calendar) calendar.gotoDate(cible);
        openComposer(cible, null);
    }

    function persistState() {
        try {
            localStorage.setItem(STORE_KEY, JSON.stringify({
                status: filters.status, platform: filters.platform, q: filters.q, view: view,
            }));
        } catch (e) { /* stockage indisponible : l'URL suffit */ }

        const url = new URLSearchParams();
        if (filters.status !== 'all') url.set('status', filters.status);
        if (filters.platform) url.set('platform', filters.platform);
        if (filters.q) url.set('q', filters.q);
        if (view !== 'dayGridMonth') url.set('view', view);
        const qs = url.toString();
        history.replaceState(null, '', qs ? '?' + qs : location.pathname);
    }

    function matches(post) {
        const p = post.extendedProps || {};
        if (filters.status !== 'all' && p.status !== filters.status) return false;
        if (filters.platform) {
            const list = p.platforms || [];
            if (filters.platform === 'none') { if (list.length) return false; }
            else if (!list.includes(filters.platform)) return false;
        }
        if (filters.q) {
            const hay = ((post.title || '') + ' ' + (p.caption || '')).toLowerCase();
            if (!hay.includes(filters.q.toLowerCase())) return false;
        }
        return true;
    }

    function visiblePosts() {
        return posts.filter(matches);
    }

    // ─────────────────────────────────────────────────────────
    // Rendus
    // ─────────────────────────────────────────────────────────
    function renderFilterChips() {
        const counts = { all: posts.length };
        Object.keys(STATUS).forEach((k) => { counts[k] = 0; });
        posts.forEach((post) => {
            const s = (post.extendedProps || {}).status;
            if (counts[s] === undefined) counts[s] = 0;
            counts[s]++;
        });

        const order = ['all', 'failed', 'draft', 'scheduled', 'ready', 'published'];
        const labels = { all: 'Tous' };
        Object.keys(STATUS).forEach((k) => { labels[k] = STATUS[k].label + 's'; });
        labels.ready = 'En attente';

        elFilterStatus.innerHTML = '';
        order.forEach((key) => {
            if (key !== 'all' && !counts[key]) return;
            const b = document.createElement('button');
            b.type = 'button';
            b.className = 'chip chip--' + key;
            b.setAttribute('aria-pressed', filters.status === key ? 'true' : 'false');
            b.innerHTML = '<span>' + esc(labels[key] || key) + '</span>'
                + '<span class="chip__n num">' + (counts[key] || 0) + '</span>';
            b.addEventListener('click', () => {
                filters.status = filters.status === key ? 'all' : key;
                persistState();
                renderAll();
            });
            elFilterStatus.appendChild(b);
        });
    }

    function renderAlert() {
        const failed = posts.filter((p) => (p.extendedProps || {}).status === 'failed');
        if (!failed.length) { elAlert.hidden = true; return; }
        elAlert.hidden = false;
        $('cal-alert-title').textContent =
            failed.length + (failed.length > 1 ? ' posts n’ont pas été publiés' : ' post n’a pas été publié');
        $('cal-alert-text').textContent =
            'Ils restent dans la file — rien n’a été archivé en silence. Chaque carte porte sa cause et ses deux reprises : '
            + failed.map((p) => p.title || 'Sans titre').slice(0, 3).join(', ') + '.';
    }

    function renderTray() {
        const undated = visiblePosts().filter((p) => !p.start);
        elTrayCount.textContent = undated.length;
        // En vue Liste, la file possède déjà sa section « Sans date » :
        // garder le tiroir en plus ne ferait que dupliquer les mêmes cartes.
        if (!undated.length || view === 'queue') { elTray.hidden = true; return; }
        elTray.hidden = false;
        elTrayList.innerHTML = '';
        undated.forEach((post) => {
            const card = postCard(post, { tile: true, tag: 'button' });
            card.type = 'button';
            card.addEventListener('click', () => openInspector(post));
            elTrayList.appendChild(card);
        });
    }

    /** Vue Liste : file d'attente, problèmes d'abord (C14). */
    function renderQueue() {
        const list = visiblePosts().slice().sort((a, b) => {
            const ra = statusOf(a.extendedProps || {}).rank;
            const rb = statusOf(b.extendedProps || {}).rank;
            if (ra !== rb) return ra - rb;
            const ta = startOf(a) ? startOf(a).getTime() : Infinity;
            const tb = startOf(b) ? startOf(b).getTime() : Infinity;
            return ta - tb;
        });

        elQueue.innerHTML = '';

        if (!list.length) {
            elQueue.innerHTML =
                '<div class="empty">' +
                '<h2 class="empty__title">Aucun post dans cette sélection</h2>' +
                '<p class="empty__text">Retire un filtre pour élargir la file, ou crée un post en cliquant une case du calendrier.</p>' +
                '</div>';
            return;
        }

        const groups = [
            { key: 'failed', title: 'À corriger', danger: true, of: (p) => p.status === 'failed' },
            { key: 'undated', title: 'Sans date', of: (p, post) => p.status !== 'failed' && !post.start },
            { key: 'next', title: 'À venir', of: (p, post) => p.status !== 'failed' && post.start && new Date(post.start) >= new Date() },
            { key: 'past', title: 'Passés', of: (p, post) => p.status !== 'failed' && post.start && new Date(post.start) < new Date() },
        ];

        groups.forEach((g) => {
            const items = list.filter((post) => g.of(post.extendedProps || {}, post));
            if (!items.length) return;

            const section = document.createElement('section');
            section.className = 'queue__section';
            const head = document.createElement('h2');
            head.className = 'queue__head' + (g.danger ? ' queue__head--danger' : '');
            head.innerHTML = esc(g.title) + ' <span class="queue__n">' + items.length + '</span>';
            section.appendChild(head);

            const ul = document.createElement('ul');
            ul.className = 'queue__list';
            items.forEach((post) => {
                const li = document.createElement('li');
                const card = postCard(post, { row: true, withDate: true, tag: 'button' });
                card.type = 'button';
                card.addEventListener('click', () => openInspector(post));
                li.appendChild(card);
                ul.appendChild(li);
            });
            section.appendChild(ul);
            elQueue.appendChild(section);
        });
    }

    // ─────────────────────────────────────────────────────────
    // FullCalendar
    // ─────────────────────────────────────────────────────────
    function calendarEvents() {
        const evts = visiblePosts()
            .filter((post) => post.start)
            .map((post) => {
                const p = post.extendedProps || {};
                const st = statusOf(p);
                return {
                    id: String(post.id),
                    title: post.title || 'Sans titre',
                    start: post.start,
                    editable: st.movable,
                    rank: st.rank,           // → extendedProps.rank, lu par eventOrder
                    extendedProps: p,
                };
            });

        // Créneaux hebdomadaires matérialisés en vue Semaine (C11, amorce).
        if (view === 'timeGridWeek') {
            const base = new Date();
            base.setHours(0, 0, 0, 0);
            for (let day = -7; day <= 14; day++) {
                SLOTS.forEach(([h, m]) => {
                    const d = new Date(base);
                    d.setDate(d.getDate() + day);
                    d.setHours(h, m, 0, 0);
                    const busy = posts.some((post) => {
                        const s = startOf(post);
                        return s && Math.abs(s.getTime() - d.getTime()) < 30 * 60000;
                    });
                    if (busy) return;
                    evts.push({
                        start: new Date(d),
                        end: new Date(d.getTime() + 30 * 60000),
                        display: 'background',
                        classNames: ['slot-free'],
                        title: 'Créneau libre',
                    });
                });
            }
        }
        return evts;
    }

    function initCalendar() {
        calendar = new FullCalendar.Calendar(elCalendar, {
            initialView: view === 'queue' ? 'dayGridMonth' : view,
            locale: 'fr',
            firstDay: 1,
            headerToolbar: false,          // notre barre d'outils s'en charge
            height: 'auto',
            expandRows: true,
            editable: true,
            eventStartEditable: true,
            eventDurationEditable: false,
            selectable: false,
            nowIndicator: true,
            allDaySlot: false,
            slotMinTime: '06:00:00',
            slotMaxTime: '24:00:00',
            slotDuration: '01:00:00',
            dayMaxEvents: 4,
            moreLinkText: (n) => '+ ' + n + ' autres',
            // Problèmes d'abord, y compris dans une case de mois.
            eventOrder: 'rank,start,title',

            events: (info, ok) => ok(calendarEvents()),

            eventContent: (arg) => {
                if (arg.event.display === 'background') return true;
                const post = postById(arg.event.id);
                if (!post) return true;
                return { domNodes: [postCard(post)] };
            },

            eventClick: (info) => {
                info.jsEvent.preventDefault();
                const post = postById(info.event.id);
                if (post) openInspector(post);
            },

            // C6 : le nœud a déjà bougé (FullCalendar l'a déplacé) ;
            // en cas de refus serveur on le remet et on dit pourquoi.
            eventDrop: (info) => {
                const ts = Math.floor(info.event.start.getTime() / 1000);
                patchPost(info.event.id, { scheduled_at: ts })
                    .then(() => {
                        const post = postById(info.event.id);
                        if (post) post.start = info.event.start.toISOString();
                        toast('Replanifié : ' + fmtDay.format(info.event.start)
                            + ' à ' + fmtTime.format(info.event.start) + '.', 'success');
                        renderQueue();
                    })
                    .catch((err) => {
                        info.revert();
                        toast('Déplacement annulé — '
                            + causeDe(err, 'le serveur a refusé la nouvelle date.')
                            + ' Le post est revenu à son créneau.', 'error');
                    });
            },

            // C2 : dépôt d'un média venu de la bibliothèque. `create:false`
            // côté Draggable : FullCalendar ne fabrique aucun événement
            // fantôme, il nous donne juste la date exacte de la case.
            droppable: true,
            drop: (info) => {
                const media = mediaById(info.draggedEl && info.draggedEl.dataset.mediaId);
                disarmLibrary();
                openComposer(info.date, media || undefined);
            },

            // C3 : chemin de secours sans drag. Si une vignette est
            // « armée » d'un clic, la case la récupère (trackpad, tactile).
            dateClick: (info) => {
                const media = library.armed ? mediaById(library.armed) : null;
                disarmLibrary();
                openComposer(info.date, media || undefined);
            },

            datesSet: (info) => {
                elPeriod.textContent = info.view.title;
            },
        });

        calendar.render();
    }

    function postById(id) {
        return posts.find((p) => String(p.id) === String(id));
    }

    // ─────────────────────────────────────────────────────────
    // Inspecteur
    // ─────────────────────────────────────────────────────────
    function openInspector(post) {
        selectedId = post.id;
        const p = post.extendedProps || {};
        const st = statusOf(p);
        const at = startOf(post);

        elSide.hidden = false;
        elSideTitle.textContent = post.title || 'Sans titre';

        elSideBody.innerHTML = '';

        // Aperçu
        const preview = document.createElement('div');
        preview.className = 'side__preview';
        const src = '/api/calendar/posts/' + encodeURIComponent(post.id) + '/media';
        if (p.media_type === 'video') {
            preview.innerHTML = '<video src="' + esc(src) + '#t=0.1" muted controls preload="metadata"></video>';
        } else {
            const img = document.createElement('img');
            img.src = src;
            img.alt = 'Aperçu du média';
            img.addEventListener('error', () => {
                preview.classList.add('side__preview--empty');
                preview.textContent = 'Aucun média attaché';
            });
            preview.appendChild(img);
        }
        elSideBody.appendChild(preview);

        // État + diagnostic
        const stateBlock = document.createElement('div');
        stateBlock.className = 'pcard pcard--' + (STATUS[p.status] ? p.status : 'draft');
        stateBlock.style.paddingLeft = 'var(--sp-5)';
        stateBlock.innerHTML = '<span class="pcard__body"><span class="pcard__state">'
            + '<i class="dot" aria-hidden="true">' + esc(st.glyph) + '</i>' + esc(st.label) + '</span></span>';
        stateBlock.style.gridTemplateColumns = '1fr';
        elSideBody.appendChild(stateBlock);

        if (p.status === 'failed') {
            const d = diagnose(post);
            const fix = document.createElement('div');
            fix.className = 'pfix';
            fix.style.borderTop = '0';
            fix.innerHTML = '<p class="pfix__cause">' + esc(d.cause) + '</p>'
                + '<p class="pfix__do">' + esc(d.fix) + '</p>';
            const actions = document.createElement('div');
            actions.className = 'pfix__actions';
            const b1 = document.createElement('button');
            b1.type = 'button'; b1.className = 'btn btn--mini'; b1.textContent = 'Réessayer maintenant';
            b1.addEventListener('click', () => retryNow(post));
            const b2 = document.createElement('button');
            b2.type = 'button'; b2.className = 'btn btn--mini'; b2.textContent = 'Remettre en file';
            b2.addEventListener('click', () => requeuePost(post));
            actions.appendChild(b1); actions.appendChild(b2);
            fix.appendChild(actions);
            elSideBody.appendChild(fix);
        }

        // Champs éditables
        elSideBody.appendChild(field('Titre', '<input type="text" class="input" id="edit-title" value="'
            + esc(post.title || '') + '">'));
        elSideBody.appendChild(field('Légende', '<textarea class="input" id="edit-caption">'
            + esc(p.caption || '') + '</textarea>'));
        elSideBody.appendChild(field('Date et heure', '<input type="datetime-local" class="input" id="edit-datetime" value="'
            + (at ? toLocalInput(at) : '') + '">'));

        // Plateformes
        const platWrap = document.createElement('div');
        platWrap.className = 'field';
        platWrap.innerHTML = '<span class="field__label">Plateformes</span>';
        const pick = document.createElement('div');
        pick.className = 'plats-pick';
        pick.id = 'edit-platforms';
        Object.keys(PLATFORMS).forEach((key) => {
            const meta = platformOf(key);
            const b = document.createElement('button');
            b.type = 'button';
            b.className = 'plats-pick__btn';
            b.dataset.platform = key;
            b.setAttribute('aria-pressed', (p.platforms || []).includes(key) ? 'true' : 'false');
            b.innerHTML = '<span class="plat">' + esc(meta.mono) + '</span>' + esc(meta.label);
            b.addEventListener('click', () => {
                b.setAttribute('aria-pressed', b.getAttribute('aria-pressed') === 'true' ? 'false' : 'true');
            });
            pick.appendChild(b);
        });
        platWrap.appendChild(pick);
        elSideBody.appendChild(platWrap);

        // Métadonnées
        const dl = document.createElement('dl');
        dl.className = 'side__rows';
        dl.innerHTML =
            row('Format', p.template_format || '—') +
            row('Type', p.media_type === 'video' ? 'Vidéo' : 'Image') +
            row('Créé le', p.created_at ? fmtFull.format(new Date(p.created_at * 1000)) : '—') +
            row('Programmé', at ? fmtFull.format(at) : '—');
        elSideBody.appendChild(dl);

        // AUDIT.md §4.18 : ne JAMAIS renvoyer scheduled_at si l'utilisateur
        // n'a pas touché le champ. Le serveur sérialise l'heure sans fuseau ;
        // réémettre la valeur relue ferait dériver le post à chaque save.
        const dt = $('edit-datetime');
        dt.dataset.dirty = 'false';
        dt.addEventListener('input', () => { dt.dataset.dirty = 'true'; });

        // Actions
        elSideFoot.innerHTML = '';
        elSideFoot.appendChild(button('Enregistrer', 'btn btn--primary', () => saveInspector(post)));
        if (p.status !== 'published') {
            elSideFoot.appendChild(button('Publier', 'btn', () => doPublish(post)));
        }
        elSideFoot.appendChild(button('Supprimer', 'btn btn--danger', () => askDelete(post)));
    }

    function field(label, inner) {
        const d = document.createElement('div');
        d.className = 'field';
        d.innerHTML = '<span class="field__label">' + esc(label) + '</span>' + inner;
        return d;
    }

    function row(k, v) {
        return '<div class="side__row"><dt>' + esc(k) + '</dt><dd>' + esc(v) + '</dd></div>';
    }

    function button(label, cls, onClick) {
        const b = document.createElement('button');
        b.type = 'button';
        b.className = cls;
        b.textContent = label;
        b.addEventListener('click', onClick);
        return b;
    }

    function closeInspector() {
        elSide.hidden = true;
        selectedId = null;
    }

    async function saveInspector(post) {
        const data = {
            title: $('edit-title').value,
            caption: $('edit-caption').value,
            platforms: Array.from(document.querySelectorAll('#edit-platforms [aria-pressed="true"]'))
                .map((b) => b.dataset.platform),
        };

        const dt = $('edit-datetime');
        if (dt.dataset.dirty === 'true' && dt.value) {
            data.scheduled_at = Math.floor(new Date(dt.value).getTime() / 1000);
            // Une date posée sur un brouillon le programme — mais on ne
            // touche jamais au statut d'un post publié ou échoué.
            const cur = (post.extendedProps || {}).status;
            if (cur === 'draft') data.status = 'scheduled';
        }

        try {
            await patchPost(post.id, data);
            await reload();
            toast('Post enregistré.', 'success');
        } catch (e) {
            toast('Enregistrement refusé par le serveur. Rien n’a été modifié.', 'error');
        }
    }

    // ─────────────────────────────────────────────────────────
    // Dialogues — remplacent confirm() et les modales maison
    // ─────────────────────────────────────────────────────────
    function openDialog(html) {
        elDialog.innerHTML = html;
        if (typeof elDialog.showModal === 'function') elDialog.showModal();
        else elDialog.setAttribute('open', '');
        const first = elDialog.querySelector('[data-autofocus]') || elDialog.querySelector('button, input, textarea');
        if (first) first.focus();
        return elDialog;
    }

    function closeDialog() {
        if (typeof elDialog.close === 'function' && elDialog.open) elDialog.close();
        else elDialog.removeAttribute('open');
        elDialog.innerHTML = '';
    }
    window.addEventListener('keydown', (e) => { if (e.key === 'Escape' && elDialog.open) closeDialog(); });

    /**
     * Confirmation nommant l'élément concerné (G16).
     * Remplace le confirm() natif : une boîte système ne peut ni
     * nommer ce qu'elle détruit, ni être refusée par le navigateur
     * sans casser le flux.
     */
    function askDelete(post) {
        openDialog(
            '<div class="dlg__head">' +
            '<h2 class="dlg__title">Supprimer ce post&nbsp;?</h2>' +
            '<p class="dlg__sub">« ' + esc(post.title || 'Sans titre') + ' »'
            + (post.start ? ', programmé le ' + esc(fmtFull.format(startOf(post))) : ', sans date')
            + '. Le média associé sera effacé du dossier calendrier. Cette action est définitive.</p>' +
            '</div>' +
            '<div class="dlg__foot">' +
            '<button type="button" class="btn" data-autofocus id="dlg-cancel">Annuler</button>' +
            '<button type="button" class="btn btn--danger" id="dlg-ok">Supprimer définitivement</button>' +
            '</div>'
        );
        $('dlg-cancel').addEventListener('click', closeDialog);
        $('dlg-ok').addEventListener('click', async () => {
            closeDialog();
            try {
                await destroyPost(post.id);
                closeInspector();
                await reload();
                toast('Post supprimé.', 'success');
            } catch (err) {
                toast('Suppression refusée — ' + causeDe(err, 'le serveur a refusé.')
                    + ' Le post est toujours là.', 'error');
            }
        });
    }

    /**
     * Composer pré-rempli par la case (C3) et, si un média vient de la
     * bibliothèque, par ce média (C2) : vignette posée, date/heure de
     * la case, et la légende du média en amorce de légende.
     */
    function openComposer(date, media) {
        const when = new Date(date);
        if (!when.getHours() && !when.getMinutes()) when.setHours(SLOTS[2][0], SLOTS[2][1], 0, 0);

        const amorce = media ? String(media.caption || '') : '';
        const titre = media
            ? (String(media.caption || '').split('\n')[0].slice(0, 60)
                || ('@' + (media.profile_username || 'média') + ' — ' + (media.id)))
            : '';

        openDialog(
            '<div class="dlg__head">' +
            '<h2 class="dlg__title">Nouveau post</h2>' +
            '<p class="dlg__sub">Créneau retenu&nbsp;: ' + esc(fmtFull.format(when))
            + '. Tu peux le changer ci-dessous.</p>' +
            '</div>' +
            '<div class="dlg__body">' +
            (media
                ? '<div class="dlg__media">'
                  + '<img class="dlg__media-thumb" src="' + esc(media.thumb_url || media.file_url) + '" alt="">'
                  + '<div class="dlg__media-txt">'
                  + '<p class="dlg__media-name">Média de la bibliothèque</p>'
                  + '<p class="dlg__media-meta">' + esc('@' + (media.profile_username || '?')) + ' · '
                  + esc(media.media_type === 'video' ? 'Vidéo' : 'Image') + ' · '
                  + esc((media.width || '?') + '×' + (media.height || '?')) + '</p>'
                  + '</div></div>'
                : '') +
            '<div class="field"><span class="field__label">Titre</span>'
            + '<input type="text" class="input" id="new-title" data-autofocus placeholder="Titre du post"'
            + ' value="' + esc(titre) + '"></div>' +
            '<div class="field"><span class="field__label">Légende</span>'
            + '<textarea class="input" id="new-caption" placeholder="Légende pour les réseaux…">'
            + esc(amorce) + '</textarea></div>' +
            '<div class="field"><span class="field__label">Date et heure</span>'
            + '<input type="datetime-local" class="input" id="new-datetime" value="' + esc(toLocalInput(when)) + '"></div>' +
            '<div class="field"><span class="field__label">Plateformes</span>'
            + '<div class="plats-pick" id="new-platforms">'
            + Object.keys(PLATFORMS).map((k) => '<button type="button" class="plats-pick__btn" data-platform="'
                + esc(k) + '" aria-pressed="false"><span class="plat">' + esc(PLATFORMS[k].mono) + '</span>'
                + esc(PLATFORMS[k].label) + '</button>').join('')
            + '</div></div>' +
            '</div>' +
            '<div class="dlg__foot">' +
            '<button type="button" class="btn" id="dlg-cancel">Annuler</button>' +
            '<button type="button" class="btn btn--primary" id="dlg-ok">Programmer</button>' +
            '</div>'
        );

        elDialog.querySelectorAll('#new-platforms .plats-pick__btn').forEach((b) => {
            b.addEventListener('click', () => {
                b.setAttribute('aria-pressed', b.getAttribute('aria-pressed') === 'true' ? 'false' : 'true');
            });
        });

        $('dlg-cancel').addEventListener('click', closeDialog);
        $('dlg-ok').addEventListener('click', async () => {
            const dt = $('new-datetime').value;
            const data = {
                title: $('new-title').value || 'Sans titre',
                caption: $('new-caption').value,
                platforms: JSON.stringify(Array.from(
                    elDialog.querySelectorAll('#new-platforms [aria-pressed="true"]')).map((b) => b.dataset.platform)),
                status: dt ? 'scheduled' : 'draft',
            };
            if (dt) data.scheduled_at = Math.floor(new Date(dt).getTime() / 1000);
            if (media) {
                data.media_type = media.media_type || 'image';
                data.source_media_id = media.id;
            }
            closeDialog();
            try {
                if (media) await createPostWithMedia(data, media);
                else await createPost(data);
                library.armed = null;
                await Promise.all([reload(), loadLibrary()]);
                toast(media ? 'Post créé avec le média.' : 'Post créé.', 'success');
            } catch (err) {
                toast('Création refusée — ' + causeDe(err, 'le serveur a refusé.')
                    + ' Rien n’a été enregistré.', 'error');
            }
        });
    }

    /**
     * Publication. Le flux est MANUEL et assumé : le serveur répond
     * mode:"manual", on affiche la légende à copier et les liens.
     * Réserve dite au point d'usage : l'état persisté ne distingue
     * pas encore « publié à la main » de « publié par API »
     * (AUDIT.md §3, « Publication manuelle assistée »).
     */
    async function doPublish(post) {
        let result;
        try {
            result = await publishPost(post.id);
        } catch (e) {
            toast('La publication n’a pas pu être enregistrée : le serveur a refusé.', 'error');
            return;
        }
        await reload();

        const platforms = result.platforms || [];
        openDialog(
            '<div class="dlg__head">' +
            '<h2 class="dlg__title">Publication manuelle</h2>' +
            '<p class="dlg__sub">Copie la légende, ouvre la plateforme et dépose le média. Le post est désormais marqué '
            + '« Publié » — l’état enregistré ne distingue pas encore une publication manuelle d’une publication par API.</p>' +
            '</div>' +
            '<div class="dlg__body">' +
            '<div class="field"><span class="field__label">Légende</span>'
            + '<textarea class="input" id="publish-caption" readonly rows="6">' + esc(result.caption || '') + '</textarea></div>' +
            '<div class="dlg__links">' +
            '<button type="button" class="btn" id="dlg-copy" data-autofocus>Copier la légende</button>'
            + (platforms.length
                ? platforms.map((k) => '<a class="btn" target="_blank" rel="noopener" href="'
                    + esc(platformOf(k).url) + '">Ouvrir ' + esc(platformOf(k).label) + '</a>').join('')
                : '<span class="dlg__sub">Aucune plateforme cochée sur ce post.</span>')
            + '</div></div>' +
            '<div class="dlg__foot"><button type="button" class="btn" id="dlg-cancel">Fermer</button></div>'
        );
        $('dlg-cancel').addEventListener('click', closeDialog);
        $('dlg-copy').addEventListener('click', () => {
            const ta = $('publish-caption');
            if (!ta) return;
            if (navigator.clipboard && navigator.clipboard.writeText) {
                navigator.clipboard.writeText(ta.value)
                    .then(() => toast('Légende copiée.', 'success'))
                    .catch(() => { ta.select(); toast('Copie refusée par le navigateur : le texte est sélectionné, fais ⌘C.', 'info'); });
            } else {
                ta.select();
                toast('Le texte est sélectionné : fais ⌘C pour le copier.', 'info');
            }
        });
    }

    // ─────────────────────────────────────────────────────────
    // Toasts
    // ─────────────────────────────────────────────────────────
    function toast(message, kind) {
        const t = document.createElement('div');
        t.className = 'toast toast--' + (kind || 'info');
        t.setAttribute('role', kind === 'error' ? 'alert' : 'status');
        t.textContent = message;
        elToasts.appendChild(t);
        requestAnimationFrame(() => t.dataset.show = 'true');
        setTimeout(() => {
            t.dataset.show = 'false';
            setTimeout(() => t.remove(), 300);
        }, kind === 'error' ? 6000 : 3500);
    }

    // ─────────────────────────────────────────────────────────
    // Orchestration
    // ─────────────────────────────────────────────────────────
    function applyView() {
        document.querySelectorAll('.seg__btn').forEach((b) => {
            b.setAttribute('aria-pressed', b.dataset.view === view ? 'true' : 'false');
        });
        const isQueue = view === 'queue';
        elGridWrap.hidden = isQueue;
        elQueue.hidden = !isQueue;
        if (isQueue) {
            elPeriod.textContent = 'File d’attente';
        } else if (calendar) {
            calendar.changeView(view);
            calendar.updateSize();
        }
    }

    function renderAll() {
        renderFilterChips();
        renderAlert();
        renderTray();
        renderQueue();
        if (calendar) calendar.refetchEvents();
        applyView();
    }

    async function reload() {
        if (loading) return;
        loading = true;
        try {
            posts = await fetchPosts();
        } catch (e) {
            toast('Les posts n’ont pas pu être chargés. Vérifie que le serveur répond, puis recharge la page.', 'error');
            posts = [];
        } finally {
            loading = false;
        }
        renderAll();
    }

    // ─────────────────────────────────────────────────────────
    // BIBLIOTHÈQUE LATÉRALE — C1, C2, C10
    //
    // Elle lit l'API du Viewer (`/api/viewer/media`) : une seule
    // source de vérité pour « qu'est-ce qui existe » et « qu'est-ce
    // qui a déjà servi ». Le filtre `used=non` est le défaut EXIGÉ
    // par C10 ; la case à cocher le rend visible et désactivable.
    // ─────────────────────────────────────────────────────────
    const LIB_PAGE = 60;

    function mediaById(id) {
        if (id == null || id === '') return null;
        return library.items.find((m) => String(m.id) === String(id)) || null;
    }

    async function loadLibrary() {
        const params = new URLSearchParams({ per_page: String(LIB_PAGE), sort: 'date_desc' });
        if (library.unusedOnly) params.set('used', 'non');
        try {
            const resp = await fetch('/api/viewer/media?' + params.toString());
            if (!resp.ok) throw await refus(resp);
            const body = await resp.json();
            library.items = Array.isArray(body.items) ? body.items : [];
            library.total = typeof body.total === 'number' ? body.total : library.items.length;
            library.loaded = true;
        } catch (err) {
            library.items = [];
            library.total = 0;
            library.loaded = true;
            library.error = causeDe(err, 'la bibliothèque n’a pas répondu.');
        }
        renderLibrary();
    }

    function renderLibrary() {
        if (!elLibGrid) return;

        if (elLibCount) {
            elLibCount.textContent = library.loaded ? String(library.total || 0) : '…';
            elLibCount.title = library.unusedOnly
                ? 'Médias jamais utilisés dans un post'
                : 'Tous les médias de la bibliothèque';
        }

        if (library.error) {
            elLibGrid.innerHTML = '<p class="lib__empty"><strong>Bibliothèque indisponible</strong><br>'
                + esc(library.error) + '<br>Recharge la page une fois le serveur revenu.</p>';
            library.error = null;
            return;
        }

        if (!library.items.length) {
            elLibGrid.innerHTML = '<p class="lib__empty">'
                + (library.unusedOnly
                    ? '<strong>Tout est déjà programmé</strong><br>Aucun média non utilisé. Décoche le filtre pour revoir toute la bibliothèque.'
                    : '<strong>Aucun média</strong><br>Lance un scrape depuis Profils pour peupler la bibliothèque.')
                + '</p>';
            return;
        }

        // Les vignettes gardent leur ratio d'origine et leur emplacement
        // est dimensionné AVANT l'image (width/height + aspect-ratio) :
        // rien ne bouge pendant le chargement.
        elLibGrid.innerHTML = library.items.map((m) => {
            const w = m.width || 4;
            const h = m.height || 5;
            return '<figure class="libtile" role="listitem" draggable="true"'
                + ' data-media-id="' + esc(m.id) + '"'
                + ' tabindex="0"'
                + ' aria-label="' + esc('Média ' + m.id + ' de @' + (m.profile_username || '?')
                    + ' — glisser sur une case, ou cliquer puis cliquer une case') + '"'
                + ' style="--ar:' + w + '/' + h + '">'
                + '<img class="libtile__img" src="' + esc(m.thumb_url || m.file_url) + '" alt=""'
                + ' width="' + esc(w) + '" height="' + esc(h) + '" loading="lazy" draggable="false">'
                + (m.media_type === 'video'
                    ? '<span class="libtile__badge" title="Vidéo" aria-hidden="true">▶</span>' : '')
                + (m.used
                    ? '<span class="libtile__used">Utilisé</span>' : '')
                + '<figcaption class="libtile__cap">' + esc('@' + (m.profile_username || '?')) + '</figcaption>'
                + '</figure>';
        }).join('');

        if (library.armed) {
            const t = elLibGrid.querySelector('[data-media-id="' + CSS.escape(String(library.armed)) + '"]');
            if (t) t.dataset.armed = 'true';
        }
    }

    function disarmLibrary() {
        library.armed = null;
        if (elLibGrid) elLibGrid.querySelectorAll('[data-armed]').forEach((t) => delete t.dataset.armed);
        document.documentElement.removeAttribute('data-lib-armed');
    }

    function armLibrary(tile) {
        const id = tile.dataset.mediaId;
        const deja = String(library.armed) === String(id);
        disarmLibrary();
        if (deja) return;
        library.armed = id;
        tile.dataset.armed = 'true';
        document.documentElement.setAttribute('data-lib-armed', 'true');
        toast('Média retenu. Clique maintenant la case du calendrier qui doit le recevoir.', 'info');
    }

    function bindLibrary() {
        if (!elLibGrid) return;

        // Drag natif FullCalendar : il donne la date ET l'heure exactes
        // de la case survolée, en Mois comme en Semaine.
        if (window.FullCalendar && FullCalendar.Draggable) {
            new FullCalendar.Draggable(elLibGrid, {
                itemSelector: '.libtile',
                eventData: () => ({ create: false }),
            });
        }

        // Chemin de secours sans drag (C3) : clic = armer.
        elLibGrid.addEventListener('click', (e) => {
            const tile = e.target.closest('.libtile');
            if (tile) armLibrary(tile);
        });
        elLibGrid.addEventListener('keydown', (e) => {
            if (e.key !== 'Enter' && e.key !== ' ') return;
            const tile = e.target.closest('.libtile');
            if (!tile) return;
            e.preventDefault();
            armLibrary(tile);
        });

        const unused = $('lib-unused');
        if (unused) {
            unused.addEventListener('change', () => {
                library.unusedOnly = unused.checked;
                loadLibrary();
            });
        }

        const toggle = $('lib-toggle');
        if (toggle && elLib) {
            toggle.addEventListener('click', () => {
                library.open = !library.open;
                elLib.dataset.open = String(library.open);
                toggle.setAttribute('aria-expanded', String(library.open));
                toggle.setAttribute('aria-label',
                    library.open ? 'Masquer la bibliothèque' : 'Afficher la bibliothèque');
                if (calendar) calendar.updateSize();
            });
        }
    }

    function bind() {
        document.querySelectorAll('.seg__btn').forEach((b) => {
            b.addEventListener('click', () => {
                view = b.dataset.view;
                persistState();
                renderAll();
            });
        });

        $('cal-prev').addEventListener('click', () => { if (calendar && view !== 'queue') calendar.prev(); });
        $('cal-next').addEventListener('click', () => { if (calendar && view !== 'queue') calendar.next(); });
        $('cal-today').addEventListener('click', () => { if (calendar && view !== 'queue') calendar.today(); });
        $('cal-new').addEventListener('click', () => openComposer(new Date()));
        $('side-close').addEventListener('click', closeInspector);

        $('cal-alert-go').addEventListener('click', () => {
            view = 'queue';
            filters.status = 'failed';
            persistState();
            renderAll();
        });

        $('filter-platform').addEventListener('change', (e) => {
            filters.platform = e.target.value;
            persistState();
            renderAll();
        });

        let qTimer = null;
        $('filter-q').addEventListener('input', (e) => {
            filters.q = e.target.value.trim();
            clearTimeout(qTimer);
            qTimer = setTimeout(() => { persistState(); renderAll(); }, 120);
        });

        $('filter-reset').addEventListener('click', () => {
            filters.status = 'all';
            filters.platform = '';
            filters.q = '';
            $('filter-platform').value = '';
            $('filter-q').value = '';
            persistState();
            renderAll();
        });

        const trayToggle = $('cal-tray-toggle');
        trayToggle.addEventListener('click', () => {
            const open = elTray.dataset.open !== 'false';
            elTray.dataset.open = open ? 'false' : 'true';
            trayToggle.setAttribute('aria-expanded', open ? 'false' : 'true');
        });

        // Raccourcis à une touche, inactifs dans un champ (G15).
        document.addEventListener('keydown', (e) => {
            if (e.metaKey || e.ctrlKey || e.altKey) return;
            if (e.target.matches('input, textarea, select, [contenteditable]')) return;
            if (e.key === 'Escape') { disarmLibrary(); closeInspector(); return; }
            const map = { m: 'dayGridMonth', s: 'timeGridWeek', l: 'queue' };
            const next = map[e.key.toLowerCase()];
            if (next) { view = next; persistState(); renderAll(); }
        });
    }

    /**
     * Reprise d'un meme envoyé par l'Éditeur (sessionStorage).
     * Conservé tel quel : c'est le seul pont Éditeur → Calendrier.
     */
    function checkPendingPost() {
        let raw = null;
        try { raw = sessionStorage.getItem('samourais_pending_post'); } catch (e) { return; }
        if (!raw) return;
        try {
            const pending = JSON.parse(raw);
            sessionStorage.removeItem('samourais_pending_post');
            createPost({
                title: 'Meme — ' + (pending.template || 'custom'),
                caption: pending.caption || '',
                media_type: pending.mediaType || 'image',
                template_format: pending.template || 'square',
                thumbnail: pending.mediaSrc,
                status: 'draft',
                platforms: '[]',
            })
                .then(() => reload())
                .then(() => toast('Meme ajouté en brouillon — il t’attend dans « Sans date ».', 'success'))
                .catch(() => toast('Le meme n’a pas pu être ajouté au calendrier.', 'error'));
        } catch (e) {
            console.error('Post en attente illisible :', e);
        }
    }

    // ─────────────────────────────────────────────────────────
    // Démarrage — aucune boîte système, aucun appel bloquant.
    // ─────────────────────────────────────────────────────────
    function boot() {
        renderTimezone();
        restoreState();
        // L'état restauré est immédiatement réécrit dans l'URL : ouvrir
        // l'écran puis copier l'adresse redonne exactement cette vue.
        persistState();
        bind();
        bindLibrary();
        initCalendar();
        loadLibrary();
        reload().then(checkPendingPost).then(consumePendingSlot);
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', boot);
    } else {
        boot();
    }
})();
