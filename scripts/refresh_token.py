"""
Threads アクセストークン自動リフレッシュ

長期トークン（60日有効）をリフレッシュして GitHub Secrets を更新する。
毎月1回 GitHub Actions から呼び出される。
"""
import os
import sys
import json
import base64
import requests
from datetime import datetime, timezone

THREADS_API_BASE = "https://graph.threads.net/v1.0"
GITHUB_API_BASE = "https://api.github.com"


def refresh_threads_token(current_token: str) -> str:
    """現在のトークンをリフレッシュして新しいトークンを返す"""
    url = f"{THREADS_API_BASE}/refresh_access_token"
    resp = requests.get(url, params={
        "grant_type": "th_refresh_token",
        "access_token": current_token,
    }, timeout=15)

    if resp.status_code != 200:
        raise Exception(f"Token refresh failed: {resp.status_code} {resp.text}")

    data = resp.json()
    new_token = data.get("access_token")
    expires_in = data.get("expires_in", 0)
    if not new_token:
        raise Exception(f"No access_token in response: {data}")

    expires_days = expires_in // 86400
    print(f"[OK] Token refreshed. Expires in {expires_days} days.")
    return new_token


def get_repo_public_key(owner: str, repo: str, gh_token: str) -> tuple[str, str]:
    """GitHub Secret 暗号化用のリポジトリ公開鍵を取得"""
    url = f"{GITHUB_API_BASE}/repos/{owner}/{repo}/actions/secrets/public-key"
    resp = requests.get(url, headers={
        "Authorization": f"Bearer {gh_token}",
        "Accept": "application/vnd.github+json",
    }, timeout=10)
    if resp.status_code != 200:
        raise Exception(f"Failed to get public key: {resp.status_code} {resp.text}")
    data = resp.json()
    return data["key_id"], data["key"]


def encrypt_secret(public_key_b64: str, secret_value: str) -> str:
    """libsodium sealed box でシークレットを暗号化"""
    try:
        from nacl import encoding, public
    except ImportError:
        raise Exception("PyNaCl not installed. Add 'PyNaCl' to requirements.txt.")

    public_key_bytes = base64.b64decode(public_key_b64)
    sealed_box = public.SealedBox(public.PublicKey(public_key_bytes))
    encrypted = sealed_box.encrypt(secret_value.encode("utf-8"))
    return base64.b64encode(encrypted).decode("utf-8")


def update_github_secret(owner: str, repo: str, gh_token: str, secret_name: str, secret_value: str):
    """GitHub Actions シークレットを更新する"""
    key_id, public_key = get_repo_public_key(owner, repo, gh_token)
    encrypted_value = encrypt_secret(public_key, secret_value)

    url = f"{GITHUB_API_BASE}/repos/{owner}/{repo}/actions/secrets/{secret_name}"
    resp = requests.put(url, headers={
        "Authorization": f"Bearer {gh_token}",
        "Accept": "application/vnd.github+json",
    }, json={
        "encrypted_value": encrypted_value,
        "key_id": key_id,
    }, timeout=10)

    if resp.status_code not in (201, 204):
        raise Exception(f"Failed to update secret: {resp.status_code} {resp.text}")
    print(f"[OK] GitHub Secret '{secret_name}' updated in {owner}/{repo}.")


def main():
    current_token = os.getenv("THREADS_ACCESS_TOKEN", "")
    gh_token = os.getenv("GH_PAT", "")
    repo_owner = os.getenv("REPO_OWNER", "adatchie")
    repo_name = os.getenv("REPO_NAME", "threads-auto")

    if not current_token:
        print("[ERROR] THREADS_ACCESS_TOKEN is not set.", file=sys.stderr)
        sys.exit(1)
    if not gh_token:
        print("[ERROR] GH_PAT is not set.", file=sys.stderr)
        sys.exit(1)

    print(f"[INFO] Refreshing token at {datetime.now(timezone.utc).isoformat()}")

    try:
        new_token = refresh_threads_token(current_token)
    except Exception as e:
        print(f"[ERROR] {e}", file=sys.stderr)
        sys.exit(1)

    try:
        update_github_secret(repo_owner, repo_name, gh_token, "THREADS_ACCESS_TOKEN", new_token)
    except Exception as e:
        print(f"[ERROR] Failed to update secret: {e}", file=sys.stderr)
        sys.exit(1)

    print("[DONE] Token refresh complete.")


if __name__ == "__main__":
    main()
