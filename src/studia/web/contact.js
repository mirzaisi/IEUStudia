/* IEUStudia — how to reach the person who makes it, on Instagram or LinkedIn: a small card with a note in Settings →
 * About, one line on the setup's welcome step, and two small buttons at the bottom of the sidebar (on every page).
 * The links open in the browser through the app (open_url), as every other link does; their marks are in icons.js. */
(function () {
  "use strict";
  const St = window.Studia;
  const { esc, I } = St;

  // a function: the tooltips are in the language of the moment
  const LINKS = () => [
    { id: "ig", name: "Instagram", site: "instagram.com/mirzaisi", url: "https://www.instagram.com/mirzaisi",
      tip: t("Message me on Instagram") },
    { id: "in", name: "LinkedIn", site: "linkedin.com/in/mirzaisi", url: "https://www.linkedin.com/in/mirzaisi/",
      tip: t("Message me on LinkedIn") },
  ];
  const mark = (l, cls = "") => `<button class="btn ghost icon sm ct-btn ct-${l.id}${cls ? " " + cls : ""}" data-act="url" data-url="${esc(l.url)}"
    title="${esc(l.tip)}" aria-label="${esc(l.tip)}">${I(l.id === "ig" ? "instagram" : "linkedin", "sm")}</button>`;

  St.contact = {
    links: LINKS,
    // the bottom of the sidebar, under the profile: small, quiet, there on every page
    side: () => `<div class="side-contact"><span class="lbl">${t("Questions? Message me")}</span>${LINKS().map((l) => mark(l)).join("")}</div>`,
    // the setup's welcome step: one short line under the notice
    line: () => `<div class="su-contact"><span>${t("Questions, or something not working? Message me.")}</span>${LINKS().map((l) => mark(l)).join("")}</div>`,
    // Settings → About: the note, and both links with their names
    card: () => `<div class="contact-card"><div class="grow"><b>${t("Contact")}</b>
        <div class="dim">${t("Questions, ideas, or something not working? Message me, I'd like to hear about it.")}</div></div>
      <div class="ct-links">${LINKS().map((l) => `<button class="btn sm ct-btn ct-${l.id}" data-act="url" data-url="${esc(l.url)}"
        title="${esc(t("Opens {site} in your browser", { site: l.site }))}">${I(l.id === "ig" ? "instagram" : "linkedin", "sm")} ${l.name}</button>`).join("")}</div></div>`,
  };
})();
