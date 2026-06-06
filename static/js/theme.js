(function () {
  var toggle = document.getElementById('theme-toggle');
  if (!toggle) return;

  function apply(theme) {
    document.documentElement.setAttribute('data-theme', theme);
    localStorage.setItem('theme', theme);
    toggle.textContent = theme === 'dark' ? '☀' : '☽';
    toggle.setAttribute('aria-label',
      theme === 'dark' ? 'Switch to light mode' : 'Switch to dark mode');
  }

  toggle.addEventListener('click', function () {
    var next = document.documentElement.getAttribute('data-theme') === 'dark' ? 'light' : 'dark';
    apply(next);
  });

  var current = document.documentElement.getAttribute('data-theme') || 'light';
  apply(current);
})();
