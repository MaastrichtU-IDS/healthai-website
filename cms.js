/* ================================================================
   HAIR Group — Shared CMS Utility
   Loads and parses YAML content files using js-yaml.
   Exposes window.CMS for use in all pages.
================================================================ */

window.CMS = (function () {
  const cache = {};

  async function load(path) {
    if (cache[path]) return cache[path];
    const res = await fetch(path);
    if (!res.ok) throw new Error(`CMS: failed to load ${path} (${res.status})`);
    const text = await res.text();
    const parsed = jsyaml.load(text);
    cache[path] = parsed;
    return parsed;
  }

  async function loadAll(...paths) {
    return Promise.all(paths.map(load));
  }

  /* ── Render helpers ── */

  function initials(name) {
    return name.split(' ').map(w => w[0]).join('').slice(0, 2).toUpperCase();
  }

  function tag(label, variant = '') {
    const cls = variant === 'blue'  ? 'tag-blue'
               : variant === 'gold' ? 'tag-gold'
               : variant === 'muted'? 'tag-muted'
               : '';
    return `<span class="tag ${cls}">${label}</span>`;
  }

  function levelClass(level) {
    const l = (level || '').toLowerCase().replace(/\s.*/, '');
    return l === 'bsc' ? 'level-bsc' : l === 'msc' ? 'level-msc' : l === 'phd' ? 'level-phd' : 'level-muted';
  }

  /* Trigger scroll-reveal on newly added elements */
  function revealNew(container) {
    const els = (container || document).querySelectorAll('.reveal:not(.visible)');
    if (!els.length) return;
    const io = new IntersectionObserver((entries) => {
      entries.forEach(e => {
        if (e.isIntersecting) {
          const delay = parseInt(e.target.dataset.delay || 0);
          setTimeout(() => e.target.classList.add('visible'), delay);
          io.unobserve(e.target);
        }
      });
    }, { threshold: 0.1 });
    els.forEach(el => io.observe(el));
  }

  /* ── News / activity shared helpers ── */

  function normalizeNewsItems(data) {
    const toDate = s => s ? new Date(s) : new Date(0);
    const dp = s => { const p = (s || '').split(' '); return { day: p[0] || '', mon: p[1] || '' }; };
    return [
      ...(data.news || []).map(n => ({
        type: 'news', label: 'News',
        title: n.title, url: n.url || null,
        meta: [n.day, n.month, n.year].filter(Boolean).join(' '),
        excerpt: n.body || '', audio_url: null, slides_url: null,
        sortDate: toDate(`${n.day} ${n.month} ${n.year}`),
        day: n.day || '', mon: n.month || '',
        search: [n.title, n.body].join(' '),
      })),
      ...(data.blog || []).map(b => ({
        type: 'blog', label: 'Blog',
        title: b.title, url: b.url || null,
        meta: [b.author, b.tag, b.date].filter(Boolean).join(' · '),
        excerpt: b.excerpt || '', audio_url: null, slides_url: null,
        sortDate: toDate(b.date), ...dp(b.date),
        search: [b.title, b.excerpt, b.author, b.tag].join(' '),
      })),
      ...(data.talks || []).map(t => ({
        type: 'talk', label: 'Talk',
        title: t.title, url: t.url || null,
        slides_url: t.slides_url || null, audio_url: null,
        meta: [t.type, t.speaker, t.event, t.date].filter(Boolean).join(' · '),
        excerpt: t.description || '',
        sortDate: toDate(t.date), ...dp(t.date),
        search: [t.title, t.speaker, t.event, t.type, t.description].join(' '),
      })),
      ...(data.podcasts || []).map(p => ({
        type: 'podcast', label: 'Podcast',
        title: p.title, url: p.url || null,
        audio_url: p.audio_url || null, slides_url: null,
        meta: [p.show, p.guest, p.date, p.duration].filter(Boolean).join(' · '),
        excerpt: p.description || '',
        sortDate: toDate(p.date), ...dp(p.date),
        search: [p.title, p.show, p.guest, p.description].join(' '),
      })),
    ].sort((a, b) => b.sortDate - a.sortDate);
  }

  let _niCtr = 0;
  function renderNewsItem(item) {
    const sid = 'ni-slides-' + (_niCtr++);
    return `<div class="ni-row">
      <span class="ni-badge nitype-${item.type}">${item.label}</span>
      <div class="ni-body">
        <div class="ni-title">${item.url ? `<a href="${item.url}" target="_blank" rel="noopener">${item.title}</a>` : item.title}</div>
        <div class="ni-meta">${item.meta}</div>
        ${item.excerpt ? `<div class="ni-excerpt">${item.excerpt.trim()}</div>` : ''}
        ${item.audio_url ? `<audio controls preload="none" style="margin-top:8px;width:100%;max-width:480px"><source src="${item.audio_url}" type="audio/mp4"></audio>` : ''}
        ${item.slides_url ? `<button class="ni-slides-btn" onclick="CMS.toggleSlides(this,'${encodeURIComponent(item.slides_url)}','${sid}')">▶ View slides</button><div id="${sid}" style="display:none;margin-top:8px"><iframe src="" style="width:100%;height:480px;border:1px solid #e5e7eb;border-radius:6px" allowfullscreen></iframe></div>` : ''}
      </div>
    </div>`;
  }

  function toggleSlides(btn, encodedUrl, id) {
    const panel = document.getElementById(id);
    const open = panel.style.display === 'block';
    panel.style.display = open ? 'none' : 'block';
    btn.textContent = open ? '▶ View slides' : '▼ Hide slides';
    if (!open) {
      const iframe = panel.querySelector('iframe');
      if (!iframe.src || iframe.src === window.location.href) {
        iframe.src = 'https://docs.google.com/viewer?url=' + encodedUrl + '&embedded=true';
      }
    }
  }

  return { load, loadAll, initials, tag, levelClass, revealNew,
           normalizeNewsItems, renderNewsItem, toggleSlides };
})();
