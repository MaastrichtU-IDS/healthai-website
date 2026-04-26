/* Shared navigation + footer — Style C */
(function () {
  const NAV_LINKS = [
    { href: 'index.html',        label: 'Research',     special: false },
    { href: 'research.html',     label: 'Research',     special: false },
    { href: 'people.html',       label: 'People',       special: false },
    { href: 'publications.html', label: 'Publications', special: false },
    { href: 'news.html',        label: 'News',        special: false },
  ];

  const DISPLAY_LINKS = [
    { href: 'research.html',     label: 'Research'     },
    { href: 'projects.html',     label: 'Projects'     },
    { href: 'people.html',       label: 'People'       },
    { href: 'publications.html', label: 'Publications' },
    { href: 'news.html',         label: 'News'         },
  ];

  function buildNav() {
    const current = location.pathname.split('/').pop() || 'index.html';
    const linksHtml = DISPLAY_LINKS.map(l => {
      const active = l.href === current ? ' active' : '';
      const ctaCls = l.cta ? ' nav-cta' : '';
      return `<li><a href="${l.href}" class="${active}${ctaCls}">${l.label}</a></li>`;
    }).join('');

    const nav = document.createElement('nav');
    nav.className = 'site-nav';
    nav.id = 'site-nav';
    nav.innerHTML = `
      <div class="nav-inner">
        <a href="index.html" class="nav-logo">
          <div class="nav-logo-mark">✦</div>
          <div class="nav-logo-text">
            <span class="name">Health AI Research Group</span>
            <span class="dept">Maastricht University</span>
          </div>
        </a>
        <ul class="nav-links">${linksHtml}</ul>
      </div>`;
    document.body.insertBefore(nav, document.body.firstChild);
    window.addEventListener('scroll', () => {
      nav.classList.toggle('scrolled', window.scrollY > 12);
    });
  }

  function buildFooter() {
    const footer = `
    <footer class="site-footer">
      <div class="container">
        <div class="footer-inner">
          <div class="footer-brand">
            <div class="name">Health AI Research Group</div>
            <p>Department of Advanced Computing Sciences<br>Maastricht University<br>the Netherlands</p>
            <div style="margin-top:16px">
              <a href="mailto:hair@maastrichtuniversity.nl" style="color:rgba(255,255,255,0.45);font-size:0.82rem;transition:color 0.2s" onmouseover="this.style.color='#fff'" onmouseout="this.style.color='rgba(255,255,255,0.45)'">hair@maastrichtuniversity.nl</a>
            </div>
          </div>
          <div class="footer-col">
            <h5>Research</h5>
            <ul>
              <li><a href="research.html">Research Themes</a></li>
              <li><a href="projects.html">Projects</a></li>
              <li><a href="publications.html">Publications</a></li>
            </ul>
          </div>
          <div class="footer-col">
            <h5>Group</h5>
            <ul>
              <li><a href="people.html">People</a></li>
              <li><a href="news.html">News & Media</a></li>
            </ul>
          </div>
          <div class="footer-col">
            <h5>University</h5>
            <ul>
              <li><a href="https://www.maastrichtuniversity.nl" target="_blank">Maastricht University</a></li>
              <li><a href="https://maastrichtuniversity.nl/dacs" target="_blank">DACS</a></li>
            </ul>
          </div>
        </div>
        <div class="footer-bottom">
          <span>© 2026 Health AI Research Group · Maastricht University</span>
          <span style="display:flex;gap:18px">
            <a href="#">Privacy</a><a href="#">Accessibility</a><a href="#">Sitemap</a>
          </span>
        </div>
      </div>
    </footer>`;
    document.body.insertAdjacentHTML('beforeend', footer);
  }

  function initReveal() {
    const els = document.querySelectorAll('.reveal');
    if (!els.length) return;
    const io = new IntersectionObserver(entries => {
      entries.forEach(e => {
        if (e.isIntersecting) {
          setTimeout(() => e.target.classList.add('visible'), parseInt(e.target.dataset.delay || 0));
          io.unobserve(e.target);
        }
      });
    }, { threshold: 0.1 });
    els.forEach(el => io.observe(el));
  }

  document.addEventListener('DOMContentLoaded', () => {
    buildNav();
    buildFooter();
    initReveal();
  });
})();
