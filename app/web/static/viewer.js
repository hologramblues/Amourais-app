/* SAMOURAIS SCRAPPER — Media Viewer */

(function () {
  "use strict";

  // ─── State ───────────────────────────────────────────────────
  let mediaItems = [];
  let currentIndex = -1;
  let currentPage = 1;
  let totalPages = 1;
  let loading = false;
  let userName = localStorage.getItem("viewer_user") || "";

  const API = "/api/viewer";

  // ─── DOM refs ────────────────────────────────────────────────
  const grid = document.getElementById("media-grid");
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
  const filterSearch = document.getElementById("filter-search");
  const statsEl = document.getElementById("topbar-stats");
  const userNameEl = document.getElementById("user-name-display");

  // ─── Init ────────────────────────────────────────────────────
  function init() {
    if (!userName) {
      promptUserName();
    } else {
      userNameEl.textContent = userName;
    }

    loadProfiles();
    loadMedia();
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
    if (filterSearch.value) params.set("search", filterSearch.value);

    try {
      const res = await fetch(`${API}/media?${params}`);
      const data = await res.json();

      totalPages = data.total_pages;

      if (!append) {
        grid.innerHTML = "";
        mediaItems = [];
      }

      const startIdx = mediaItems.length;
      mediaItems.push(...data.items);

      data.items.forEach((item, i) => {
        grid.appendChild(createCard(item, startIdx + i));
      });

      // Lazy load images
      observeImages();

      // Stats
      statsEl.textContent = `${data.total} medias`;

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
  }

  // ─── Card creation ──────────────────────────────────────────
  function createCard(item, index) {
    const card = document.createElement("div");
    card.className = "media-card loading";
    card.dataset.index = index;
    card.onclick = () => openLightbox(index);

    // Determine thumbnail source
    const src = item.file_url || item.media_url || "";

    if (item.media_type === "video") {
      // For videos, show a poster placeholder or use file_url
      const img = document.createElement("img");
      img.dataset.src = src;
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
    return card;
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
            observer.unobserve(card);
          }
        });
      },
      { rootMargin: "200px" }
    );

    document.querySelectorAll(".media-card.loading").forEach((card) => {
      observer.observe(card);
    });
  }

  // ─── Infinite scroll ───────────────────────────────────────
  function setupInfiniteScroll() {
    window.addEventListener("scroll", () => {
      if (loading) return;
      if (currentPage >= totalPages) return;
      const bottom = document.documentElement.scrollHeight - window.innerHeight - window.scrollY;
      if (bottom < 500) {
        currentPage++;
        loadMedia(true);
      }
    });
  }

  // ─── Filters ────────────────────────────────────────────────
  function setupFilters() {
    [filterPlatform, filterProfile, filterRating, filterSort].forEach((el) => {
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
    renderLightbox();
  }

  function closeLightbox() {
    lightbox.classList.remove("active");
    document.body.style.overflow = "";
    currentIndex = -1;
    // Stop any playing video
    const vid = lbMedia.querySelector("video");
    if (vid) vid.pause();
  }

  function navigateLightbox(dir) {
    const newIdx = currentIndex + dir;
    if (newIdx < 0 || newIdx >= mediaItems.length) return;
    // Stop current video
    const vid = lbMedia.querySelector("video");
    if (vid) vid.pause();
    currentIndex = newIdx;
    renderLightbox();
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
      editBtn.style.cssText = "display: inline-block; margin-top: 6px; background: #ef4444; color: #fff; padding: 6px 14px; border-radius: 6px; text-decoration: none; font-size: 13px; font-weight: 600;";
      lbPostLink.parentNode.insertBefore(editBtn, lbPostLink.nextSibling);
    }
    editBtn.href = `/editor?media_id=${item.id}`;
    editBtn.textContent = "\uD83C\uDFA8 Editer dans le Meme Editor";

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
          if (mediaItems[currentIndex]) {
            rateMedia(mediaItems[currentIndex].id, parseInt(e.key));
          }
          break;
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

  // ─── Global handlers ───────────────────────────────────────
  window.__closeLightbox = closeLightbox;
  window.__navLightbox = navigateLightbox;
  window.__changeUser = function () {
    promptUserName();
    if (currentIndex >= 0 && mediaItems[currentIndex]) {
      loadMediaDetail(mediaItems[currentIndex].id);
    }
  };

  // ─── Boot ───────────────────────────────────────────────────
  document.addEventListener("DOMContentLoaded", init);
})();
