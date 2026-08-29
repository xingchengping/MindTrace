"""下载 GGUF 模型（薄封装：实际逻辑在 app/core/downloader.py，exe 内可直接调用）。

用法：
    python scripts/download_models.py                  # 下载当前档位所需（chat + 档位视觉 + mmproj）
    python scripts/download_models.py --model all      # chat + embedding + vision
    python scripts/download_models.py --list           # 列出可下载项
    python scripts/download_models.py --source hf-mirror
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.core.config import AppConfig  # noqa: E402
from app.core.downloader import SOURCES, download, profile_entries, human  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="下载 GGUF 模型")
    parser.add_argument("--model", choices=["chat", "embedding", "vision", "all", "profile"],
                        default="profile", help="默认 profile=当前档位所需")
    parser.add_argument("--list", action="store_true", help="列出可下载项")
    parser.add_argument("--source", choices=list(SOURCES), default=None, help="强制下载源")
    parser.add_argument("--file", default=None, help="只下载指定文件名")
    args = parser.parse_args()

    cfg = AppConfig.load()
    models_cfg = cfg.raw.get("models", {}) or {}

    entries: list[tuple[str, str, dict]] = []
    if args.model in ("chat", "all"):
        for e in models_cfg.get("chat", {}).get("entries", []):
            entries.append(("chat", models_cfg["chat"].get("source", "modelscope"), e))
    if args.model in ("embedding", "all"):
        for e in models_cfg.get("embedding", {}).get("entries", []):
            entries.append(("embedding", models_cfg["embedding"].get("source", "hf-mirror"), e))
    if args.model in ("vision", "all"):
        for e in models_cfg.get("vision", {}).get("entries", []):
            entries.append(("vision", models_cfg["vision"].get("source", "modelscope"), e))
    if args.model == "profile":
        entries = profile_entries(cfg)
        print(f"[info] 档位 {cfg.profile} -> 下载 chat + 档位视觉 + mmproj")

    if args.list:
        for kind, source, e in entries:
            url = SOURCES[args.source or source].format(repo=e["repo"], file=e["file"])
            print(f"  [{kind}] {e['file']:50s} {human((e.get('size_hint_mb') or 0) * (1 << 20)):>8s}  {url}")
        return

    ok_all = True
    for kind, source, e in entries:
        if args.file and e["file"] != args.file:
            continue
        source = args.source or source
        url = SOURCES[source].format(repo=e["repo"], file=e["file"])
        dest = cfg.models_dir / e["file"]
        print(f"[info] [{kind}] {e['file']} <- {source}")
        ok = download(url, dest, e.get("size_hint_mb"))
        ok_all = ok_all and ok

    print("\n完成。" if ok_all else "\n部分失败，可重试（支持断点续传）。")
    sys.exit(0 if ok_all else 1)


if __name__ == "__main__":
    main()
