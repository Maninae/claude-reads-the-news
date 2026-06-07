// Theme handling for The Watcher.
//
// Loaded synchronously in <head> (no defer) so the anti-FOUC step
// runs before first paint. This file replaces the previous inline
// <script> block, which was blocked by our Content-Security-Policy
// (script-src 'self', no 'unsafe-inline').
//
// Two responsibilities:
//   1. Set data-theme on <html> before paint, based on localStorage.
//      Defaults to "light" for first-time visitors.
//   2. After DOMContentLoaded, wire up the theme toggle button.

(function () {
  var root = document.documentElement;

  // --- 1. Anti-FOUC: apply stored theme immediately ---
  var stored;
  try {
    stored = localStorage.getItem('theme');
  } catch (e) {
    // localStorage can throw in private mode / sandboxed iframes.
    stored = null;
  }
  var initial = stored === 'dark' ? 'dark' : 'light';
  root.setAttribute('data-theme', initial);

  // --- 2. Wire up the toggle once the DOM is ready ---
  function wireToggle() {
    var toggle = document.getElementById('theme-toggle');
    if (!toggle) return;

    function apply(theme) {
      root.setAttribute('data-theme', theme);
      try { localStorage.setItem('theme', theme); } catch (e) { /* ignore */ }
      toggle.textContent = theme === 'dark' ? '☀' : '☾'; // ☀ / ☾
      toggle.setAttribute(
        'aria-label',
        theme === 'dark' ? 'Switch to light mode' : 'Switch to dark mode'
      );
      toggle.setAttribute('aria-pressed', theme === 'dark' ? 'true' : 'false');
    }

    toggle.addEventListener('click', function () {
      var next = root.getAttribute('data-theme') === 'dark' ? 'light' : 'dark';
      apply(next);
    });

    apply(root.getAttribute('data-theme') || 'light');
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', wireToggle);
  } else {
    wireToggle();
  }
})();
