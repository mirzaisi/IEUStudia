/* IEUStudia — a few quiet lines about Solvequill, the maker's other app (video lessons for STEM questions):
 * a card on maths, science and engineering course pages (hidden for 30 days by its ×), a line in the Get ready
 * checklist of such a course, and one in Settings → About. The links to solvequill.com say which of these they came
 * from (utm_ parameters; IEUStudia itself counts nothing); the images are bundled. */
(function () {
  "use strict";
  const St = window.Studia;
  const { S, A, I } = St;
  const SITE = "https://solvequill.com";
  // course codes look like "MATH 240", "EEE 215": the subjects where Solvequill helps, by their prefix
  const FITS = new Set(["MATH", "STAT", "PHYS", "CHEM", "EEE", "EE", "CE", "CENG", "COMP", "CS", "SE", "ME", "MCE", "IE", "BME", "AE", "CIVE", "GBE"]);
  const HIDE_KEY = "studia-sq-hide", HIDE_DAYS = 30;

  const fits = (c) => !!c && FITS.has(((c.code || "").match(/^[A-Za-z]+/) || [""])[0].toUpperCase());
  let hiddenNow = false;                     // also when storage is unavailable
  const hidden = () => { if (hiddenNow) return true; try { return Date.now() < +(localStorage.getItem(HIDE_KEY) || 0); } catch (e) { return false; } };
  // the light theme's image and the dark theme's: styles.css shows one
  const logo = (name, cls) => ["black", "white"].map((tone) => `<img class="${cls} sq-${tone}" src="img/${name}-${tone}.png" alt="Solvequill">`).join("");
  const open = (label, cls, from) => `<button class="${cls}" data-act="url" data-url="${SITE}/?utm_source=ieustudia&amp;utm_medium=app&amp;utm_content=${from}" title="${t("Opens solvequill.com in your browser")}">${label} ${I("external", "xs")}</button>`;

  St.sq = {
    // a course page (Weeks and Overview tabs)
    card(c) {
      if (!fits(c) || hidden()) return "";
      return `<aside class="sq sq-card" aria-label="Solvequill"><span class="sq-mark">${logo("solvequill-mark", "sq-img")}</span>
        <div class="grow"><b>${t.html("Stuck on a {course} question?", { course: c.code })}</b>
          <div class="dim">${t("Solvequill turns it into a narrated whiteboard lesson: every line appears on the board as it's explained.")}</div></div>
        ${open(t("Open Solvequill"), "btn sm", "course")}
        <button class="btn ghost icon sm" data-act="sq-hide" title="${t("Hide for 30 days")}" aria-label="${t("Hide for 30 days")}">${I("x", "sm")}</button></aside>`;
    },
    // the Get ready checklist of a quiz or exam
    line(c) {
      if (!fits(c)) return "";
      return `<div class="sq sq-line">${logo("solvequill-mark", "sq-img")}<span>${t("Stuck on a question? Watch the solution as a video.")}</span>${open("Solvequill", "link", "get-ready")}</div>`;
    },
    // Settings → About
    about() {
      return `<div class="sq sq-about">${logo("solvequill", "sq-word")}
        <span class="dim grow">${t("From the maker of IEUStudia: Solvequill, video lessons for your STEM questions. We explain not the answer, but the reason for it.")}</span>
        ${open("solvequill.com", "btn ghost sm", "about")}</div>`;
    },
  };

  Object.assign(A, {
    "sq-hide": () => {
      hiddenNow = true;
      try { localStorage.setItem(HIDE_KEY, String(Date.now() + HIDE_DAYS * 864e5)); } catch (e) { /* storage may be unavailable */ }
      for (const el of document.querySelectorAll(".sq-card")) el.remove();
      if (S.view.name === "course") St.renderView();
    },
  });
})();
