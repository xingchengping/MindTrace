# MindTrace · Personal Cognitive OS（可直接运行实际可直接运行MindTrace.exe\MindTrace.exe，因为配置文件_intelnal偏大不便上传，故无法运行exe文件）

个人认知操作系统 / AI 第二大脑 —— 完全本地运行、长期理解用户的个人记忆系统。

**当前状态：Phase 4（尖端检索 + 跨时间推理 + 语音问答）**

## 快速开始

```bash
# 1. 环境
#    已内置项目 venv：.venv（Python 3.12；含 fastapi/uvicorn/sqlalchemy/pyyaml/psutil）（过大无法上传，需手动安装库，在MindTrace/requirements.txt）
#    网络受限/沙箱环境：pip 不可用时用 scripts/install_wheels.py 装 wheel（见下）

# 2. 下载模型（默认下载当前硬件档位所需的 chat 模型）
python scripts/download_models.py
# 可用: --model all（chat + embedding）| --model embedding | --source hf-mirror

# 3.（可选）语音问答：安装 Vosk 转写模型（语音输入）+ Piper1（语音合成）
python.exe scripts/setup_voice.py
# 产物：models/vosk-model-small-cn-0.22/（Vosk 中文 ASR，~44MB）
# 语音合成：piper1-gpl Python 包（.venv 内，含 espeak-ng-data + g2pw）
#            + models/zh_CN-huayan-medium.onnx（中文女声，自行提供）
# 缺模型时语音功能自动降级（按钮无效化），TTS 失败自动回退 Windows SAPI

# 4. 启动
python main.py
# 浏览器打开 http://127.0.0.1:4000
```

> **LLM 运行时**：llama.cpp 官方二进制（`llama-b8981-bin-win-cpu-x64/llama-server.exe`），
> 由 `main.py` 自动拉起子进程，通过 OpenAI 兼容 HTTP API 通信；退出时自动关闭。
> llama-server 端口在 `config.yaml → llm.server.port`（默认 18081；被占用时改这里）。

> **沙箱环境装依赖**（pip 解包被拒时）：`python scripts/install_wheels.py <pkg> [pkg...]`
> 从 PyPI 直下 cp312 win_amd64 wheel 并直接解压进 site-packages，不依赖 pip 临时目录。

## 目录结构

```
main.py                 入口（python main.py → :4000）
config.yaml             配置：硬件档位 / llama-server(chat+embedding+vision) / 记忆 / 模型清单
requirements.txt        Phase 0 依赖（不含 llama-cpp-python）
models/                 下载的 GGUF 模型（chat + embedding + vision）+ 语音（Vosk ASR / huayan TTS）
data/                   mindtrace.db（SQLite）、日志
scripts/
  download_models.py    GGUF 下载（ModelScope/HF 直链 + 断点续传）
  setup_voice.py        语音资源安装（Vosk ASR 模型下载 + Piper1 检查）
  init_db.py            建表
app/
  core/                 配置（硬件档位探测）、数据库 schema、迁移、日志
  llm/                  llama-server 封装（chat / embedding / vision 三模式）
  memory/               向量存储（SQLite BLOB + numpy）、记忆服务（HyDE+重排检索/入库/上下文）
  voice.py              语音问答（录音→Vosk 转写→Piper1 合成，SAPI 兜底）
  graph/                NetworkX 图谱存储 + 增量构建器（事件/经验/任务）
  api/                  chat（会话/记忆对话/SSE流式）、memory、settings、graph
  desktop/              悬浮球（Qt Chromium 果冻球 + Tk 回退）、弹窗对话、托盘、通知、全屏流光特效（glow.py，含唤醒音效）
  web/                  四视图前端（对话/任务/经验/时间线/图谱，Apple 毛玻璃 + 深色模式）
```

## 硬件档位

启动时按内存自动探测（`config.yaml` 可手动覆盖）。**模型分两套，以 8GB 为界**——≤8GB 用轻量模型+视觉懒启动，为工作环境留足内存：

| 档位 | 判定 | 模型套 | 上下文 | 视觉 |
|---|---|---|---|---|
| light | RAM ≤ 8GB | **MindTrace-1.5b Q3_K_M** + MindTrace-Embedding | 2048 | **UD-IQ2_M 懒启动**（有截图才拉起） |
| standard（默认） | 8GB < RAM ≤ 16GB | **MindTrace-3b Q4_K_M** + MindTrace-Embedding | 4096 | Q4_K_M 常驻 |
| powerful | RAM > 16GB | **MindTrace-3b Q4_K_M** + MindTrace-Embedding | 8192 | Q4_K_M 常驻（双槽并行） |

> light 档常驻 ≈2.4GB（1.5B Q3 + Embedding + 应用），视觉按需启停不占内存。
> 下载当前档位模型：`scripts/download_models.py`（自动含档位视觉模型）。
> 模型文件：`models/MindTrace-1.5b-instruct-q3_k_m.gguf` / `MindTrace-3b-instruct-q4_k_m.gguf` / `MindTrace-Embedding-0.6B-Q8_0.gguf`（源自 Qwen 系列，更名以统一品牌）。

## Phase 4 已完成（尖端检索 + 跨时间推理 + 语音问答）

- [x] **HyDE 检索**：提问前由本地 LLM 生成"假设的用户记录"扩充查询语义，与原查询**双路向量召回**——模糊描述（"那个蓝色图标的项目"）也能命中
- [x] **多信号融合重排**：向量(45%) + 关键词重叠(25%) + 时效衰减(15%) + 重要度(15%) 四路打分重排，零新增模型内存
- [x] **跨时间推理问答**：图谱问答升级——命中实体间的**多跳决策路径**（项目→决策→方案→经验，带置信度）+ **关联事件时间线**（按时间排序），回答"去年为什么换方案"时给出带日期的因果链；证据分级 A/B/C 与"根据记录推断"标注不变
- [x] **语音问答**：弹窗内 🎤 按钮 → **循环对话模式**（点一次开始、再点结束）——静音检测录音（说完即停）→ **Vosk 小中文模型**本地转写（进程内 ~150MB）→ 自动发送 → 流式回答 → **Piper1**（zh_CN-huayan-medium 中文女声，进程内 onnxruntime ~200MB）播报 → 播报完自动开始下一轮录音；失败自动回退 Windows SAPI
- [x] **内存红线 ≤1GB**：HyDE/重排/跨时间推理全部复用现有 LLM 与图谱（0 新增）；语音 Vosk 常驻 ~150MB + Piper1 进程内 ~200MB，总新增 <400MB

> 语音依赖：Vosk（`models/vosk-model-small-cn-0.22`，来自 alphacephei.com）+ Piper1（.venv Python 包 piper==1.7.0，来自 GitHub releases，含 espeak-ng-data）+ 中文女声 `models/zh_CN-huayan-medium.onnx`；`scripts/setup_voice.py` 一键安装。

## Phase 3 已完成（知识图谱 + 时间推理）

- [x] **图谱构建**：事件/经验/任务 → 节点（项目/文件/问题/方案/决策/经验/论文）与带时间边（修改/导致/失败/成功/替代/复用/来源），增量同步（10 分钟）
- [x] **图谱手动编辑**：网页图谱视图"✏️ 编辑图谱"模式——点击节点编辑名称/摘要/删除、点击边中点编辑关系/删除、手动新增节点；PATCH/DELETE 接口与 SQLite 持久化同步
- [x] **图谱导出**：网页图谱视图一键导出——📷 高清 PNG 图片（3x 缩放 + 标题/统计）或 🌐 可交互 HTML（自包含、滚轮缩放/拖拽平移/点击节点高亮看详情）
- [x] **图谱存储**：NetworkX 内存图 + SQLite 持久化 + 重复节点合并（每日巩固）
- [x] **推理查询**：路径（无向可达 + 置信度×跳数衰减打分）、邻域（N 跳）、时间窗口、节点搜索
- [x] **/chat 图谱问答**：新增"图谱推理"意图分类 → 向量+图谱检索融合 → **证据分级**（A 确认经验 > B 情景事件 > C 图谱推断标注"根据记录推断"）→ 引用脚注 → **反问澄清**（多候选先让用户选）
- [x] **图谱可视化**：网页"🕸 图谱"视图（Canvas 分列布局，点击节点展开邻域）
- [x] **w6 接入**：图谱被引用次数 → 经验 ref_count → 重要性评分
- [x] **截图视觉理解闭环**：Qwen2.5-VL-3B Q4_K_M + mmproj（第三 llama-server）→ "截图待理解"事件 → 视觉理解成 Event → **立即删除图片**
- [x] 实测：问"为什么换方案"→ 返回因果链 + "根据记录推断"标注 + 引用

> 三引擎：chat(18081) + embedding(18082) + vision(18083)，全部本地 CPU。

## Phase 2 已完成（自动跟踪 + 经验沉淀 + 桌面层）

- [x] **Windows 采集**：前台窗口（Win32，跟随/锁定/关闭三模式，右键悬浮球"🎯 监控目标"热切换、重启后保持）、文件事件（watchdog）、剪贴板、Git、浏览器历史（授权项默认关）
- [x] **默认暂停采集 + 唤醒流光音效**：启动即暂停（`collector.start_paused: true`），右键菜单显示"开始采集"；点击开始采集 → **全屏边缘流光 + 同步唤醒音效**（Apple Intelligence 唤醒风格：QPainter 四边暖色渐变边框——橙/金/黄暖色正弦波浪沿边流动（色彩流动+波动）、DestinationIn 向屏幕内羽化无硬边、四角径向补角无缝闭合、中央完全透明、60fps、鼠标穿透/置顶/无任务栏、淡入淡出；音效播放 `music.mp3`（MCI → ffplay → 合成 Chime 逐级降级，与淡入同步、异步防重入），点击暂停 → 特效淡出隐藏
- [x] **两级事件提取**：规则级即时（问题/成功信号、文件、Git）+ 模型级批量压缩（5 分钟，LLM 批处理日志→事件）
- [x] **向量去重**：相似度 >0.95 合并重要度，不产生重复记忆
- [x] **Task Intelligence Engine**：任务状态机（planning→working→blocked⇄testing→solved/abandoned）
  - 会话上下文 LSM（recent_bullets + summary_chunks，只追加不重写，防摘要漂移）
  - 卡点检测（连续 N 批失败信号 + LLM 提炼卡点描述）
  - 攻克检测（多信号投票 + 迟滞：成功信号/错误消失/焦点转移/用户确认）
- [x] **攻克确认**：系统 Toast 弹窗（win11toast）+ 通知中心（Web 铃铛）
- [x] **自动经验沉淀**：攻克后 LLM 提炼七字段经验（问题/背景/尝试/失败/方案/场景/建议）→ 待确认
- [x] **记忆巩固流水线**：降级不删除（短期日志→回收站/事件→回收站）+ 保留上限 + 回收站 30 天 + 可解释重要性评分
- [x] **桌面层**：果冻球悬浮球（Qt Chromium，左键对话小窗/右键菜单/待确认角标，Tk 兜底）+ 系统托盘 + 原生对话窗（pywebview→Edge app 模式→浏览器）
- [x] **任务面板**：实时显示任务/阶段/卡点，可手动标记完成/放弃
- [x] **维护视图**：数据量/上限/保留策略 + 手动触发巩固
- [x] **并行槽位**：powerful 档 -np 2（对话与后台提取互不阻塞）；SQLite 忙等 30s + LLM 调用前提交事务（防锁）

> 启动：`python main.py`（正常模式带悬浮球/托盘）；`python main.py --headless`（仅 Web，测试用）

## Phase 1 已完成（MVP：有记忆、能回忆、带证据）

- [x] **四视图界面**：对话 / 当前任务 / 经验库 / 记忆时间线（Apple 极简 + 毛玻璃 + 深色模式）
- [x] **双引擎**：chat（MindTrace-3b Q4）+ embedding（MindTrace-Embedding-0.6B Q8），各跑一个 llama-server
- [x] **向量检索**：SQLite BLOB 存向量 + numpy 余弦（零原生依赖；万级向量毫秒级）
- [x] **手动记忆**：`记住：xxx`（事件）/ `记住经验：问题 => 方案`（经验）
- [x] **自动沉淀**：每轮问答自动摘要成事件入库并向量化
- [x] **记忆问答**：问题分类（回忆/经验/当前/通用）→ 向量检索 → 上下文组装 → 来源脚注 [1][2]
- [x] **覆盖度检查**：无相关记忆时明确"这部分我没记录到"
- [x] **反馈回流**：回答后 👍/👎 → user_signals 表
- [x] **CRUD API**：events / experiences（确认=证据A级）/ tasks / feedback

## Phase 0 已完成（冒烟测试通过）

- [x] 项目骨架与配置系统（硬件档位自动探测：light/standard/powerful）
- [x] SQLite schema（14 张核心表：事件/任务/经验/图谱/短期记忆/会话/来源/设置/反馈/回收站/通知/提醒）
- [x] llama-server 子进程管理（自动启动/健康检查/退出清理）
- [x] /api/chat SSE 流式对话 + 对话历史持久化（已实测：3B Q4 首轮 4.1s 流式返回）
- [x] **会话管理**：新建 / 列表 / 重命名 / 删除（侧边栏）
- [x] **消息管理**：编辑用户消息（保存后自动重新生成回复，SSE 流式）/ 删除（截断其后消息）
- [x] 最小前端聊天页 + /api/health
- [x] 模型下载脚本（断点续传，ModelScope/HF-mirror/HF 三通道）
- [x] 工作区 venv（.venv）+ wheel 直装工具（scripts/install_wheels.py）

## Phase 1（下一步）

记忆检索接入 /chat、四视图界面（聊天/任务/经验/时间线）、Apple 毛玻璃设计系统、悬浮球 + 原生对话窗。

## 设计文档

完整架构、原理与技术栈详见 `docs/PLAN.md`（v2.6）。
