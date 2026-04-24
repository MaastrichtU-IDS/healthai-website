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

  return { load, loadAll, initials, tag, levelClass, revealNew };
})();
