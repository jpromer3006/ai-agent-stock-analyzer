# OROS Market Desk on YouTube — lunch-break setup (~10 minutes)

When done, every cloud run auto-publishes the day's video as an **unlisted**
episode and puts the watch link in your email. Unlisted = only people with
the link can see it (professors, Nathan). Going public later is one toggle —
but talk to a securities attorney first (see the advising discussion).

## Step 1 — The channel (2 min)
Your Google account already has a personal channel (@JP.Romero3006), so
YouTube won't show "Create a channel". Make a separate BRAND channel:

1. Signed in as **jp.romero3006@gmail.com**, go to **youtube.com/channel_switcher**
   (or avatar → Settings → "Add or manage your channel(s)").
2. Click **Create a channel** → name it **OROS Market Desk**.
3. IMPORTANT for Step 3 later: when Google's consent screen asks which
   account/channel to authorize, pick **OROS Market Desk** — that's what
   routes the uploads to the brand channel instead of your personal one.

## Step 2 — Google Cloud credentials (5 min)
1. Go to https://console.cloud.google.com (same Google account).
2. Top bar → project picker → **New project** → name `oros-desk` → Create.
3. Search "**YouTube Data API v3**" → Enable.
4. Left menu → **APIs & Services → OAuth consent screen**:
   - User type **External** → Create.
   - App name `OROS`, your email for both contact fields → Save through
     the steps (no scopes needed here) → back to dashboard.
   - Under **Test users** → Add `jp.romero3006@gmail.com`. (Testing mode is
     fine forever for a single-user app; refresh tokens for test users
     expire after 7 days ONLY on "external/testing" — so ALSO click
     **Publish app** on the consent screen to move to "In production".
     No verification is needed for the upload scope with your own account.)
5. **APIs & Services → Credentials → Create credentials → OAuth client ID**:
   - Application type **Desktop app**, name `oros-uploader` → Create.
   - Copy the **Client ID** and **Client secret**.

## Step 3 — Authorize once (2 min, on the Mac)
Open Terminal:

```bash
cd "/Users/jpromero/Ai-Agent Oros"
python3 scripts/yt_authorize.py PASTE_CLIENT_ID PASTE_CLIENT_SECRET
```

Your browser opens → choose jp.romero3006 → Allow. The script prints
SUCCESS and writes the three `YT_*` lines into `.env`.

## Step 4 — GitHub secrets (2 min)
Repo → Settings → Secrets and variables → Actions → New repository secret,
three times (values are now in `.env` if you need to copy them):

```
YT_CLIENT_ID
YT_CLIENT_SECRET
YT_REFRESH_TOKEN
```

Done. The next cloud run uploads the episode and the email gains a
"Watch today's episode" link. Verify on the channel's Content page.

## Troubleshooting
- "Access blocked / app not verified": you skipped Publish app in Step 2.4,
  or the signed-in account isn't the test user.
- Email says "YouTube: skipped (... not configured)": secrets missing or
  misnamed on GitHub.
- Want it off temporarily: set repo secret/env `OROS_YT=0`.
