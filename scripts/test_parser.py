"""解析器兼容性测试。"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.collector.monitor import MonitorService

cases = [
    '[{"activity": "修复bug", "intent": "调试", "importance": 0.8}]',
    '[["activity", "打开文件", "project", "MindTrace", "importance", 0.9]]',
    '```json\n[{"activity": "重构", "project": null}]\n```',
    'not json at all',
]
for c in cases:
    print(" ->", MonitorService._parse_events(c))
