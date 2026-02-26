// ============================================
// SAMOURAIS Analytics — Chart.js dashboard
// ============================================

(function () {
    'use strict';

    // ─── State ────────────────────────────────────────────
    let currentDays = 30;
    let timelineChart = null;
    let platformChart = null;
    let activityChart = null;
    let postingTimesChart = null;
    let contentPage = 1;
    let contentSort = 'discovered_at';
    let contentOrder = 'desc';
    let contentPlatform = '';

    // ─── Chart.js defaults ────────────────────────────────
    Chart.defaults.color = '#888';
    Chart.defaults.borderColor = 'rgba(255,255,255,0.06)';
    Chart.defaults.font.family = "'Inter', sans-serif";

    const PLATFORM_COLORS = {
        instagram: '#e1306c',
        tiktok: '#00f2ea',
        twitter: '#1da1f2',
        reddit: '#ff4500',
    };

    const STATUS_COLORS = {
        completed: '#22c55e',
        failed: '#ef4444',
        partial: '#eab308',
        running: '#3b82f6',
        queued: '#6b7280',
    };

    // ─── Fetch helper ─────────────────────────────────────
    async function api(endpoint, params = {}) {
        params.days = currentDays;
        const qs = new URLSearchParams(params).toString();
        const resp = await fetch(`/api/analytics/${endpoint}?${qs}`);
        if (!resp.ok) throw new Error(`API error: ${resp.status}`);
        return resp.json();
    }

    // ─── Period selector ──────────────────────────────────
    document.querySelectorAll('.period-btn').forEach(btn => {
        btn.addEventListener('click', function () {
            document.querySelector('.period-btn.active')?.classList.remove('active');
            this.classList.add('active');
            currentDays = parseInt(this.dataset.days);
            refreshAll();
        });
    });

    // ─── Refresh all data ─────────────────────────────────
    async function refreshAll() {
        await Promise.all([
            loadOverview(),
            loadTimeline(),
            loadPlatformBreakdown(),
            loadTopRated(),
            loadScrapeActivity(),
            loadPostingTimes(),
            loadContentTable(),
        ]);
    }

    // ─── KPI Cards ────────────────────────────────────────
    async function loadOverview() {
        try {
            const d = await api('overview');
            setText('kpi-total', d.total_media.toLocaleString());
            setText('kpi-period', d.period_media.toLocaleString());
            setText('kpi-period-label', `${d.days} derniers jours`);
            setText('kpi-rating', d.avg_rating ? `${d.avg_rating} ★` : '—');
            setText('kpi-profiles', d.active_profiles);
            setText('kpi-storage', `${d.storage_mb} MB`);
            setText('kpi-success', `${d.success_rate}%`);
            setText('kpi-comments', d.total_comments);
            setText('kpi-scheduled', d.scheduled_posts);
        } catch (e) {
            console.error('Overview error:', e);
        }
    }

    // ─── Collection Timeline ──────────────────────────────
    async function loadTimeline() {
        try {
            const d = await api('collection-timeline');
            const ctx = document.getElementById('timeline-chart');
            if (!ctx) return;

            const datasets = d.platforms.map(p => ({
                label: p,
                data: d.series[p] || [],
                backgroundColor: PLATFORM_COLORS[p] || '#666',
                borderColor: PLATFORM_COLORS[p] || '#666',
                borderWidth: 1,
                borderRadius: 3,
            }));

            if (timelineChart) timelineChart.destroy();
            timelineChart = new Chart(ctx, {
                type: 'bar',
                data: { labels: d.labels, datasets },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    plugins: {
                        legend: { display: true, position: 'top', labels: { boxWidth: 12, padding: 16 } },
                    },
                    scales: {
                        x: { stacked: true, grid: { display: false } },
                        y: { stacked: true, beginAtZero: true, ticks: { stepSize: 1 } },
                    },
                },
            });
        } catch (e) {
            console.error('Timeline error:', e);
        }
    }

    // ─── Platform Breakdown (Doughnut) ────────────────────
    async function loadPlatformBreakdown() {
        try {
            const d = await api('platform-breakdown');
            const ctx = document.getElementById('platform-chart');
            if (!ctx) return;

            const labels = Object.keys(d);
            const data = Object.values(d);
            const colors = labels.map(l => PLATFORM_COLORS[l] || '#666');

            if (platformChart) platformChart.destroy();
            platformChart = new Chart(ctx, {
                type: 'doughnut',
                data: {
                    labels,
                    datasets: [{ data, backgroundColor: colors, borderWidth: 0 }],
                },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    cutout: '65%',
                    plugins: {
                        legend: { position: 'bottom', labels: { boxWidth: 12, padding: 12 } },
                    },
                },
            });
        } catch (e) {
            console.error('Platform breakdown error:', e);
        }
    }

    // ─── Top Rated ────────────────────────────────────────
    async function loadTopRated() {
        try {
            const items = await api('top-rated');
            const container = document.getElementById('top-rated-list');
            if (!container) return;

            if (items.length === 0) {
                container.innerHTML = '<div class="empty-state"><p>Aucune note pour le moment</p></div>';
                return;
            }

            container.innerHTML = items.map((item, i) => {
                const rankClass = i === 0 ? 'gold' : i === 1 ? 'silver' : i === 2 ? 'bronze' : '';
                return `
                    <div class="best-post-item">
                        <div class="best-post-rank ${rankClass}">${i + 1}</div>
                        <div class="best-post-info">
                            <div class="best-post-caption">${escHtml(item.caption || 'Sans caption')}</div>
                            <div class="best-post-meta">${item.platform} · ${item.rating_count} note(s)</div>
                        </div>
                        <div class="best-post-rating">${item.avg_rating} ★</div>
                    </div>
                `;
            }).join('');
        } catch (e) {
            console.error('Top rated error:', e);
        }
    }

    // ─── Scrape Activity ──────────────────────────────────
    async function loadScrapeActivity() {
        try {
            const d = await api('scrape-activity');
            const ctx = document.getElementById('activity-chart');
            if (!ctx) return;

            const datasets = Object.entries(d.series).map(([status, data]) => ({
                label: status,
                data,
                backgroundColor: STATUS_COLORS[status] || '#666',
                borderColor: STATUS_COLORS[status] || '#666',
                borderWidth: 1,
                borderRadius: 3,
            }));

            if (activityChart) activityChart.destroy();
            activityChart = new Chart(ctx, {
                type: 'bar',
                data: { labels: d.labels, datasets },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    plugins: {
                        legend: { display: true, position: 'top', labels: { boxWidth: 12, padding: 16 } },
                    },
                    scales: {
                        x: { stacked: true, grid: { display: false } },
                        y: { stacked: true, beginAtZero: true, ticks: { stepSize: 1 } },
                    },
                },
            });
        } catch (e) {
            console.error('Activity error:', e);
        }
    }

    // ─── Best Posting Times ───────────────────────────────
    async function loadPostingTimes() {
        try {
            const d = await api('best-posting-times');
            const ctx = document.getElementById('posting-times-chart');
            if (!ctx) return;

            if (postingTimesChart) postingTimesChart.destroy();
            postingTimesChart = new Chart(ctx, {
                type: 'bar',
                data: {
                    labels: d.labels,
                    datasets: [{
                        label: 'Posts par heure',
                        data: d.data,
                        backgroundColor: 'rgba(239, 68, 68, 0.6)',
                        borderColor: '#ef4444',
                        borderWidth: 1,
                        borderRadius: 4,
                    }],
                },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    plugins: {
                        legend: { display: false },
                    },
                    scales: {
                        x: { grid: { display: false } },
                        y: { beginAtZero: true, ticks: { stepSize: 1 } },
                    },
                },
            });
        } catch (e) {
            console.error('Posting times error:', e);
        }
    }

    // ─── Content Table ────────────────────────────────────
    async function loadContentTable() {
        try {
            const params = {
                page: contentPage,
                per_page: 15,
                sort: contentSort,
                order: contentOrder,
            };
            if (contentPlatform) params.platform = contentPlatform;

            const d = await api('content-table', params);
            const tbody = document.getElementById('content-tbody');
            const footer = document.getElementById('table-footer-info');
            const pagination = document.getElementById('table-pagination');
            if (!tbody) return;

            if (d.items.length === 0) {
                tbody.innerHTML = '<tr><td colspan="7" style="text-align:center;color:#888;padding:40px;">Aucun média trouvé</td></tr>';
            } else {
                tbody.innerHTML = d.items.map(item => `
                    <tr>
                        <td><span class="platform-badge ${item.platform}">${item.platform}</span></td>
                        <td>${item.media_type === 'video' ? '🎥' : '🖼'} ${item.media_type}</td>
                        <td title="${escHtml(item.caption)}">${escHtml(item.caption) || '—'}</td>
                        <td>${formatDate(item.discovered_at)}</td>
                        <td>${item.avg_rating > 0 ? `<span class="rating-stars">${item.avg_rating} ★</span>` : '—'}</td>
                        <td>${item.comment_count || 0}</td>
                        <td>${formatSize(item.file_size)}</td>
                    </tr>
                `).join('');
            }

            if (footer) {
                const start = (d.page - 1) * d.per_page + 1;
                const end = Math.min(d.page * d.per_page, d.total);
                footer.textContent = `${start}-${end} sur ${d.total} médias`;
            }

            if (pagination) {
                let btns = '';
                btns += `<button class="page-btn" onclick="changePage(${d.page - 1})" ${d.page <= 1 ? 'disabled' : ''}>←</button>`;
                for (let p = Math.max(1, d.page - 2); p <= Math.min(d.pages, d.page + 2); p++) {
                    btns += `<button class="page-btn ${p === d.page ? 'active' : ''}" onclick="changePage(${p})">${p}</button>`;
                }
                btns += `<button class="page-btn" onclick="changePage(${d.page + 1})" ${d.page >= d.pages ? 'disabled' : ''}>→</button>`;
                pagination.innerHTML = btns;
            }
        } catch (e) {
            console.error('Content table error:', e);
        }
    }

    // ─── Table interactions ───────────────────────────────
    window.changePage = function (p) {
        if (p < 1) return;
        contentPage = p;
        loadContentTable();
    };

    window.sortTable = function (col) {
        if (contentSort === col) {
            contentOrder = contentOrder === 'desc' ? 'asc' : 'desc';
        } else {
            contentSort = col;
            contentOrder = 'desc';
        }
        contentPage = 1;
        loadContentTable();
    };

    window.filterPlatform = function (platform) {
        contentPlatform = contentPlatform === platform ? '' : platform;
        contentPage = 1;
        // Update button states
        document.querySelectorAll('.filter-btn').forEach(b => {
            b.classList.toggle('active', b.dataset.platform === contentPlatform);
        });
        loadContentTable();
    };

    // ─── Helpers ──────────────────────────────────────────
    function setText(id, text) {
        const el = document.getElementById(id);
        if (el) el.textContent = text;
    }

    function escHtml(str) {
        if (!str) return '';
        const div = document.createElement('div');
        div.textContent = str;
        return div.innerHTML;
    }

    function formatDate(ts) {
        if (!ts) return '—';
        try {
            return new Date(ts * 1000).toLocaleDateString('fr-FR', {
                day: '2-digit', month: '2-digit', year: 'numeric'
            });
        } catch {
            return '—';
        }
    }

    function formatSize(bytes) {
        if (!bytes) return '—';
        if (bytes < 1024) return `${bytes} B`;
        if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`;
        return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
    }

    // ─── Boot ─────────────────────────────────────────────
    document.addEventListener('DOMContentLoaded', refreshAll);

})();
