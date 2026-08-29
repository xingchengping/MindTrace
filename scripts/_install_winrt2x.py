"""安装 winrt 2.x 投影包（匹配 winrt-runtime 2.1.0；3.x 投影声明了不存在的 runtime）。

用法: python scripts/_install_winrt2x.py
"""
from __future__ import annotations

import json
import shutil
import site
import sys
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WHEELS_DIR = ROOT / ".wheels"

PKGS = [
    "winrt-Windows.Data.Xml.Dom",
    "winrt-Windows.Foundation",
    "winrt-Windows.Foundation.Collections",
    "winrt-Windows.Globalization",
    "winrt-Windows.Graphics.Imaging",
    "winrt-Windows.Media.Core",
    "winrt-Windows.Media.Ocr",
    "winrt-Windows.Media.Playback",
    "winrt-Windows.Media.SpeechSynthesis",
    "winrt-Windows.Storage",
    "winrt-Windows.Storage.Streams",
    "winrt-Windows.UI.Notifications",
    "winrt-Windows.System",
]

# 必须与 winrt-runtime 2.1.0 的 C API 主版本匹配：
#   投影 2.3.0 → 期望 C API 2（runtime 2.1.0 只有 1，报 ABI mismatch）
#   投影 2.1.0 → 期望 C API 1 ✓
PIN = "2.1.0"


def site_packages() -> Path:
    for p in sys.path:
        if "site-packages" in p:
            return Path(p)
    raise SystemExit("no site-packages")


def latest_2x(pkg: str) -> tuple[str, str, str]:
    """返回 (version, filename, download_url)：该包 PIN 版本（需匹配 runtime C API）。"""
    data = None
    for base in ("https://pypi.org", "https://pypi.tuna.tsinghua.edu.cn"):
        try:
            with urllib.request.urlopen(f"{base}/pypi/{pkg}/json", timeout=90) as r:
                data = json.load(r)
            break
        except Exception:
            continue
    if data is None:
        raise SystemExit(f"{pkg}: PyPI 不可达")
    ver = PIN
    if ver not in data.get("releases", {}):
        raise SystemExit(f"{pkg}: 版本 {ver} 不存在，全部: {sorted(data['releases'].keys())}")
    for f in data["releases"][ver]:
        fn = f["filename"]
        if fn.endswith(".whl") and "win_amd64" in fn and "cp312" in fn:
            dl = f["url"].replace(
                "https://files.pythonhosted.org/", "https://pypi.tuna.tsinghua.edu.cn/"
            )
            return ver, fn, dl
    raise SystemExit(f"{pkg} {ver}: 没有 cp312 win_amd64 wheel")


def main() -> None:
    target = site_packages()
    print(f"[info] target: {target}")
    # 清掉旧残留（同一个 winrt 命名空间目录，版本不能混装）
    for p in (target / "winrt",):
        if p.exists():
            shutil.rmtree(p)
            print(f"[rm] {p}")
    for p in target.glob("winrt-*.dist-info"):
        shutil.rmtree(p)
        print(f"[rm] {p}")
    # 先装 runtime（含 _winrt.pyd + winrt/system）
    for base in ("https://pypi.org", "https://pypi.tuna.tsinghua.edu.cn"):
        try:
            with urllib.request.urlopen(f"{base}/pypi/winrt-runtime/json", timeout=90) as r:
                rd = json.load(r)
            break
        except Exception:
            continue
    for f in rd["releases"]["2.1.0"]:
        if f["filename"].endswith(".whl") and "win_amd64" in f["filename"] and "cp312" in f["filename"]:
            rwhl = WHEELS_DIR / f["filename"]
            dl = f["url"].replace("https://files.pythonhosted.org/",
                                  "https://pypi.tuna.tsinghua.edu.cn/")
            if not (rwhl.exists() and rwhl.stat().st_size > 0 and zipfile.is_zipfile(rwhl)):
                print(f"[down] {rwhl.name} ...", end="", flush=True)
                with urllib.request.urlopen(dl, timeout=900) as resp, open(rwhl, "wb") as fh:
                    while True:
                        chunk = resp.read(1 << 20)
                        if not chunk:
                            break
                        fh.write(chunk)
                print(" ok")
            count = 0
            with zipfile.ZipFile(rwhl) as z:
                for name in z.namelist():
                    if name.endswith("/") or ".data/" in name:
                        continue
                    out = target / name
                    out.parent.mkdir(parents=True, exist_ok=True)
                    out.write_bytes(z.read(name))
                    count += 1
            print(f"[ok] runtime {rwhl.name} ({count} files)")
            break
    for pkg in PKGS:
        ver, fn, dl = latest_2x(pkg)
        print(f"[info] {pkg} {ver} -> {fn}")
        whl = WHEELS_DIR / fn
        if not (whl.exists() and whl.stat().st_size > 0 and zipfile.is_zipfile(whl)):
            print(f"[down] {fn} ...", end="", flush=True)
            with urllib.request.urlopen(dl, timeout=900) as resp, open(whl, "wb") as f:
                while True:
                    chunk = resp.read(1 << 20)
                    if not chunk:
                        break
                    f.write(chunk)
            print(" ok")
        count = 0
        with zipfile.ZipFile(whl) as z:
            for name in z.namelist():
                if name.endswith("/") or ".data/" in name:
                    continue
                out = target / name
                out.parent.mkdir(parents=True, exist_ok=True)
                out.write_bytes(z.read(name))
                count += 1
        print(f"[ok] {fn} ({count} files)")


if __name__ == "__main__":
    main()
