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

  // ── Publication Browser ──────────────────────────────────────────────────
  const PubBrowser = (() => {
    let _pubs = [], _type = 'All', _chartMode = 'publications';
    let _yearMin = null, _yearMax = null, _page = 1, _opts = {};

    const _fmt = n => Number(n).toLocaleString();

    function _hIdx(arr) {
      const s = arr.slice().sort((a, b) => b - a);
      let h = 0;
      for (let i = 0; i < s.length; i++) { if (s[i] >= i + 1) h = i + 1; else break; }
      return h;
    }

    function _hl(authors) {
      const name = _opts.highlightName;
      if (!name || !authors) return authors || '';
      const e = name.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
      return authors.replace(new RegExp(`(${e})`, 'gi'), '<strong style="color:var(--slate);font-weight:700">$1</strong>');
    }

    function _vancouver(p) {
      const parts = [];
      if (p.authors) parts.push(p.authors + '.');
      if (p.title)   parts.push(p.title + '.');
      if (p.venue)   parts.push(p.venue + '.');
      parts.push(String(p.year) + '.');
      if (p.doi)     parts.push('doi:' + p.doi);
      return parts.join(' ');
    }

    function _bibtex(p) {
      const tm = { 'Journal':'article','Conference':'inproceedings','Book Chapter':'incollection','Book':'book','Preprint':'misc','Report':'techreport','Dataset':'misc','Thesis':'phdthesis' };
      const et = tm[p.type] || 'misc';
      const fa = (p.authors||'').split(',')[0].trim().split(' ').pop();
      const tw = (p.title||'').split(/\s+/).find(w => w.length > 3) || '';
      const key = (fa + p.year + tw).replace(/[^a-zA-Z0-9]/g, '');
      const vf = et === 'article' ? 'journal' : et === 'inproceedings' ? 'booktitle' : 'publisher';
      const lines = [`@${et}{${key},`];
      if (p.authors) lines.push(`  author    = {${p.authors}},`);
      if (p.title)   lines.push(`  title     = {${p.title}},`);
      if (p.venue)   lines.push(`  ${vf.padEnd(9)} = {${p.venue}},`);
      lines.push(`  year      = {${p.year}},`);
      if (p.doi)     lines.push(`  doi       = {${p.doi}},`);
      lines.push('}');
      return lines.join('\n');
    }

    function citeToClipboard(btn) {
      navigator.clipboard.writeText(btn.dataset.citation).then(() => {
        const orig = btn.innerHTML;
        btn.innerHTML = '✓ Copied!'; btn.classList.add('pub-action-success');
        setTimeout(() => { btn.innerHTML = orig; btn.classList.remove('pub-action-success'); }, 2000);
      });
    }

    function _sliderUpdate() {
      const mn = document.getElementById('_pbYMin'), mx = document.getElementById('_pbYMax');
      if (!mn) return;
      const lo = +mn.value, hi = +mx.value, tot = (+mn.max - +mn.min) || 1;
      const lp = (lo - +mn.min) / tot * 100, rp = (hi - +mn.min) / tot * 100;
      document.getElementById('_pbRFill').style.cssText = `left:${lp}%;width:${rp - lp}%`;
      document.getElementById('_pbRVal').textContent = lo === hi ? lo : `${lo} – ${hi}`;
    }

    function _renderStats(filtered) {
      const totC = _pubs.reduce((s, p) => s + (p.citations || 0), 0);
      const fltC = filtered.reduce((s, p) => s + (p.citations || 0), 0);
      const h = _hIdx(filtered.map(p => p.citations || 0));
      const isFlt = filtered.length !== _pubs.length;
      const num = (v, t) => isFlt ? `${_fmt(v)}<span class="denom">/ ${_fmt(t)}</span>` : _fmt(t);
      let html = `
        <div class="pub-stat"><div class="pub-stat-val">${num(filtered.length, _pubs.length)}</div><div class="pub-stat-lbl">${isFlt ? 'Matching' : 'Total'} publications</div></div>
        <div class="pub-stat"><div class="pub-stat-val">${num(fltC, totC)}</div><div class="pub-stat-lbl">${isFlt ? 'Citations in selection' : 'Total citations'}</div></div>
        <div class="pub-stat"><div class="pub-stat-val">${_fmt(h)}</div><div class="pub-stat-lbl">h-index${isFlt ? ' (selection)' : ''}</div></div>`;
      if (_opts.showThisYear) {
        const yr = new Date().getFullYear();
        html += `<div class="pub-stat"><div class="pub-stat-val">${_fmt(filtered.filter(p => p.year === yr).length)}</div><div class="pub-stat-lbl">${_opts.thisYearLabel || 'This year'}</div></div>`;
      }
      document.getElementById('_pbStats').innerHTML = html;
    }

    function _renderChart(filtered) {
      window._pbFlt = filtered;
      const ic = _chartMode === 'citations';
      const tog = `<div class="chart-toggle">
        <button class="chart-toggle-btn${!ic ? ' active' : ''}" onclick="CMS.PubBrowser.setChart('publications')">Publications</button>
        <button class="chart-toggle-btn${ic ? ' active' : ''}" onclick="CMS.PubBrowser.setChart('citations')">Citations</button>
      </div>`;
      const el = document.getElementById('_pbChart');
      if (!filtered.length) {
        el.innerHTML = `<div class="year-dist-header"><div class="year-dist-title">No data</div>${tog}</div>`;
        return;
      }
      const byY = {};
      filtered.forEach(p => {
        byY[p.year] = byY[p.year] || { pubs: 0, cites: 0 };
        byY[p.year].pubs++;
        byY[p.year].cites += (p.citations || 0);
      });
      const minY = Math.min(...filtered.map(p => p.year)), maxY = Math.max(...filtered.map(p => p.year));
      const years = []; for (let y = minY; y <= maxY; y++) years.push(y);
      const key = ic ? 'cites' : 'pubs';
      const dmax = Math.max(1, ...years.map(y => (byY[y] || {})[key] || 0));
      el.innerHTML = `
        <div class="year-dist-header"><div class="year-dist-title">${ic ? 'Citations' : 'Publications'} per year</div>${tog}</div>
        <div class="year-chart">${years.map(y => {
          const val = (byY[y] || {})[key] || 0, pct = Math.max(val > 0 ? 2 : 0, val / dmax * 100);
          return `<div class="year-bar-col" title="${y}: ${_fmt(val)} ${ic ? 'citations' : 'publications'}">
            <div class="year-bar-stack"><span class="year-bar-val">${val > 0 ? _fmt(val) : ''}</span><div class="year-bar-fill" style="height:${pct}%"></div></div>
            <div class="year-bar-label">${String(y).slice(2)}</div></div>`;
        }).join('')}</div>`;
    }

    function _renderPubs() {
      const q = ((document.getElementById('_pbSearch') || {}).value || '').toLowerCase();
      const om = _opts.orcidToName || {};
      const filtered = _pubs.filter(p => {
        const typeOk = _type === 'All' || p.type === _type;
        const yearOk = _yearMin === null || (p.year >= _yearMin && p.year <= _yearMax);
        const mn = (p.orcids || []).map(o => om[o] || '').join(' ');
        const hay = [p.title, p.authors, (p.tags || []).join(' '), mn, p.summary || '', p.abstract || ''].join(' ').toLowerCase();
        return typeOk && yearOk && (!q || hay.includes(q));
      }).sort((a, b) => b.year - a.year);

      _renderStats(filtered);
      _renderChart(filtered);

      const ps = _opts.pageSize || Infinity;
      const tp = ps === Infinity ? 1 : Math.max(1, Math.ceil(filtered.length / ps));
      if (_page > tp) _page = tp;
      const items = ps === Infinity ? filtered : filtered.slice((_page - 1) * ps, _page * ps);

      const byY = {};
      items.forEach(p => { (byY[p.year] = byY[p.year] || []).push(p); });
      const years = Object.keys(byY).sort((a, b) => b - a);

      const listEl = document.getElementById('_pbList');
      listEl.innerHTML = filtered.length ? years.map(y => `
        <div>
          <div class="year-heading">${y}</div>
          ${byY[y].map(p => `
            <div class="pub-row reveal">
              <div class="pub-year-lbl">${p.type === 'Preprint' ? '<span style="font-size:0.62rem;font-family:monospace;color:var(--text-light)">pre</span>' : ''}</div>
              <div>
                <a class="pub-title-link" href="${p.doi ? 'https://doi.org/' + p.doi : '#'}" target="_blank">${p.title}</a>
                <div class="pub-authors">${_hl(p.authors)}</div>
                <div class="pub-venue">${p.venue || ''}</div>
                ${p.summary ? `<div class="pub-summary">${p.summary}</div>` : ''}
                <div style="display:flex;gap:5px;flex-wrap:wrap;margin-bottom:4px">${(p.tags || []).map(t => `<span class="tag tag-muted">${t}</span>`).join('')}</div>
                <div class="pub-actions">
                  ${p.doi ? `<a class="pub-action" href="https://doi.org/${p.doi}" target="_blank">🔗 DOI</a>` : ''}
                  <button class="pub-action" onclick="CMS.PubBrowser.citeToClipboard(this)" data-citation="${_vancouver(p).replace(/"/g, '&quot;')}">📋 Cite</button>
                  <button class="pub-action" onclick="CMS.PubBrowser.citeToClipboard(this)" data-citation="${_bibtex(p).replace(/"/g, '&quot;')}">BibTeX</button>
                </div>
              </div>
            </div>`).join('')}
        </div>`).join('')
      : '<p style="text-align:center;padding:40px;color:var(--text-muted)">No publications match your search.</p>';

      revealNew(listEl);
      if (ps !== Infinity) _renderPagination(tp, filtered.length);
    }

    function _renderPagination(tp, total) {
      const el = document.getElementById('_pbPagination'), info = document.getElementById('_pbPageInfo');
      if (!el) return;
      const ps = _opts.pageSize || Infinity;
      const from = Math.min((_page - 1) * ps + 1, total), to = Math.min(_page * ps, total);
      if (info) info.textContent = total ? `Showing ${from}–${to} of ${_fmt(total)} publications` : '';
      if (tp <= 1) { el.innerHTML = ''; return; }
      const pages = _paginPages(_page, tp);
      let html = `<button class="page-btn" onclick="CMS.PubBrowser.goPage(${_page - 1})" ${_page === 1 ? 'disabled' : ''}>‹</button>`;
      pages.forEach(p => {
        if (p === '…') html += `<span class="page-btn" style="cursor:default;border:none">…</span>`;
        else html += `<button class="page-btn${p === _page ? ' active' : ''}" onclick="CMS.PubBrowser.goPage(${p})">${p}</button>`;
      });
      html += `<button class="page-btn" onclick="CMS.PubBrowser.goPage(${_page + 1})" ${_page === tp ? 'disabled' : ''}>›</button>`;
      el.innerHTML = html;
    }

    function _paginPages(cur, tot) {
      if (tot <= 7) return Array.from({ length: tot }, (_, i) => i + 1);
      const pages = []; const add = p => { if (!pages.includes(p)) pages.push(p); };
      add(1); if (cur > 3) pages.push('…');
      for (let p = Math.max(2, cur - 1); p <= Math.min(tot - 1, cur + 1); p++) add(p);
      if (cur < tot - 2) pages.push('…'); add(tot);
      return pages;
    }

    function init(container, pubs, opts = {}) {
      _pubs = pubs; _type = 'All'; _chartMode = 'publications'; _page = 1; _opts = opts;
      const ps = opts.pageSize || Infinity, hasPag = ps !== Infinity;
      let html = '';
      if (opts.sectionTitle) html += `<div class="profile-section-title">${opts.sectionTitle}</div>`;
      html += `
        <div class="pub-stats" id="_pbStats"></div>
        <div class="year-dist" id="_pbChart"></div>
        <div class="year-range-wrap hidden" id="_pbYRange">
          <div class="year-range-header">
            <span class="year-dist-title">Year range</span>
            <span class="year-range-label-val" id="_pbRVal"></span>
          </div>
          <div class="year-range-track">
            <div class="year-range-fill" id="_pbRFill"></div>
            <input type="range" id="_pbYMin" />
            <input type="range" id="_pbYMax" />
          </div>
          <div class="year-range-ticks"><span id="_pbTMin"></span><span id="_pbTMax"></span></div>
        </div>
        <div class="filter-bar">
          <input class="search-input" id="_pbSearch" type="text" placeholder="Search title, author or keyword…" oninput="CMS.PubBrowser.onSearch()" />
          <div class="filter-chips" id="_pbChips"></div>
        </div>
        <div id="_pbList"></div>
        ${hasPag ? '<div class="pagination" id="_pbPagination"></div><div class="page-info" id="_pbPageInfo"></div>' : ''}`;
      container.innerHTML = html;
      if (!pubs.length) return;

      const types = ['All', ...new Set(pubs.map(p => p.type).filter(Boolean))];
      document.getElementById('_pbChips').innerHTML = types.map(t =>
        `<button class="chip${t === 'All' ? ' active' : ''}" onclick="CMS.PubBrowser.setType('${t}')">${t}</button>`
      ).join('');

      const minY = Math.min(...pubs.map(p => p.year)), maxY = Math.max(...pubs.map(p => p.year));
      _yearMin = minY; _yearMax = maxY;
      const mn = document.getElementById('_pbYMin'), mx = document.getElementById('_pbYMax');
      mn.min = mx.min = minY; mn.max = mx.max = maxY; mn.value = minY; mx.value = maxY;
      document.getElementById('_pbTMin').textContent = minY;
      document.getElementById('_pbTMax').textContent = maxY;
      mn.oninput = () => { if (+mn.value > +mx.value) mn.value = mx.value; _yearMin = +mn.value; _page = 1; _sliderUpdate(); _renderPubs(); };
      mx.oninput = () => { if (+mx.value < +mn.value) mx.value = mn.value; _yearMax = +mx.value; _page = 1; _sliderUpdate(); _renderPubs(); };
      _sliderUpdate();
      document.getElementById('_pbYRange').classList.remove('hidden');

      if (opts.initialSearch) document.getElementById('_pbSearch').value = opts.initialSearch;

      _renderPubs();
    }

    function setType(t) {
      _type = t; _page = 1;
      document.querySelectorAll('#_pbChips .chip').forEach(c => c.classList.toggle('active', c.textContent === t));
      _renderPubs();
    }

    function setChart(mode) { _chartMode = mode; _renderChart(window._pbFlt || []); }

    function onSearch() { _page = 1; _renderPubs(); }

    function goPage(p) {
      _page = p; _renderPubs();
      const t = _opts.scrollTarget;
      if (t) { const el = typeof t === 'string' ? document.getElementById(t) : t; if (el) el.scrollIntoView({ behavior: 'smooth', block: 'start' }); }
    }

    return { init, setType, setChart, onSearch, goPage, citeToClipboard };
  })();

  return { load, loadAll, initials, tag, levelClass, revealNew,
           normalizeNewsItems, renderNewsItem, toggleSlides, PubBrowser };
})();
