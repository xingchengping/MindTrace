"""Windows 采集系统（Phase 2）。

文本优先：一切可提取的文字是第一信息通道；截图仅兜底（text_first/auto/off）。
所有采集器产出原始日志（memories_short），规则级即时提取 + 模型级批量压缩为事件。
"""
