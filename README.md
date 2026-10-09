# Huzza Instagram Auto-Poster

A free scheduler for the Huzza Instagram account, running on GitHub. You (or Claude) put post files in a folder, and GitHub publishes them to Instagram at the time written in each file.

- **Free:** GitHub Actions and the Instagram API cost nothing for this.
- **Safe:** it uses Instagram's official API. Your password is never stored, so there's no ban risk.
- **Approve-first:** posts in `posts/drafts/` are never published. Moving a file into `posts/queue/` is how you approve it.
- Supports single images, carousels (2–10 items), Reels and Stories.

## How it works

```
posts/drafts/     ← new posts land here; nothing happens to them
posts/queue/      ← approved posts; published once their time arrives
posts/published/  ← posted, with the Instagram link added to the file
posts/failed/     ← anything Instagram rejected, with the reason added
media/            ← images and videos the posts use
```

Every 15 minutes GitHub checks `posts/queue/`. Any post whose `publish_at` time has passed is sent to Instagram, then moved to `published/` or `failed/`. Each run writes a short report you can read under the **Actions** tab.

A post file looks like this:

```yaml
account: huzza
type: image                     # image | carousel | reel | story
publish_at: 2026-10-20 18:00    # UAE time
caption: |
  Huzza is live. 🚀
  #Huzza #UAEstudents
alt_text: Huzza logo                # optional, images only
media:
  - media/launch.jpg           # a file in this repo, or a full https:// link
```

PNG and WebP images are converted to JPEG automatically, because Instagram only accepts JPEG.

---

## One-time setup (about 30–45 minutes)

### 1. Make your Instagram a professional account

In the Instagram app: **Settings → Account type and tools → Switch to professional account**. Business or Creator both work. A Business account suits Huzza best.

### 2. Create a free Meta developer app

1. Go to [developers.facebook.com](https://developers.facebook.com), log in with Facebook, and register as a developer if asked.
2. **My Apps → Create app.** Give it any name (e.g. "Huzza Poster").
3. When asked for a use case, pick **Manage messaging & content on Instagram**, then finish creating the app.
4. In the app's left menu open **Instagram → API setup with Instagram login**.
5. Under **Generate access tokens**, click **Add account** and log in to the Huzza Instagram account. Approve the permissions.
6. Click **Generate token** next to that account and copy the token. Keep it private; it's like a key to post on that account.

> If Instagram says the account isn't authorised: in the app go to **App roles → Roles**, add the account as an **Instagram Tester**, then accept the invite in Instagram under **Settings → Website permissions → Apps and websites → Tester invites**. Then repeat step 5.

The app can stay in **Development** mode. That's enough for posting to accounts you manage; App Review is only needed if strangers will use your app.

### 3. Put this project on GitHub

1. Create a free account at [github.com](https://github.com) if you don't have one.
2. Create a **new public repository**, e.g. `huzza-instagram`.
   - It must be **public**: Instagram downloads your images from GitHub, which only works for public repos, and public repos get unlimited free Actions minutes.
   - Your queued posts will be visible to anyone who finds the repo before they go live. If that matters for a launch, such as the 20 October go-live, use a full `https://` image link from elsewhere instead.
3. Upload everything from this folder, **including the hidden `.github` folder**. The easiest no-code way is [GitHub Desktop](https://desktop.github.com): clone the empty repo, copy these files into it, then **Commit** and **Push**.

### 4. Add the token as a secret

In the repo: **Settings → Secrets and variables → Actions → New repository secret**.

| Name | Value |
|---|---|
| `IG_TOKEN_HUZZA` | the token from step 2 |

The name must match `token_secret` in `accounts.yml`.

### 5. Keep tokens alive automatically (recommended)

Instagram tokens expire after 60 days. A weekly job renews them, but it needs permission to save the new token back into your secrets:

1. GitHub → your profile picture → **Settings → Developer settings → Personal access tokens → Fine-grained tokens → Generate new token**.
2. **Repository access:** only this repo. **Permissions → Secrets:** Read and write. Set the expiry as long as allowed.
3. Copy it and add it as another repo secret named `GH_PAT`.

Without this, generate a fresh token from the Meta dashboard every couple of months and paste it into the secret.

### 6. Test it

1. **Actions** tab → enable workflows if GitHub asks.
2. Open **Publish to Instagram → Run workflow**, tick **Only check the queue**, and run it. The report should list your queue with no errors.
3. For a real test, copy the example from `posts/drafts/` into `posts/queue/`, change `publish_at` to a few minutes from now, commit, and wait. It should appear on Instagram within about 15–20 minutes.

---

## Day-to-day use

1. Claude writes the post files and images (and can make visuals in Canva).
2. They go into `posts/drafts/`.
3. You look them over and move the ones you like into `posts/queue/`. In GitHub's website, open the file, click the pencil, and change `drafts` to `queue` in the file name box.
4. They publish on time, and the file moves to `posts/published/` with the Instagram link.

If something lands in `posts/failed/`, the `result: error:` line says why. Fix it and move the file back to `posts/queue/`.

## Good to know

- **Time zone:** every `publish_at` is read as **UAE time** (Asia/Dubai), since that's where Huzza's audience is. Add `timezone: Europe/London` to a single post to write that one in UK time.
- **Timing:** GitHub's scheduler can run a few minutes late at busy times, so a post set for 18:00 might go out at about 18:10. For the launch post, set it 15 minutes early.
- **Partner posts:** for Engagement Partner content, put the partner logo images in `media/` like any other image. Tagging the partner's account isn't supported by the API, so mention them in the caption or tag them in the app after it posts.
- **Limits:** Instagram allows 100 API posts per account per day, far more than you'll need. The bot posts at most 5 per run so a backlog doesn't flood your feed.
- **Not supported by Instagram's API:** filters, music on Reels, shopping tags, branded-content tags, and collaborator invites. Add those by hand in the app if needed.
- **Reels:** MP4, ideally 9:16, under 90 seconds. Big files are fine; Instagram downloads them from GitHub.
- **Inactive repos:** GitHub pauses scheduled jobs after 60 days with no commits. Publishing creates commits, so this only happens if you stop posting for two months; re-enable it in the Actions tab.
- **Run locally:** `pip install -r requirements.txt` then `python scripts/publish.py --dry-run` checks every queued file.
