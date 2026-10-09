#!/usr/bin/env python3
"""
Instagram auto-publisher.

Reads post files from posts/queue/, publishes any that are due through the
official Instagram API (Instagram Login, graph.instagram.com), then moves them
to posts/published/ (or posts/failed/) with the result recorded in the file.

Runs on GitHub Actions on a schedule, but works locally too:

    python scripts/publish.py --dry-run      # show what would post, call nothing
    python scripts/publish.py                # publish everything that is due

Tokens come from environment variables named in accounts.yml.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
import yaml

ROOT = Path(__file__).resolve().parent.parent
QUEUE = ROOT / "posts" / "queue"
PUBLISHED = ROOT / "posts" / "published"
FAILED = ROOT / "posts" / "failed"
MEDIA_OUT = ROOT / "posts" / "_converted"

GRAPH = "https://graph.instagram.com"
API_VERSION = os.environ.get("IG_API_VERSION", "v25.0")
DEFAULT_TZ = "Asia/Dubai"  # UAE time; an account or post can override it

IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp", ".heic"}
VIDEO_EXT = {".mp4", ".mov"}
POST_TYPES = {"image", "carousel", "reel", "story"}
CAPTION_MAX = 2200
HASHTAG_MAX = 30


class PostError(Exception):
    """A problem with a post file the user needs to fix."""


# ---------------------------------------------------------------- config

def load_accounts() -> dict:
    path = ROOT / "accounts.yml"
    if not path.exists():
        raise SystemExit("accounts.yml is missing")
    data = yaml.safe_load(path.read_text()) or {}
    return data.get("accounts", {})


def secret(name: str) -> str | None:
    """Read a secret from env directly, or from the SECRETS_JSON blob the workflow passes."""
    if os.environ.get(name):
        return os.environ[name]
    blob = os.environ.get("SECRETS_JSON")
    if blob:
        try:
            return json.loads(blob).get(name)
        except json.JSONDecodeError:
            return None
    return None


# ---------------------------------------------------------------- post files

@dataclass
class Post:
    path: Path
    data: dict
    account: str = ""
    type: str = "image"
    publish_at: datetime | None = None
    caption: str = ""
    media: list[str] = field(default_factory=list)

    @property
    def name(self) -> str:
        return self.path.name


def parse_time(value, tz_name: str) -> datetime:
    tz = ZoneInfo(tz_name)
    if isinstance(value, datetime):
        dt = value
    else:
        text = str(value).strip().replace("T", " ")
        dt = None
        for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
            try:
                dt = datetime.strptime(text, fmt)
                break
            except ValueError:
                continue
        if dt is None:
            raise PostError(f"publish_at '{value}' should look like 2026-10-14 18:30")
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=tz)
    return dt


def load_post(path: Path, accounts: dict) -> Post:
    try:
        data = yaml.safe_load(path.read_text()) or {}
    except yaml.YAMLError as e:
        raise PostError(f"not valid YAML: {e}")

    post = Post(path=path, data=data)
    post.account = str(data.get("account", "")).strip()
    if not post.account:
        raise PostError("missing 'account'")
    if post.account not in accounts:
        raise PostError(f"account '{post.account}' isn't listed in accounts.yml")

    post.type = str(data.get("type", "image")).lower().strip()
    if post.type not in POST_TYPES:
        raise PostError(f"type must be one of {sorted(POST_TYPES)}")

    if "publish_at" not in data:
        raise PostError("missing 'publish_at'")
    tz_name = data.get("timezone") or accounts[post.account].get("timezone") or DEFAULT_TZ
    post.publish_at = parse_time(data["publish_at"], tz_name)

    post.caption = str(data.get("caption") or "").strip()
    if len(post.caption) > CAPTION_MAX:
        raise PostError(f"caption is {len(post.caption)} characters; Instagram's limit is {CAPTION_MAX}")
    if post.caption.count("#") > HASHTAG_MAX:
        raise PostError(f"caption has more than {HASHTAG_MAX} hashtags")

    media = data.get("media") or []
    if isinstance(media, str):
        media = [media]
    post.media = [str(m).strip() for m in media if str(m).strip()]
    if not post.media:
        raise PostError("needs at least one item under 'media'")

    if post.type == "carousel" and not (2 <= len(post.media) <= 10):
        raise PostError("a carousel needs between 2 and 10 media items")
    if post.type in {"image", "reel", "story"} and len(post.media) != 1:
        raise PostError(f"a {post.type} post takes exactly one media item")
    if post.type == "image" and is_video(post.media[0]):
        raise PostError("type 'image' got a video; use type 'reel'")
    if post.type == "reel" and not is_video(post.media[0]):
        raise PostError("type 'reel' needs a video (.mp4 or .mov)")

    for m in post.media:
        if not is_url(m):
            local = ROOT / m
            if not local.exists():
                raise PostError(f"media file not found: {m}")
    return post


def is_url(s: str) -> bool:
    return s.startswith("http://") or s.startswith("https://")


def is_video(s: str) -> bool:
    return Path(s.split("?")[0]).suffix.lower() in VIDEO_EXT


# ---------------------------------------------------------------- media hosting

def ensure_jpeg(rel_path: str) -> str:
    """Instagram only accepts JPEG images. Convert anything else and return the new repo path."""
    src = ROOT / rel_path
    if src.suffix.lower() in {".jpg", ".jpeg"}:
        return rel_path
    from PIL import Image

    MEDIA_OUT.mkdir(parents=True, exist_ok=True)
    dest = MEDIA_OUT / (src.stem + ".jpg")
    with Image.open(src) as im:
        if im.mode in ("RGBA", "LA", "P"):
            im = im.convert("RGBA")
            bg = Image.new("RGB", im.size, (255, 255, 255))
            bg.paste(im, mask=im.split()[-1])
            im = bg
        else:
            im = im.convert("RGB")
        im.save(dest, "JPEG", quality=92)
    return str(dest.relative_to(ROOT))


def git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=ROOT, check=True,
                          capture_output=True, text=True).stdout.strip()


def commit_and_push(message: str, *paths: str) -> None:
    """Commit the given paths if anything changed, then push (rebasing on top of any new commits)."""
    git("add", "-A", *paths)
    staged = subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=ROOT)
    if staged.returncode != 0:
        git("commit", "-m", message)
    git("pull", "--rebase", "--quiet")
    git("push", "--quiet")


def public_url(rel_path: str, sha: str) -> str:
    """Raw GitHub URL for a file in this repo, pinned to a commit so it can't go stale."""
    repo = os.environ.get("GITHUB_REPOSITORY")
    if not repo:
        raise PostError("local media files need GITHUB_REPOSITORY set (it is, automatically, on GitHub Actions)")
    return f"https://raw.githubusercontent.com/{repo}/{sha}/{rel_path}"


def resolve_media(posts: list[Post], push: bool) -> dict[str, str]:
    """Convert images where needed, push any new files, and map each media entry to a public URL."""
    converted: dict[str, str] = {}
    for p in posts:
        for m in p.media:
            if not is_url(m) and not is_video(m):
                converted[m] = ensure_jpeg(m)

    new_files = [v for k, v in converted.items() if k != v]
    if new_files and push:
        commit_and_push("Convert images to JPEG for Instagram", *new_files)
    sha = git("rev-parse", "HEAD") if push else "LOCAL"

    urls: dict[str, str] = {}
    for p in posts:
        for m in p.media:
            if is_url(m):
                urls[m] = m
            else:
                urls[m] = public_url(converted.get(m, m), sha) if push else f"(local) {converted.get(m, m)}"
    return urls


# ---------------------------------------------------------------- Instagram API

class Instagram:
    def __init__(self, token: str, session: requests.Session | None = None):
        self.token = token
        self.s = session or requests.Session()
        self._user_id: str | None = None

    def _call(self, method: str, path: str, **params) -> dict:
        url = f"{GRAPH}/{API_VERSION}/{path}"
        params["access_token"] = self.token
        r = self.s.request(method, url, params=params, timeout=60)
        try:
            body = r.json()
        except ValueError:
            body = {"raw": r.text}
        if r.status_code >= 400 or "error" in body:
            err = body.get("error", body)
            msg = err.get("message", str(err)) if isinstance(err, dict) else str(err)
            raise RuntimeError(f"Instagram API error on {path}: {msg}")
        return body

    @property
    def user_id(self) -> str:
        if not self._user_id:
            me = self._call("GET", "me", fields="user_id,username")
            self._user_id = str(me.get("user_id") or me["id"])
        return self._user_id

    def quota_left(self) -> int | None:
        try:
            res = self._call("GET", f"{self.user_id}/content_publishing_limit",
                             fields="quota_usage,config")
            item = (res.get("data") or [{}])[0]
            total = item.get("config", {}).get("quota_total", 100)
            return int(total) - int(item.get("quota_usage", 0))
        except Exception:
            return None

    def container(self, **params) -> str:
        return self._call("POST", f"{self.user_id}/media", **params)["id"]

    def wait_ready(self, container_id: str, timeout_s: int = 300, every_s: int = 10) -> None:
        waited = 0
        while True:
            status = self._call("GET", container_id, fields="status_code").get("status_code")
            if status in ("FINISHED", "PUBLISHED"):
                return
            if status in ("ERROR", "EXPIRED"):
                raise RuntimeError(f"Instagram couldn't process the media (status {status})")
            if waited >= timeout_s:
                raise RuntimeError("Instagram is still processing the media after 5 minutes")
            time.sleep(every_s)
            waited += every_s

    def publish(self, container_id: str) -> str:
        return self._call("POST", f"{self.user_id}/media_publish", creation_id=container_id)["id"]

    def permalink(self, media_id: str) -> str | None:
        try:
            return self._call("GET", media_id, fields="permalink").get("permalink")
        except Exception:
            return None


def media_params(url: str, *, kind: str) -> dict:
    if is_video(url):
        return {"video_url": url, "media_type": "REELS" if kind == "reel" else
                ("STORIES" if kind == "story" else "VIDEO")}
    params = {"image_url": url}
    if kind == "story":
        params["media_type"] = "STORIES"
    return params


def publish_post(ig: Instagram, post: Post, urls: dict[str, str]) -> dict:
    d = post.data
    if post.type == "carousel":
        children = []
        for m in post.media:
            p = media_params(urls[m], kind="item")
            if "video_url" in p:
                p["media_type"] = "VIDEO"
            cid = ig.container(is_carousel_item="true", **p)
            if is_video(m):
                ig.wait_ready(cid)
            children.append(cid)
        cid = ig.container(media_type="CAROUSEL", children=",".join(children), caption=post.caption)
        ig.wait_ready(cid)
    else:
        params = media_params(urls[post.media[0]], kind=post.type)
        if post.type != "story":
            params["caption"] = post.caption
        if post.type == "image" and d.get("alt_text"):
            params["alt_text"] = str(d["alt_text"])[:1000]
        if post.type == "reel":
            params["share_to_feed"] = "true" if d.get("share_to_feed", True) else "false"
            if d.get("cover_url"):
                params["cover_url"] = d["cover_url"]
        cid = ig.container(**params)
        ig.wait_ready(cid, timeout_s=600 if is_video(post.media[0]) else 120)

    media_id = ig.publish(cid)
    return {"media_id": media_id, "permalink": ig.permalink(media_id)}


# ---------------------------------------------------------------- bookkeeping

def archive(post: Post, folder: Path, result: dict) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    data = dict(post.data)
    data["result"] = result
    dest = folder / post.name
    dest.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True))
    post.path.unlink()
    return dest


def write_summary(lines: list[str]) -> None:
    out = os.environ.get("GITHUB_STEP_SUMMARY")
    text = "\n".join(lines) + "\n"
    print(text)
    if out:
        with open(out, "a") as f:
            f.write(text)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="check posts and show what would publish")
    ap.add_argument("--now", help="pretend it is this time (YYYY-MM-DD HH:MM, UAE time)")
    ap.add_argument("--max", type=int, default=int(os.environ.get("MAX_POSTS_PER_RUN", 5)),
                    help="most posts to publish in one run (default 5)")
    args = ap.parse_args(argv)

    accounts = load_accounts()
    now = parse_time(args.now, DEFAULT_TZ) if args.now else datetime.now(timezone.utc)
    on_actions = os.environ.get("GITHUB_ACTIONS") == "true"

    files = sorted(list(QUEUE.glob("*.yml")) + list(QUEUE.glob("*.yaml")))
    summary = [f"## Instagram run · {now.astimezone(ZoneInfo(DEFAULT_TZ)):%a %d %b %Y %H:%M} UAE time"]
    due: list[Post] = []
    problems = 0

    for f in files:
        try:
            post = load_post(f, accounts)
        except PostError as e:
            problems += 1
            summary.append(f"- ⚠️ `{f.name}`: {e}")
            continue
        if post.publish_at <= now:
            due.append(post)

    waiting = len(files) - len(due) - problems
    summary.append(f"- {len(due)} due, {waiting} waiting, {problems} with problems")
    due.sort(key=lambda p: p.publish_at)
    due = due[: args.max]

    if args.dry_run or not due:
        for p in due:
            summary.append(f"- would publish `{p.name}` ({p.type}) to **{p.account}**")
        write_summary(summary)
        return 1 if problems else 0

    urls = resolve_media(due, push=on_actions)
    clients: dict[str, Instagram] = {}
    changed = False

    for post in due:
        env_name = accounts[post.account].get("token_secret")
        token = secret(env_name) if env_name else None
        if not token:
            summary.append(f"- ❌ `{post.name}`: no token found in secret `{env_name}`")
            problems += 1
            continue
        ig = clients.setdefault(post.account, Instagram(token))
        left = ig.quota_left()
        if left is not None and left <= 0:
            summary.append(f"- ⏸️ `{post.name}`: daily publishing limit reached for {post.account}; will retry")
            continue
        try:
            res = publish_post(ig, post, urls)
            res["published_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
            archive(post, PUBLISHED, res)
            summary.append(f"- ✅ `{post.name}` → {res.get('permalink') or res['media_id']}")
        except Exception as e:  # keep going with other posts
            archive(post, FAILED, {"error": str(e),
                                   "failed_at": datetime.now(timezone.utc).isoformat(timespec="seconds")})
            summary.append(f"- ❌ `{post.name}`: {e}")
            problems += 1
        changed = True

    if changed and on_actions:
        commit_and_push("Instagram: record published posts", "posts")

    write_summary(summary)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
