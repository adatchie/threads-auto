#!/usr/bin/env python3
"""セットアップ確認スクリプト。実行すると未設定の項目を一覧で教えてくれる"""
import json
import os
import sys
from pathlib import Path

BASE_DIR = Path(__file__).parent.parent

def load_env_values() -> dict:
    """.env を手動でパースして辞書で返す"""
    env_path = BASE_DIR / ".env"
    env_vals = {}
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            if "=" in line and not line.startswith("#"):
                k, _, v = line.partition("=")
                env_vals[k.strip()] = v.strip()
    return env_vals

def check_env(env_vals):
    print("\n=== .env の確認 ===")
    required = {
        "THREADS_APP_ID": "Threads App ID",
        "THREADS_APP_SECRET": "Threads App Secret",
        "THREADS_ACCESS_TOKEN": "Threads アクセストークン（長期）",
        "THREADS_USER_ID": "Threads ユーザーID（数値）",
    }
    optional = {
        "YOUTUBE_API_KEY": "YouTube Data API キー（任意）",
        "X_BEARER_TOKEN": "X（Twitter）Bearer Token（任意）",
    }

    all_ok = True
    for key, desc in required.items():
        val = env_vals.get(key, "")
        if not val or val.startswith("REPLACE") or val == "":
            print(f"  [NG] {key}: 未設定 ({desc})")
            all_ok = False
        else:
            masked = val[:6] + "..." if len(val) > 6 else "***"
            print(f"  [OK] {key}: {masked}")
    for key, desc in optional.items():
        val = env_vals.get(key, "")
        if not val:
            print(f"  [--] {key}: 未設定（{desc}）")
        else:
            print(f"  [OK] {key}: 設定済み")
    return all_ok

def check_llm(env_vals):
    print("\n=== LLM の確認 ===")
    backends = {
        "ANTHROPIC_API_KEY": "Anthropic",
        "GEMINI_API_KEY": "Gemini",
        "GLM_API_KEY": "GLM",
    }
    enabled = [name for key, name in backends.items() if env_vals.get(key)]
    if enabled:
        print(f"  [OK] 設定済み: {', '.join(enabled)}")
        return True

    print("  [NG] LLM API キーが未設定です（ANTHROPIC_API_KEY / GEMINI_API_KEY / GLM_API_KEY のいずれか）")
    return False

def check_search(env_vals):
    print("\n=== 検索 API の確認 ===")
    brave = env_vals.get("BRAVE_SEARCH_API_KEY", "")
    google_key = env_vals.get("GOOGLE_CSE_KEY", "")
    google_id = env_vals.get("GOOGLE_CSE_ID", "")

    if brave:
        print("  [OK] Brave Search API キー: 設定済み")
        return True
    if google_key and google_id:
        print("  [OK] Google CSE: 設定済み")
        return True

    print("  [NG] 検索 API が未設定です（BRAVE_SEARCH_API_KEY または GOOGLE_CSE_KEY + GOOGLE_CSE_ID）")
    return False

def check_affiliate():
    aff_path = BASE_DIR / "knowledge" / "affiliate.json"
    print("\n=== アフィリエイトURLの確認 ===")
    with open(aff_path, encoding="utf-8") as f:
        aff = json.load(f)

    all_ok = True
    for camp in aff["campaigns"]:
        url = camp.get("url", "")
        if not url or url.startswith("REPLACE"):
            print(f"  [NG] [{camp['asp']}] {camp['name']}: URL未設定")
            print(f"      → knowledge/affiliate.json の \"{camp['id']}\" を編集してください")
            all_ok = False
        else:
            print(f"  [OK] [{camp['asp']}] {camp['name']}: 設定済み")
    return all_ok

def check_dependencies():
    print("\n=== Pythonライブラリの確認 ===")
    packages = ["requests", "dotenv", "sklearn", "nacl", "youtube_transcript_api", "openai"]
    all_ok = True
    for pkg in packages:
        try:
            __import__(pkg if pkg != "dotenv" else "dotenv")
            print(f"  [OK] {pkg}")
        except ImportError:
            print(f"  [NG] {pkg}: 未インストール")
            all_ok = False
    return all_ok

def show_next_steps(env_ok, llm_ok, search_ok, aff_ok, dep_ok):
    print("\n=== 次のステップ ===")
    if not dep_ok:
        print("  1. pip install -r requirements.txt")
    if not env_ok:
        print("  2. threads-auto/.env を編集して Threads の必須項目を埋める")
    if not llm_ok:
        print("  3. LLM は ANTHROPIC_API_KEY / GEMINI_API_KEY / GLM_API_KEY のいずれか1つを設定")
    if not search_ok:
        print("  4. 検索は BRAVE_SEARCH_API_KEY か GOOGLE_CSE_KEY + GOOGLE_CSE_ID のどちらかを設定")
    if not aff_ok:
        print("  5. threads-auto/knowledge/affiliate.json のURLを埋める")
        print()
        print("  各ASPでのリンク取得方法:")
        print("  afb           → プログラム管理 > 提携中プログラム > 広告リンク取得")
        print("  ValueCommerce → ツール > リンク作成 > テキストリンク")
        print("  A8.net        → プログラム管理 > 提携プログラム > 広告リンク > テキストリンク")
    if env_ok and llm_ok and search_ok and aff_ok and dep_ok:
        print("  全項目OK！以下で動作確認できます:")
        print("  cd threads-auto/agents && python poster.py --dry-run")
    print()

if __name__ == "__main__":
    print("Threads自動運用 セットアップチェッカー")
    print("=" * 40)
    env_vals = load_env_values()
    env_ok = check_env(env_vals)
    llm_ok = check_llm(env_vals)
    search_ok = check_search(env_vals)
    aff_ok = check_affiliate()
    dep_ok = check_dependencies()
    show_next_steps(env_ok, llm_ok, search_ok, aff_ok, dep_ok)
    sys.exit(0 if (env_ok and llm_ok and search_ok and aff_ok and dep_ok) else 1)
