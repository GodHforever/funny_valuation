# Dexter 深度分析：自主金融研究 Agent 的设计与实现

> 基于 https://github.com/virattt/dexter 仓库源码的全面分析
> 重点关注：可借鉴的轻依赖、个人定制化、低成本金融助手设计思路

---

## 一、项目定位与核心理念

### 1.1 是什么

Dexter 是一个 **终端原生的自主金融研究 Agent**，用 TypeScript 编写，基于 LangChain 构建。它接收用户的自然语言金融问题，自动分解为研究步骤，调用实时市场数据工具，自我验证结果，最终输出有数据支撑的研究结论。

关键定位词：**CLI-first、Agent 驱动、金融专用、自主研究**。

### 1.2 核心理念（来自 SOUL.md）

Dexter 有一个独特的"灵魂文件" `SOUL.md`，定义了它的投资哲学和人格：

- **价值投资导向**：站在 Buffett/Munger 肩膀上，强调内在价值、安全边际、能力圈
- **逆向思维**：先问"什么会失败"再问"为什么会成功"
- **数据先行**：先收集数据再形成观点，而非先有观点再找证据
- **诚实面对局限**：每个模型都是错的，关键在于理解假设和不确定性的范围

**可借鉴点**：为个人金融助手定义一份"灵魂文件"，将个人的投资理念和分析偏好编码为 System Prompt 的一部分，这样 AI 的输出就天然契合你的思维框架。

### 1.3 技术栈总览

| 层次 | 技术选型 | 说明 |
|------|---------|------|
| 运行时 | Bun | 比 Node.js 更快的启动和运行 |
| 语言 | TypeScript (严格模式) | 类型安全 |
| LLM 框架 | LangChain | 多模型抽象、工具绑定 |
| CLI UI | pi-tui (类 Ink) | React 式终端渲染 |
| 数据库 | SQLite (bun:sqlite / better-sqlite3) | 记忆系统的向量存储 |
| 金融数据 | Financial Datasets API | 财报、指标、SEC 文件 |
| 搜索 | Exa / Perplexity / Tavily (按优先级降级) | 网络搜索 |
| 浏览器 | Playwright | 页面抓取 |
| 消息网关 | WhatsApp (Baileys) | 多渠道触达 |

---

## 二、整体架构

### 2.1 目录结构与模块职责

```
src/
├── agent/           # 核心 Agent 循环
│   ├── agent.ts     # 主循环：迭代调用 LLM → 执行工具 → 管理上下文
│   ├── prompts.ts   # System Prompt 构建
│   ├── scratchpad.ts # 工具调用追踪（JSONL 格式）
│   ├── compact.ts   # LLM 驱动的上下文压缩
│   ├── microcompact.ts # 轻量级上下文裁剪
│   ├── tool-executor.ts # 并发工具执行引擎
│   ├── run-context.ts   # 单次运行的可变状态
│   └── types.ts     # 事件类型定义
├── model/
│   └── llm.ts       # 多供应商 LLM 抽象层
├── providers.ts     # 供应商注册中心
├── tools/           # 工具集
│   ├── registry.ts  # 工具注册与发现
│   ├── finance/     # 金融数据工具（8+ 子工具）
│   ├── search/      # 网络搜索（多后端降级）
│   ├── browser/     # Playwright 浏览器
│   ├── memory/      # 记忆读写工具
│   ├── filesystem/  # 文件读写
│   └── skill.ts     # Skill 调用工具
├── skills/          # 可扩展技能（Markdown 定义）
│   ├── dcf/         # DCF 估值技能
│   └── registry.ts  # 技能发现与加载
├── memory/          # 持久化记忆系统
│   ├── database.ts  # SQLite 向量存储
│   ├── embeddings.ts # 多后端向量嵌入
│   ├── search.ts    # 混合搜索（向量+关键词）
│   ├── store.ts     # Markdown 文件存储
│   └── indexer.ts   # 增量索引与文件监听
├── cli.ts           # 终端 UI 主循环
├── controllers/     # MVC 控制器
├── components/      # UI 组件
├── gateway/         # WhatsApp 网关
├── cron/            # 定时任务
├── evals/           # 评估框架
└── utils/           # 通用工具
```

### 2.2 数据流全景

```
用户输入 (CLI / WhatsApp)
    │
    ▼
┌─────────────────────────────────────────────┐
│  AgentRunnerController                       │
│  - 管理会话状态                              │
│  - 事件驱动 UI 更新                          │
│  - 中断/恢复控制                             │
└─────────────────┬───────────────────────────┘
                  │
                  ▼
┌─────────────────────────────────────────────┐
│  Agent.run() — 核心异步生成器循环             │
│                                              │
│  for iteration in 1..maxIterations:          │
│    1. microcompact(messages)     ← 轻量裁剪  │
│    2. stripOldThinking(msgs, 2) ← 保留最近2轮│
│    3. streamLLM(messages)       ← 流式调用   │
│    4. if no tool_calls → 直接回答，退出       │
│    5. executeTools(response)    ← 并发执行    │
│    6. enforceResultBudget(msgs) ← 结果预算    │
│    7. manageContextThreshold()  ← 上下文管理  │
│       ├── memoryFlush → 写入持久记忆          │
│       ├── compactContext → LLM 总结压缩       │
│       └── truncateMessages → 截断旧轮次       │
│    8. drainQueue()              ← 处理排队消息│
│                                              │
│  yield AgentEvent (tool_start, tool_end,     │
│         thinking, stream_progress, done)     │
└─────────────────────────────────────────────┘
                  │
                  ▼
┌─────────────────────────────────────────────┐
│  工具层                                      │
│  get_financials (元工具) → 路由到子工具       │
│  get_market_data (元工具) → 路由到子工具      │
│  web_search, browser, read_filings, skill... │
└─────────────────────────────────────────────┘
                  │
                  ▼
┌─────────────────────────────────────────────┐
│  外部数据源                                  │
│  Financial Datasets API / Exa / Tavily / Web │
└─────────────────────────────────────────────┘
```

---

## 三、核心设计思想深度解析

### 3.1 Agent 循环 — 有限迭代 + 自我验证

**设计核心** (`agent.ts`):

Agent 采用"迭代-工具-反思"三段式循环，默认最多 10 次迭代。每次迭代中：

1. LLM 分析当前上下文，决定调用什么工具（或直接回答）
2. 工具并发执行，结果注入上下文
3. LLM 反思结果，决定是否需要更多数据

```
while (iteration < maxIterations) {
    response = callLLM(messages)
    if (no tool_calls) → 输出最终答案
    else → 执行工具 → 结果加入 messages → 继续循环
}
```

**防失控机制**：
- `maxIterations` 硬上限（默认 10）
- Scratchpad 工具调用计数 + 相似查询检测（Jaccard 相似度 ≥ 0.7 时警告）
- 每个工具默认 3 次调用软限制，超过后注入警告而非阻断
- 独立的 `AbortController` 支持用户随时中断

**可借鉴点**：有限迭代 + 软限制是比硬阻断更好的策略。Agent 有时确实需要多次调用同一工具（不同参数），硬阻断会导致研究半途而废。

### 3.2 元工具架构 — 用 LLM 路由 LLM

这是 Dexter 最巧妙的设计之一。

`get_financials` 和 `get_market_data` 都是"元工具"——它们本身不直接调用 API，而是用一个 LLM 调用来路由用户的自然语言查询到具体的子工具：

```
用户: "比较 AAPL 和 MSFT 最近3年的营收增长"
    │
    ▼
get_financials(query="比较 AAPL 和 MSFT 最近3年的营收增长")
    │
    ▼ (LLM 路由决策)
    ├── get_income_statements(ticker="AAPL", period="annual", limit=3)
    └── get_income_statements(ticker="MSFT", period="annual", limit=3)
    │
    ▼ (并行执行，合并结果)
    返回结构化 JSON
```

**路由 LLM 的 System Prompt** (`get-financials.ts:buildRouterPrompt`) 包含：
- Ticker 解析规则（公司名 → 代码）
- 日期推断规则（"去年" → `report_period_gte`）
- 工具选择指南（财务指标用 snapshot、历史趋势用 key_ratios）
- 效率规则（只请求必要数据量）

**可借鉴点**：这个模式非常适合中国市场场景——定义一个 `get_a_stock_data` 元工具，内部路由到东方财富/Tushare/AKShare 等不同数据源。用 LLM 理解用户意图，自动选择最合适的 API 和参数。

### 3.3 三级上下文管理 — 从不丢失关键数据

Dexter 的上下文管理是层层递进的，核心目标是：**在有限 token 窗口内保留尽可能多的有用信息**。

**第一级：Microcompact（轻量裁剪）** (`microcompact.ts`)

每轮迭代前触发，替换旧 ToolMessage 内容为 `[Old tool result content cleared]`：
- 计数触发：可压缩 ToolMessage > 8 个时，保留最近 4 个
- Token 触发：可压缩 ToolMessage 总 token > 80K 时触发
- 只对只读工具（get_financials、web_search 等）生效
- 不调用 LLM，零额外成本

**第二级：Compaction（LLM 压缩）** (`compact.ts`)

当估计 token 超过阈值时（基于模型 context window 动态计算），用快速模型（如 gpt-4.1 或 claude-haiku）生成结构化摘要：
- 9 段式摘要模板：原始查询、关键概念、数据检索、错误与重试、分析进展、数值数据、待获取数据、当前状态、建议下一步
- 使用 `<analysis>` + `<summary>` 标签引导思考
- 压缩后的摘要替换整个消息数组，但新获取的数据仍追加在摘要后

**第三级：Truncate（硬截断）**

当 compaction 失败时的兜底方案，直接删除最旧的消息轮次，保留最近 5 轮。

**额外优化 — Strip Old Thinking**：
每轮只保留最近 2 条 AIMessage 的推理文本，更早的 AIMessage 只保留 tool_calls 结构（必须保留以配对 ToolMessage）。

**可借鉴点**：三级管理策略值得完整借用。对于个人金融助手：
1. Microcompact 几乎零成本，应首选
2. Compaction 用快速便宜的模型（如 DeepSeek），每次约 0.01-0.05 元
3. 硬截断作为最后保障

### 3.4 Scratchpad — 可审计的研究过程

`scratchpad.ts` 是 Agent 的"实验笔记本"：

- **存储格式**：JSONL（每行一个 JSON），追加写入，不修改
- **记录类型**：`init`（原始查询）、`tool_result`（工具调用及结果）、`thinking`（推理过程）
- **文件位置**：`.dexter/scratchpad/{timestamp}_{hash}.jsonl`
- **双重职责**：
  - 运行时：工具调用计数、相似查询检测、结果格式化
  - 运行后：完整的调试审计日志

**可借鉴点**：JSONL 格式的 scratchpad 非常实用。对于个人金融助手：
- 可以回顾 AI 查看了哪些数据、做了什么推理
- 可以作为后续分析的数据源
- 可以用来评估 AI 的研究质量
- 每次研究一个文件，天然隔离

### 3.5 Skill 系统 — Markdown 定义的可复用工作流

Dexter 的 Skill 系统将复杂分析流程编码为 Markdown 文件：

```yaml
---
name: dcf-valuation
description: Performs discounted cash flow (DCF) valuation analysis...
---

# DCF Valuation Skill

## Step 1: Gather Financial Data
Call the `get_financials` tool with these queries:
### 1.1 Cash Flow History
**Query:** `"[TICKER] annual cash flow statements for the last 5 years"`
**Extract:** `free_cash_flow`, `net_cash_flow_from_operations`
...
```

**工作机制**：
1. 启动时扫描 `src/skills/` 和 `.dexter/skills/` 目录
2. 提取 YAML frontmatter 作为元数据，注入 system prompt
3. LLM 判断用户查询匹配某个 skill 时，调用 `skill` 工具
4. `skill` 工具返回完整的 Markdown 指令，LLM 按步骤执行
5. 每个 skill 每次查询只执行一次（去重机制）

**DCF 估值 Skill 的 8 步流程**：
1. 收集财务数据（现金流、指标、资产负债表、当前价格、公司信息）
2. 计算 FCF 增长率（5 年 CAGR，上限 15%）
3. 估算 WACC（基于行业基准 + 公司特征调整）
4. 预测未来现金流（5 年 + 终值，增长率每年衰减 5%）
5. 计算现值和每股公允价值
6. 敏感性分析（3×3 矩阵：WACC ±1% × 终值增长率）
7. 验证结果（EV 对比、终值占比、每股交叉验证）
8. 结构化输出

**可借鉴点**：这是个人金融助手最核心的可借鉴设计。可以定义：
- `pe-band-analysis.md` — PE Band 估值
- `dividend-analysis.md` — 股息分析
- `industry-comparison.md` — 行业对比
- `earnings-review.md` — 财报速评
- 任何你常用的分析框架都可以编码为 Skill

### 3.6 记忆系统 — 向量 + 关键词混合搜索

Dexter 的记忆系统是一个完整的 RAG 管线：

**存储层** (`store.ts`)：
- Markdown 文件存储：`MEMORY.md`（长期）+ 每日日记 `YYYY-MM-DD.md`
- 路径安全：防止路径遍历攻击

**索引层** (`indexer.ts` + `database.ts`)：
- SQLite 存储分块后的文本和向量嵌入
- FTS5 全文搜索索引
- 增量索引 + 文件监听（debounce 1.5s）
- 供应商指纹追踪（更换 embedding 模型时自动清空重建）

**嵌入层** (`embeddings.ts`)：
- 多后端：OpenAI → Gemini → Ollama（自动降级）
- 批量处理（64/批）+ 超时控制（15s）
- 嵌入缓存（避免重复计算）

**搜索层** (`search.ts`)：
- 混合搜索：向量搜索（余弦相似度）+ 关键词搜索（BM25）
- 权重合并：默认 70% 向量 + 30% 关键词
- 单路径可用时自动切换全权重
- 时间衰减（半衰期 30 天，MEMORY.md 常青）
- MMR（Maximal Marginal Relevance）去冗余，确保结果多样性

**可借鉴点**：对于个人金融助手，记忆系统能记住：
- 你的持仓和关注列表
- 你的风险偏好和投资风格
- 之前的分析结论和决策理由
- 市场观察和判断
- 但注意：全套 RAG 管线是重依赖，轻量化方案可以只用 Markdown 文件 + 简单文本搜索

---

## 四、多模型供应商抽象

### 4.1 Provider 注册机制

`providers.ts` 定义了统一的供应商注册中心：

```typescript
interface ProviderDef {
  id: string;           // 'anthropic'
  displayName: string;  // 'Anthropic'
  modelPrefix: string;  // 'claude-' （用于自动路由）
  apiKeyEnvVar?: string;
  fastModel?: string;   // 轻量任务用的快速模型
  contextWindow?: number;
}
```

支持 8 个供应商：OpenAI、Anthropic、Google、xAI、Moonshot（Kimi）、DeepSeek、OpenRouter、Ollama（本地）。

### 4.2 智能路由

模型路由基于名称前缀：
- `claude-*` → Anthropic
- `gemini-*` → Google
- `grok-*` → xAI
- `deepseek-*` → DeepSeek
- `kimi-*` → Moonshot
- `ollama:*` → Ollama
- `openrouter:*` → OpenRouter
- 其他 → OpenAI（默认）

### 4.3 成本优化

- **双模型策略**：主模型用于推理（如 GPT-5.4），快速模型用于压缩/摘要（如 GPT-4.1）
- **Anthropic Prompt Caching**：对 System Prompt 标记 `cache_control: { type: 'ephemeral' }`，后续调用输入 token 节省约 90%
- **Ollama 本地推理**：完全免费，适合隐私敏感场景

**可借鉴点**：
- 对于个人金融助手，可以用 DeepSeek 作为主模型（极低成本），Ollama 本地模型作为 fallback
- Prompt Caching 在频繁分析场景下能大幅降低成本
- 双模型策略（贵模型推理 + 便宜模型做 compaction）是成本控制的关键

---

## 五、CLI 交互设计

### 5.1 终端 UI 架构

CLI 使用 `pi-tui`（类似 Ink 的 React 式终端渲染库）：

- **组件树**：Intro → ChatLog → ErrorText → WorkingIndicator → Editor → HintBar → DebugPanel
- **增量渲染**：只渲染新增事件，不重绘全部历史
- **30fps 节流**：`setTimeout` + `renderPending` 标志，避免事件风暴
- **实时工具进度**：每个工具调用显示 spinner → 完成后显示摘要和耗时

### 5.2 交互特性

- 斜杠命令（/model, /rules, /clear, /memory, /heartbeat, /history, /help）
- 输入历史（上下箭头导航）
- 双击 ESC 退出/清空
- 消息队列：Agent 运行中可继续输入，消息排队等待注入
- 工具审批流程：敏感工具（write_file, edit_file）需用户确认

**可借鉴点**：CLI-first 是个人工具的最佳选择——启动快、无依赖、可在任何终端运行。消息队列机制允许"边思考边追加信息"，非常实用。

---

## 六、多渠道网关

### 6.1 WhatsApp 集成

通过 `@whiskeysockets/baileys`（非官方 WhatsApp Web 协议）实现：

- 扫码登录（QR Terminal）
- 发送消息给自己 → Dexter 处理 → 回复到同一聊天
- 群聊支持（@提及触发）
- 访问控制：只响应授权号码
- Markdown → WhatsApp 格式转换

### 6.2 定时任务（Cron）

内置 cron 系统支持定期研究任务：

- 用户通过对话创建 cron job（如"每天早上检查 AAPL 是否跌破 200"）
- 执行流程：检查活跃时段 → 运行 Agent → 抑制重复通知 → WhatsApp 推送
- 三种完成模式：`repeat`（持续监控）、`once`（满足条件后自动禁用）、`ask`（满足后询问是否继续）
- 错误指数退避：30s → 1m → 5m → 15m → 60m

**可借鉴点**：定时任务 + 消息推送是金融助手的杀手级功能。个人版本可以：
- 用微信机器人替代 WhatsApp
- 每日收盘后自动检查持仓异动
- 财报发布日自动拉取速评
- 监控目标价位触发提醒

---

## 七、评估框架

`src/evals/` 提供了基于 LangSmith 的评估系统：

- 预置金融问题数据集
- LLM-as-Judge 评分（正确性）
- 支持全量运行或随机采样
- 实时终端 UI 显示进度和准确率

**可借鉴点**：即使是个人工具，简单的评估框架也很有价值。可以维护一组"已知答案"的测试问题，用来验证新模型或新工具的效果。

---

## 八、可借鉴的整体设计模式总结

### 8.1 API 响应缓存

`utils/cache.ts` 实现了本地文件缓存：

- 缓存路径：`.dexter/cache/{endpoint}/{TICKER}_{hash}.json`
- 存储元数据：endpoint, params, cachedAt, url
- 可选 TTL（过期时间）
- 损坏检测 + 自动清理
- 人类可读的文件名（包含 ticker）

**可借鉴点**：金融数据有天然的缓存友好性——历史财报不会变化。缓存能大幅减少 API 调用次数和延迟。

### 8.2 工具并发执行

`tool-executor.ts` 实现了智能的工具并发策略：

- 每个工具标记 `concurrencySafe`（只读工具 = true）
- 连续的并发安全工具批量并行执行（最大并发 10）
- 非并发工具串行执行
- 结果按原始顺序返回（不因并发而乱序）

### 8.3 渐进式错误处理

不是简单的 try/catch，而是分层降级：
- 流式调用失败 → 退回阻塞调用
- 上下文溢出 → 截断 + 重试（最多 2 次）
- 工具执行失败 → 错误信息作为 ToolMessage 返回（让 LLM 决定如何处理）
- LLM API 失败 → 指数退避重试（最多 3 次）
- 可区分可重试/不可重试错误

### 8.4 工具结果预算控制

两个层次的结果大小管理：
1. **单结果上限** (`tool-result-storage.ts`)：超大结果持久化到磁盘，只在上下文中注入摘要 + 文件路径
2. **每轮总预算** (`tool-result-budget.ts`)：限制单轮所有工具结果的总 token 数

---

## 九、个人定制化金融助手的可借鉴路径

### 9.1 最小可行架构（轻依赖版）

从 Dexter 提炼出最精简的个人金融助手架构：

```
┌────────────────────────────────────────────┐
│  入口层：CLI (readline) 或 简单 Web UI      │
│  - 不需要 pi-tui/Ink 等重渲染库            │
│  - readline 足够个人使用                    │
└──────────────────┬─────────────────────────┘
                   │
┌──────────────────▼─────────────────────────┐
│  Agent 循环（核心，~300 行即可实现）          │
│  - 迭代调用 LLM → 执行工具 → 判断完成       │
│  - 最多 N 次迭代                            │
│  - 简单的 token 计数 + 截断                 │
└──────────────────┬─────────────────────────┘
                   │
┌──────────────────▼─────────────────────────┐
│  工具层（按需实现）                          │
│  必备：                                     │
│  ├── A 股数据（AKShare / Tushare，免费）     │
│  ├── 财报数据（东方财富 / 巨潮资讯，免费）    │
│  └── 网络搜索（DuckDuckGo，免费）            │
│  可选：                                     │
│  ├── 港股/美股数据                           │
│  ├── 浏览器（Playwright）                    │
│  └── 研报摘要（自建爬虫）                    │
└──────────────────┬─────────────────────────┘
                   │
┌──────────────────▼─────────────────────────┐
│  LLM 层（低成本选择）                        │
│  方案 A：DeepSeek API（~0.001 元/千 token）  │
│  方案 B：Ollama 本地（完全免费，需要 GPU）    │
│  方案 C：OpenRouter 按量付费                 │
└────────────────────────────────────────────┘
```

### 9.2 必须借鉴的核心设计

| 设计 | 价值 | 实现难度 |
|------|------|---------|
| **SOUL.md 灵魂文件** | 让 AI 输出匹配你的投资理念 | ★☆☆ |
| **SKILL.md 技能文件** | 复杂分析流程标准化、可复用 | ★☆☆ |
| **元工具路由** | 一次调用处理复杂查询 | ★★☆ |
| **Scratchpad 日志** | 完整的研究过程审计 | ★☆☆ |
| **API 缓存** | 减少重复请求，降低成本和延迟 | ★☆☆ |
| **三级上下文管理** | 长对话不丢失关键数据 | ★★★ |
| **记忆系统** | 跨会话记住你的偏好和持仓 | ★★★ |
| **工具并发** | 多数据源并行查询，大幅提速 | ★★☆ |

### 9.3 针对 A 股/港股场景的适配建议

**数据源替换**：

| Dexter 数据源 | 替代方案 | 成本 |
|--------------|---------|------|
| Financial Datasets API | AKShare / Tushare | 免费 |
| Exa Search | DuckDuckGo / Bing | 免费 |
| SEC Filings | 巨潮资讯 / 上交所 / 深交所 | 免费 |
| Playwright | 同上 | 免费 |

**Skill 适配**：

```markdown
---
name: a-stock-valuation
description: A 股公司估值分析，综合 PE Band、PB Band、DCF
---

## Step 1: 获取基本面数据
调用 get_a_stock_data：
- "[代码] 最近 5 年利润表"
- "[代码] 最近资产负债表"
- "[代码] 最近 5 年现金流量表"

## Step 2: 获取估值数据
- 当前 PE/PB/PS
- 历史 PE 分位数（3年/5年/10年）
- 同行业可比公司估值

## Step 3: 分析
- PE Band 分析（当前位置 vs 历史区间）
- 基于 ROE 的合理 PB 估算
- 自由现金流折现（如适用）
- 股息率 vs 十年期国债收益率

## Step 4: 输出
- 估值区间（低估/合理/高估）
- 关键假设和风险
- 与同行对比表格
```

### 9.4 成本估算

以每日使用 10 次深度分析为例：

| 方案 | 月成本 | 说明 |
|------|--------|------|
| DeepSeek V4 Flash | ~5-15 元 | 最佳性价比 |
| DeepSeek V4 Pro | ~30-60 元 | 更好的推理能力 |
| Ollama (Qwen3-32B) | 0 元 | 需要本地 GPU (24GB VRAM) |
| GPT-4o | ~100-200 元 | 最好的效果但成本高 |
| Claude Sonnet | ~60-120 元 | 平衡选择 |

数据源成本：AKShare + 巨潮资讯 = **0 元**。

### 9.5 推荐的渐进实现路径

**Phase 1：最小可用版（1-2 天）**
- 基于 Python/TS 的简单 Agent 循环
- 1-2 个数据工具（AKShare 获取行情 + 财报）
- SOUL.md 定义投资理念
- 1 个 Skill（基础估值分析）
- 使用 DeepSeek API

**Phase 2：实用增强（1 周）**
- 添加更多数据工具（研报、新闻、公告）
- 文件缓存
- Scratchpad 日志
- 更多 Skill（财报速评、行业对比）
- 简单的上下文截断

**Phase 3：高级功能（2-4 周）**
- 记忆系统（Markdown 文件 + 简单搜索，不需要向量数据库）
- 定时任务（每日复盘、价格监控）
- 微信/Telegram 推送
- 多模型切换
- 评估框架

---

## 十、关键源码片段速查

| 功能 | 文件 | 行数 | 说明 |
|------|------|------|------|
| Agent 主循环 | `src/agent/agent.ts` | ~500 行 | `Agent.run()` 异步生成器 |
| System Prompt 构建 | `src/agent/prompts.ts` | ~250 行 | `buildSystemPrompt()` |
| 工具注册 | `src/tools/registry.ts` | ~220 行 | 所有工具的中心注册表 |
| 元工具路由 | `src/tools/finance/get-financials.ts` | ~250 行 | LLM 路由到子工具 |
| 上下文压缩 | `src/agent/compact.ts` | ~230 行 | LLM 驱动的结构化摘要 |
| 轻量裁剪 | `src/agent/microcompact.ts` | ~100 行 | 零成本旧结果清除 |
| Scratchpad | `src/agent/scratchpad.ts` | ~400 行 | JSONL 工具调用追踪 |
| 记忆管理 | `src/memory/index.ts` | ~180 行 | MemoryManager 单例 |
| 混合搜索 | `src/memory/search.ts` | ~120 行 | 向量 + 关键词 + 时间衰减 + MMR |
| Skill 加载 | `src/skills/registry.ts` | ~100 行 | SKILL.md 发现和解析 |
| 多模型抽象 | `src/model/llm.ts` | ~330 行 | 8 个供应商统一接口 |
| API 缓存 | `src/utils/cache.ts` | ~170 行 | 本地文件缓存 |
| 工具并发 | `src/agent/tool-executor.ts` | ~180 行 | 批量并行执行 |
| 供应商注册 | `src/providers.ts` | ~75 行 | 供应商元数据定义 |

---

## 十一、总结

Dexter 的核心价值不在于它用了什么 API 或技术栈，而在于它的 **架构思想**：

1. **Agent 是研究者，不是搜索引擎** — 先规划研究路径，再执行，最后验证
2. **工具是可组合的** — 元工具 + 子工具的路由模式让一次调用就能处理复杂查询
3. **上下文是珍贵的** — 三级管理策略最大化利用有限窗口
4. **工作流是可编码的** — Skill 把专家知识变成可复用的 Markdown 脚本
5. **记忆让 Agent 更个人化** — 跨会话积累对用户的理解
6. **成本是可控的** — 双模型策略 + 缓存 + prompt caching

对于构建个人金融助手，最值得优先实现的是：**SOUL.md + SKILL.md + 元工具路由 + Scratchpad**。这四个组件只需几百行代码，却能提供 80% 的核心价值。记忆系统和上下文压缩可以在后续迭代中逐步添加。
