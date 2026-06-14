/**
 * VoiceUse Landing Page Scripts
 * Handles navigation, scroll animations, voice wave canvas, and interactions.
 */

(function () {
  'use strict';

  // ==========================================
  // Navigation
  // ==========================================
  const nav = document.getElementById('nav');
  const navToggle = document.getElementById('navToggle');
  const navLinks = document.getElementById('navLinks');

  // Add scroll class to nav
  function handleNavScroll() {
    if (window.scrollY > 50) {
      nav.classList.add('scrolled');
    } else {
      nav.classList.remove('scrolled');
    }
  }

  window.addEventListener('scroll', handleNavScroll, { passive: true });
  handleNavScroll();

  // Mobile nav toggle
  if (navToggle && navLinks) {
    navToggle.addEventListener('click', () => {
      const isOpen = navLinks.classList.toggle('open');
      navToggle.setAttribute('aria-expanded', isOpen);
    });

    // Close mobile nav when clicking a link
    navLinks.querySelectorAll('a').forEach(link => {
      link.addEventListener('click', () => {
        navLinks.classList.remove('open');
        navToggle.setAttribute('aria-expanded', 'false');
      });
    });
  }

  // ==========================================
  // Scroll Reveal Animation
  // ==========================================
  const revealElements = document.querySelectorAll(
    '.feature-card, .pipeline-step, .download-card, .safety-card, .plugin-mini, .plugin-highlight, .section-header'
  );

  revealElements.forEach((el, i) => {
    el.classList.add('reveal');
    // Stagger cards within their containers
    const delayClass = `reveal-delay-${(i % 5) + 1}`;
    el.classList.add(delayClass);
  });

  const revealObserver = new IntersectionObserver(
    (entries) => {
      entries.forEach(entry => {
        if (entry.isIntersecting) {
          entry.target.classList.add('visible');
          revealObserver.unobserve(entry.target);
        }
      });
    },
    { threshold: 0.1, rootMargin: '0px 0px -40px 0px' }
  );

  revealElements.forEach(el => revealObserver.observe(el));

  // ==========================================
  // Voice Wave Canvas Animation
  // ==========================================
  const canvas = document.getElementById('voiceWave');
  if (canvas) {
    const ctx = canvas.getContext('2d');
    let animationId;
    let time = 0;

    function resizeCanvas() {
      const dpr = window.devicePixelRatio || 1;
      const rect = canvas.getBoundingClientRect();
      canvas.width = rect.width * dpr;
      canvas.height = rect.height * dpr;
      ctx.scale(dpr, dpr);
    }

    function drawWave() {
      const width = canvas.getBoundingClientRect().width;
      const height = canvas.getBoundingClientRect().height;

      ctx.clearRect(0, 0, width, height);

      const barCount = 64;
      const barWidth = width / barCount * 0.6;
      const gap = width / barCount * 0.4;

      for (let i = 0; i < barCount; i++) {
        const x = i * (barWidth + gap) + gap / 2;
        const normalizedIndex = i / barCount;
        const centerDist = Math.abs(normalizedIndex - 0.5) * 2;
        const envelope = 1 - centerDist * centerDist;

        // Multiple sine waves for organic feel
        const wave1 = Math.sin(time * 2 + i * 0.3) * 0.5 + 0.5;
        const wave2 = Math.sin(time * 3.5 + i * 0.15) * 0.3 + 0.3;
        const wave3 = Math.sin(time * 1.2 + i * 0.5) * 0.2 + 0.2;

        const amplitude = (wave1 + wave2 + wave3) / 3;
        const barHeight = amplitude * envelope * height * 0.8;
        const y = (height - barHeight) / 2;

        // Gradient
        const gradient = ctx.createLinearGradient(0, y, 0, y + barHeight);
        gradient.addColorStop(0, 'rgba(0, 240, 255, 0)');
        gradient.addColorStop(0.5, 'rgba(0, 240, 255, 0.6)');
        gradient.addColorStop(1, 'rgba(0, 240, 255, 0)');

        ctx.fillStyle = gradient;
        ctx.fillRect(x, y, barWidth, barHeight);
      }

      time += 0.016;
      animationId = requestAnimationFrame(drawWave);
    }

    resizeCanvas();
    drawWave();

    window.addEventListener('resize', () => {
      resizeCanvas();
    });

    // Pause animation when not visible
    const canvasObserver = new IntersectionObserver(
      (entries) => {
        entries.forEach(entry => {
          if (entry.isIntersecting) {
            if (!animationId) drawWave();
          } else {
            cancelAnimationFrame(animationId);
            animationId = null;
          }
        });
      },
      { threshold: 0 }
    );

    canvasObserver.observe(canvas);
  }

  // ==========================================
  // Copy to Clipboard
  // ==========================================
  document.querySelectorAll('.copy-btn').forEach(btn => {
    btn.addEventListener('click', async () => {
      const targetId = btn.getAttribute('data-target');
      const targetEl = document.getElementById(targetId);
      if (!targetEl) return;

      try {
        await navigator.clipboard.writeText(targetEl.textContent);
        btn.classList.add('copied');
        setTimeout(() => btn.classList.remove('copied'), 1500);
      } catch (err) {
        // Fallback: select text
        const range = document.createRange();
        range.selectNode(targetEl);
        window.getSelection().removeAllRanges();
        window.getSelection().addRange(range);
        document.execCommand('copy');
        window.getSelection().removeAllRanges();
        btn.classList.add('copied');
        setTimeout(() => btn.classList.remove('copied'), 1500);
      }
    });
  });

  // ==========================================
  // Show Install Options Toggle
  // ==========================================
  const showInstall = document.getElementById('showInstall');
  const installOptions = document.getElementById('installOptions');

  if (showInstall && installOptions) {
    showInstall.addEventListener('click', (e) => {
      e.preventDefault();
      const isHidden = installOptions.style.display === 'none';
      installOptions.style.display = isHidden ? 'block' : 'none';
      showInstall.textContent = isHidden ? 'Hide install options' : 'More install options';

      if (isHidden) {
        // Trigger reveal animation for newly shown elements
        installOptions.querySelectorAll('.code-block').forEach((el, i) => {
          el.classList.add('reveal', `reveal-delay-${i + 1}`);
          setTimeout(() => el.classList.add('visible'), 50);
        });
      }
    });
  }

  // ==========================================
  // Smooth Scroll for Anchor Links
  // ==========================================
  document.querySelectorAll('a[href^="#"]').forEach(anchor => {
    anchor.addEventListener('click', function (e) {
      const href = this.getAttribute('href');
      if (href === '#') return;

      const target = document.querySelector(href);
      if (target) {
        e.preventDefault();
        target.scrollIntoView({ behavior: 'smooth' });
      }
    });
  });

})();