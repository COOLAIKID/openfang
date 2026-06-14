# 📱 Run AutoEarn on your phone — 24/7, no computer needed

You can do all of this **from your phone right now.** It takes about 3 minutes.

> **One honest note:** your AI workers earn money by talking to AI brains
> (Groq, Gemini, etc.) over the internet. So the *workers* always need the
> internet to actually do work. What we set up below runs them in the cloud so
> they keep going 24/7 even with your phone off — and the app still **opens**
> on your phone when you have no signal (it shows the last numbers it saw).

---

## Step 1 — Put it in the cloud (one tap)

Tap this button on your phone:

[![Deploy to Render](https://render.com/images/deploy-to-render-button.svg)](https://render.com/deploy?repo=https://github.com/coolaikid/openfang)

1. Sign in with GitHub (free account).
2. When asked which branch, pick **`claude/repo-cleanup-wfqs9t`** (or merge it to
   `main` first and use `main`).
3. Render reads `render.yaml` and sets everything up automatically.

## Step 2 — Set your password + paste your keys (secure)

Render will ask for the values it needs:

| Key | What to put |
|-----|-------------|
| `AUTOEARN_PASSWORD` | **Pick any password** — this is what you'll type to sign in |
| `GROQ_API_KEY` | A free AI key from https://console.groq.com → API Keys |
| `GOOGLE_API_KEY` | (optional) https://aistudio.google.com/app/apikey |

Set **`AUTOEARN_PASSWORD`** (so only you can get in) and at least **one** AI key.
The publishing keys (WordPress / Medium / Telegram / Reddit) are optional — add
any you have so the workers can post for you. These are typed into **Render's**
secure settings, never into chat or this repo.

Tap **Create / Deploy**. Wait ~2 minutes for it to go live.

## Step 3 — Sign in & install the app on your phone

Render gives you a web address like `https://autoearn-xxxx.onrender.com`.

1. Open that link in your phone browser.
2. **Sign in** with the password you just set.
3. **iPhone:** tap **Share → Add to Home Screen.**
   **Android:** tap **⋮ menu → Install app / Add to Home Screen.**

Now AutoEarn is an icon on your home screen. It opens like a real app, stays
signed in, and even with no signal it still opens (showing the last data)
instead of a blank error.

That's it — your 21 workers are now running around the clock. ✅

---

## Why "free" still runs 24/7
Free cloud instances normally fall asleep when nobody's looking. AutoEarn quietly
pings itself every 10 minutes (using the public URL Render provides) so it stays
awake and the workers never stop. No setup needed — it just works.

## Want it on your home Wi-Fi instead?
Run `./autoearn.sh` (Mac/Linux) or `autoearn.bat` (Windows) on a computer, then
open `http://<that-computer-ip>:4200` on your phone. Same installable app.
