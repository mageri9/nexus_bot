import os
import re
import subprocess
import sys
import time
import requests

GITHUB_TOKEN = os.environ["GITHUB_TOKEN"]
GITHUB_REPO = os.environ["GITHUB_REPO"]
GITHUB_WEBHOOK_SECRET = os.environ["GITHUB_WEBHOOK_SECRET"]

URL_PATTERN = re.compile(r"https://[a-zA-Z0-9-]+\.trycloudflare\.com")
HEADERS = {
    "Authorization": f"Bearer {GITHUB_TOKEN}",
    "Accept": "application/vnd.github+json",
}


def get_existing_hook_id() -> int | None:
    resp = requests.get(
        f"https://api.github.com/repos/{GITHUB_REPO}/hooks", headers=HEADERS
    )
    resp.raise_for_status()
    for hook in resp.json():
        if hook["config"].get("url", "").endswith("/webhooks/github"):
            return hook["id"]
    return None


def upsert_webhook(new_base_url: str) -> None:
    full_url = f"{new_base_url}/webhooks/github"
    hook_id = get_existing_hook_id()
    payload = {
        "config": {
            "url": full_url,
            "content_type": "json",
            "secret": GITHUB_WEBHOOK_SECRET,
        },
        "events": ["workflow_run"],
        "active": True,
    }

    if hook_id:
        resp = requests.patch(
            f"https://api.github.com/repos/{GITHUB_REPO}/hooks/{hook_id}",
            headers=HEADERS,
            json=payload,
        )
    else:
        resp = requests.post(
            f"https://api.github.com/repos/{GITHUB_REPO}/hooks",
            headers=HEADERS,
            json=payload,
        )

    if resp.status_code >= 300:
        print(f"[sync_webhook] Failed to update webhook: {resp.status_code} {resp.text}", flush=True)
    else:
        print(f"[sync_webhook] Webhook synced to {full_url}", flush=True)


def main():
    # Читаем stdout процесса cloudflared построчно и ждём URL в логах
    proc = subprocess.Popen(
        ["cloudflared", "tunnel", "--url", "http://webhook:8000"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )

    found = False
    for line in proc.stdout:
        print(line, end="", flush=True)  # прозрачно прокидываем логи cloudflared дальше
        if not found:
            match = URL_PATTERN.search(line)
            if match:
                found = True
                try:
                    upsert_webhook(match.group(0))
                except Exception as e:
                    print(f"[sync_webhook] Error syncing webhook: {e}", flush=True)

    proc.wait()
    sys.exit(proc.returncode)


if __name__ == "__main__":
    main()