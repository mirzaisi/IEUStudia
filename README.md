<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="docs/logo-dark.svg">
    <img src="src/studia/assets/logo.svg" width="92" height="92" alt="IEUStudia logo">
  </picture>
</p>

<h1 align="center">IEUStudia</h1>

<p align="center">
  A Windows app for İzmir University of Economics students.<br>
  It puts your IEULearn courses in one window, turns your Panopto lectures into searchable text on your own PC,<br>
  and keeps track of what your lecturers say about quizzes and exams.
</p>

<p align="center">
  <a href="https://github.com/mirzaisi/IEUStudia/releases/latest"><img alt="Latest release" src="https://img.shields.io/github/v/release/mirzaisi/IEUStudia?label=download&color=111111"></a>
  <a href="https://github.com/mirzaisi/IEUStudia/actions/workflows/ci.yml"><img alt="Tests" src="https://github.com/mirzaisi/IEUStudia/actions/workflows/ci.yml/badge.svg"></a>
  <a href="LICENSE"><img alt="MIT license" src="https://img.shields.io/badge/license-MIT-111111"></a>
</p>

<p align="center">
  <a href="https://github.com/mirzaisi/IEUStudia/releases/latest/download/IEUStudia-Setup.exe"><b>Download IEUStudia for Windows</b></a>
  &nbsp;·&nbsp; <a href="#installing">How to install it</a>
</p>

<p align="center">
  <img src="docs/demo.gif" width="880" alt="A short tour with made-up data: the home screen, a course with what's coming up, a transcribed lecture with its slides, a search across all lectures and a chat about them">
</p>

> [!NOTE]
> IEUStudia is an unofficial project made by students. İzmir University of Economics, IEULearn and Panopto have
> nothing to do with it and don't support it.

## Why it exists

Everything you need for a course is on IEULearn, but it's spread out. The slides are on one page, the recording is
somewhere in Panopto, and the announcement about Thursday's quiz is buried under three others. When a lecturer says
"this will be on the midterm", that sentence is somewhere in a 75-minute video.

IEUStudia collects what you can already open with your own account, sorts it by course and week, and transcribes the
lectures on your computer, so you can search them and jump straight to the moment something was said.

## What you can do with it

**See each course week by week.** Every course gets a page with its weekly topics (from the syllabus, in English and
Turkish), the slides and PDFs, the lecture recordings of your own section and the announcements. The learning
outcomes and the assessment plan are there too, so you can see which quiz covers what.

**Read and search your lectures.** Pick a lecture and it's transcribed on your PC with Whisper. Lectures are mostly in
English; Turkish parts are kept the way they were said and marked `[TR]`. Each paragraph links back to its moment in
the recording, and the slides from the screen recording show up next to the text. One search box covers all your
lectures and course files, in English or Turkish. A long queue can be paused, the lectures you ask for go before a
full sync's batch, and if you close IEUStudia before it's done, it offers to go on next time. Mark a paragraph to come
back to it, with a note if you like; your marks show up again in *Get ready* for the weeks a quiz covers.

**Don't miss a quiz.** Whenever a lecture or an announcement mentions a quiz, the midterm, the final, homework or a
change in the schedule, it shows up on the course page. Before a quiz, *Get ready* lists the lectures to catch up on,
the files to download and what the lecturer said about it. The evening before an announced quiz, IEUStudia reminds
you. Quizzes and exams can go into your calendar, and a weekly digest sums up what happened.

**Get a notification when something new appears.** If you want, IEUStudia keeps checking IEULearn every half hour
after you close its window (its icon stays next to the clock) and shows a Windows notification for new recordings,
announcements and files. It can start with Windows. It never transcribes or downloads anything on its own.

**In English or Turkish.** IEUStudia speaks the language of your Windows (Turkish on a Turkish Windows, English
otherwise). You can switch in *Settings → Appearance* or on the first screen of the setup. Transcripts and AI notes
stay in the language of the lecture, and in a chat the AI answers in the language you ask in.

**Use AI with your own subscription.** With ChatGPT or Claude you can chat about your lectures (answers point to the
exact moment they come from), write study notes or flashcards (and practise them one at a time), put together an exam
review sheet, have a study pack written (a PDF that teaches everything in the files and lectures you pick, with worked
examples, diagrams and your slides), make a cheatsheet (every formula, definition and method of what you pick, compact:
a few weeks fit on one or two A4 pages, a whole term on a few more), or make a video study plan: YouTube videos for your
weeks, each one checked through its captions against what your course actually covers. All of this is optional, see
[AI features](#ai-features).

<table>
  <tr>
    <td width="50%"><img src="docs/screenshots/course.png" alt="A course page"><br><sub>A course, week by week, with what's coming up.</sub></td>
    <td width="50%"><img src="docs/screenshots/transcript.png" alt="A transcribed lecture"><br><sub>A transcribed lecture. A timestamp opens the recording at that moment.</sub></td>
  </tr>
  <tr>
    <td width="50%"><img src="docs/screenshots/get-ready.png" alt="The Get ready checklist"><br><sub>Getting ready for a quiz.</sub></td>
    <td width="50%"><img src="docs/screenshots/plan.png" alt="A video study plan"><br><sub>A video study plan, with the caption quotes it relied on.</sub></td>
  </tr>
</table>

## How it works

```mermaid
flowchart LR
    ieu["IEULearn<br/>courses, files, announcements"] -- "your EkoID sign-in" --> app
    pan["Panopto<br/>lecture recordings"] --> app
    app["IEUStudia<br/>on your PC"] --> whisper["Whisper<br/>transcribes on your PC"]
    whisper --> lib["Your library<br/>transcripts, slides, heads-ups"]
    lib -. "only when you ask" .-> ai["ChatGPT or Claude<br/>with your own account"]
```

Everything runs on your computer. There's no IEUStudia server and no account with us. Your EkoID password goes only
to learn.ieu.edu.tr, the same as when you sign in on the website.

## Installing

1. Download [IEUStudia-Setup.exe](https://github.com/mirzaisi/IEUStudia/releases/latest/download/IEUStudia-Setup.exe).
   This link always gets the newest version (it's also on the [releases page](https://github.com/mirzaisi/IEUStudia/releases/latest)).
   The installer isn't code-signed yet, and few people have downloaded it so far, so your browser may say it
   "isn't commonly downloaded". In Edge, open the download list, click *⋯* next to the file, then *Keep*, *Show
   more* and *Keep anyway*. In Chrome, click *Keep* (or *Download anyway*).
2. Run it. It installs for your Windows account only, so you don't need admin rights. Windows SmartScreen will
   probably warn you as well: click *More info*, then *Run anyway*. If you want to check the file first, each
   release lists its SHA-256 checksum.
3. Open IEUStudia. The first start takes you through a few short steps:
   - a notice about what the app does with your data,
   - a check of your PC (if it can't run IEUStudia, it tells you why and stops there),
   - the speech model for transcription,
   - AI, if you want it,
   - background checks and notifications,
   - the downloads, with their sizes shown before anything starts,
   - and signing in to IEULearn.

Once it's installed, IEUStudia tells you when there's a new version and updates itself when you click
*Update*. You don't download anything yourself, Windows doesn't warn you again, and your courses and transcripts
stay. Each release also has a portable zip
if you'd rather not install anything (that one doesn't update itself). Windows marks a downloaded zip as coming from
the internet, and blocks some of the files you extract from it; IEUStudia unblocks its own files when it starts. If
it says it can't, right-click the zip, choose *Properties*, tick *Unblock* and extract it again.

### What your PC needs

|  | Minimum | Better |
|---|---|---|
| Windows | 10 (version 1809) or 11, 64-bit | 11 |
| Processor | x64 with AVX | 8 or more threads, with AVX2 |
| Memory | 4 GB | 8 GB or more |
| Free disk space | about 3 GB | about 10 GB with the offline AI model |
| Graphics card | not needed | NVIDIA with 4 GB or more |

The Microsoft Edge WebView2 Runtime is needed as well. Windows 11 and up-to-date Windows 10 already have it, and the
installer checks.

Transcription speed depends mostly on the graphics card. With a recent NVIDIA card, an hour of lecture takes a few
minutes. Without one, the setup picks the small speech model, which runs on the processor: an hour of lecture then
takes roughly 20 minutes to an hour, depending on the processor. It runs in the background, so you can keep using
the app (or your PC) meanwhile.

### On Linux

IEUStudia also runs on Linux from its source (tested on Ubuntu 26.04 with GNOME). There's no installer: you need
[uv](https://docs.astral.sh/uv/), and then

```bash
git clone https://github.com/mirzaisi/IEUStudia.git
cd IEUStudia
uv sync                        # add --extra gpu for an NVIDIA graphics card (cuBLAS and cuDNN, about 1.3 GB)
uv run studia-gui              # the app; `uv run studia --help` lists the command-line version
```

What is different from Windows:

- **Your EkoID password** is encrypted with a key kept in your desktop's keyring (GNOME Keyring or KWallet),
  instead of Windows DPAPI. Without a keyring, IEUStudia doesn't store it at all and asks for it again.
- **The window** is Qt WebEngine instead of WebView2 (installed by `uv sync`).
- **Background checks** run without an icon next to the clock (GNOME has none); their notifications come from your
  desktop, and *Start at login* adds an entry to `~/.config/autostart`.
- **Claude** gets a Claude Code profile of its own (`~/.local/share/IEUStudia/tools/claude-config`), so you sign in
  once in the setup and your own Claude Code settings, CLAUDE.md and hooks never come along. The Codex CLI isn't
  installed for you: install it with `npm install -g @openai/codex`.
- **Data** is in `~/.local/share/IEUStudia`, transcripts and notes in `~/Documents/IEUStudia`.
- **Updates**: `git pull` and `uv sync`. The app's own updater only installs the Windows version.

Linux support is looked after by the community: the Linux tests run in CI but don't block a change.

## AI features

AI is optional. Transcripts, heads-ups, search and everything else work without it. There are five choices:

| Choice | What you need | What you get |
|---|---|---|
| ChatGPT | A ChatGPT account whose plan apps may use: Plus and Pro can; for Free and Go, OpenAI decides | Everything: chat, notes, flashcards, review sheets, study packs, cheatsheets, video study plans. |
| Claude | A Claude Pro or Max subscription | The same features. |
| ChatGPT through the Codex CLI | A ChatGPT plan with Codex (Free and Go have smaller limits) | The same features, for people who already use OpenAI's Codex CLI. |
| Offline model | Nothing to pay for, but at least 8 GB of memory | Short notes and simple questions, on your PC (Qwen3 1.7B or 4B). No study plans. |
| No AI | Nothing | The AI buttons are hidden. |

You can change your choice any time in *Settings → AI*.

### How ChatGPT and Claude are connected

You don't need the ChatGPT or Claude desktop apps, and IEUStudia never sees your ChatGPT or Claude password. When you
pick one of them, the setup shows a short checklist and goes through it with you. You don't have to open a terminal.

**ChatGPT** uses *Continue with ChatGPT*, OpenAI's sign-in for open-source apps. You sign in on OpenAI's page in your
browser and allow IEUStudia to use your ChatGPT plan; there's nothing to install. You can see how much IEUStudia
uses, set a limit or disconnect it any time in [ChatGPT's settings](https://chatgpt.com/settings/usage). On Plus,
apps share the plan's five-hour usage limit with each other.

**Claude** works through [Claude Code](https://code.claude.com/docs), Anthropic's official command-line tool, because
Anthropic only allows signing in with a Claude subscription through its own apps. The checklist installs Claude Code
from claude.ai, opens Anthropic's sign-in page, checks that your plan includes Claude Code, and sends a short test
message.

**ChatGPT through the Codex CLI** is for people who already use OpenAI's [Codex CLI](https://github.com/openai/codex).
The checklist works like Claude's: it installs the Codex CLI if it's missing (from OpenAI's own package, without
Node.js), opens its sign-in and sends a short test message.

<p align="center"><img src="docs/screenshots/setup-ai.png" width="640" alt="The checklist that connects ChatGPT: Continue with ChatGPT, then your plan and a test message"></p>

If something goes wrong, the step that failed says why, and *Settings → AI* has the same checklist for later.

AI only runs when you ask for something, and it counts against your plan's normal usage limits. AI can also be wrong.
Answers point to the lecture moments they're based on so you can check them, and video study plans show the caption
quotes behind every video they recommend.

## Privacy

- Your EkoID password is sent only to learn.ieu.edu.tr. On your PC it's stored encrypted with Windows DPAPI, so only
  your Windows account can read it.
- There's no IEUStudia server, no account, no analytics and no tracking.
- IEUStudia connects to IEULearn and Panopto (your courses and recordings), the public İEÜ syllabus pages, Hugging Face
  (to download the speech model and the offline AI model, once), PyPI (NVIDIA's GPU libraries, once, only if you have
  an NVIDIA card), YouTube (search, captions and channel pictures, only for video study plans and the channel lists in
  Settings), and Anthropic or OpenAI (only when you use an AI feature; the lecture text you chose is sent to them under
  your account), and GitHub (see below). Setting up Claude downloads Claude Code from claude.ai; signing in with ChatGPT
  happens on auth.openai.com.
- An installed copy looks at IEUStudia's newest release on GitHub about once a day, so it can tell you about a new
  version. The request says which IEUStudia version is asking and nothing about you or your courses. You can turn it
  off in *Settings → About*. When you choose to update, the installer is downloaded from GitHub and checked against
  the release's SHA-256 checksums before it runs.
- The ChatGPT sign-in is kept on your PC encrypted with DPAPI, like your EkoID password. It only allows requests on
  your plan, not access to your ChatGPT conversations.
- App data (settings, the encrypted login, your library, models, logs) is kept in `%LOCALAPPDATA%\IEUStudia`.
  Transcripts, notes and downloaded files go to `Documents\IEUStudia`.
- The services IEUStudia uses have their own privacy policies:
  [GitHub](https://docs.github.com/en/site-policy/privacy-policies/github-general-privacy-statement),
  [Hugging Face](https://huggingface.co/privacy), [PyPI](https://policies.python.org/pypi.org/Privacy-Notice/),
  [YouTube (Google)](https://policies.google.com/privacy), [OpenAI](https://openai.com/policies/privacy-policy/) and
  [Anthropic](https://www.anthropic.com/legal/privacy). IEULearn and Panopto are run for your university and fall
  under its rules.

## Uninstalling

Use *Windows Settings → Apps*. The uninstaller asks whether to delete IEUStudia's data too. Your transcripts and notes
in Documents are never deleted.

## Questions

**Is this allowed?**
IEUStudia uses your own IEULearn account and only gets what you could already open in your browser. Recordings and
slides belong to your lecturers and the university, so keep what you download for your own studying and don't share
it. Follow your university's rules.

**Do I need a powerful PC?**
No. The setup checks your PC and picks a speech model that fits. An NVIDIA graphics card just makes transcription a
lot faster.

**Do I need the ChatGPT or Claude app?**
No, you need the subscription, not the app. ChatGPT connects with a sign-in in your browser. For Claude, IEUStudia
installs Claude Code for you during setup (see [above](#how-chatgpt-and-claude-are-connected)).

**Does the free ChatGPT or Claude plan work?**
For ChatGPT, maybe: OpenAI decides which plans apps like IEUStudia may use. Plus and Pro can; Free and Go come with
smaller limits, if OpenAI allows them at all. The setup sends a short test message, which shows whether yours works.
Claude needs Claude Pro or Max (Claude Code isn't part of the free plan). Everything else in IEUStudia works without
any subscription, and the offline model, which runs on your PC, is free.

**Does it work on a Mac or on Linux?**
On Linux, yes, from the source: see [On Linux](#on-linux). Not on a Mac yet.

**Something stopped working after IEULearn changed.**
That can happen. Please [open an issue](https://github.com/mirzaisi/IEUStudia/issues) and describe what you were
doing. Leave out your name, student number and course content, also in screenshots.

## Building it yourself

You need Windows and [uv](https://docs.astral.sh/uv/).

```powershell
uv sync --group dev            # a Python 3.12 environment in .venv
uv run studia --debug          # run the app with developer tools
uv run pytest -q               # run the tests
.\scripts\build_release.ps1    # the installer and the portable zip, in build\installer
```

The interface is plain HTML, CSS and JavaScript in `src/studia/web`. Open `index.html` in a browser to work on it with
made-up data; `#dark`, `#signin` and `#setup` show other states. Setting `STUDIA_HOME` to a folder runs the app with
separate data (the tests do this), `STUDIA_FAKE_ENGINE=1` replaces Whisper with a stub, and `STUDIA_UPDATE_FEED`
points the update check at another release feed (a URL answering like GitHub's latest-release API), for testing. `install.cmd` installs a
development build for your Windows account.

Releases are built by GitHub Actions: pushing a version tag such as `v2.1.0` builds the installer and the zip and
attaches them to a draft release.

<details>
<summary>Where things are in the code</summary>

| Part | Files |
|---|---|
| IEULearn and Panopto | `ieulearn.py`, `panopto.py`, `coursesite.py`, `syllabus.py`, `sync.py` |
| Transcription | `engine/`, `jobs.py`, `quality.py`, `slides.py`, `outputs/` |
| Study features | `alerts.py`, `catchup.py`, `plans.py`, `videos.py`, `digest.py`, `calendar_export.py` |
| AI | `ai.py`, `ai_setup.py`, `local_llm.py`, `chat.py`, `knowledge.py` |
| Desktop | `app.py`, `api.py`, `splash.py`, `agent.py`, `tray.py`, `hardware.py`, `components.py` |
| Interface | `web/` |

</details>

## Contributing

Issues and pull requests are welcome, and [CONTRIBUTING.md](CONTRIBUTING.md) explains how to get started. Please
never put real student data, passwords or lecture content in issues, tests or screenshots. Security problems should
be reported privately, as described in [SECURITY.md](SECURITY.md).

## Disclaimer

IEUStudia is an independent project. It isn't affiliated with, endorsed by or supported by İzmir University of
Economics, IEULearn or Panopto; their names are only used to say what the app works with. It's provided as is,
without any warranty (see the [license](LICENSE)), and its authors aren't responsible for how it's used or for any
data you lose. AI features can make mistakes, so check AI answers against your lectures and materials.

## License

IEUStudia is released under the [MIT License](LICENSE). The parts it builds on keep their own licenses:
faster-whisper and the Whisper models (MIT), CTranslate2 (MIT), the Qwen3 models (Apache-2.0), pywebview
(BSD-3-Clause), PyAV (BSD-3-Clause), yt-dlp (Unlicense) and pypdfium2 (Apache-2.0 or BSD-3-Clause). NVIDIA's CUDA
libraries are downloaded from NVIDIA's own packages on PyPI and are covered by NVIDIA's license.

The Windows build also contains Python (PSF License); FFmpeg through PyAV, in a build that includes x264 and x265 and
is therefore under the GPL; CTranslate2's Intel oneMKL and OpenMP runtime and NVIDIA's cuDNN loader (their makers'
licenses); Microsoft's WebView2 files and the Visual C++ runtime (Microsoft's licenses); and PyInstaller's
bootloader (GPL-2.0 with an exception that allows bundling any program). The installer is made with Inno Setup. Claude Code and the
Codex CLI are installed from their official sources and come under Anthropic's and OpenAI's own terms. IEUStudia
isn't made or endorsed by Anthropic or OpenAI.
