"""What checking 3.2.3 in the real app found.

A PC whose date is wrong (a flat clock battery, a date typed by hand) fails every secure connection on the
certificate's dates. Windows says so with a code and in its own language, so on a Turkish Windows the English words
the app looked for were never there: signing in to ChatGPT named an antivirus, and a question to ChatGPT said "check
your internet connection". IEULearn, Panopto, downloads, Claude Code and the Codex CLI never named the date at all.

And after Continue with ChatGPT, a browser that was asked but never showed the page (the friend's case) read "Opened
with Brave" under "hasn't shown the sign-in page yet", with no other way in the row (now: Open in Microsoft Edge, the
in-app sign-in window being gone); Settings → AI also said "finish this later in Settings → AI" to someone already
there."""

import json
import shutil
import ssl
import subprocess
from pathlib import Path

import pytest
import requests
import urllib3

from studia import ai, chatgpt, i18n, net
from studia.i18n import t

WEB = Path(i18n.__file__).parent / "web"
EN_WORDS = ("A required certificate is not within its validity period when verifying against the current system clock "
            "or the timestamp in the signed file.")
TR_WORDS = ("Gerekli bir sertifika, geçerli sistem saatine veya imzalı dosyadaki zaman damgasına göre doğrulanırken "
            "geçerlilik süresi içinde değil.")


def _windows_tls(words: str, code: int = 0x800B0101, url: str = "https://learn.ieu.edu.tr/login"):
    """requests' SSLError as it comes through Windows' certificate check (truststore): Windows' code and its words."""
    inner = ssl.SSLCertVerificationError(words)
    inner.verify_code = code
    u3 = urllib3.exceptions.SSLError(inner)
    u3.__cause__ = inner
    return requests.exceptions.SSLError(urllib3.exceptions.MaxRetryError(None, "/", u3),
                                        request=requests.Request("GET", url).prepare())


@pytest.mark.parametrize("words", [EN_WORDS, TR_WORDS], ids=["english-windows", "turkish-windows"])
def test_a_wrong_pc_date_is_said_whatever_the_language_of_windows(words):
    exc = _windows_tls(words)
    assert net.cert_out_of_date(exc)
    assert "date or time is wrong" in chatgpt._net_message(exc)              # signing in to ChatGPT
    assert "date or time is wrong" in str(chatgpt._unreachable(exc))         # a question to ChatGPT
    said = net.plain_error(exc)                                               # IEULearn, Panopto, downloads, updates
    assert "learn.ieu.edu.tr" in said and "date or time is wrong" in said and "Antivirus" not in said
    assert "Set time automatically" in said or "automatically" in said


def test_other_certificate_failures_still_name_what_changes_secure_connections():
    untrusted = _windows_tls("A certificate chain processed, but terminated in a root certificate which is not "
                             "trusted by the trust provider.", code=0x800B0109)                # CERT_E_UNTRUSTEDROOT
    assert not net.cert_out_of_date(untrusted) and net.cert_refused(untrusted)
    assert net.plain_error(untrusted).startswith("A secure connection to learn.ieu.edu.tr was blocked. Antivirus")
    # a question to ChatGPT: the internet works, so not "check your internet connection"
    assert str(chatgpt._unreachable(untrusted)).startswith("A secure connection to ChatGPT was blocked or changed")
    # a connection that broke off halfway is no refused certificate: said as before
    broke = requests.exceptions.SSLError("EOF occurred in violation of protocol (_ssl.c:2427)")
    assert not net.cert_refused(broke)
    assert str(chatgpt._unreachable(broke)).startswith("Can't reach ChatGPT")
    assert "didn't finish uploading" in str(chatgpt._unreachable(broke, chatgpt.BIG_BODY))


def test_claude_code_and_the_codex_cli_say_a_wrong_date_too():
    assert "date or time is wrong" in ai.friendly_error("API Error: Unable to connect to API (CERT_HAS_EXPIRED)")
    assert "date or time is wrong" in ai._codex_error(
        "error sending request for url (https://chatgpt.com/backend-api/codex/responses): invalid peer certificate: "
        "certificate expired: verification time 1790000000 (UNIX), but certificate is not valid after 1780000000")
    assert "date or time is wrong" in ai._login_failure("claude", 1, ["Login failed: certificate has expired"], 3.0)
    # the rest as before: another certificate problem names the antivirus, an expired device code is the code's
    assert ai.friendly_error("unable to get local issuer certificate") == t(ai.INSECURE)
    assert ai._login_failure("codex", 1, ["Error logging in with device code: device code expired"], 900.0).startswith(
        "The code expired before the sign-in was finished")


PAGE = """
globalThis.window = globalThis;
globalThis.location = { hash: "" };
globalThis.document = { documentElement: {}, querySelector: () => null, querySelectorAll: () => [], addEventListener() {},
  getElementById: () => null };
require(%(i18n)s);
const answers = {
  ai_login: { ok: true, state: "waiting", link: "http://127.0.0.1:52817/sign-in", page: "https://auth.openai.com/page",
    launched: true, browser: "Brave", via: "default", why: "", edge: true },
  ai_status: { ok: true, provider: "chatgpt", chosen: "chatgpt", chatgpt: { signed_in: false, plan_allowed: false, sign_in: {}, test: {} } },
};
const S = { mode: "app", view: { name: "settings" }, boot: { app: { ai_provider: "chatgpt" } } };
window.Studia = { S, A: {}, I: (n) => `[${n}]`, SPIN: () => "(spin)", esc: (s) => String(s), onEvents: {}, mocks: {},
  toast() {}, renderView() {}, call: (name) => Promise.resolve(answers[name] || { ok: true }) };
require(%(setup)s);
const St = window.Studia, A = St.A, U = St.setup, tick = () => new Promise((r) => setTimeout(r, 5));
const out = {};
(async () => {
  U.ai = { chatgpt: { signed_in: false, plan_allowed: false, sign_in: {}, test: {} } };
  out.settings = St.settingsAI();
  await A["ai-login"]({ dataset: { id: "chatgpt" } }); await tick();
  out.sent = St.settingsAI();                                     // handed to Brave, no page yet
  U.busy["login:chatgpt"] = Date.now() - 20000;                   // 20 s later, still no page
  out.slow = St.settingsAI();
  St.onEvents.chatgpt({ type: "chatgpt", state: "waiting", step: "opened", opened: true });
  out.opened = St.settingsAI();                                   // the browser asked for it after all
  S.mode = "setup";
  out.setup = St.settingsAI();
  console.log(JSON.stringify(out));
  process.stdout.write("", () => process.exit(0));      // after the output is out (Linux pipes)
})();
"""
EDGE_IN_ROW = 'class="btn primary sm" data-act="ai-login-edge"'


@pytest.mark.skipif(not shutil.which("node"), reason="node is not installed")
def test_a_page_that_never_showed_up_offers_microsoft_edge_in_the_row(tmp_path):
    script = tmp_path / "page.js"
    script.write_text(PAGE % {"i18n": json.dumps(str(WEB / "i18n.js")), "setup": json.dumps(str(WEB / "setup.js"))},
                      encoding="utf-8")
    out = json.loads(subprocess.run(["node", str(script)], capture_output=True, text=True, encoding="utf-8",
                                    check=True, timeout=60).stdout)
    front = lambda page: out[page].split('data-act="gpt-trouble"')[0]          # noqa: E731
    # handed to the browser: not "opened" yet, and the calm normal path (Microsoft Edge only under Trouble?)
    assert "Sent to Brave." in out["sent"] and "Opened with Brave." not in out["sent"]
    assert 'data-act="ai-login-edge"' not in front("sent")
    # 15 s and no page: the text names Microsoft Edge, and it is right there in the row (whatever the default is)
    assert "Click Open in Microsoft Edge" in out["slow"] and EDGE_IN_ROW in out["slow"]
    assert "Opened with Brave." not in out["slow"] and "IEUStudia asked your browser (Brave) to open it." in out["slow"]
    # the page opened in the browser: said so, and the row is the calm one again
    assert "Opened with Brave." in out["opened"] and 'data-act="ai-login-edge"' not in front("opened")
    # "finish this later in Settings → AI": in the setup only, not in Settings → AI itself
    for page in ("settings", "sent", "opened"):
        assert "finish this later in Settings" not in out[page]
    assert "finish this later in Settings" in out["setup"]


def test_no_text_ends_on_a_question_mark_and_a_full_stop():
    """ "...use Paste the address from your browser under Trouble?." read sloppy: a button's name that ends in "?" goes
    in brackets, as the other texts have it ("(under Trouble?).")."""
    locales = Path(i18n.__file__).parent / "locales"
    odd = [k for f in sorted(locales.glob("*/*.json")) for k, v in json.loads(f.read_text(encoding="utf-8")).items()
           if "?." in k or (isinstance(v, str) and "?." in v)]
    assert not odd, odd


COPY = """
globalThis.window = globalThis;
globalThis.location = { hash: "" };
const els = { "#cn-copy-err": { textContent: "" }, "#cn-copybox": { focus() {}, select() {} } };
globalThis.document = { documentElement: {}, querySelector: (s) => els[s] || null, querySelectorAll: () => [],
  addEventListener() {}, getElementById: () => null };
require(%(i18n)s);
const LINK = "https://auth.openai.com/oauth/authorize?client_id=app_x&state=s1";
const REPORT = "IEUStudia 3.2.3, ChatGPT sign-in\\nWindows 11, language en\\nNow: waiting";
const refused = { ok: false, error: "Could not access the clipboard." };
let clipboard = "refuse", copies = [];
const S = { mode: "app", view: { name: "settings" }, boot: { app: { ai_provider: "chatgpt" } } };
const seen = { modal: [], closed: 0, toasts: [] };
window.Studia = { S, A: {}, I: (n) => `[${n}]`, SPIN: () => "(spin)", esc: (s) => String(s), onEvents: {}, mocks: {},
  toast: (m, level) => seen.toasts.push([m, level]), renderView() {}, modal: (h) => seen.modal.push(h),
  closeModal: () => { seen.closed++; },
  call: (name, arg) => {
    if (name === "copy_text") { copies.push(arg); return Promise.resolve(clipboard === "refuse" ? refused : { ok: true }); }
    if (name === "ai_login_details") return Promise.resolve(clipboard === "refuse" ? { ...refused, text: REPORT }
      : clipboard === "broken" ? { ok: false, error: "Something went wrong." } : { ok: true, text: REPORT });
    return Promise.resolve({ ok: true });
  } };
require(%(setup)s);
const A = window.Studia.A, out = {};
const take = () => { const r = { modal: seen.modal.splice(0), closed: seen.closed, toasts: seen.toasts.splice(0) }; seen.closed = 0; return r; };
(async () => {
  await A["ai-copy"]({ dataset: { text: LINK } });                 // Copy link: the clipboard is refused
  out.link = take();
  await A["ai-copy-again"]();                                      // still refused: said in the dialog
  out.again = { ...take(), err: els["#cn-copy-err"].textContent };
  clipboard = "ok";
  await A["ai-copy-again"]();                                      // free again: copied, the dialog closes
  out.freed = take();
  clipboard = "refuse";
  await A["ai-login-details"]();                                   // Copy details: the report is shown, not lost
  out.details = take();
  clipboard = "broken";
  await A["ai-login-details"]();                                   // no report at all: the error, as before
  out.broken = take();
  clipboard = "ok";
  await A["ai-copy"]({ dataset: { text: "WXYZ-12345" } });
  out.ok = take();
  out.copies = copies.splice(0);
  clipboard = "refuse";
  await A["ai-copy"]({ dataset: { text: LINK + "&nonce=" + "n".repeat(480) } });   // a real sign-in link: ~550
  out.long = take();
  window.LS_LINUX = true;                                          // Linux (from source): not Windows' words
  await A["ai-copy"]({ dataset: { text: LINK } });
  out.linux = take();
  console.log(JSON.stringify(out));
  process.stdout.write("", () => process.exit(0));      // after the output is out (Linux pipes)
})();
"""


@pytest.mark.skipif(not shutil.which("node"), reason="node is not installed")
def test_a_copy_the_clipboard_refuses_shows_the_text_to_copy_by_hand(tmp_path):
    """Windows refuses the clipboard while another program holds it open (or the PC is locked, as on the night of the
    check): Copy link said only "Could not access the clipboard.", and Copy details' report was lost."""
    script = tmp_path / "copy.js"
    script.write_text(COPY % {"i18n": json.dumps(str(WEB / "i18n.js")), "setup": json.dumps(str(WEB / "setup.js"))},
                      encoding="utf-8")
    out = json.loads(subprocess.run(["node", str(script)], capture_output=True, text=True, encoding="utf-8",
                                    check=True, timeout=60).stdout)
    link = "https://auth.openai.com/oauth/authorize?client_id=app_x&state=s1"
    (box,) = out["link"]["modal"]
    assert "Copy failed" in box and "another program may be using it" in box and "Ctrl+A, then Ctrl+C" in box
    assert f'readonly spellcheck="false" rows="2">{link}</textarea>' in box
    assert 'data-act="ai-copy-again"' in box and 'data-act="modal-close"' in box
    assert out["link"]["toasts"] == []                                  # no red toast that leaves it nowhere
    assert out["again"]["modal"] == [] and out["again"]["closed"] == 0 and out["again"]["toasts"] == []
    assert out["again"]["err"].startswith("Still not possible. Copy it from the box yourself")
    assert out["freed"]["closed"] == 1 and out["freed"]["toasts"] == [["Copied", "success"]]
    (report,) = out["details"]["modal"]
    assert "Now: waiting</textarea>" in report and "Windows 11, language en" in report
    assert out["broken"]["modal"] == [] and out["broken"]["toasts"] == [["Something went wrong.", "error"]]
    assert out["ok"]["modal"] == [] and out["ok"]["toasts"] == [["Copied", "success"]]
    # each try copies the same text again (the link, then the code)
    assert out["copies"] == [link, link, link, "WXYZ-12345"]
    # a whole sign-in link is seen at once (as many lines as it takes, up to 7), not two lines of it
    (long,) = out["long"]["modal"]
    assert 'rows="7"' in long and 'class="err-txt cn-copyerr"' in long
    (linux,) = out["linux"]["modal"]
    assert "Windows" not in linux and "wl-copy, xclip or xsel" in linux and link in linux
    # the box is a block of its own (no empty line under it while there is no error), and the report keeps its words
    # whole ("lan-guage" with break-all) while a link, one long word, still wraps
    css = (WEB / "styles.css").read_text(encoding="utf-8")
    rule = next(line for line in css.splitlines() if line.startswith(".cn-copybox {"))
    assert "display: block" in rule and "overflow-wrap: anywhere" in rule and "break-all" not in rule
    assert ".cn-copyerr:empty { display: none; }" in css
