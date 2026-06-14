# 💸 AutoEarn — your AI money machine

A team of 21 AI workers that run around the clock trying to make money for you —
writing, building, posting, and improving themselves — all managed from one clean
app on your **phone** or **computer**. The AI handles 99% of it; you just watch,
chat, and steer.

---

## ▶️ Open it right now — 3 taps on your phone

**Open in GitHub Codespace** (free, no install, works on any phone or computer):

[![Open in GitHub Codespaces](https://github.com/codespaces/badge.svg)](https://codespaces.new/coolaikid/openfang?quickstart=1)

1. Tap the button above → **Create new codespace** (GitHub account required — free).
2. Wait ~2 minutes while it sets up.
3. A public HTTPS link appears automatically — **that's your dashboard URL**. Bookmark it or add it to your home screen.

> Add your AI keys (Groq, etc.) in the **Capabilities** tab once you're in.
> The Codespace sleeps after 30 min of inactivity but wakes up the moment you
> open the link again. For always-on 24/7, use the Render deploy below.

---

## ☁️ Always-on 24/7 cloud (stays awake even when you're offline)

1. Tap **Deploy to Render** (free):

   [![Deploy to Render](https://render.com/images/deploy-to-render-button.svg)](https://render.com/deploy?repo=https://github.com/coolaikid/openfang)

2. Sign in with GitHub → Render reads `render.yaml` and sets everything up.
3. Enter two things when asked:
   - **`AUTOEARN_PASSWORD`** — pick any password (this is how you sign in).
   - **`GROQ_API_KEY`** — a free AI key from <https://console.groq.com>.
4. When it's live, open the URL it gives you → **sign in** → **Add to Home Screen**.

That's it. Workers run 24/7, and the app even opens (showing the last data)
when you have no signal.

👉 Full walkthrough with pictures: **[RUN_ON_YOUR_PHONE.md](RUN_ON_YOUR_PHONE.md)**

---

## 💻 On your computer (a real app icon)

Install it once and you get an **AutoEarn icon** in your Applications / Start
Menu. Click it and the dashboard opens in your browser — that's it.

```bash
git clone https://github.com/coolaikid/openfang
cd openfang
./install-app.sh          # macOS / Linux
```

On **Windows**, run this in PowerShell instead:

```powershell
powershell -ExecutionPolicy Bypass -File install-app.ps1
```

Now find **AutoEarn** in your apps and click it. The first click sets things up
(~1 minute), then the dashboard opens automatically — and every click after is
instant. No password is needed for local use; set `AUTOEARN_PASSWORD` first if
you want a sign-in prompt.

> **Want it on your phone over home Wi-Fi?** Run the command above, then open
> `http://<your-computer-ip>:4200` on your phone — same app.

---

## 🔗 Connect your own computer to the cloud dashboard

Want your cloud dashboard (on your phone) to run agents and tasks on your *home
computer*? Connect it once:

```bash
./connect-to-cloud.sh        # macOS / Linux   (connect-to-cloud.bat on Windows)
```

It asks for your dashboard URL + password, then dials **out** to the cloud — so
it works behind any home Wi-Fi with no ports to open. Leave it running. Your
computer now appears under **Private Workspaces → Your computers**, where you can
run a command on it or have agents run there — straight from your phone.

---

## What you'll see

- **Chat** — talk to your AI team, give direction, ask what they're working on.
- **Your AI Team** — all 21 workers grouped by role, with live status.
- **Earnings & Activity** — total earned, plus a **real-time timeline** that shows
  each worker's every step as it happens ("Creating a file…" → "Created a file ✓").
- **Capabilities** — what your team can do, and skills you can add.
- **Private Workspaces** — each worker's own isolated environment.

## One honest note

Your workers think by talking to AI brains (Groq, Gemini, etc.) over the
internet, so they need a connection to actually *do* work. The cloud setup keeps
them running 24/7 regardless of whether your phone is on; the app itself still
*opens* offline and shows the last data it saw.

---

## How it works (for the curious)

- **21 agents**: 5 leadership (council), 12 specialists across Content / Dev /
  Market / Outreach teams, and 4 quality reviewers that gate every output.
- **All local data**: SQLite + plain files. Your keys live only in your own
  environment (or your host's secret settings), never in this repo.
- **Self-improving**: agents can rewrite their own goals, schedules, and tools.
- **Two run modes**: each agent in its own Docker container (`docker compose up -d`),
  or all in one process (the default) — auto-detected at startup.

See **[RUN_ON_YOUR_PHONE.md](RUN_ON_YOUR_PHONE.md)** to deploy, or just run
`./autoearn.sh` to start locally.
