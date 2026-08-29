"""语音问答：录音（sounddevice）→ Vosk 本地转写 → Piper1 语音合成（SAPI 兜底）。

内存策略（≤1GB 红线）：
  - Vosk 小中文模型（~44MB）进程内加载，常驻约 150MB；
  - Piper1（piper1-gpl）Python 包 + onnxruntime：语音模型 ~200MB 常驻（懒加载）；
  - 配置不完整 / 合成失败自动降级 Windows SAPI（零内存）。
整体语音功能峰值新增内存 ~350-400MB。
"""
from __future__ import annotations

import io
import json
import logging
import os
import wave

from app.core.paths import EXE_DIR

log = logging.getLogger("mindtrace.voice")

# Vosk（语音输入）
VOSK_MODEL_DIR = EXE_DIR / "models" / "vosk-model-small-cn-0.22"
# Piper1（语音输出）
PIPER_VOICE = EXE_DIR / "models" / "zh_CN-huayan-medium.onnx"
PIPER_JSON = EXE_DIR / "models" / "zh_CN-huayan-medium.onnx.json"

SAMPLE_RATE = 16000
_vosk_model = None
_piper_voice = None


def _load_vosk():
    """Vosk 模型懒加载（单例，常驻 ~150MB）。"""
    global _vosk_model
    if _vosk_model is None:
        import vosk
        _vosk_model = vosk.Model(str(VOSK_MODEL_DIR))
    return _vosk_model


def _load_piper():
    """Piper1 语音懒加载（单例，~200MB）。配置缺 num_symbols 时返回 None。"""
    global _piper_voice
    if _piper_voice is None and not tts_available():
        return None
    if _piper_voice is None:
        try:
            from piper import PiperVoice
            _piper_voice = PiperVoice.load(str(PIPER_VOICE), config_path=str(PIPER_JSON))
        except Exception as exc:  # noqa: BLE001
            log.warning("Piper1 加载失败: %s", exc)
            _piper_voice = False  # 记住失败，不再重试
    return _piper_voice if _piper_voice else None


def asr_available() -> bool:
    return VOSK_MODEL_DIR.is_dir() and (VOSK_MODEL_DIR / "am").exists()


def tts_available() -> bool:
    """Piper 可用：模型 + 配置齐全（配置需含 num_symbols/phoneme_id_map）。"""
    if not (PIPER_VOICE.exists() and PIPER_JSON.exists()):
        return False
    try:
        cfg = json.loads(PIPER_JSON.read_text(encoding="utf-8"))
        return "num_symbols" in cfg and "phoneme_id_map" in cfg
    except Exception:  # noqa: BLE001
        return False


def available() -> bool:
    return asr_available()


def record(duration: float = 15.0, silence_timeout: float = 1.2) -> bytes | None:
    """录音（静音检测提前结束）→ wav 字节（16k 单声道 16bit）。

    - 检测到连续静音 ``silence_timeout`` 秒即提前停止（用户说完即停，不等满时长）；
    - 最长 ``duration`` 秒兜底；
    - 全程无有效语音返回 None（调用方按"没说话"处理）。
    """
    try:
        import numpy as np
        import sounddevice as sd
    except Exception as exc:  # noqa: BLE001
        log.warning("录音依赖不可用: %s", exc)
        return None
    try:
        block = 1600  # 0.1s @16k
        silence_limit = max(1, int(silence_timeout * SAMPLE_RATE / block))
        max_blocks = int(duration * SAMPLE_RATE / block)
        frames: list[bytes] = []
        silence_blocks = 0
        voice_started = False
        with sd.InputStream(samplerate=SAMPLE_RATE, channels=1,
                            dtype="int16", blocksize=block) as stream:
            for _ in range(max_blocks):
                data, _overflowed = stream.read(block)
                frames.append(data.tobytes())
                audio = np.frombuffer(data, dtype=np.int16).astype(np.float32) / 32768.0
                rms = float(np.sqrt(np.mean(audio * audio)))
                if rms > 0.015:          # 有声音
                    voice_started = True
                    silence_blocks = 0
                elif voice_started:      # 说完后的静音计时
                    silence_blocks += 1
                    if silence_blocks >= silence_limit:
                        break
        if not frames or not voice_started:
            return None
        buf = io.BytesIO()
        with wave.open(buf, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(SAMPLE_RATE)
            w.writeframes(b"".join(frames))
        return buf.getvalue()
    except Exception as exc:  # noqa: BLE001
        log.warning("录音失败: %s", exc)
        return None


def transcribe(wav_bytes: bytes) -> str:
    """Vosk 转写 wav → 文本（进程内，模型常驻）。"""
    if not asr_available():
        log.warning("语音输入不可用：缺少 Vosk 模型")
        return ""
    try:
        import vosk

        model = _load_vosk()
        recognizer = vosk.KaldiRecognizer(model, SAMPLE_RATE)
        # 跳过 wav 头，喂裸 PCM
        data = wav_bytes
        if data[:4] == b"RIFF":
            off = 12
            while off < len(data) - 8:
                cid = data[off:off + 4]
                size = int.from_bytes(data[off + 4:off + 8], "little")
                if cid == b"data":
                    data = data[off + 8:off + 8 + size]
                    break
                off += 8 + size
        for i in range(0, len(data), 8000):
            recognizer.AcceptWaveform(data[i:i + 8000])
        res = json.loads(recognizer.FinalResult())
        return (res.get("text") or "").strip()
    except Exception as exc:  # noqa: BLE001
        log.warning("转写失败: %s", exc)
        return ""


def speak(text: str) -> None:
    """Piper1 语音合成（中文女声），失败/配置不全降级 SAPI。"""
    if not text:
        return
    voice = _load_piper()
    if voice is None:
        _speak_sapi(text)
        return
    try:
        import winsound

        buf = io.BytesIO()
        with wave.open(buf, "wb") as w:
            voice.synthesize_wav(text, w)
        winsound.PlaySound(buf.getvalue(), winsound.SND_MEMORY)
    except Exception as exc:  # noqa: BLE001
        log.warning("Piper1 合成失败: %s，降级 SAPI", exc)
        _speak_sapi(text)


def _speak_sapi(text: str) -> None:
    """降级：Windows 自带 SAPI 语音合成（零内存）。"""
    if not text:
        return
    try:
        import win32com.client

        speaker = win32com.client.Dispatch("SAPI.SpVoice")
        speaker.Speak(text)
    except Exception as exc:  # noqa: BLE001
        log.warning("SAPI 合成失败: %s", exc)
