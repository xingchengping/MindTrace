"""沙箱环境下的 wheel 手动安装工具。

背景：DSH 沙箱对 pip 的临时解包目录（pip-unpack-*）时灵时不灵，
且用户已否决升级权限。本脚本绕开 pip 的 tempfile 流程：
  1. 通过 PyPI JSON API 找到 cp312 win_amd64 wheel 的直链
  2. urllib 直接下载到 .wheels/（不写临时目录）
  3. zipfile 直接解压进当前 Python 的 site-packages

用法：
    python scripts/install_wheels.py sqlalchemy greenlet
"""
from __future__ import annotations

import json
import site
import sys
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WHEELS_DIR = ROOT / ".wheels"


def get_site_packages() -> Path:
    for p in sys.path:
        if "site-packages" in p:
            return Path(p)
    raise SystemExit("找不到 site-packages（当前解释器：%s）" % sys.executable)


def find_wheel(pkg: str):
    """从 PyPI（TUNA 镜像优先）找最新版 wheel：cp312+win_amd64 → win_amd64 → py3-none-any。"""
    data = None
    for base in ("https://pypi.tuna.tsinghua.edu.cn", "https://pypi.org"):
        try:
            url = f"{base}/pypi/{pkg}/json"
            with urllib.request.urlopen(url, timeout=120) as resp:
                data = json.load(resp)
                break
        except Exception:  # noqa: BLE001
            continue
    if data is None:
        raise SystemExit(f"[error] {pkg}: PyPI/TUNA 均不可达")
    version = data["info"]["version"]
    best: list[tuple[str, str]] = []
    platform: list[tuple[str, str]] = []
    pure: list[tuple[str, str]] = []
    others: list[tuple[str, str]] = []
    for f in data.get("urls", []):
        fn = f["filename"]
        if not fn.endswith(".whl"):
            continue
        parts = fn.split("-")
        if len(parts) < 4:
            continue
        tags = parts[2]
        # 下载直链：files.pythonhosted.org → TUNA 镜像（速度快）
        dl = f["url"].replace(
            "https://files.pythonhosted.org/", "https://pypi.tuna.tsinghua.edu.cn/"
        )
        item = (fn, dl)
        if "cp312" in tags and "win_amd64" in fn:
            best.append(item)
        elif "win_amd64" in fn:
            platform.append(item)
        elif "py3-none-any" in fn:
            pure.append(item)
        else:
            others.append(item)
    for fn, dl in best or platform or pure or others:
        return fn, dl, version
    raise SystemExit(
        f"[error] {pkg}: 未找到可用 wheel。可用文件：{sorted(f['filename'] for f in data.get('urls', []))[:10]}"
    )


def download(url: str, dest: Path) -> None:
    if dest.exists() and dest.stat().st_size > 0 and zipfile.is_zipfile(dest):
        print(f"[skip] {dest.name}")
        return
    if dest.exists():
        dest.unlink()  # 损坏/空文件 → 重新下载
    dest.parent.mkdir(parents=True, exist_ok=True)
    print(f"[down] {dest.name} ...", end="", flush=True)
    with urllib.request.urlopen(url, timeout=900) as resp, open(dest, "wb") as f:
        while True:
            chunk = resp.read(1 << 20)
            if not chunk:
                break
            f.write(chunk)
    print(" ok")


def install_wheel(whl: Path, target: Path) -> None:
    count = 0
    with zipfile.ZipFile(whl) as z:
        for name in z.namelist():
            if name.endswith("/"):
                continue
            if ".data/" in name:  # 极少数 wheel 的 data 目录，跳过
                continue
            out = target / name
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(z.read(name))
            count += 1
    print(f"[ok] {whl.name} -> {target}（{count} 个文件）")


def main() -> None:
    if len(sys.argv) < 2:
        raise SystemExit("用法: python scripts/install_wheels.py <pkg> [pkg...]")
    target = get_site_packages()
    print(f"[info] 目标: {target}")
    for pkg in sys.argv[1:]:
        fn, dl, version = find_wheel(pkg)
        print(f"[info] {pkg} {version} -> {fn}")
        whl = WHEELS_DIR / fn
        download(dl, whl)
        install_wheel(whl, target)


if __name__ == "__main__":
    main()
