"""语音资源安装：Vosk 中文转写模型（语音输入）+ Piper1（语音输出，Python 包自带 espeak-ng-data）。

用法：python scripts/setup_voice.py
已就绪：
  models/vosk-model-small-cn-0.22/   （Vosk 中文 ASR，~44MB，来自 alphacephei.com，本机已装）
  piper1-gpl（.venv 内 Python 包 piper==1.7.0，含 espeak-ng-data + g2pw，来自 GitHub releases）
  models/zh_CN-huayan-medium.onnx + .json （中文女声，需自行提供/下载）

依赖包（install_wheels.py 自动处理）：vosk srt requests tqdm cffi pycparser onnxruntime
"""
from __future__ import annotations

import io
import sys
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VOSK_DIR = ROOT / "models" / "vosk-model-small-cn-0.22"

VOSK_URL = "https://alphacephei.com/vosk/models/vosk-model-small-cn-0.22.zip"


def _fetch(url: str, timeout: int = 300) -> bytes:
    last = None
    for attempt in range(4):
        try:
            print(f"  下载（尝试 {attempt + 1}/4）...", flush=True)
            return urllib.request.urlopen(url, timeout=timeout).read()
        except Exception as exc:  # noqa: BLE001
            last = exc
            print(f"  失败: {type(exc).__name__} {str(exc)[:60]}", flush=True)
            import time

            time.sleep(3)
    raise last


def setup_vosk() -> bool:
    if (VOSK_DIR / "am").exists():
        print("[1] Vosk 模型已存在")
        return True
    print("[1] 下载 Vosk 中文模型（~44MB）")
    data = _fetch(VOSK_URL)
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        z.extractall(ROOT / "models")
    ok = (VOSK_DIR / "am").exists()
    print("    Vosk 模型就绪" if ok else "    Vosk 解压异常")
    return ok


def check_piper1() -> bool:
    """Piper1 已作为 Python 包安装（含 espeak-ng-data + g2pw，无需单独下载二进制）。"""
    try:
        import piper  # noqa: F401

        print("[2] Piper1 已安装（.venv Python 包，自带 espeak-ng-data）")
        return True
    except ImportError:
        print("[2] Piper1 未安装——需手动安装 piper1-gpl wheel（GitHub releases，见 README）")
        return False


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ok = setup_vosk()
    piper_ok = check_piper1()
    print("语音资源就绪" if ok and piper_ok else "部分缺失，请见 README「语音问答」一节")


if __name__ == "__main__":
    main()
