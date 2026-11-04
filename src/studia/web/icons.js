/* IEUStudia — inline SVG icon set.
 *
 * Hand-written 24x24 stroke icons in the Lucide style (round caps/joins, 1.75 stroke set in CSS).
 * Usage: LSIcons.svg("name", "extra-class")  ->  '<svg class="i extra-class" ...>…</svg>'
 */
(function () {
  "use strict";

  const P = {
    sparkle:
      '<path d="M12 3.5l1.7 4.6a2 2 0 0 0 1.2 1.2l4.6 1.7-4.6 1.7a2 2 0 0 0-1.2 1.2L12 18.5l-1.7-4.6a2 2 0 0 0-1.2-1.2L4.5 11l4.6-1.7a2 2 0 0 0 1.2-1.2z"/>' +
      '<path d="M19 3v3.5M20.75 4.75h-3.5"/>',
    library:
      '<path d="M4 19.5V5.5A2.5 2.5 0 0 1 6.5 3H20v15H6.5A2.5 2.5 0 0 0 4 20.5 2.5 2.5 0 0 0 6.5 23H20v-5"/>' +
      '<path d="M8.5 7.5h7M8.5 11h5"/>',
    sigma: '<path d="M17.5 7V4.5h-11l6 7.5-6 7.5h11V17"/>',
    book:
      '<path d="M2.5 4.5h6a3.5 3.5 0 0 1 3.5 3.5v12.5a2.5 2.5 0 0 0-2.5-2.5h-7z"/>' +
      '<path d="M21.5 4.5h-6A3.5 3.5 0 0 0 12 8v12.5a2.5 2.5 0 0 1 2.5-2.5h7z"/>',
    folder:
      '<path d="M3 7.5A2.5 2.5 0 0 1 5.5 5h3.6a2 2 0 0 1 1.5.7L12 7.3h6.5A2.5 2.5 0 0 1 21 9.8v7.7a2.5 2.5 0 0 1-2.5 2.5h-13A2.5 2.5 0 0 1 3 17.5z"/>',
    "folder-open":
      '<path d="M4 19.5 6.6 12a1.8 1.8 0 0 1 1.7-1.2h12.2a1 1 0 0 1 1 1.3l-2 6.2a1.8 1.8 0 0 1-1.7 1.2H5.4A2.4 2.4 0 0 1 3 17.1V6.9A2.4 2.4 0 0 1 5.4 4.5h3.5a2 2 0 0 1 1.5.7l1.3 1.6h6a2.4 2.4 0 0 1 2.4 2.4v1.3"/>',
    settings:
      '<path d="M9.80 5.04 10.25 2.56h3.5l.45 2.48 1.17.48 2.07-1.43 2.47 2.47-1.43 2.07.48 1.17 2.48.45v3.5l-2.48.45-.48 1.17 1.43 2.07-2.47 2.47-2.07-1.43-1.17.48-.45 2.48h-3.5l-.45-2.48-1.17-.48-2.07 1.43-2.47-2.47 1.43-2.07-.48-1.17-2.48-.45v-3.5l2.48-.45.48-1.17L4.09 6.56l2.47-2.47 2.07 1.43z"/>' +
      '<circle cx="12" cy="12" r="3"/>',
    refresh: '<path d="M20.5 12a8.5 8.5 0 1 1-2.5-6l2.5 2.4"/><path d="M20.5 3.5v4.9h-4.9"/>',
    retry:
      '<path d="M3.5 12a8.5 8.5 0 0 1 14.6-5.9l2.4 2.4"/><path d="M20.5 3.5v5h-5"/>' +
      '<path d="M20.5 12a8.5 8.5 0 0 1-14.6 5.9l-2.4-2.4"/><path d="M3.5 20.5v-5h5"/>',
    check: '<path d="M20 6.5 9.5 17 4.5 12"/>',
    "check-circle": '<circle cx="12" cy="12" r="9"/><path d="m8.5 12.3 2.4 2.4 4.6-4.9"/>',
    x: '<path d="M18 6 6 18M6 6l12 12"/>',
    "x-circle": '<circle cx="12" cy="12" r="9"/><path d="m15 9-6 6M9 9l6 6"/>',
    alert:
      '<path d="M10.3 4.2 2.6 17.6A2 2 0 0 0 4.3 20.6h15.4a2 2 0 0 0 1.7-3L13.7 4.2a2 2 0 0 0-3.4 0z"/><path d="M12 9.5v4M12 17h.01"/>',
    "alert-circle": '<circle cx="12" cy="12" r="9"/><path d="M12 7.8v4.6M12 16.2h.01"/>',
    info: '<circle cx="12" cy="12" r="9"/><path d="M12 16.2v-4.6M12 7.8h.01"/>',
    clock: '<circle cx="12" cy="12" r="9"/><path d="M12 7.2V12l3.2 2"/>',
    hourglass:
      '<path d="M6.5 3h11M6.5 21h11"/><path d="M7.5 3v3.2a4.5 4.5 0 0 0 9 0V3"/><path d="M7.5 21v-3.2a4.5 4.5 0 0 1 9 0V21"/>',
    download: '<path d="M12 3.5v11.5M7 10l5 5 5-5"/><path d="M4.5 20h15"/>',
    wave:
      '<path d="M3 10.5v3M6.5 7v10M10 4v16"/><path d="M14 8.5h7M14 12h7M14 15.5h4.5"/>',
    play: '<path d="M7.5 4.8v14.4a.8.8 0 0 0 1.2.7l11.3-7.2a.8.8 0 0 0 0-1.4L8.7 4.1a.8.8 0 0 0-1.2.7z"/>',
    video: '<rect x="2.5" y="6" width="13" height="12" rx="2.5"/><path d="m15.5 10.2 4.8-3a.8.8 0 0 1 1.2.7v8.2a.8.8 0 0 1-1.2.7l-4.8-3"/>',
    external:
      '<path d="M14.5 3.5h6v6M10.5 13.5l10-10"/><path d="M18.5 13.5v5a2 2 0 0 1-2 2h-11a2 2 0 0 1-2-2v-11a2 2 0 0 1 2-2h5"/>',
    copy:
      '<rect x="8.5" y="8.5" width="12" height="12" rx="2.5"/><path d="M15.5 8.5V6a2.5 2.5 0 0 0-2.5-2.5H6A2.5 2.5 0 0 0 3.5 6v7A2.5 2.5 0 0 0 6 15.5h2.5"/>',
    ai:
      '<path d="M5 19 15.5 8.5M14 4.5v2M19.5 10h-2M17.4 6.6l1.4-1.4M10.6 6.6 9.2 5.2M17.4 13.4l1.4 1.4"/>' +
      '<path d="m13.5 10.5 2-2"/>',
    search: '<circle cx="11" cy="11" r="6.8"/><path d="m20.5 20.5-4.7-4.7"/>',
    chat:
      '<path d="M20.5 12a8.3 8.3 0 0 1-12.2 7.3L3.5 20.5l1.3-4.6A8.3 8.3 0 1 1 20.5 12z"/><path d="M8.5 10.5h7M8.5 14h4.5"/>',
    home: '<path d="M3.5 11 12 4l8.5 7"/><path d="M5.5 9.5V19a1.5 1.5 0 0 0 1.5 1.5h3.5v-6h3v6H17a1.5 1.5 0 0 0 1.5-1.5V9.5"/>',
    megaphone: '<path d="M3.5 10v4a1 1 0 0 0 1 1h2l8 4.5v-15l-8 4.5h-2a1 1 0 0 0-1 1z"/><path d="M18 9a4 4 0 0 1 0 6M7 15l1.2 4.6a1 1 0 0 0 1 .7h1.3"/>',
    target: '<circle cx="12" cy="12" r="8.5"/><circle cx="12" cy="12" r="4.8"/><circle cx="12" cy="12" r="1.2"/>',
    cap: '<path d="m2.5 9 9.5-4.5L21.5 9 12 13.5z"/><path d="M6.5 11v4.5c0 1.5 2.5 3 5.5 3s5.5-1.5 5.5-3V11M21.5 9v5"/>',
    layers: '<path d="m12 3.5 9 4.5-9 4.5-9-4.5z"/><path d="m3 12 9 4.5 9-4.5M3 16l9 4.5 9-4.5"/>',
    presentation: '<rect x="3" y="4" width="18" height="12" rx="1.8"/><path d="M12 16v4M8.5 20.5h7M7.5 12.5l3-3 2.2 2.2 3.8-3.7"/>',
    youtube: '<rect x="2.5" y="5.5" width="19" height="13" rx="3.5"/><path d="m10 9.2 5 2.8-5 2.8z"/>',
    "chevron-down": '<path d="m6 9 6 6 6-6"/>',
    "chevron-up": '<path d="m18 15-6-6-6 6"/>',
    "chevron-right": '<path d="m9 6 6 6-6 6"/>',
    "chevron-left": '<path d="m15 18-6-6 6-6"/>',
    "chevrons-up-down": '<path d="m7.5 15.5 4.5 4.5 4.5-4.5M7.5 8.5 12 4l4.5 4.5"/>',
    "arrow-left": '<path d="M19.5 12h-15M11 18.5 4.5 12 11 5.5"/>',
    "arrow-right": '<path d="M4.5 12h15M13 5.5l6.5 6.5-6.5 6.5"/>',
    eye: '<path d="M2.5 12S6 5 12 5s9.5 7 9.5 7-3.5 7-9.5 7-9.5-7-9.5-7z"/><circle cx="12" cy="12" r="3"/>',
    "eye-off":
      '<path d="M10 5.2A9.6 9.6 0 0 1 12 5c6 0 9.5 7 9.5 7a17 17 0 0 1-2.4 3.4M6.6 6.6C3.9 8.4 2.5 12 2.5 12S6 19 12 19a9.4 9.4 0 0 0 5.4-1.6"/>' +
      '<path d="M14.1 14.1a3 3 0 1 1-4.2-4.2M3 3l18 18"/>',
    user: '<circle cx="12" cy="8" r="4"/><path d="M4.5 20.5a7.5 7.5 0 0 1 15 0"/>',
    users:
      '<circle cx="9" cy="8" r="3.5"/><path d="M2.5 20a6.5 6.5 0 0 1 13 0"/><path d="M16 4.6a3.5 3.5 0 0 1 0 6.8M18 14.2a6.5 6.5 0 0 1 3.5 5.8"/>',
    logout: '<path d="M9.5 20.5H5.5a2 2 0 0 1-2-2v-13a2 2 0 0 1 2-2h4"/><path d="m16 16.5 4.5-4.5L16 7.5M20.5 12H9"/>',
    monitor: '<rect x="2.5" y="3.5" width="19" height="13" rx="2.5"/><path d="M8 20.5h8M12 16.5v4"/>',
    sun:
      '<circle cx="12" cy="12" r="4"/><path d="M12 2.5v2M12 19.5v2M4.6 4.6 6 6M18 18l1.4 1.4M2.5 12h2M19.5 12h2M4.6 19.4 6 18M18 6l1.4-1.4"/>',
    moon: '<path d="M20.5 13.2A8.5 8.5 0 1 1 10.8 3.5a6.6 6.6 0 0 0 9.7 9.7z"/>',
    contrast: '<circle cx="12" cy="12" r="9"/><path d="M12 3v18a9 9 0 0 0 0-18z" fill="currentColor" stroke="none"/>',
    cpu:
      '<rect x="5" y="5" width="14" height="14" rx="2.5"/><rect x="9" y="9" width="6" height="6" rx="1"/>' +
      '<path d="M9.5 2v3M14.5 2v3M9.5 19v3M14.5 19v3M2 9.5h3M2 14.5h3M19 9.5h3M19 14.5h3"/>',
    zap: '<path d="M13 2.5 4.5 13.5H12l-1 8 8.5-11H12z"/>',
    file: '<path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z"/><path d="M14 3v5h5"/>',
    "file-text":
      '<path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z"/><path d="M14 3v5h5M9 12.5h6M9 16h6M9 9h1.5"/>',
    "file-audio":
      '<path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z"/><path d="M14 3v5h5M9 17v-3M12 17v-6M15 17v-2"/>',
    star:
      '<path d="m12 3.2 2.7 5.5 6 .9-4.3 4.2 1 6-5.4-2.8-5.4 2.8 1-6-4.3-4.2 6-.9z"/>',
    more: '<circle cx="5.5" cy="12" r="1.1"/><circle cx="12" cy="12" r="1.1"/><circle cx="18.5" cy="12" r="1.1"/>',
    lecture:
      '<path d="M2.5 4.5h19"/><path d="M4 4.5v9.5a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V4.5"/><path d="M12 16v3.5M8.5 21l3.5-1.5 3.5 1.5"/>',
    lab:
      '<path d="M9 3h6M10 3v5.8L4.6 18.2A1.9 1.9 0 0 0 6.3 21h11.4a1.9 1.9 0 0 0 1.7-2.8L14 8.8V3"/><path d="M7.2 15h9.6"/>',
    shield: '<path d="M12 21.5s7.5-3.6 7.5-9.5V5.6L12 2.8 4.5 5.6V12c0 5.9 7.5 9.5 7.5 9.5z"/><path d="m9 12 2.2 2.2L15.5 10"/>',
    lock: '<rect x="4.5" y="10.5" width="15" height="10.5" rx="2.5"/><path d="M8 10.5V7.5a4 4 0 0 1 8 0v3"/>',
    calendar: '<rect x="3.5" y="4.5" width="17" height="16" rx="2.5"/><path d="M16 2.5v4M8 2.5v4M3.5 10h17"/>',
    "list-checks": '<path d="m3.5 6.5 1.8 1.8L8.5 5M3.5 16.5l1.8 1.8L8.5 15M12 6.5h8.5M12 12h8.5M12 17.5h8.5"/>',
    ban: '<circle cx="12" cy="12" r="9"/><path d="m5.7 5.7 12.6 12.6"/>',
    subtitles: '<rect x="2.5" y="5" width="19" height="14" rx="2.5"/><path d="M6.5 15h4M13 15h4.5M6.5 11.5h2.5M12 11.5h5.5"/>',
    music: '<path d="M9 18V5.5l11-2v12.5"/><circle cx="6.5" cy="18" r="2.5"/><circle cx="17.5" cy="16" r="2.5"/>',
    timer: '<path d="M10 2.5h4M12 13.5l3-3"/><circle cx="12" cy="13.5" r="7.5"/>',
    terminal: '<rect x="2.5" y="4" width="19" height="16" rx="2.5"/><path d="m7 9.5 3 2.5-3 2.5M12.5 15h4.5"/>',
    "wifi-off":
      '<path d="M3 3l18 18M8.6 16.4a4.8 4.8 0 0 1 6.8 0M2.5 8.8a14.6 14.6 0 0 1 4-2.6M10.7 5a14.6 14.6 0 0 1 10.8 3.8M5.3 12.6a10 10 0 0 1 4.4-2.4M17 11a9.8 9.8 0 0 1 1.7 1.6M12 20h.01"/>',
    pencil: '<path d="M16.5 3.8a2.5 2.5 0 0 1 3.6 3.6L7.5 20 2.8 21.2 4 16.5z"/><path d="m14.5 5.8 3.6 3.6"/>',
    quote: '<path d="M4 18.5c3-1 4.5-3.5 4.5-6.5V7H4v5h4.5M15.5 18.5c3-1 4.5-3.5 4.5-6.5V7h-4.5v5H20"/>',
    plus: '<path d="M12 5v14M5 12h14"/>',
    minus: '<path d="M5 12h14"/>',
    globe: '<circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3a14 14 0 0 1 0 18 14 14 0 0 1 0-18z"/>',
    inbox: '<path d="M21.5 12.5h-5.5l-2 3h-4l-2-3H2.5"/><path d="M5.5 5.6 2.5 12.5V18a2 2 0 0 0 2 2h15a2 2 0 0 0 2-2v-5.5l-3-6.9A2 2 0 0 0 16.7 4.5H7.3a2 2 0 0 0-1.8 1.1z"/>',
    keyboard: '<rect x="2.5" y="5.5" width="19" height="13" rx="2.5"/><path d="M6.5 9.5h.01M10 9.5h.01M14 9.5h.01M17.5 9.5h.01M6.5 13h.01M17.5 13h.01M9.5 13h5M8 15.5h8"/>',
    stop: '<rect x="6" y="6" width="12" height="12" rx="2.5"/>',
    pause: '<rect x="6.5" y="5" width="3.5" height="14" rx="1"/><rect x="14" y="5" width="3.5" height="14" rx="1"/>',
    bookmark: '<path d="M6.5 4.5A1.5 1.5 0 0 1 8 3h8a1.5 1.5 0 0 1 1.5 1.5v16l-5.5-3.6-5.5 3.6z"/>',
    link: '<path d="M10.2 13.8a4.5 4.5 0 0 0 6.4 0l2.8-2.8a4.5 4.5 0 0 0-6.4-6.4l-1.2 1.2"/><path d="M13.8 10.2a4.5 4.5 0 0 0-6.4 0L4.6 13a4.5 4.5 0 0 0 6.4 6.4l1.2-1.2"/>',
    "hard-drive": '<path d="M21.5 13H2.5M5.4 5.6 2.5 13v5a2 2 0 0 0 2 2h15a2 2 0 0 0 2-2v-5l-2.9-7.4a1.8 1.8 0 0 0-1.7-1.1H7.1a1.8 1.8 0 0 0-1.7 1.1z"/><path d="M6.5 16.5h.01M10 16.5h.01"/>',
  };

  function svg(name, cls) {
    const body = P[name] || P.info;
    return (
      '<svg class="i' + (cls ? " " + cls : "") + '" viewBox="0 0 24 24" aria-hidden="true" focusable="false">' +
      body +
      "</svg>"
    );
  }

  /** Indeterminate spinner (rotation is done in CSS). */
  function spinner(cls) {
    return (
      '<svg class="i spin' + (cls ? " " + cls : "") + '" viewBox="0 0 24 24" aria-hidden="true" focusable="false">' +
      '<circle cx="12" cy="12" r="8.5" opacity=".22"/><path d="M20.5 12A8.5 8.5 0 0 0 12 3.5"/></svg>'
    );
  }

  /** App logo (same mark as assets/logo.svg): black tile, white bookmark with a play button cut out of it. */
  function logo(size) {
    const s = size || 28;
    return (
      '<svg class="logo" width="' + s + '" height="' + s + '" viewBox="0 0 256 256" aria-hidden="true" focusable="false">' +
      '<path fill="#000" d="M56 0h144a56 56 0 0 1 56 56v144a56 56 0 0 1-56 56H56a56 56 0 0 1-56-56V56A56 56 0 0 1 56 0Z"/>' +
      '<path fill="#fff" d="M85 59h86c8 0 14 6 14 14v120c0 4-4 7-9 4l-48-29-48 29c-5 3-9 0-9-4V73c0-8 6-14 14-14Z"/>' +
      '<path fill="#000" d="m110 93 48 31-48 31V93Z"/></svg>'
    );
  }

  window.LSIcons = { svg: svg, spinner: spinner, logo: logo, has: (n) => n in P };
})();
