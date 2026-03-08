/* SAMOURAIS SCRAPPER — Media Viewer */

(function () {
  "use strict";

  // ─── State ───────────────────────────────────────────────────
  let mediaItems = [];
  let memeItems = [];
  let currentIndex = -1;
  let currentPage = 1;
  let totalPages = 1;
  let memePage = 1;
  let memeTotalPages = 1;
  let loading = false;
  let memeLoading = false;
  let activeTab = "media"; // "media" or "memes"
  let userName = localStorage.getItem("viewer_user") || "";

  // ─── Selection mode ────────────────────────────────────────
  let selectionMode = false;
  let selectedIds = new Set();

  const API = "/api/viewer";

  // ─── DOM refs ────────────────────────────────────────────────
  const grid = document.getElementById("media-grid");
  const memesGrid = document.getElementById("memes-grid");
  const lightbox = document.getElementById("lightbox");
  const lbMedia = document.getElementById("lb-media");
  const lbCaption = document.getElementById("lb-caption");
  const lbInfo = document.getElementById("lb-info");
  const lbPostLink = document.getElementById("lb-post-link");
  const lbStars = document.getElementById("lb-stars");
  const lbRatingInfo = document.getElementById("lb-rating-info");
  const lbComments = document.getElementById("lb-comments");
  const lbCommentInput = document.getElementById("lb-comment-input");
  const lbCommentBtn = document.getElementById("lb-comment-btn");
  const filterPlatform = document.getElementById("filter-platform");
  const filterProfile = document.getElementById("filter-profile");
  const filterRating = document.getElementById("filter-rating");
  const filterSort = document.getElementById("filter-sort");
  const filterSource = document.getElementById("filter-source");
  const filterSearch = document.getElementById("filter-search");
  const statsEl = document.getElementById("topbar-stats");
  const userNameEl = document.getElementById("user-name-display");
  const mediaFilters = document.getElementById("media-filters");
  const mediaCountEl = document.getElementById("media-count");
  const memesCountEl = document.getElementById("memes-count");

  // ─── Init ────────────────────────────────────────────────────
  function init() {
    if (!userName) {
      promptUserName();
    } else {
      userNameEl.textContent = userName;
    }

    initZoom();
    loadProfiles();
    loadMedia();
    loadMemes();
    setupInfiniteScroll();
    setupKeyboard();
    setupFilters();
  }

  function promptUserName() {
    const name = prompt("Choisis ton pseudo pour commenter et noter :");
    if (name && name.trim()) {
      userName = name.trim();
      localStorage.setItem("viewer_user", userName);
      userNameEl.textContent = userName;
    } else {
      userName = "Anonyme";
      userNameEl.textContent = userName;
    }
  }

  // ─── Tab switching ─────────────────────────────────────────
  function switchTab(tab) {
    activeTab = tab;

    // Update tab buttons
    document.querySelectorAll(".viewer-tab").forEach((btn) => {
      btn.classList.toggle("active", btn.dataset.tab === tab);
    });

    // Show/hide grids
    if (tab === "media") {
      grid.style.display = "";
      memesGrid.style.display = "none";
      mediaFilters.style.display = "";
    } else {
      grid.style.display = "none";
      memesGrid.style.display = "";
      mediaFilters.style.display = "none";
    }
  }

  // ─── Profiles dropdown ──────────────────────────────────────
  async function loadProfiles() {
    try {
      const res = await fetch(`${API}/profiles`);
      const profiles = await res.json();
      profiles.forEach((p) => {
        const opt = document.createElement("option");
        opt.value = p.id;
        opt.textContent = `${p.platform} / @${p.username}`;
        filterProfile.appendChild(opt);
      });
    } catch (e) {
      console.error("Failed to load profiles", e);
    }
  }

  // ─── Load media ─────────────────────────────────────────────
  async function loadMedia(append = false) {
    if (loading) return;
    loading = true;

    if (!append) {
      currentPage = 1;
      mediaItems = [];
      grid.innerHTML = '<div class="loader"><div class="loader-spinner"></div><p>Chargement...</p></div>';
    }

    const params = new URLSearchParams({
      page: currentPage,
      per_page: 60,
      sort: filterSort.value,
    });

    if (filterPlatform.value) params.set("platform", filterPlatform.value);
    if (filterProfile.value) params.set("profile_id", filterProfile.value);
    if (filterRating.value) params.set("min_rating", filterRating.value);
    if (filterSource.value) params.set("source", filterSource.value);
    if (filterSearch.value) params.set("search", filterSearch.value);

    try {
      const res = await fetch(`${API}/media?${params}`);
      const data = await res.json();

      totalPages = data.total_pages;

      if (!append) {
        grid.innerHTML = "";
        mediaItems = [];
        lastWeekKey = "";
        currentWeekGroup = null;
      }

      const startIdx = mediaItems.length;
      mediaItems.push(...data.items);

      data.items.forEach((item, i) => {
        // Insert week separator + group when the week changes
        const ts = item.posted_at || item.discovered_at;
        const wk = weekKey(ts);
        if (wk !== lastWeekKey) {
          grid.appendChild(createWeekSeparator(ts, wk));
          currentWeekGroup = createWeekGroup(wk);
          grid.appendChild(currentWeekGroup);
          lastWeekKey = wk;
        }
        if (currentWeekGroup) {
          currentWeekGroup.appendChild(createCard(item, startIdx + i));
        } else {
          grid.appendChild(createCard(item, startIdx + i));
        }
      });

      // Update media count badges on each week header
      updateWeekCounts();

      // Lazy load images
      observeImages();

      // Stats
      statsEl.textContent = `${data.total} medias`;
      mediaCountEl.textContent = data.total;

      if (data.items.length === 0 && !append) {
        grid.innerHTML = '<div class="loader"><p>Aucun media trouve</p></div>';
      }
    } catch (e) {
      console.error("Failed to load media", e);
      if (!append) {
        grid.innerHTML = '<div class="loader"><p>Erreur de chargement</p></div>';
      }
    }

    loading = false;

    // If grid doesn't fill viewport (e.g. zoomed out), load more
    requestAnimationFrame(() => loadMoreIfNeeded());
  }

  // ─── Load memes ──────────────────────────────────────────────
  async function loadMemes(append = false) {
    if (memeLoading) return;
    memeLoading = true;

    if (!append) {
      memePage = 1;
      memeItems = [];
      memesGrid.innerHTML = '<div class="loader"><div class="loader-spinner"></div><p>Chargement des memes...</p></div>';
    }

    try {
      const res = await fetch(`${API}/memes?page=${memePage}&per_page=60`);
      const data = await res.json();

      memeTotalPages = data.total_pages;

      if (!append) {
        memesGrid.innerHTML = "";
        memeItems = [];
      }

      const startIdx = memeItems.length;
      memeItems.push(...data.items);

      data.items.forEach((item, i) => {
        memesGrid.appendChild(createMemeCard(item, startIdx + i));
      });

      // Lazy load meme images
      observeMemeImages();

      memesCountEl.textContent = data.total;

      if (data.items.length === 0 && !append) {
        memesGrid.innerHTML = '<div class="loader"><p>Aucun meme sauvegarde. Cree-en dans l\'editeur !</p></div>';
      }
    } catch (e) {
      console.error("Failed to load memes", e);
      if (!append) {
        memesGrid.innerHTML = '<div class="loader"><p>Erreur de chargement des memes</p></div>';
      }
    }

    memeLoading = false;
  }

  // ─── Card creation ──────────────────────────────────────────
  function createCard(item, index) {
    const card = document.createElement("div");
    card.className = "media-card loading";
    card.dataset.index = index;
    card.dataset.mediaId = item.id;
    card.onclick = () => {
      if (selectionMode) {
        toggleCardSelection(item.id, card);
      } else {
        openLightbox(index);
      }
    };

    // Determine thumbnail source
    const src = item.file_url || item.media_url || "";

    if (item.media_type === "video") {
      // Use server-side thumbnail (ffmpeg JPEG) instead of loading the video
      const img = document.createElement("img");
      img.dataset.src = item.thumb_url || src;
      img.alt = item.caption || "Video";
      card.appendChild(img);

      const play = document.createElement("div");
      play.className = "play-icon";
      play.textContent = "\u25B6";
      card.appendChild(play);
    } else {
      const img = document.createElement("img");
      img.dataset.src = src;
      img.alt = item.caption || "Image";
      card.appendChild(img);
    }

    // Rating badge
    if (item.avg_rating > 0) {
      const badge = document.createElement("div");
      badge.className = "card-rating-badge";
      badge.textContent = `\u2605 ${item.avg_rating}`;
      card.appendChild(badge);
    }

    // Overlay
    const overlay = document.createElement("div");
    overlay.className = "card-overlay";

    const platform = document.createElement("span");
    platform.className = "platform-badge";
    platform.textContent = platformEmoji(item.platform);
    overlay.appendChild(platform);

    if (item.comment_count > 0) {
      const comments = document.createElement("span");
      comments.className = "comment-badge";
      comments.textContent = `\uD83D\uDCAC ${item.comment_count}`;
      overlay.appendChild(comments);
    }

    card.appendChild(overlay);

    // Selection checkbox (hidden by default)
    const cb = document.createElement("div");
    cb.className = "card-checkbox";
    cb.innerHTML = '<svg viewBox="0 0 24 24" width="22" height="22"><circle cx="12" cy="12" r="10" fill="rgba(0,0,0,0.4)" stroke="white" stroke-width="2"/></svg>';
    card.appendChild(cb);

    return card;
  }

  function createMemeCard(item, index) {
    const card = document.createElement("div");
    card.className = "media-card loading";
    card.dataset.index = index;
    card.onclick = (e) => {
      // Don't open lightbox if clicking download
      if (e.target.classList.contains("meme-download-btn")) return;
      openMemeLightbox(index);
    };

    const img = document.createElement("img");
    img.dataset.src = item.thumbnail_url || item.file_url;
    img.alt = item.title || "Meme";
    card.appendChild(img);

    // Download button
    const dlBtn = document.createElement("button");
    dlBtn.className = "meme-download-btn";
    dlBtn.textContent = "\u2B07 Download";
    dlBtn.onclick = (e) => {
      e.stopPropagation();
      downloadMeme(item);
    };
    card.appendChild(dlBtn);

    // Meme badge
    const badge = document.createElement("div");
    badge.className = "meme-badge";
    badge.textContent = item.template_format || "MEME";
    card.appendChild(badge);

    return card;
  }

  function downloadMeme(item) {
    const link = document.createElement("a");
    link.href = item.file_url;
    link.download = `samourais_meme_${item.id}.${item.media_type === "video" ? "mp4" : "png"}`;
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
  }

  function openMemeLightbox(index) {
    const item = memeItems[index];
    if (!item) return;

    lightbox.classList.add("active");
    document.body.style.overflow = "hidden";

    // Show meme in lightbox
    lbMedia.innerHTML = `<img src="${escHtml(item.file_url)}" alt="${escHtml(item.title || "Meme")}" style="max-width:100%;max-height:100vh;">`;

    lbInfo.textContent = `🎨 Meme • ${item.template_format || ""}`;
    lbCaption.textContent = item.caption || item.title || "";

    // Show download link instead of post link
    lbPostLink.href = item.file_url;
    lbPostLink.textContent = "\u2B07 Telecharger le meme";
    lbPostLink.setAttribute("download", `meme_${item.id}.png`);
    lbPostLink.style.display = "";

    // Remove edit button if exists
    const editBtn = document.getElementById("lb-edit-btn");
    if (editBtn) editBtn.style.display = "none";

    // Hide rating and comments for memes
    document.querySelector(".rating-section").style.display = "none";
    document.querySelector(".comments-section").style.display = "none";

    // Store context for nav
    currentIndex = index;
  }

  function platformEmoji(p) {
    const map = { instagram: "\uD83D\uDCF7", tiktok: "\uD83C\uDFB5", twitter: "\uD83D\uDC26", reddit: "\uD83D\uDC7D" };
    return map[p] || "\uD83C\uDF10";
  }

  // ─── Lazy loading ───────────────────────────────────────────
  let observer;
  function observeImages() {
    if (observer) observer.disconnect();
    observer = new IntersectionObserver(
      (entries) => {
        entries.forEach((entry) => {
          if (entry.isIntersecting) {
            const card = entry.target;

            // Handle images
            const img = card.querySelector("img[data-src]");
            if (img) {
              img.src = img.dataset.src;
              img.removeAttribute("data-src");
              img.onload = () => card.classList.remove("loading");
              img.onerror = () => {
                card.classList.remove("loading");
                img.style.opacity = "0.3";
              };
            }

            // Videos now use <img> with server-side thumbnails — no special handling needed

            observer.unobserve(card);
          }
        });
      },
      { rootMargin: "200px" }
    );

    document.querySelectorAll("#media-grid .media-card.loading").forEach((card) => {
      // Skip cards inside collapsed week groups (will be observed when expanded)
      const weekGroup = card.closest(".week-group");
      if (weekGroup && weekGroup.classList.contains("collapsed")) return;
      observer.observe(card);
    });
  }

  let memeObserver;
  function observeMemeImages() {
    if (memeObserver) memeObserver.disconnect();
    memeObserver = new IntersectionObserver(
      (entries) => {
        entries.forEach((entry) => {
          if (entry.isIntersecting) {
            const card = entry.target;
            const img = card.querySelector("img[data-src]");
            if (img) {
              img.src = img.dataset.src;
              img.removeAttribute("data-src");
              img.onload = () => card.classList.remove("loading");
              img.onerror = () => {
                card.classList.remove("loading");
                img.style.opacity = "0.3";
              };
            }
            memeObserver.unobserve(card);
          }
        });
      },
      { rootMargin: "200px" }
    );

    document.querySelectorAll("#memes-grid .media-card.loading").forEach((card) => {
      memeObserver.observe(card);
    });
  }

  // ─── Infinite scroll ───────────────────────────────────────
  function needsMoreContent() {
    return document.documentElement.scrollHeight <= window.innerHeight + 500;
  }

  function loadMoreIfNeeded() {
    const bottom = document.documentElement.scrollHeight - window.innerHeight - window.scrollY;
    if (bottom < 500 || needsMoreContent()) {
      if (activeTab === "media" && !loading && currentPage < totalPages) {
        currentPage++;
        loadMedia(true);
      } else if (activeTab === "memes" && !memeLoading && memePage < memeTotalPages) {
        memePage++;
        loadMemes(true);
      }
    }
  }

  function setupInfiniteScroll() {
    window.addEventListener("scroll", loadMoreIfNeeded);
  }

  // ─── Filters ────────────────────────────────────────────────
  function setupFilters() {
    [filterPlatform, filterProfile, filterRating, filterSort, filterSource].forEach((el) => {
      el.addEventListener("change", () => loadMedia());
    });

    let searchTimeout;
    filterSearch.addEventListener("input", () => {
      clearTimeout(searchTimeout);
      searchTimeout = setTimeout(() => loadMedia(), 400);
    });
  }

  // ─── Lightbox ───────────────────────────────────────────────
  function openLightbox(index) {
    currentIndex = index;
    lightbox.classList.add("active");
    document.body.style.overflow = "hidden";

    // Restore rating/comments visibility
    document.querySelector(".rating-section").style.display = "";
    document.querySelector(".comments-section").style.display = "";

    renderLightbox();
  }

  function closeLightbox() {
    lightbox.classList.remove("active");
    document.body.style.overflow = "";
    currentIndex = -1;
    // Stop any playing video
    const vid = lbMedia.querySelector("video");
    if (vid) vid.pause();

    // Reset post link
    lbPostLink.removeAttribute("download");
  }

  function navigateLightbox(dir) {
    const items = activeTab === "media" ? mediaItems : memeItems;
    const newIdx = currentIndex + dir;
    if (newIdx < 0 || newIdx >= items.length) return;
    // Stop current video
    const vid = lbMedia.querySelector("video");
    if (vid) vid.pause();
    currentIndex = newIdx;

    if (activeTab === "memes") {
      openMemeLightbox(currentIndex);
    } else {
      renderLightbox();
    }
  }

  async function renderLightbox() {
    const item = mediaItems[currentIndex];
    if (!item) return;

    // Media
    const src = item.file_url || item.media_url || "";
    if (item.media_type === "video") {
      lbMedia.innerHTML = `<video src="${escHtml(src)}" controls autoplay playsinline style="max-width:100%;max-height:100vh;"></video>`;
    } else {
      lbMedia.innerHTML = `<img src="${escHtml(src)}" alt="${escHtml(item.caption || "")}" style="max-width:100%;max-height:100vh;">`;
    }

    // Info
    const dateStr = item.posted_at ? new Date(item.posted_at * 1000).toLocaleDateString("fr-FR") : "";
    lbInfo.textContent = `${platformEmoji(item.platform)} ${item.platform} ${dateStr ? "• " + dateStr : ""}`;

    // Caption
    lbCaption.textContent = item.caption || "";

    // Post link
    if (item.post_url) {
      lbPostLink.href = item.post_url;
      lbPostLink.textContent = "Voir le post original \u2197";
      lbPostLink.removeAttribute("download");
      lbPostLink.style.display = "";
    } else {
      lbPostLink.style.display = "none";
    }

    // Edit in editor button
    let editBtn = document.getElementById("lb-edit-btn");
    if (!editBtn) {
      editBtn = document.createElement("a");
      editBtn.id = "lb-edit-btn";
      editBtn.className = "post-link";
      editBtn.style.cssText = "display: inline-block; margin-top: 6px; background: #E21B3C; color: #fff; padding: 6px 14px; border-radius: 6px; text-decoration: none; font-size: 13px; font-weight: 600;";
      lbPostLink.parentNode.insertBefore(editBtn, lbPostLink.nextSibling);
    }
    editBtn.href = `/editor?media_id=${item.id}`;
    editBtn.textContent = "\uD83C\uDFA8 Editer dans le Meme Editor";
    editBtn.style.display = "";

    // Load full details (comments + ratings)
    await loadMediaDetail(item.id);
  }

  async function loadMediaDetail(mediaId) {
    try {
      const res = await fetch(`${API}/media/${mediaId}`);
      const detail = await res.json();

      // Rating stars
      renderStars(detail);

      // Comments
      renderComments(detail.comments, mediaId);
    } catch (e) {
      console.error("Failed to load media detail", e);
    }
  }

  // ─── Rating ─────────────────────────────────────────────────
  function renderStars(detail) {
    const myRating = detail.ratings.find((r) => r.user_name === userName);
    const myValue = myRating ? myRating.rating : 0;

    lbStars.innerHTML = "";
    for (let i = 1; i <= 5; i++) {
      const star = document.createElement("span");
      star.className = "star" + (i <= myValue ? " active" : "");
      star.textContent = "\u2605";
      star.onclick = () => rateMedia(detail.id, i);
      star.onmouseenter = () => highlightStars(i);
      star.onmouseleave = () => highlightStars(myValue);
      lbStars.appendChild(star);
    }

    lbRatingInfo.innerHTML = detail.avg_rating > 0
      ? `<span class="avg-value">${detail.avg_rating}</span> / 5 (${detail.rating_count || detail.ratings.length} votes)`
      : "Pas encore note";
  }

  function highlightStars(n) {
    const stars = lbStars.querySelectorAll(".star");
    stars.forEach((s, i) => {
      s.classList.toggle("active", i < n);
    });
  }

  async function rateMedia(mediaId, rating) {
    try {
      const res = await fetch(`${API}/media/${mediaId}/rate`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ user_name: userName, rating }),
      });
      const data = await res.json();

      // Update the grid card's rating badge
      if (mediaItems[currentIndex]) {
        mediaItems[currentIndex].avg_rating = data.avg_rating;
        mediaItems[currentIndex].rating_count = data.rating_count;
      }

      // Re-render with fresh data
      await loadMediaDetail(mediaId);
    } catch (e) {
      console.error("Failed to rate", e);
    }
  }

  // ─── Comments ───────────────────────────────────────────────
  function renderComments(comments, mediaId) {
    if (!comments || comments.length === 0) {
      lbComments.innerHTML = '<div class="comment-empty">Aucun commentaire. Sois le premier !</div>';
    } else {
      lbComments.innerHTML = comments
        .map(
          (c) => `
        <div class="comment-item">
          <div class="comment-header">
            <span class="comment-author">${escHtml(c.user_name)}</span>
            <span>
              <span class="comment-date">${formatDate(c.created_at)}</span>
              ${c.user_name === userName ? `<button class="comment-delete" onclick="window.__deleteComment(${mediaId}, ${c.id})">\u2716</button>` : ""}
            </span>
          </div>
          <div class="comment-text">${escHtml(c.text)}</div>
        </div>
      `
        )
        .join("");
    }

    // Wire up comment form
    lbCommentBtn.onclick = () => submitComment(mediaId);
    lbCommentInput.onkeydown = (e) => {
      if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        submitComment(mediaId);
      }
    };
  }

  async function submitComment(mediaId) {
    const text = lbCommentInput.value.trim();
    if (!text) return;

    try {
      await fetch(`${API}/media/${mediaId}/comment`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ user_name: userName, text }),
      });
      lbCommentInput.value = "";

      // Update comment count in grid
      if (mediaItems[currentIndex]) {
        mediaItems[currentIndex].comment_count++;
      }

      await loadMediaDetail(mediaId);
    } catch (e) {
      console.error("Failed to add comment", e);
    }
  }

  window.__deleteComment = async function (mediaId, commentId) {
    try {
      await fetch(`${API}/media/${mediaId}/comment/${commentId}?user_name=${encodeURIComponent(userName)}`, {
        method: "DELETE",
      });

      if (mediaItems[currentIndex]) {
        mediaItems[currentIndex].comment_count = Math.max(0, mediaItems[currentIndex].comment_count - 1);
      }

      await loadMediaDetail(mediaId);
    } catch (e) {
      console.error("Failed to delete comment", e);
    }
  };

  // ─── Keyboard ───────────────────────────────────────────────
  function setupKeyboard() {
    document.addEventListener("keydown", (e) => {
      if (!lightbox.classList.contains("active")) return;

      // Don't capture when typing in input
      if (e.target.tagName === "INPUT" || e.target.tagName === "TEXTAREA") return;

      switch (e.key) {
        case "Escape":
          closeLightbox();
          break;
        case "ArrowLeft":
          navigateLightbox(-1);
          break;
        case "ArrowRight":
          navigateLightbox(1);
          break;
        case "1":
        case "2":
        case "3":
        case "4":
        case "5":
          if (activeTab === "media" && mediaItems[currentIndex]) {
            rateMedia(mediaItems[currentIndex].id, parseInt(e.key));
          }
          break;
      }
    });
  }

  // ─── Week separator helpers ────────────────────────────────
  let lastWeekKey = "";  // Tracks last week rendered (for append mode)
  let currentWeekGroup = null; // Current week-group container

  // Track collapsed weeks in localStorage
  const collapsedWeeks = new Set(
    JSON.parse(localStorage.getItem("viewer_collapsed_weeks") || "[]")
  );
  function saveCollapsedWeeks() {
    localStorage.setItem("viewer_collapsed_weeks", JSON.stringify([...collapsedWeeks]));
  }

  function getWeekMonday(ts) {
    if (!ts) return null;
    const d = new Date(ts * 1000);
    const day = d.getDay();
    const diff = d.getDate() - day + (day === 0 ? -6 : 1); // Monday
    const monday = new Date(d);
    monday.setDate(diff);
    monday.setHours(0, 0, 0, 0);
    return monday;
  }

  function weekKey(ts) {
    const mon = getWeekMonday(ts);
    if (!mon) return "unknown";
    return mon.toISOString().slice(0, 10);
  }

  function weekLabel(ts) {
    const mon = getWeekMonday(ts);
    if (!mon) return "Date inconnue";
    return "Semaine du " + mon.toLocaleDateString("fr-FR", {
      day: "numeric", month: "long", year: "numeric"
    });
  }

  function countWeekItems(wk) {
    return mediaItems.filter(item => {
      const ts = item.posted_at || item.discovered_at;
      return weekKey(ts) === wk;
    }).length;
  }

  function createWeekSeparator(ts, wk) {
    const sep = document.createElement("div");
    sep.className = "week-separator";
    sep.dataset.weekKey = wk;

    const isCollapsed = collapsedWeeks.has(wk);
    if (isCollapsed) sep.classList.add("collapsed");

    // Arrow icon
    const arrow = document.createElement("span");
    arrow.className = "week-arrow";
    sep.appendChild(arrow);

    // Label
    const label = document.createElement("span");
    label.className = "week-label";
    label.textContent = weekLabel(ts);
    sep.appendChild(label);

    // Media count badge (updated later)
    const badge = document.createElement("span");
    badge.className = "week-count";
    sep.appendChild(badge);

    // Toggle on click
    sep.onclick = () => toggleWeekGroup(wk);

    return sep;
  }

  function createWeekGroup(wk) {
    const group = document.createElement("div");
    group.className = "week-group";
    group.dataset.weekKey = wk;
    if (collapsedWeeks.has(wk)) group.classList.add("collapsed");
    return group;
  }

  function toggleWeekGroup(wk) {
    const sep = grid.querySelector(`.week-separator[data-week-key="${wk}"]`);
    const group = grid.querySelector(`.week-group[data-week-key="${wk}"]`);
    if (!sep || !group) return;

    const isCollapsed = group.classList.toggle("collapsed");
    sep.classList.toggle("collapsed", isCollapsed);

    if (isCollapsed) {
      collapsedWeeks.add(wk);
    } else {
      collapsedWeeks.delete(wk);
      // Re-observe lazy images that were hidden
      observeImages();
    }
    saveCollapsedWeeks();
  }

  function updateWeekCounts() {
    grid.querySelectorAll(".week-separator").forEach(sep => {
      const wk = sep.dataset.weekKey;
      const group = grid.querySelector(`.week-group[data-week-key="${wk}"]`);
      const badge = sep.querySelector(".week-count");
      if (group && badge) {
        const count = group.querySelectorAll(".media-card").length;
        badge.textContent = count + " media" + (count > 1 ? "s" : "");
      }
    });
  }

  // ─── Helpers ────────────────────────────────────────────────
  function escHtml(str) {
    if (!str) return "";
    const div = document.createElement("div");
    div.textContent = str;
    return div.innerHTML;
  }

  function formatDate(ts) {
    if (!ts) return "";
    return new Date(ts * 1000).toLocaleDateString("fr-FR", {
      day: "2-digit",
      month: "short",
      hour: "2-digit",
      minute: "2-digit",
    });
  }

  // ─── Zoom ──────────────────────────────────────────────────

  function initZoom() {
    const saved = localStorage.getItem("viewer_zoom");
    if (saved) {
      setZoom(parseInt(saved));
      const slider = document.getElementById("zoom-slider");
      if (slider) slider.value = saved;
    }
  }

  function setZoom(size) {
    size = Math.max(60, Math.min(300, parseInt(size)));
    document.documentElement.style.setProperty("--grid-size", size + "px");
    localStorage.setItem("viewer_zoom", size);
    // When zooming out, grid may need more content to fill viewport
    requestAnimationFrame(() => loadMoreIfNeeded());
  }

  // ─── Selection mode ─────────────────────────────────────────

  function toggleSelectionMode() {
    selectionMode = !selectionMode;
    selectedIds.clear();

    const toolbar = document.getElementById("selection-toolbar");
    const selectBtn = document.getElementById("btn-select-mode");
    const cards = document.querySelectorAll("#media-grid .media-card");

    if (selectionMode) {
      toolbar.style.display = "flex";
      selectBtn.textContent = "Annuler";
      selectBtn.classList.add("active");
      document.body.classList.add("selection-active");
    } else {
      toolbar.style.display = "none";
      selectBtn.textContent = "\u2714 Selectionner";
      selectBtn.classList.remove("active");
      document.body.classList.remove("selection-active");
    }

    // Update all cards visual state
    cards.forEach((card) => {
      card.classList.remove("selected");
      const cb = card.querySelector(".card-checkbox svg");
      if (cb) {
        cb.innerHTML = '<circle cx="12" cy="12" r="10" fill="rgba(0,0,0,0.4)" stroke="white" stroke-width="2"/>';
      }
    });

    updateSelectionCount();
  }

  function toggleCardSelection(mediaId, card) {
    if (selectedIds.has(mediaId)) {
      selectedIds.delete(mediaId);
      card.classList.remove("selected");
      const cb = card.querySelector(".card-checkbox svg");
      if (cb) cb.innerHTML = '<circle cx="12" cy="12" r="10" fill="rgba(0,0,0,0.4)" stroke="white" stroke-width="2"/>';
    } else {
      selectedIds.add(mediaId);
      card.classList.add("selected");
      const cb = card.querySelector(".card-checkbox svg");
      if (cb) cb.innerHTML = '<circle cx="12" cy="12" r="10" fill="#E21B3C" stroke="white" stroke-width="2"/><path d="M8 12l3 3 5-5" stroke="white" stroke-width="2" fill="none" stroke-linecap="round" stroke-linejoin="round"/>';
    }
    updateSelectionCount();
  }

  function selectAllVisible() {
    const cards = document.querySelectorAll("#media-grid .media-card");
    cards.forEach((card) => {
      const id = parseInt(card.dataset.mediaId);
      if (id && !selectedIds.has(id)) {
        selectedIds.add(id);
        card.classList.add("selected");
        const cb = card.querySelector(".card-checkbox svg");
        if (cb) cb.innerHTML = '<circle cx="12" cy="12" r="10" fill="#E21B3C" stroke="white" stroke-width="2"/><path d="M8 12l3 3 5-5" stroke="white" stroke-width="2" fill="none" stroke-linecap="round" stroke-linejoin="round"/>';
      }
    });
    updateSelectionCount();
  }

  function clearSelection() {
    selectedIds.clear();
    const cards = document.querySelectorAll("#media-grid .media-card");
    cards.forEach((card) => {
      card.classList.remove("selected");
      const cb = card.querySelector(".card-checkbox svg");
      if (cb) cb.innerHTML = '<circle cx="12" cy="12" r="10" fill="rgba(0,0,0,0.4)" stroke="white" stroke-width="2"/>';
    });
    updateSelectionCount();
  }

  function updateSelectionCount() {
    const countEl = document.getElementById("selection-count");
    const deleteBtn = document.getElementById("btn-delete-selected");
    if (countEl) countEl.textContent = selectedIds.size + " selectionne" + (selectedIds.size > 1 ? "s" : "");
    if (deleteBtn) deleteBtn.disabled = selectedIds.size === 0;
  }

  async function deleteSelected() {
    if (selectedIds.size === 0) return;

    const count = selectedIds.size;
    if (!confirm("Supprimer " + count + " media" + (count > 1 ? "s" : "") + " ?\n\nCette action est irreversible.")) {
      return;
    }

    const deleteBtn = document.getElementById("btn-delete-selected");
    if (deleteBtn) {
      deleteBtn.disabled = true;
      deleteBtn.textContent = "Suppression...";
    }

    try {
      const res = await fetch(API + "/media/batch", {
        method: "DELETE",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ids: Array.from(selectedIds) }),
      });
      const data = await res.json();

      if (data.error) {
        alert("Erreur: " + data.error);
        return;
      }

      // Remove deleted cards from DOM
      selectedIds.forEach((id) => {
        const card = document.querySelector('#media-grid .media-card[data-media-id="' + id + '"]');
        if (card) card.remove();
      });

      // Remove from local state
      mediaItems = mediaItems.filter((item) => !selectedIds.has(item.id));

      // Exit selection mode
      toggleSelectionMode();

      // Reload to get fresh data
      loadMedia();
    } catch (e) {
      console.error("Failed to delete", e);
      alert("Erreur lors de la suppression");
    } finally {
      if (deleteBtn) {
        deleteBtn.disabled = false;
        deleteBtn.textContent = "\uD83D\uDDD1 Supprimer";
      }
    }
  }

  // ─── Global handlers ───────────────────────────────────────
  window.__closeLightbox = closeLightbox;
  window.__navLightbox = navigateLightbox;
  window.__switchTab = switchTab;
  window.__setZoom = setZoom;
  window.__toggleSelectionMode = toggleSelectionMode;
  window.__selectAllVisible = selectAllVisible;
  window.__clearSelection = clearSelection;
  window.__deleteSelected = deleteSelected;
  window.__changeUser = function () {
    promptUserName();
    if (currentIndex >= 0 && mediaItems[currentIndex]) {
      loadMediaDetail(mediaItems[currentIndex].id);
    }
  };

  // ─── Boot ───────────────────────────────────────────────────
  document.addEventListener("DOMContentLoaded", init);
})();
