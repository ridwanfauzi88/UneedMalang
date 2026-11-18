(function () {
  'use strict';

  function setSidebar(open) {
    const sidebar = document.getElementById('sidebar');
    const overlay = document.getElementById('sidebar-overlay');
    if (!sidebar || !overlay) return;
    const shouldOpen = typeof open === 'boolean' ? open : sidebar.classList.contains('-translate-x-full');
    sidebar.classList.toggle('-translate-x-full', !shouldOpen);
    overlay.classList.toggle('hidden', !shouldOpen);
    sidebar.setAttribute('aria-hidden', String(!shouldOpen));
    overlay.setAttribute('aria-hidden', String(!shouldOpen));
    document.body.classList.toggle('overflow-hidden', shouldOpen);
    const trigger = document.querySelector('[aria-controls="sidebar"]');
    if (trigger) trigger.setAttribute('aria-expanded', String(shouldOpen));
  }

  window.toggleSidebar = setSidebar;
  document.addEventListener('keydown', function (event) {
    if (event.key === 'Escape') setSidebar(false);
  });
})();
