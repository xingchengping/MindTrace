"""LLM 运行时：llama.cpp 官方 llama-server 封装。

后端通过 llama-server 的 OpenAI 兼容 HTTP API 通信：
- chat 模式：/v1/chat/completions（SSE 流式）
- embedding 模式：/v1/embeddings
使用 urllib（标准库）实现，零额外依赖。
"""
from __future__ import annotations

import json
import logging
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

log = logging.getLogger("mindtrace.llm")

# GUI 版（console=False）父进程启动控制台子程序时，不加此标志每个子进程都会弹一个
# 黑色控制台窗口（打包后双击会看到三个黑窗：对话/嵌入/视觉 llama-server）。
_WIN_NO_WINDOW = {"creationflags": subprocess.CREATE_NO_WINDOW} if sys.platform == "win32" else {}


class LlamaServer:
    def __init__(
        self,
        server_path: str | Path,
        model_path: str | Path | None,
        host: str = "127.0.0.1",
        port: int = 8081,
        n_ctx: int = 4096,
        n_threads: int = 4,
        n_batch: int = 256,
        log_path: str | Path | None = None,
        mode: str = "chat",
        n_parallel: int = 1,
        mmproj_path: str | Path | None = None,
    ):
        self.server_path = Path(server_path)
        self.model_path = Path(model_path) if model_path else None
        self.host = host
        self.port = port
        self.n_ctx = n_ctx
        self.n_threads = n_threads
        self.n_batch = n_batch
        self.n_parallel = n_parallel
        self.mmproj_path = Path(mmproj_path) if mmproj_path else None
        self.log_path = Path(log_path) if log_path else None
        self.mode = mode  # "chat" | "embedding" | "vision"
        self.base_url = f"http://{host}:{port}"
        self.proc: subprocess.Popen | None = None
        self.error: str | None = None
        self.ready = False

    # ---------- 生命周期 ----------

    def start(self, wait_seconds: int = 180) -> bool:
        if not self.server_path.exists():
            self.error = f"未找到 llama-server：{self.server_path}"
            return False
        if self.model_path is None or not self.model_path.exists():
            self.error = (
                f"未找到模型：{self.model_path}。请先运行："
                "python scripts/download_models.py"
            )
            return False

        cmd = [
            str(self.server_path),
            "-m", str(self.model_path),
            "--host", self.host,
            "--port", str(self.port),
            "-c", str(self.n_ctx),
            "-t", str(self.n_threads),
            "-b", str(self.n_batch),
        ]
        if self.mode == "embedding":
            cmd += ["--embeddings"]
        elif self.mode == "vision":
            if self.mmproj_path is None or not self.mmproj_path.exists():
                self.error = f"缺少视觉投影模型：{self.mmproj_path}"
                return False
            cmd += ["--mmproj", str(self.mmproj_path), "--jinja", "--no-webui"]
        else:
            cmd += [
                "-np", "1",      # 单槽位，节省 KV 缓存内存（新版默认 auto=4）
                "--jinja",       # 使用 GGUF 内置 chat template
                "--no-webui",
            ]
        log.info("启动 llama-server: %s", " ".join(cmd))
        stdout = stderr = subprocess.DEVNULL
        if self.log_path:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            fh = open(self.log_path, "a", encoding="utf-8")
            stdout = stderr = fh
        try:
            self.proc = subprocess.Popen(cmd, stdout=stdout, stderr=stderr, **_WIN_NO_WINDOW)
        except Exception as exc:  # pragma: no cover
            self.error = f"无法启动 llama-server：{exc}"
            return False

        deadline = time.time() + wait_seconds
        while time.time() < deadline:
            if self.proc.poll() is not None:
                self.error = f"llama-server 退出（exit={self.proc.returncode}），见 {self.log_path}"
                return False
            if self._health():
                self.ready = True
                log.info("llama-server 就绪：%s", self.base_url)
                return True
            time.sleep(1.0)
        self.error = f"llama-server 启动超时（{wait_seconds}s），见 {self.log_path}"
        return False

    def attach_external(self, wait_seconds: int = 15) -> bool:
        """假设 llama-server 已由外部启动，仅做健康检查。"""
        deadline = time.time() + wait_seconds
        while time.time() < deadline:
            if self._health():
                self.ready = True
                return True
            time.sleep(1.0)
        self.error = "外部 llama-server 不可达"
        return False

    def stop(self) -> None:
        if self.proc and self.proc.poll() is None:
            log.info("关闭 llama-server...")
            self.proc.terminate()
            try:
                self.proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        self.ready = False

    # ---------- HTTP ----------

    def _health(self) -> bool:
        try:
            with urllib.request.urlopen(f"{self.base_url}/health", timeout=2) as resp:
                return resp.status == 200
        except Exception:
            return False

    def chat_stream(self, messages: list[dict], temperature: float = 0.7):
        """流式聊天生成，逐段产出 content delta。"""
        if not self.ready:
            raise RuntimeError(self.error or "llama-server 未就绪")
        body = json.dumps(
            {"messages": messages, "stream": True, "temperature": temperature},
            ensure_ascii=False,
        ).encode("utf-8")
        req = urllib.request.Request(
            f"{self.base_url}/v1/chat/completions",
            data=body,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=1800) as resp:
            for raw in resp:
                line = raw.decode("utf-8", "replace").strip()
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                try:
                    obj = json.loads(data)
                    delta = obj["choices"][0]["delta"].get("content", "")
                except (json.JSONDecodeError, KeyError, IndexError, TypeError):
                    continue
                if delta:
                    yield delta

    def chat(self, messages: list[dict], temperature: float = 0.7) -> str:
        """非流式生成，返回完整文本。"""
        return "".join(self.chat_stream(messages, temperature=temperature))

    # ---------- Embedding ----------

    def embed(self, texts: list[str]) -> list[list[float]]:
        """批量文本向量化（/v1/embeddings）。"""
        if not self.ready:
            raise RuntimeError(self.error or "embedding 服务未就绪")
        body = json.dumps({"input": texts}, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            f"{self.base_url}/v1/embeddings",
            data=body,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=600) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        items = sorted(data.get("data", []), key=lambda x: x.get("index", 0))
        return [item["embedding"] for item in items]

    # ---------- Vision ----------

    def vision_chat(self, image_path: str | Path, prompt: str = "描述这张截图的内容，提取关键信息") -> str:
        """多模态理解：图片 → 文本描述（/v1/chat/completions 图片输入）。"""
        if not self.ready:
            raise RuntimeError(self.error or "视觉服务未就绪")
        import base64

        with open(image_path, "rb") as f:
            b64 = base64.b64encode(f.read()).decode("ascii")
        mime = "image/png" if str(image_path).lower().endswith(".png") else "image/jpeg"
        body = json.dumps({
            "messages": [{
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}},
                ],
            }],
            "stream": False,
            "temperature": 0.2,
        }, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            f"{self.base_url}/v1/chat/completions",
            data=body,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=600) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return data["choices"][0]["message"]["content"]
