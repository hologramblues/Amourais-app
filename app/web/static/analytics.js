// ============================================
// SAMOURAIS Analytics — Instagram Account Stats
// ============================================

(function () {
    'use strict';

    // ─── State ────────────────────────────────────────────
    let currentDays = 30;
    let followerChart = null;
    let contentChart = null;
    let engagementChart = null;
    let postingTimesChart = null;
    let frequencyChart = null;

    // ─── Chart.js defaults ────────────────────────────────
    Chart.defaults.color = '#888';
    Chart.defaults.borderColor = 'rgba(0,0,0,0.06)';
    Chart.defaults.font.family = "'Inter', sans-serif";

    const ACCENT = '#E21B3C';
    const ACCENT_LIGHT = 'rgba(226, 27, 60, 0.15)';

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
            loadAccountOverview(),
            loadFollowerGrowth(),
            loadContentBreakdown(),
            loadEngagement(),
            loadPostingTimes(),
            loadTopPosts(),
            loadPostingFrequency(),
        ]);
    }

    // ─── Account Overview (Profile Header + KPIs) ─────────
    async function loadAccountOverview() {
        try {
            const d = await api('account-overview');

            // Profile header
            const avatar = document.getElementById('profile-avatar');
            if (avatar && d.avatar_url) avatar.src = d.avatar_url;

            setText('profile-display-name', d.display_name || d.username);
            setText('profile-username', `@${d.username}`);
            setText('profile-bio', d.biography || '');
            setText('profile-followers', formatNumber(d.followers_count));
            setText('profile-following', formatNumber(d.following_count));
            setText('profile-posts', formatNumber(d.media_count));

            const badge = document.getElementById('verified-badge');
            if (badge) badge.style.display = d.is_verified ? 'flex' : 'none';

            // KPI Cards
            setText('kpi-followers', formatNumber(d.followers_count));
            const deltaEl = document.getElementById('kpi-follower-delta');
            if (deltaEl) {
                if (d.follower_delta > 0) {
                    deltaEl.textContent = `+${formatNumber(d.follower_delta)} sur ${d.days}j`;
                    deltaEl.className = 'stat-sub positive';
                } else if (d.follower_delta < 0) {
                    deltaEl.textContent = `${formatNumber(d.follower_delta)} sur ${d.days}j`;
                    deltaEl.className = 'stat-sub negative';
                } else {
                    deltaEl.textContent = `Stable sur ${d.days}j`;
                    deltaEl.className = 'stat-sub';
                }
            }

            setText('kpi-engagement', `${d.engagement_rate}%`);
            setText('kpi-engagement-sub', `Moy. likes + comments / followers`);
            setText('kpi-avg-likes', formatNumber(Math.round(d.avg_likes)));
            setText('kpi-avg-comments', `${formatNumber(Math.round(d.avg_comments))} commentaires moy.`);
            setText('kpi-total-posts', formatNumber(d.media_count));
            setText('kpi-scraped-posts', `${d.total_posts_scraped} medias scrapes`);
        } catch (e) {
            console.error('Account overview error:', e);
        }
    }

    // ─── Follower Growth ──────────────────────────────────
    async function loadFollowerGrowth() {
        try {
            const d = await api('follower-growth');
            const ctx = document.getElementById('follower-chart');
            if (!ctx) return;

            if (followerChart) followerChart.destroy();

            const datasets = [
                {
                    label: 'Followers',
                    data: d.followers,
                    borderColor: ACCENT,
                    backgroundColor: ACCENT_LIGHT,
                    fill: true,
                    tension: 0.3,
                    pointRadius: 3,
                    pointBackgroundColor: ACCENT,
                    borderWidth: 2,
                },
            ];

            followerChart = new Chart(ctx, {
                type: 'line',
                data: { labels: d.labels, datasets },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    plugins: {
                        legend: { display: false },
                    },
                    scales: {
                        x: { grid: { display: false } },
                        y: {
                            beginAtZero: false,
                            ticks: { callback: v => formatNumber(v) },
                        },
                    },
                },
            });
        } catch (e) {
            console.error('Follower growth error:', e);
        }
    }

    // ─── Content Breakdown (Doughnut) ─────────────────────
    async function loadContentBreakdown() {
        try {
            const d = await api('content-breakdown');
            const ctx = document.getElementById('content-chart');
            if (!ctx) return;

            const labels = Object.keys(d).map(k => k === 'image' ? 'Photos' : k === 'video' ? 'Videos/Reels' : k);
            const data = Object.values(d);
            const colors = ['#e1306c', '#833AB4', '#F77737', '#405DE6'];

            if (contentChart) contentChart.destroy();
            contentChart = new Chart(ctx, {
                type: 'doughnut',
                data: {
                    labels,
                    datasets: [{ data, backgroundColor: colors.slice(0, data.length), borderWidth: 0 }],
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
            console.error('Content breakdown error:', e);
        }
    }

    // ─── Engagement per Post ──────────────────────────────
    async function loadEngagement() {
        try {
            const d = await api('engagement');
            const ctx = document.getElementById('engagement-chart');
            if (!ctx) return;

            if (engagementChart) engagementChart.destroy();
            engagementChart = new Chart(ctx, {
                type: 'bar',
                data: {
                    labels: d.labels,
                    datasets: [
                        {
                            label: 'Likes',
                            data: d.likes,
                            backgroundColor: 'rgba(226, 27, 60, 0.7)',
                            borderRadius: 3,
                        },
                        {
                            label: 'Commentaires',
                            data: d.comments,
                            backgroundColor: 'rgba(131, 58, 180, 0.7)',
                            borderRadius: 3,
                        },
                    ],
                },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    plugins: {
                        legend: { display: true, position: 'top', labels: { boxWidth: 12, padding: 16 } },
                    },
                    scales: {
                        x: { stacked: true, grid: { display: false } },
                        y: { stacked: true, beginAtZero: true, ticks: { callback: v => formatNumber(v) } },
                    },
                },
            });
        } catch (e) {
            console.error('Engagement error:', e);
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
                        backgroundColor: 'rgba(226, 27, 60, 0.5)',
                        borderColor: ACCENT,
                        borderWidth: 1,
                        borderRadius: 4,
                    }],
                },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    plugins: { legend: { display: false } },
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

    // ─── Top Posts ─────────────────────────────────────────
    async function loadTopPosts() {
        try {
            const items = await api('top-posts');
            const tbody = document.getElementById('top-posts-tbody');
            if (!tbody) return;

            if (items.length === 0) {
                tbody.innerHTML = '<tr><td colspan="7" style="text-align:center;color:#888;padding:40px;">Pas encore de donnees d\'engagement</td></tr>';
                return;
            }

            tbody.innerHTML = items.map((item, i) => {
                const rankClass = i === 0 ? 'rank-gold' : i === 1 ? 'rank-silver' : i === 2 ? 'rank-bronze' : '';
                return `
                    <tr>
                        <td><span class="rank-badge ${rankClass}">${i + 1}</span></td>
                        <td>${item.media_type === 'video' ? '🎥' : '🖼'}</td>
                        <td class="caption-cell">
                            ${item.post_url ? `<a href="${item.post_url}" target="_blank" rel="noopener">${escHtml(item.caption || 'Sans caption')}</a>` : escHtml(item.caption || 'Sans caption')}
                        </td>
                        <td class="num-cell">${formatNumber(item.likes)}</td>
                        <td class="num-cell">${formatNumber(item.comments)}</td>
                        <td class="num-cell">${item.views ? formatNumber(item.views) : '—'}</td>
                        <td>${formatDate(item.posted_at)}</td>
                    </tr>
                `;
            }).join('');
        } catch (e) {
            console.error('Top posts error:', e);
        }
    }

    // ─── Posting Frequency ────────────────────────────────
    async function loadPostingFrequency() {
        try {
            const d = await api('posting-frequency');
            const ctx = document.getElementById('frequency-chart');
            if (!ctx) return;

            if (frequencyChart) frequencyChart.destroy();
            frequencyChart = new Chart(ctx, {
                type: 'bar',
                data: {
                    labels: d.labels,
                    datasets: [{
                        label: 'Posts / semaine',
                        data: d.data,
                        backgroundColor: 'rgba(226, 27, 60, 0.6)',
                        borderColor: ACCENT,
                        borderWidth: 1,
                        borderRadius: 4,
                    }],
                },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    plugins: { legend: { display: false } },
                    scales: {
                        x: { grid: { display: false } },
                        y: { beginAtZero: true, ticks: { stepSize: 1 } },
                    },
                },
            });
        } catch (e) {
            console.error('Posting frequency error:', e);
        }
    }

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

    function formatNumber(n) {
        if (n == null) return '—';
        if (n >= 1000000) return `${(n / 1000000).toFixed(1)}M`;
        if (n >= 1000) return `${(n / 1000).toFixed(1)}K`;
        return n.toLocaleString('fr-FR');
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

    // ─── Boot ─────────────────────────────────────────────
    document.addEventListener('DOMContentLoaded', refreshAll);

})();
