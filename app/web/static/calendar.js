// ============================================
// SAMOURAIS Calendar — FullCalendar.js integration
// ============================================

(function () {
    'use strict';

    // ─── State ────────────────────────────────────────────
    let calendar;
    let selectedPost = null;
    let allPosts = [];

    // ─── DOM refs ─────────────────────────────────────────
    const calendarEl = document.getElementById('calendar');
    const sidebar = document.getElementById('calendar-sidebar');
    const sidebarContent = document.getElementById('sidebar-body');
    const sidebarActions = document.getElementById('sidebar-actions');
    const statDrafts = document.getElementById('stat-drafts');
    const statScheduled = document.getElementById('stat-scheduled');
    const statPublished = document.getElementById('stat-published');
    const modalOverlay = document.getElementById('modal-overlay');

    // ─── Init FullCalendar ────────────────────────────────
    function initCalendar() {
        calendar = new FullCalendar.Calendar(calendarEl, {
            initialView: 'dayGridMonth',
            locale: 'fr',
            firstDay: 1, // Monday
            headerToolbar: {
                left: 'prev,next today',
                center: 'title',
                right: ''
            },
            height: 'auto',
            editable: true,
            droppable: false,
            eventStartEditable: true,
            eventDurationEditable: false,
            selectable: true,
            dayMaxEvents: 4,

            // Fetch events from API
            events: function (info, successCallback, failureCallback) {
                const params = new URLSearchParams({
                    start: info.startStr,
                    end: info.endStr
                });
                fetch(`/api/calendar/posts?${params}`)
                    .then(r => r.json())
                    .then(events => {
                        allPosts = events;
                        updateStats();
                        successCallback(events);
                    })
                    .catch(err => {
                        console.error('Failed to load events:', err);
                        failureCallback(err);
                    });
            },

            // Click on event → open sidebar
            eventClick: function (info) {
                info.jsEvent.preventDefault();
                openPostSidebar(info.event);
            },

            // Drag to reschedule
            eventDrop: function (info) {
                const newDate = info.event.start;
                const ts = Math.floor(newDate.getTime() / 1000);
                updatePost(info.event.id, { scheduled_at: ts })
                    .then(() => showToast('Post replanifié !', 'success'))
                    .catch(() => {
                        info.revert();
                        showToast('Erreur lors du déplacement', 'error');
                    });
            },

            // Click on empty date → create new post
            dateClick: function (info) {
                openCreateModal(info.dateStr);
            },

            // Style events
            eventDidMount: function (info) {
                const props = info.event.extendedProps;
                if (props.platforms && props.platforms.length > 0) {
                    const icons = props.platforms.map(p => platformEmoji(p)).join(' ');
                    const titleEl = info.el.querySelector('.fc-event-title');
                    if (titleEl) {
                        titleEl.innerHTML = `${icons} ${info.event.title}`;
                    }
                }
            }
        });

        calendar.render();
    }

    // ─── Stats ────────────────────────────────────────────
    function updateStats() {
        let drafts = 0, scheduled = 0, published = 0;
        allPosts.forEach(p => {
            const status = p.extendedProps?.status || p.status;
            if (status === 'draft') drafts++;
            else if (status === 'scheduled') scheduled++;
            else if (status === 'published') published++;
        });
        if (statDrafts) statDrafts.textContent = drafts;
        if (statScheduled) statScheduled.textContent = scheduled;
        if (statPublished) statPublished.textContent = published;
    }

    // ─── Sidebar ──────────────────────────────────────────
    function openPostSidebar(event) {
        selectedPost = event;
        const props = event.extendedProps;
        const platforms = props.platforms || [];
        const status = props.status || 'draft';
        const mediaUrl = `/api/calendar/posts/${event.id}/media`;

        sidebar.classList.add('open');

        sidebarContent.innerHTML = `
            <div class="post-preview">
                ${props.thumbnail_path || props.media_type === 'image'
                    ? `<img src="${mediaUrl}" alt="Preview" onerror="this.parentNode.innerHTML='<div class=\\'no-media\\'>🖼</div>'">`
                    : props.media_type === 'video'
                        ? `<video src="${mediaUrl}" style="width:100%;height:100%;object-fit:cover;" muted></video>`
                        : `<div class="no-media">🖼</div>`
                }
            </div>

            <div class="sidebar-section">
                <span class="sidebar-label">Statut</span>
                <span class="status-badge ${status}">${statusLabel(status)}</span>
            </div>

            <div class="sidebar-section">
                <span class="sidebar-label">Titre</span>
                <input type="text" class="sidebar-input" id="edit-title" value="${escHtml(event.title)}" placeholder="Titre du post">
            </div>

            <div class="sidebar-section">
                <span class="sidebar-label">Caption</span>
                <textarea class="sidebar-input" id="edit-caption" placeholder="Caption pour les réseaux...">${escHtml(props.caption || '')}</textarea>
            </div>

            <div class="sidebar-section">
                <span class="sidebar-label">Date planifiée</span>
                <input type="datetime-local" class="sidebar-input" id="edit-datetime"
                    value="${event.start ? toLocalDatetime(event.start) : ''}">
            </div>

            <div class="sidebar-section">
                <span class="sidebar-label">Plateformes</span>
                <div class="platform-selector" id="edit-platforms">
                    ${['instagram', 'tiktok', 'twitter', 'reddit'].map(p => `
                        <div class="platform-pill ${platforms.includes(p) ? 'selected' : ''}"
                             data-platform="${p}" onclick="togglePlatform(this)">
                            ${platformEmoji(p)} ${p}
                        </div>
                    `).join('')}
                </div>
            </div>

            <div class="sidebar-section">
                <span class="sidebar-label">Format</span>
                <span style="color: var(--cal-text); font-size: 14px;">
                    ${props.template_format || '—'}
                </span>
            </div>
        `;

        // Actions
        sidebarActions.innerHTML = `
            <button class="btn btn-danger" onclick="deleteCurrentPost()">🗑 Supprimer</button>
            <button class="btn btn-secondary" onclick="saveCurrentPost()">💾 Sauvegarder</button>
            ${status !== 'published'
                ? `<button class="btn btn-publish" onclick="publishCurrentPost()">🚀 Publier</button>`
                : ''
            }
        `;
    }

    function closeSidebar() {
        sidebar.classList.remove('open');
        selectedPost = null;
    }

    // ─── CRUD helpers ─────────────────────────────────────
    async function updatePost(id, data) {
        const resp = await fetch(`/api/calendar/posts/${id}`, {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(data)
        });
        if (!resp.ok) throw new Error('Update failed');
        return resp.json();
    }

    async function createPost(data) {
        const resp = await fetch('/api/calendar/posts', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(data)
        });
        if (!resp.ok) throw new Error('Create failed');
        return resp.json();
    }

    async function deletePost(id) {
        const resp = await fetch(`/api/calendar/posts/${id}`, {
            method: 'DELETE'
        });
        if (!resp.ok) throw new Error('Delete failed');
        return resp.json();
    }

    async function publishPost(id) {
        const resp = await fetch(`/api/calendar/posts/${id}/publish`, {
            method: 'POST'
        });
        if (!resp.ok) throw new Error('Publish failed');
        return resp.json();
    }

    // ─── Sidebar actions (global) ─────────────────────────
    window.saveCurrentPost = async function () {
        if (!selectedPost) return;
        const title = document.getElementById('edit-title')?.value || '';
        const caption = document.getElementById('edit-caption')?.value || '';
        const datetime = document.getElementById('edit-datetime')?.value;
        const platformEls = document.querySelectorAll('#edit-platforms .platform-pill.selected');
        const platforms = Array.from(platformEls).map(el => el.dataset.platform);

        const data = { title, caption, platforms };
        if (datetime) {
            data.scheduled_at = Math.floor(new Date(datetime).getTime() / 1000);
            data.status = 'scheduled';
        }

        try {
            await updatePost(selectedPost.id, data);
            calendar.refetchEvents();
            showToast('Post sauvegardé !', 'success');
        } catch (e) {
            showToast('Erreur de sauvegarde', 'error');
        }
    };

    window.deleteCurrentPost = async function () {
        if (!selectedPost) return;
        if (!confirm('Supprimer ce post ?')) return;
        try {
            await deletePost(selectedPost.id);
            closeSidebar();
            calendar.refetchEvents();
            showToast('Post supprimé', 'success');
        } catch (e) {
            showToast('Erreur de suppression', 'error');
        }
    };

    window.publishCurrentPost = async function () {
        if (!selectedPost) return;
        try {
            const result = await publishPost(selectedPost.id);
            closeSidebar();
            calendar.refetchEvents();

            // Show manual publish instructions
            if (result.mode === 'manual') {
                showPublishModal(result);
            } else {
                showToast('Post publié !', 'success');
            }
        } catch (e) {
            showToast('Erreur de publication', 'error');
        }
    };

    window.togglePlatform = function (el) {
        el.classList.toggle('selected');
    };

    window.closeSidebar = closeSidebar;

    // ─── Create modal ─────────────────────────────────────
    function openCreateModal(dateStr) {
        const modal = modalOverlay;
        if (!modal) return;

        modal.innerHTML = `
            <div class="modal">
                <h3>📅 Nouveau post</h3>
                <div class="modal-field">
                    <label>Titre</label>
                    <input type="text" id="new-title" placeholder="Titre du post">
                </div>
                <div class="modal-field">
                    <label>Caption</label>
                    <textarea id="new-caption" placeholder="Caption pour les réseaux..."></textarea>
                </div>
                <div class="modal-field">
                    <label>Date planifiée</label>
                    <input type="datetime-local" id="new-datetime" value="${dateStr}T12:00">
                </div>
                <div class="modal-field">
                    <label>Plateformes</label>
                    <div class="platform-selector" id="new-platforms">
                        ${['instagram', 'tiktok', 'twitter', 'reddit'].map(p => `
                            <div class="platform-pill" data-platform="${p}" onclick="togglePlatform(this)">
                                ${platformEmoji(p)} ${p}
                            </div>
                        `).join('')}
                    </div>
                </div>
                <div class="modal-actions">
                    <button class="btn btn-secondary" onclick="closeModal()">Annuler</button>
                    <button class="btn btn-primary" onclick="submitNewPost()">Créer</button>
                </div>
            </div>
        `;
        modal.classList.add('show');
    }

    window.submitNewPost = async function () {
        const title = document.getElementById('new-title')?.value || '';
        const caption = document.getElementById('new-caption')?.value || '';
        const datetime = document.getElementById('new-datetime')?.value;
        const platformEls = document.querySelectorAll('#new-platforms .platform-pill.selected');
        const platforms = JSON.stringify(Array.from(platformEls).map(el => el.dataset.platform));

        const data = {
            title: title || 'Sans titre',
            caption,
            platforms,
            status: datetime ? 'scheduled' : 'draft'
        };
        if (datetime) {
            data.scheduled_at = Math.floor(new Date(datetime).getTime() / 1000);
        }

        try {
            await createPost(data);
            closeModal();
            calendar.refetchEvents();
            showToast('Post créé !', 'success');
        } catch (e) {
            showToast('Erreur de création', 'error');
        }
    };

    window.closeModal = function () {
        if (modalOverlay) modalOverlay.classList.remove('show');
    };

    // ─── Publish modal (manual workflow) ──────────────────
    function showPublishModal(result) {
        const modal = modalOverlay;
        if (!modal) return;

        const platforms = (result.platforms || []).map(p =>
            `<a href="${platformUrl(p)}" target="_blank" class="btn btn-secondary" style="margin: 4px;">
                ${platformEmoji(p)} Ouvrir ${p}
            </a>`
        ).join('');

        modal.innerHTML = `
            <div class="modal" style="width: 500px;">
                <h3>🚀 Publication manuelle</h3>
                <p style="color: var(--cal-text-muted); margin-bottom: 16px;">
                    Copie le caption et uploade manuellement sur chaque plateforme.
                </p>
                <div class="modal-field">
                    <label>Caption</label>
                    <textarea id="publish-caption" readonly style="min-height: 120px;">${escHtml(result.caption || '')}</textarea>
                </div>
                <div style="display: flex; gap: 8px; flex-wrap: wrap; margin-bottom: 16px;">
                    <button class="btn btn-primary" onclick="copyCaption()">📋 Copier le caption</button>
                    ${platforms}
                </div>
                <div class="modal-actions">
                    <button class="btn btn-secondary" onclick="closeModal()">Fermer</button>
                </div>
            </div>
        `;
        modal.classList.add('show');
    }

    window.copyCaption = function () {
        const el = document.getElementById('publish-caption');
        if (el) {
            navigator.clipboard.writeText(el.value).then(() => {
                showToast('Caption copié !', 'success');
            });
        }
    };

    // ─── Check for pending post from editor ───────────────
    function checkPendingPost() {
        const raw = sessionStorage.getItem('samourais_pending_post');
        if (!raw) return;

        try {
            const postData = JSON.parse(raw);
            sessionStorage.removeItem('samourais_pending_post');

            // Create the post via API with media as thumbnail
            const data = {
                title: 'Meme — ' + (postData.template || 'custom'),
                caption: postData.caption || '',
                media_type: postData.mediaType || 'image',
                template_format: postData.template || 'square',
                thumbnail: postData.mediaSrc,  // base64 data URL
                status: 'draft',
                platforms: '[]',
            };

            createPost(data)
                .then(() => {
                    calendar.refetchEvents();
                    showToast('Meme ajouté en tant que brouillon !', 'success');
                })
                .catch(() => {
                    showToast('Erreur lors de l\'ajout du meme', 'error');
                });
        } catch (e) {
            console.error('Failed to process pending post:', e);
        }
    }

    // ─── View toggle ──────────────────────────────────────
    document.querySelectorAll('.view-toggle button').forEach(btn => {
        btn.addEventListener('click', function () {
            document.querySelector('.view-toggle button.active')?.classList.remove('active');
            this.classList.add('active');
            const view = this.dataset.view;
            if (calendar) calendar.changeView(view);
        });
    });

    // ─── Helpers ──────────────────────────────────────────
    function platformEmoji(platform) {
        const map = {
            instagram: '📷',
            tiktok: '🎵',
            twitter: '🐦',
            reddit: '👽'
        };
        return map[platform] || '🌐';
    }

    function platformUrl(platform) {
        const map = {
            instagram: 'https://www.instagram.com/',
            tiktok: 'https://www.tiktok.com/upload',
            twitter: 'https://x.com/compose/post',
            reddit: 'https://www.reddit.com/submit'
        };
        return map[platform] || '#';
    }

    function statusLabel(status) {
        const map = {
            draft: '📝 Brouillon',
            scheduled: '📅 Planifié',
            published: '✅ Publié',
            failed: '❌ Échoué'
        };
        return map[status] || status;
    }

    function escHtml(str) {
        const div = document.createElement('div');
        div.textContent = str;
        return div.innerHTML;
    }

    function toLocalDatetime(date) {
        const d = new Date(date);
        const offset = d.getTimezoneOffset();
        const local = new Date(d.getTime() - offset * 60000);
        return local.toISOString().slice(0, 16);
    }

    function showToast(message, type = 'info') {
        // Remove existing
        document.querySelectorAll('.toast').forEach(t => t.remove());

        const toast = document.createElement('div');
        toast.className = `toast ${type}`;
        toast.textContent = message;
        document.body.appendChild(toast);

        requestAnimationFrame(() => {
            toast.classList.add('show');
        });

        setTimeout(() => {
            toast.classList.remove('show');
            setTimeout(() => toast.remove(), 300);
        }, 3000);
    }

    // ─── Boot ─────────────────────────────────────────────
    document.addEventListener('DOMContentLoaded', () => {
        initCalendar();

        // Check if editor sent a post to schedule
        setTimeout(checkPendingPost, 500);
    });

})();
