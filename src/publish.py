"""Publish generated HTML files to Cloudflare Pages or GitHub Pages.

Deploy the existing data/output/ directory directly — no HTML regeneration needed.

Usage:
    python src/publish.py                     # Deploy all to Cloudflare Pages
    python src/publish.py --target github     # Deploy all to GitHub Pages
    python src/publish.py --target cloudflare --project my-subtitles
"""

import argparse
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import OUTPUT_DIR, CF_PAGES_PROJECT, GITHUB_PAGES_REPO


def _ensure_index() -> None:
    """Generate/update index.html in OUTPUT_DIR if missing."""
    index_path = OUTPUT_DIR / "index.html"
    if index_path.exists():
        return

    from src.database import Database
    from src.html_generator import generate_index_html

    db = Database()
    videos = db.get_recent_videos(limit=200)
    db.close()

    # Filter to videos with existing HTML files
    existing = []
    for v in videos:
        fn = v.get("html_filename") or f"{v.get('video_id', '')}.html"
        if (OUTPUT_DIR / fn).exists():
            existing.append(v)

    index_html = generate_index_html(existing)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    index_path.write_text(index_html, encoding="utf-8")
    print(f"  Generated index.html ({len(existing)} videos)")


def _refresh_index() -> None:
    """Always regenerate index.html with current OUTPUT_DIR contents."""
    from src.database import Database
    from src.html_generator import generate_index_html

    db = Database()
    videos = db.get_recent_videos(limit=200)
    db.close()

    existing = []
    for v in videos:
        fn = v.get("html_filename") or f"{v.get('video_id', '')}.html"
        if (OUTPUT_DIR / fn).exists():
            existing.append(v)

    index_html = generate_index_html(existing)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUTPUT_DIR / "index.html").write_text(index_html, encoding="utf-8")


def _find_wrangler() -> str:
    """Find wrangler CLI path."""
    import shutil
    path = shutil.which("wrangler")
    if path:
        return path
    path = shutil.which("npx")
    if path:
        return "npx wrangler"
    raise FileNotFoundError("wrangler not found. Install with: npm install -g wrangler")


def _run_wrangler(args: list[str], **kwargs) -> subprocess.CompletedProcess:
    """Run wrangler with the correct invocation."""
    wrangler = _find_wrangler()
    if wrangler == "npx wrangler":
        cmd = ["npx", "wrangler"] + args
    else:
        cmd = [wrangler] + args
    # Force UTF-8 encoding on Windows to avoid GBK decode errors
    if kwargs.get("text") and "encoding" not in kwargs:
        kwargs["encoding"] = "utf-8"
    return subprocess.run(cmd, **kwargs)


def _parse_deploy_url(stdout: str, project: str | None = None) -> str:
    """Extract deployment URL from wrangler output."""
    m = re.search(r'https://[a-z0-9-]+\.' + re.escape(project or "") + r'\.pages\.dev', stdout)
    if m:
        return m.group(0)
    m = re.search(r'https://[a-z0-9-]+\.pages\.dev', stdout)
    return m.group(0) if m else ""


def publish_cloudflare(project: str | None = None) -> None:
    """Deploy data/output/ directory to Cloudflare Pages."""
    project = project or CF_PAGES_PROJECT
    if not project:
        print("Error: CF_PAGES_PROJECT not configured. Set it in .env or pass --project")
        sys.exit(1)

    try:
        _run_wrangler(["--version"], capture_output=True, text=True, encoding="utf-8", timeout=30)
    except FileNotFoundError as e:
        print(f"Error: {e}")
        sys.exit(1)

    _refresh_index()

    print(f"Deploying {OUTPUT_DIR} to Cloudflare Pages project '{project}'...")
    result = _run_wrangler(
        ["pages", "deploy", str(OUTPUT_DIR), "--project-name", project, "--branch", "main"],
        capture_output=True, text=True, encoding="utf-8", timeout=120,
    )
    if result.returncode != 0:
        print(f"Deploy failed:\n{result.stderr}")
        sys.exit(1)
    url = _parse_deploy_url(result.stdout, project)
    # Strip emoji that Windows GBK console can't print
    output = result.stdout.encode("gbk", errors="replace").decode("gbk")
    print(output)
    if url:
        print(f"\nProduction URL: {url}")
    print(f"Main site: https://{project}.pages.dev")


def publish_single(html_filename: str, project: str | None = None) -> dict:
    """Deploy a single HTML file + updated index to Cloudflare Pages.

    Returns {"success": bool, "url": str, "error": str}.
    """
    project = project or CF_PAGES_PROJECT
    if not project:
        return {"success": False, "url": "", "error": "CF_PAGES_PROJECT not configured"}

    if not (OUTPUT_DIR / html_filename).exists():
        return {"success": False, "url": "", "error": f"{html_filename} not found"}

    _refresh_index()

    try:
        _run_wrangler(["--version"], capture_output=True, text=True, encoding="utf-8", timeout=30)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return {"success": False, "url": "", "error": "wrangler not found"}

    result = _run_wrangler(
        ["pages", "deploy", str(OUTPUT_DIR), "--project-name", project, "--branch", "main"],
        capture_output=True, text=True, encoding="utf-8", timeout=120,
    )
    if result.returncode != 0:
        return {"success": False, "url": "", "error": result.stderr[:200]}

    url = _parse_deploy_url(result.stdout, project)
    return {"success": True, "url": url, "error": ""}


def publish_github(repo: str | None = None) -> None:
    """Push data/output/ directory to a gh-pages branch."""
    import tempfile

    repo = repo or GITHUB_PAGES_REPO
    if not repo:
        print("Error: GITHUB_PAGES_REPO not configured. Set it in .env or pass --repo")
        sys.exit(1)

    _refresh_index()

    with tempfile.TemporaryDirectory() as tmpdir:
        repo_dir = Path(tmpdir) / "repo"
        print(f"Cloning {repo}...")
        subprocess.run(["git", "clone", repo, str(repo_dir)], capture_output=True, timeout=60)
        subprocess.run(["git", "checkout", "-B", "gh-pages"], cwd=str(repo_dir), capture_output=True)

        # Clear and copy
        for f in repo_dir.iterdir():
            if f.name == ".git":
                continue
            if f.is_dir():
                shutil.rmtree(f)
            else:
                f.unlink()

        for f in OUTPUT_DIR.iterdir():
            if f.is_file():
                shutil.copy2(f, repo_dir / f.name)

        subprocess.run(["git", "add", "-A"], cwd=str(repo_dir))
        subprocess.run(
            ["git", "commit", "-m", "Update subtitle pages"],
            cwd=str(repo_dir), capture_output=True,
        )
        subprocess.run(
            ["git", "push", "origin", "gh-pages", "--force"],
            cwd=str(repo_dir), capture_output=True, timeout=60,
        )
        print("Pushed to gh-pages branch")


def main() -> None:
    parser = argparse.ArgumentParser(description="Publish subtitle HTML files")
    parser.add_argument("--target", choices=["cloudflare", "github"], default="cloudflare")
    parser.add_argument("--project", help="Cloudflare Pages project name")
    parser.add_argument("--repo", help="GitHub repo URL for gh-pages")
    args = parser.parse_args()

    if args.target == "cloudflare":
        publish_cloudflare(args.project)
    else:
        publish_github(args.repo)


if __name__ == "__main__":
    main()
