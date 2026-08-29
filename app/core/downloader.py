"""模型下载（可被 exe 内调用）：ModelScope / HF-mirror / HF 直链 + 断点续传。

用途：开发模式由 scripts/download_models.py 调用；打包成 exe 后，
`MindTrace.exe --download-models` 直接调用本模块（用户无需 Python/脚本）。
"""
from __future__ import annotations

import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from app.core.config import AppConfig

SOURCES = {
    "modelscope": "https://modelscope.cn/models/{repo}/resolve/master/{file}",
    "hf-mirror": "https://hf-mirror.com/{repo}/resolve/main/{file}",
    "hf": "https://huggingface.co/{repo}/resolve/main/{file}",
}

VISION_MMPROJ = "mmproj-F16.gguf"


def human(n: float) -> str:
    if n >= 1 << 30:
        return f"{n / (1 << 30):.2f} GB"
    return f"{n / (1 << 20):.0f} MB"


def download(url: str, dest: Path, size_hint_mb: int | None = None) -> bool:
    dest.parent.mkdir(parents=True, exist_ok=True)
    partial = dest.with_suffix(dest.suffix + ".part")
    if dest.exists():
        print(f"[skip] 已存在 {dest.name}")
        return True
    resume = partial.stat().st_size if partial.exists() else 0
    headers = {"User-Agent": "MindTrace/0.1"}
    if resume:
        headers["Range"] = f"bytes={resume}-"
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            status = getattr(resp, "status", 200)
            if status == 416:
                print(f"[skip] 断点文件已完整，重命名 {partial.name}")
                partial.rename(dest)
                return True
            total = None
            if status == 206:
                total = resume + int(resp.headers.get("Content-Length") or 0)
            elif status == 200:
                total = int(resp.headers.get("Content-Length") or 0)
            mode = "ab" if resume else "wb"
            done = resume
            start = time.time()
            last_print = 0.0
            with open(partial, mode) as f:
                while True:
                    chunk = resp.read(1 << 20)
                    if not chunk:
                        break
                    f.write(chunk)
                    done += len(chunk)
                    now = time.time()
                    if now - last_print > 0.5:
                        last_print = now
                        if total:
                            pct = done * 100 / total
                            speed = done / max(now - start, 0.001) / (1 << 20)
                            print(f"\r  {dest.name}: {human(done)}/{human(total)} ({pct:.1f}%) "
                                  f"{speed:.1f} MB/s   ", end="", flush=True)
                        else:
                            print(f"\r  {dest.name}: {human(done)}   ", end="", flush=True)
            print()
        partial.rename(dest)
        print(f"[ok] {dest.name} 下载完成 -> {dest}")
        return True
    except urllib.error.HTTPError as exc:
        print(f"[error] HTTP {exc.code}: {url}")
        return False
    except Exception as exc:  # noqa: BLE001
        print(f"[error] {exc}")
        return False


def profile_entries(cfg: AppConfig) -> list[tuple[str, str, dict]]:
    """当前档位需要的模型条目（chat + 档位视觉 + mmproj）。"""
    models_cfg = cfg.raw.get("models", {}) or {}
    profile_cfg = (cfg.raw.get("profiles", {}).get(cfg.profile, {}) or {})
    entries: list[tuple[str, str, dict]] = []

    chat_file = profile_cfg.get("model", "")
    for e in models_cfg.get("chat", {}).get("entries", []):
        if e.get("file") == chat_file:
            entries.append(("chat", models_cfg["chat"].get("source", "modelscope"), e))
            break

    vis_model = (profile_cfg.get("vision", {}) or {}).get("model", "")
    if vis_model:
        for e in models_cfg.get("vision", {}).get("entries", []):
            if e.get("file") == vis_model:
                entries.append(("vision", models_cfg["vision"].get("source", "modelscope"), e))
                break
        for e in models_cfg.get("vision", {}).get("entries", []):
            if e.get("file") == VISION_MMPROJ:
                entries.append(("vision", models_cfg["vision"].get("source", "modelscope"), e))
                break
    return entries


def download_profile_models(cfg: AppConfig, only_file: str | None = None) -> bool:
    """下载当前档位所需模型。返回是否全部成功。"""
    entries = profile_entries(cfg)
    if not entries:
        print("[info] 当前档位无需下载（配置中未找到模型条目）")
        return True
    print(f"[info] 档位 {cfg.profile} -> 需要下载 {len(entries)} 个文件")
    ok = True
    for kind, source, e in entries:
        if only_file and e.get("file") != only_file:
            continue
        url = SOURCES[source].format(repo=e["repo"], file=e["file"])
        dest = cfg.models_dir / e["file"]
        print(f"[info] [{kind}] {e['file']} <- {source}")
        ok = download(url, dest, e.get("size_hint_mb")) and ok
    return ok


def main() -> None:  # 供 scripts/download_models.py 薄封装
    cfg = AppConfig.load(profile=None)
    ok = download_profile_models(cfg)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
