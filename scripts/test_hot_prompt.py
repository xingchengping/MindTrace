"""热信号提取提示词验证。"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.collector.monitor import MonitorService
from app.core.config import AppConfig
from app.core.database import init_db
from app.llm.engine import LlamaServer

cfg = AppConfig.load()
engine, S = init_db(cfg.data_dir)
llm = LlamaServer(server_path="x", model_path=None, port=cfg.server_port, n_ctx=128)
llm.attach_external(wait_seconds=5)

prompt = (
    "这是刚发生的一段工作过程。立即压缩为事件。\n"
    '输出格式必须严格是 JSON 数组，每个元素是对象：\n'
    '[{"activity": "做了什么", "intent": "意图", "project": "项目名或null", "importance": 0-1}]\n'
    "只输出 JSON 数组。\n\n"
    "- [11:50] agent_chat: 修复 API 网关时遇到 error：连接池耗尽\n"
    "- [11:51] agent_chat: 搞定 fixed：改用重试机制后通过"
)
raw = llm.chat(
    [{"role": "system", "content": "你是严谨的数据提取器，只输出合法 JSON。"},
     {"role": "user", "content": prompt}],
    temperature=0.2,
)
print("RAW:", raw[:250])
print("PARSED:", MonitorService._parse_events(raw))
