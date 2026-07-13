// Profile-tab strip behavior.
//
// The strip is server-rendered as a static <ul> of links. This script is
// progressive enhancement that switches on marquee behavior when the strip
// overflows its container, and leaves a static centered row when it fits.
//
// With JS disabled the strip is a plain wrapping/scrollable row (CSS-owned).
//
// Marquee mode:
//   - Clone the track once so the loop is seamless. Clones are aria-hidden
//     and tabindex="-1" so screen readers and keyboard Tab skip them.
//   - Continuous auto-scroll (~25 px/s), paused on hover and while pressed.
//   - Pointer drag scrubs the position via Pointer Events (one code path
//     for mouse and finger). Move/up listen on document during the drag so
//     a gesture that leaves the strip still updates state; we do NOT use
//     setPointerCapture because it retargets the derived click event to the
//     viewport and prevents the tab anchor from navigating on a clean tap.
//   - Trailing click is suppressed only when the gesture travelled more
//     than DRAG_CLICK_SUPPRESS_PX, so a scrub can't accidentally navigate.
//   - Auto-scroll resumes ~3s after last interaction.
//   - prefers-reduced-motion => no auto-scroll, plain overflow-x scroll strip.
//   - Focused tab links scroll into view.
//   - Window resize re-evaluates fits vs overflows.

(function () {
  var AUTO_SCROLL_PX_PER_SEC = 25;
  var RESUME_DELAY_MS = 3000;
  // A gesture that travels more than this many CSS px from pointerdown is a
  // scrub, not a tap; the trailing click is suppressed so the user doesn't
  // accidentally navigate to whichever tab their finger lifted over.
  var DRAG_CLICK_SUPPRESS_PX = 6;

  function init() {
    var nav = document.querySelector('[data-profile-tabs]');
    if (!nav) return;

    var viewport = nav.querySelector('[data-profile-tabs-viewport]');
    var track = nav.querySelector('[data-profile-tabs-track]');
    if (!viewport || !track) return;

    var reducedMotionMedia = null;
    try {
      reducedMotionMedia = window.matchMedia('(prefers-reduced-motion: reduce)');
    } catch (e) {
      reducedMotionMedia = null;
    }

    // State for the marquee loop. Rebuilt on every layout re-evaluation.
    var state = {
      mode: 'static',     // 'static' | 'scroll' | 'marquee'
      clone: null,
      trackWidth: 0,      // width of the original track content
      offset: 0,          // current translateX position (marquee)
      lastFrameTs: 0,
      rafId: 0,
      paused: false,
      pointerDown: false,
      dragStartX: 0,
      dragStartOffset: 0,
      // Cumulative horizontal distance from pointerdown (absolute). Reset
      // per gesture; the trailing click is suppressed once this crosses
      // DRAG_CLICK_SUPPRESS_PX so a scrub doesn't fire a tab navigation.
      dragDistanceX: 0,
      suppressNextClick: false,
      resumeTimer: 0
    };

    function clearResumeTimer() {
      if (state.resumeTimer) {
        clearTimeout(state.resumeTimer);
        state.resumeTimer = 0;
      }
    }

    function stopAnimation() {
      if (state.rafId) {
        cancelAnimationFrame(state.rafId);
        state.rafId = 0;
      }
    }

    function scheduleResume() {
      clearResumeTimer();
      state.resumeTimer = setTimeout(function () {
        state.paused = false;
        state.lastFrameTs = 0;
      }, RESUME_DELAY_MS);
    }

    function removeClone() {
      if (state.clone && state.clone.parentNode) {
        state.clone.parentNode.removeChild(state.clone);
      }
      state.clone = null;
    }

    function resetForStaticOrScroll() {
      stopAnimation();
      clearResumeTimer();
      removeClone();
      track.style.transform = '';
      track.style.willChange = '';
      viewport.classList.remove('profile-tabs-viewport--marquee');
      viewport.classList.remove('profile-tabs-viewport--scroll');
    }

    function reduceMotion() {
      return reducedMotionMedia && reducedMotionMedia.matches;
    }

    function marqueeStep(ts) {
      state.rafId = 0;
      if (state.mode !== 'marquee') return;

      if (!state.paused && !state.pointerDown) {
        if (state.lastFrameTs) {
          var dt = (ts - state.lastFrameTs) / 1000;
          // Clamp big frame gaps (backgrounded tab) so the jump on resume is small.
          if (dt > 0.25) dt = 0.25;
          state.offset -= AUTO_SCROLL_PX_PER_SEC * dt;
        }
        state.lastFrameTs = ts;
      } else {
        state.lastFrameTs = 0;
      }

      // Wrap so we're always within [-trackWidth, 0]. This keeps the visible
      // half continuous with the clone half.
      if (state.trackWidth > 0) {
        while (state.offset <= -state.trackWidth) state.offset += state.trackWidth;
        while (state.offset > 0) state.offset -= state.trackWidth;
      }

      track.style.transform = 'translateX(' + state.offset + 'px)';
      state.rafId = requestAnimationFrame(marqueeStep);
    }

    function startMarquee() {
      viewport.classList.add('profile-tabs-viewport--marquee');
      // Measure the original track before cloning.
      state.trackWidth = track.scrollWidth;
      if (state.trackWidth <= 0) return;

      // Clone the track's children once so the loop is seamless. Clones
      // are inert to assistive tech and keyboard focus.
      state.clone = track.cloneNode(true);
      state.clone.setAttribute('aria-hidden', 'true');
      state.clone.setAttribute('data-profile-tabs-clone', '');
      var cloneLinks = state.clone.querySelectorAll('a');
      for (var i = 0; i < cloneLinks.length; i++) {
        cloneLinks[i].setAttribute('tabindex', '-1');
        cloneLinks[i].setAttribute('aria-hidden', 'true');
      }
      // Insert the clone as a sibling right after the original track so
      // the visual loop is track|clone within the same flex flow.
      track.parentNode.insertBefore(state.clone, track.nextSibling);

      track.style.willChange = 'transform';
      state.offset = 0;
      state.lastFrameTs = 0;
      state.paused = false;
      state.pointerDown = false;
      state.rafId = requestAnimationFrame(marqueeStep);
    }

    function evaluateLayout() {
      // Tear down any previous mode before measuring: cloned width would
      // double-count and the wrong mode could persist.
      resetForStaticOrScroll();

      var overflows = track.scrollWidth > viewport.clientWidth + 1;
      if (!overflows) {
        state.mode = 'static';
        return;
      }

      if (reduceMotion()) {
        // Plain overflow scroll: user can pan with touch/wheel, no motion.
        state.mode = 'scroll';
        viewport.classList.add('profile-tabs-viewport--scroll');
        return;
      }

      state.mode = 'marquee';
      startMarquee();
    }

    // ----- Interaction: hover, pointer drag, focus-into-view -----

    function onPointerEnter() {
      if (state.mode !== 'marquee') return;
      state.paused = true;
      state.lastFrameTs = 0;
      clearResumeTimer();
    }

    function onPointerLeave() {
      if (state.mode !== 'marquee') return;
      if (state.pointerDown) return;
      scheduleResume();
    }

    function onPointerDown(event) {
      if (state.mode !== 'marquee') return;
      state.pointerDown = true;
      state.paused = true;
      state.dragStartX = event.clientX;
      state.dragStartOffset = state.offset;
      state.dragDistanceX = 0;
      // A fresh gesture: any suppression from the previous one is done.
      state.suppressNextClick = false;
      clearResumeTimer();
      // Track move/up on the document so a drag that leaves the viewport
      // still updates state. We deliberately do NOT setPointerCapture on
      // the viewport: capture retargets the derived click to the viewport
      // instead of the anchor under the finger, which breaks tab navigation.
      document.addEventListener('pointermove', onDocPointerMove);
      document.addEventListener('pointerup', onDocPointerUp);
      document.addEventListener('pointercancel', onDocPointerUp);
    }

    function onDocPointerMove(event) {
      if (!state.pointerDown) return;
      var delta = event.clientX - state.dragStartX;
      state.dragDistanceX = Math.abs(delta);
      if (state.dragDistanceX > DRAG_CLICK_SUPPRESS_PX) {
        // Latch on once crossed. Even if the user dwells back near the
        // origin before release, they still travelled — treat it as scrub.
        state.suppressNextClick = true;
      }
      state.offset = state.dragStartOffset + delta;
      // The rAF loop already handles wrapping; if paused, nudge a frame so
      // the transform paints while the user drags.
      track.style.transform = 'translateX(' + state.offset + 'px)';
    }

    function onDocPointerUp(event) {
      endDrag(event);
    }

    function onClickCapture(event) {
      // Runs in the capture phase so we intercept the click BEFORE the <a>
      // handles it. Only a scrub-classified gesture suppresses; a clean tap
      // (distance <= threshold) navigates as normal.
      if (!state.suppressNextClick) return;
      state.suppressNextClick = false;
      event.preventDefault();
      event.stopPropagation();
    }

    function endDrag(event) {
      if (!state.pointerDown) return;
      state.pointerDown = false;
      document.removeEventListener('pointermove', onDocPointerMove);
      document.removeEventListener('pointerup', onDocPointerUp);
      document.removeEventListener('pointercancel', onDocPointerUp);
      // If the pointer is still hovering the strip, stay paused; otherwise
      // schedule the auto-resume.
      var stillHovering = event && event.type !== 'pointercancel' &&
        viewport.matches && viewport.matches(':hover');
      if (!stillHovering) scheduleResume();
    }

    function onFocusIn(event) {
      // Keep the focused tab visible when the strip overflows. Skip clones.
      if (!event.target || !event.target.closest) return;
      var target = event.target.closest('a');
      if (!target) return;
      if (target.getAttribute('tabindex') === '-1') return;
      if (typeof target.scrollIntoView === 'function') {
        try {
          target.scrollIntoView({ block: 'nearest', inline: 'nearest' });
        } catch (e) { /* older browsers: ignore */ }
      }
    }

    viewport.addEventListener('pointerenter', onPointerEnter);
    viewport.addEventListener('pointerleave', onPointerLeave);
    viewport.addEventListener('pointerdown', onPointerDown);
    // pointermove/pointerup are attached to document during a drag so a
    // pointer that leaves the viewport still updates state (and so we don't
    // pull setPointerCapture, which would retarget the derived click away
    // from the actual anchor and break tab navigation).
    // Capture phase so we run BEFORE the <a>'s default click. Otherwise the
    // link would already have navigated by the time our handler ran.
    viewport.addEventListener('click', onClickCapture, true);
    nav.addEventListener('focusin', onFocusIn);

    // Debounced resize: rebuild the marquee if the viewport width crosses
    // the fit/overflow boundary.
    var resizeTimer = 0;
    window.addEventListener('resize', function () {
      if (resizeTimer) clearTimeout(resizeTimer);
      resizeTimer = setTimeout(evaluateLayout, 120);
    });

    // React to prefers-reduced-motion changes at runtime (e.g. OS setting toggled).
    if (reducedMotionMedia && typeof reducedMotionMedia.addEventListener === 'function') {
      reducedMotionMedia.addEventListener('change', evaluateLayout);
    } else if (reducedMotionMedia && typeof reducedMotionMedia.addListener === 'function') {
      // Safari <14 fallback.
      reducedMotionMedia.addListener(evaluateLayout);
    }

    evaluateLayout();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
