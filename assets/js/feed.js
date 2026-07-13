// Progressive reveal of server-rendered story cells on the home page.
//
// Why reveal (not fetch+inject):
//   Post titles/excerpts are derived from news content that is influenced by
//   external sources. Building DOM from JSON via innerHTML/template strings
//   would be an XSS hazard. Hugo already renders + escapes every cell on the
//   server; this file simply hides the overflow and re-reveals it in batches
//   as the user scrolls, so nothing untrusted is ever assembled client-side.
//
// No-JS fallback:
//   The grid ships with every cell visible. This script is what *hides* the
//   overflow. If JS is off, or if IntersectionObserver is unavailable, the
//   page degrades gracefully to "all cells visible" — no breakage.
//
// Scaling note:
//   "Render-all + reveal" is fine while the post count is in the low hundreds.
//   If the archive grows large enough that initial HTML size becomes a real
//   page-weight problem, migrate to paginated JSON endpoints (e.g. one Hugo
//   output format per page of N posts) and fetch them on scroll. Keep the same
//   "render server-side, never assemble from JSON on the client" rule: each
//   page of cells should be a pre-escaped HTML fragment, not raw data.

(function () {
  function init() {
    var grid = document.getElementById('stories-grid');
    var sentinel = document.getElementById('feed-sentinel');
    var status = document.getElementById('feed-status');
    if (!grid) return;

    var cells = grid.querySelectorAll('.story-cell');
    if (cells.length === 0) return;

    var batchSize = parseInt(grid.getAttribute('data-batch-size'), 10);
    if (!batchSize || batchSize < 1) batchSize = 9;
    var initialBatch = parseInt(grid.getAttribute('data-initial-batch'), 10);
    if (!initialBatch || initialBatch < 1) initialBatch = batchSize;

    // If everything fits in the initial batch there's nothing to progressively
    // reveal; leave the grid as-is.
    if (cells.length <= initialBatch) {
      if (sentinel) sentinel.remove();
      return;
    }

    // Hide the overflow. We add a class rather than touching .style.display so
    // CSS owns the animation + the "revealed" transition.
    for (var i = initialBatch; i < cells.length; i++) {
      cells[i].classList.add('story-cell--hidden');
    }

    var revealed = initialBatch;

    function setStatus(text) {
      if (!status) return;
      status.textContent = text || '';
    }

    function revealNext() {
      var end = Math.min(revealed + batchSize, cells.length);
      // requestAnimationFrame so the transition fires after the class flip
      // lands in a fresh frame (avoids a no-op flash on some browsers).
      for (var i = revealed; i < end; i++) {
        cells[i].classList.remove('story-cell--hidden');
        cells[i].classList.add('story-cell--revealing');
      }
      revealed = end;

      if (revealed >= cells.length) {
        setStatus('');
        if (sentinel) sentinel.remove();
        if (observer) observer.disconnect();
      } else {
        setStatus('Loading more…');
      }
    }

    // Graceful degradation: no IntersectionObserver → reveal everything now.
    if (typeof IntersectionObserver === 'undefined') {
      for (var j = revealed; j < cells.length; j++) {
        cells[j].classList.remove('story-cell--hidden');
      }
      revealed = cells.length;
      if (sentinel) sentinel.remove();
      return;
    }

    if (!sentinel) {
      // Without a sentinel we can't observe; reveal all so the user isn't
      // stuck staring at a truncated grid.
      for (var k = revealed; k < cells.length; k++) {
        cells[k].classList.remove('story-cell--hidden');
      }
      return;
    }

    // rootMargin pulls the trigger ~400px before the sentinel scrolls into
    // view, so the next batch is on screen by the time the user gets there.
    var observer = new IntersectionObserver(function (entries) {
      for (var e = 0; e < entries.length; e++) {
        if (entries[e].isIntersecting) {
          revealNext();
          break;
        }
      }
    }, { rootMargin: '400px 0px', threshold: 0 });

    observer.observe(sentinel);

    // Quiet ARIA hint that more is available.
    setStatus('');
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
