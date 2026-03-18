/* ============================================
   TheNightOps — Website Interactivity
   ============================================ */

(function () {
  'use strict';

  // ---- Stars Background ----
  function createStars() {
    const container = document.getElementById('stars');
    if (!container) return;
    const count = Math.min(120, Math.floor(window.innerWidth / 10));
    for (let i = 0; i < count; i++) {
      const star = document.createElement('div');
      star.className = 'star';
      star.style.left = Math.random() * 100 + '%';
      star.style.top = Math.random() * 100 + '%';
      star.style.setProperty('--duration', (2 + Math.random() * 4) + 's');
      star.style.setProperty('--max-opacity', (0.3 + Math.random() * 0.7).toString());
      star.style.animationDelay = Math.random() * 4 + 's';
      star.style.width = (1 + Math.random() * 2) + 'px';
      star.style.height = star.style.width;
      container.appendChild(star);
    }
  }

  // ---- Navbar Scroll ----
  function initNavbar() {
    const navbar = document.getElementById('navbar');
    if (!navbar) return;
    let ticking = false;
    window.addEventListener('scroll', function () {
      if (!ticking) {
        requestAnimationFrame(function () {
          navbar.classList.toggle('scrolled', window.scrollY > 50);
          ticking = false;
        });
        ticking = true;
      }
    });
  }

  // ---- Mobile Nav Toggle ----
  function initMobileNav() {
    const toggle = document.getElementById('navToggle');
    const links = document.getElementById('navLinks');
    if (!toggle || !links) return;

    toggle.addEventListener('click', function () {
      links.classList.toggle('open');
    });

    links.querySelectorAll('.nav-link').forEach(function (link) {
      link.addEventListener('click', function () {
        links.classList.remove('open');
      });
    });
  }

  // ---- Architecture Tabs ----
  function initArchTabs() {
    var tabs = document.querySelectorAll('.arch-tab');
    tabs.forEach(function (tab) {
      tab.addEventListener('click', function () {
        var target = this.getAttribute('data-tab');
        tabs.forEach(function (t) { t.classList.remove('active'); });
        this.classList.add('active');
        document.querySelectorAll('.arch-panel').forEach(function (p) {
          p.classList.remove('active');
        });
        var panel = document.getElementById(target);
        if (panel) panel.classList.add('active');
      });
    });
  }

  // ---- Scroll Animations ----
  function initScrollAnimations() {
    var elements = document.querySelectorAll('[data-aos]');
    if (!elements.length) return;

    var observer = new IntersectionObserver(function (entries) {
      entries.forEach(function (entry) {
        if (entry.isIntersecting) {
          entry.target.classList.add('visible');
          observer.unobserve(entry.target);
        }
      });
    }, {
      threshold: 0.1,
      rootMargin: '0px 0px -40px 0px'
    });

    elements.forEach(function (el) { observer.observe(el); });
  }

  // ---- Terminal Typing Animation ----
  function initTerminalAnimation() {
    var body = document.getElementById('terminalBody');
    if (!body) return;

    var lines = [
      { type: 'output', text: '' },
      { type: 'info', text: '🌙 TheNightOps v0.1.0b1 — Autonomous SRE Agent' },
      { type: 'output', text: '' },
      { type: 'output', text: '[Phase 1/4] Triage — checking pod status...' },
      { type: 'success', text: '  Found: pod/api-server-7d4f9 OOMKilled (exit 137)' },
      { type: 'output', text: '[Phase 2/4] Deep Investigation — querying logs & events...' },
      { type: 'warn', text: '  Memory spike: 128Mi -> 512Mi in 45s (limit: 256Mi)' },
      { type: 'output', text: '[Phase 3/4] Synthesis — correlating findings...' },
      { type: 'success', text: '  Root cause: memory leak in v2.1.3 (deployed 2h ago)' },
      { type: 'output', text: '[Phase 4/4] RCA + Remediation' },
      { type: 'success', text: '  ✓ RCA generated (confidence: 92%)' },
      { type: 'info', text: '  → Recommended: rollback to v2.1.2' },
      { type: 'output', text: '' },
      { type: 'success', text: '✓ Investigation complete — MTTR: 3m 12s' },
    ];

    var delay = 1200;
    var interval = 350;

    lines.forEach(function (line, i) {
      setTimeout(function () {
        var div = document.createElement('div');
        div.className = 'terminal-line ' + line.type;
        div.textContent = line.text;
        body.appendChild(div);
        body.scrollTop = body.scrollHeight;
      }, delay + i * interval);
    });
  }

  // ---- Smooth Scroll for anchor links ----
  function initSmoothScroll() {
    document.querySelectorAll('a[href^="#"]').forEach(function (link) {
      link.addEventListener('click', function (e) {
        var target = document.querySelector(this.getAttribute('href'));
        if (target) {
          e.preventDefault();
          var offset = 80;
          var top = target.getBoundingClientRect().top + window.pageYOffset - offset;
          window.scrollTo({ top: top, behavior: 'smooth' });
        }
      });
    });
  }

  // ---- Active nav link highlight ----
  function initActiveNavHighlight() {
    var sections = document.querySelectorAll('.section[id], .hero[id]');
    var navLinks = document.querySelectorAll('.nav-link[href^="#"]');
    if (!sections.length || !navLinks.length) return;

    var observer = new IntersectionObserver(function (entries) {
      entries.forEach(function (entry) {
        if (entry.isIntersecting) {
          var id = entry.target.getAttribute('id');
          navLinks.forEach(function (link) {
            link.style.color = '';
            if (link.getAttribute('href') === '#' + id) {
              link.style.color = 'var(--accent-blue)';
            }
          });
        }
      });
    }, {
      threshold: 0.2,
      rootMargin: '-80px 0px -50% 0px'
    });

    sections.forEach(function (s) { observer.observe(s); });
  }

  // ---- Init Everything ----
  document.addEventListener('DOMContentLoaded', function () {
    createStars();
    initNavbar();
    initMobileNav();
    initArchTabs();
    initScrollAnimations();
    initTerminalAnimation();
    initSmoothScroll();
    initActiveNavHighlight();
  });
})();
