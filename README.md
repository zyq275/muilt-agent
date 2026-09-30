# 探渊 · 多智能体数据分析系统

把一句中文业务问题，变成一份**可追溯来源、经过自检**的分析报告（Markdown + 可选 PDF）。

系统由一个主智能体编排三个专职子智能体（网络检索 / 企业数据库 / 内部知识库），
产出的报告要过一轮「硬校验 + 语义审查」的反思循环，不达标就带着纠错指令重跑，
最多 3 轮。全过程通过 WebSocket 实时推给前端，形成一棵可展开的执行树。

> 需求与技术细节以 `docs/` 下的三份文档为准（PRD / TDD / 开发步骤）。
> 本文只讲**怎么理解、怎么配、怎么跑**，以及实际实现与文档不一致的地方。

---

## 目录

- [1. 整体架构](#1-整体架构)
- [2. 一次任务发生了什么](#2-一次任务发生了什么)
- [3. 目录职责](#3-目录职责)
- [4. 核心机制](#4-核心机制)
- [5. 环境要求](#5-环境要求)
- [6. 安装](#6-安装)
- [7. 配置 .env](#7-配置-env)
- [8. 启动](#8-启动)
- [9. 接口清单](#9-接口清单)
- [10. 排查手册](#10-排查手册)
- [11. 已知限制](#11-已知限制)

---

## 1. 整体架构

```
┌──────────────────────────────────────────────────────────────┐
│  前端  Vue 3 + Vite + Element Plus        :5173              │
│  ┌────────┬──────────────────────────┬──────────────┐        │
│  │ 会话栏 │ 反思校验 / 执行树 / 结果 │  文件面板    │        │
│  └────────┴──────────────────────────┴──────────────┘        │
│              REST /api/*   +   WebSocket /ws/{tid}           │
└───────────────────────────┬──────────────────────────────────┘
                            │ vite 开发代理（生产未接入，见 §11）
┌───────────────────────────▼──────────────────────────────────┐
│  接入层  api/                            FastAPI :8000       │
│    server.py   路由：task / upload / cancel / files / download│
│    monitor.py  事件总线：按 thread_id 路由 WS 消息            │
│    context.py  会话隔离：ContextVar 保存 thread_id + 工作目录 │
└───────────────────────────┬──────────────────────────────────┘
                            │
┌───────────────────────────▼──────────────────────────────────┐
│  编排层  agent/                                              │
│    main_agent.py  主智能体组装 + 反思循环（MAX_ROUNDS=3）     │
│    llm.py         DeepSeek（OpenAI 兼容协议）                 │
│    load_prompt.py prompts.yaml 装载 + 安全边界注入            │
│    sub_agents/    三个子智能体                                │
└───────────────────────────┬──────────────────────────────────┘
                            │
┌───────────────────────────▼──────────────────────────────────┐
│  工具层  tools/                                              │
│    internet_search_tool  Tavily 联网检索                      │
│    mysql_tools           只读 SQL（关键字拦截 + 只读账号）    │
│    knowledge_base_tool   Chroma 向量检索（BGE）               │
│    markdown_tools / pdf_tools / upload_file_read_tool         │
│    reflect_check         H1–H4 硬校验                         │
└──────────────────────────────────────────────────────────────┘
                            │
        ┌───────────────────┼───────────────────┐
        ▼                   ▼                   ▼
   MySQL pharma_db     Chroma 向量库       Tavily / DeepSeek
   （只读账号）        vector_store/       （外部 API）
```

---

## 2. 一次任务发生了什么

```
用户在前端输入任务
   │
   ├─ 前端**先生成** thread_id（形如 task-{base36 时间戳}-{6 位随机}）
   │  并先连上 /ws/{thread_id}，再发 POST /api/task
   │  ── 顺序反过来会丢掉开头几十毫秒内发出的 session_created 事件
   │
   ▼
POST /api/task  {query, thread_id}      →  立即返回 {status:"started"}
   │  （后台 asyncio.create_task 执行，请求不被长任务阻塞）
   ▼
run_deep_agent(query, thread_id)                          agent/main_agent.py
   │
   ├─ 1. set_session()  建 output/session_{tid}/ 与 upload/session_{tid}/
   ├─ 2. _prepare_workspace()  把 upload/ 里的文件复制进工作目录
   │      → 推 session_created 事件
   │
   ├─ 3. 反思循环（最多 3 轮）
   │    ┌─────────────────────────────────────────────────┐
   │    │ 3.1 主智能体规划 + 执行（agent.astream）        │
   │    │     → 推 assistant_call / tool_start 事件       │
   │    │ 3.2 取报告正文（优先读工作目录里最新的 .md）    │
   │    │ 3.3 硬校验 H1–H4（程序级、确定性）              │
   │    │ 3.4 硬校验全过 → 软校验（LLM 当审稿人审语义）   │
   │    │ 3.5 全过 → 推 task_result，返回                 │
   │    │     未过 → 拼纠错指令注入下一轮，回到 3.1       │
   │    └─────────────────────────────────────────────────┘
   │
   └─ 4. 达 3 轮上限仍未过：返回报告 + 逐条列出未达标原因

前端从 WebSocket 实时收到全部事件 → 渲染执行树 / 反思卡片 / 结果页
```

---

## 3. 目录职责

| 路径 | 职责 | 是否提交 |
| --- | --- | --- |
| `main.py` | 程序入口：加载 `.env` → 启动 uvicorn | ✅ |
| `api/` | 接入层：REST 路由、WS 端点、事件总线、会话上下文 | ✅ |
| `agent/` | 编排层：主智能体、反思循环、LLM 封装、提示词装载 | ✅ |
| `agent/sub_agents/` | 三个子智能体的定义（名称 / 描述 / 工具） | ✅ |
| `tools/` | 工具层：检索、SQL、报告生成、PDF、硬校验 | ✅ |
| `utils/` | 日志、路径安全、格式转换 | ✅ |
| `prompt/prompts.yaml` | **全部提示词**。须与 TDD §6 逐字一致 | ✅ |
| `sql/init.sql` | 建库建表 + 示例数据（`drugs` / `inventory` / `sales_records`） | ✅ |
| `sql/create_readonly_user.sql` | 建只读账号（安全主防线） | ✅ |
| `scripts/build_knowledge_base.py` | 离线构建向量索引 | ✅ |
| `frontend/` | Vue 3 前端 | ✅ |
| `eval/` | RAG 检索评估 + RAGAS 评分（**独立 venv**，见 §6.5） | ✅ 脚本 |
| `docs/` | PRD / TDD / 开发步骤 | ✅ |
| `.env` | 真实密钥 | ❌ **绝不提交** |
| `knowledge_base/` | 企业内部文档（向量库的语料） | ❌ |
| `vector_store/` | Chroma 索引产物 | ❌ |
| `output/` `upload/` | 运行时产物、上传文件 | ❌ |
| `model/` | BGE 模型权重（约 1.6G） | ❌ |

---

## 4. 核心机制

### 4.1 三路检索

| 子智能体 | 工具 | 数据来源 | 配色 |
| --- | --- | --- | --- |
| 网络检索助手 | `internet_search` | Tavily 联网 | `#409EFF` |
| 数据库查询助手 | `list_sql_tables` / `get_table_data` / `execute_sql_query` | MySQL `pharma_db` | `#67C23A` |
| 内部知识库助手 | `search_knowledge_base` | Chroma 向量库 | `#9254DE` |

主智能体自己另持有三个文件工具：`generate_markdown`、`convert_md_to_pdf`、`read_file_content`。

### 4.2 反思循环（TDD §5.2）

**硬校验 H1–H4**（`tools/reflect_check.py`，程序级、确定性）：

| 编号 | 校验项 | 判定 |
| --- | --- | --- |
| H1 | 篇幅 | 去掉 Markdown 符号与空白后的有效字符数 < **1000** |
| H2 | 占位符 | 命中「待补充 / TODO / 待办 / 占位 / （略）/ 此处省略 / XXX」等关键词 |
| H3 | 格式 | 用户要求 PDF（query 里含 "pdf"）但工作目录没有 `.pdf` |
| H4 | 数据源覆盖 | 规划阶段声明的数据源里有未被实际调用的 |

**软校验**：硬校验全过之后，把报告交给 LLM 以「技术审稿人」视角审三个问题
（是否完整回答 / 数据是否充分 / 逻辑是否自洽），只回「通过」或「不通过 + 原因」。
软校验异常或输出格式不符时**保守判通过**，避免陷入死循环。

**不通过**就把 H1–H4 的失败项 + 软校验意见拼成纠错指令，注入下一轮 query 重跑。
3 轮仍不通过，返回报告并在末尾附「未达标原因」清单 —— 不假装成功。

### 4.3 事件流

WebSocket 推送统一结构（TDD §8.2）：

```json
{ "type": "monitor_event", "event": "tool_start",
  "message": "调用工具：generate_markdown", "data": {}, "timestamp": "ISO8601" }
```

| 事件 | 触发时机 |
| --- | --- |
| `session_created` | 会话工作目录创建后 |
| `assistant_call` | 主智能体调度子智能体（工具名为 `task`） |
| `tool_start` | 主智能体调用普通工具 |
| `task_result` | 返回最终结果，`data.result` 是报告正文 |
| `error` | 执行失败 |
| `cancelled` | 用户主动终止（**TDD 五种之外的扩展**，见 §11） |

> **事件只在页面打开时被收到，且后端不存历史。** 前端把事件存进浏览器
> localStorage（`eventStore.js`，每会话最多 300 条 / 最多 20 个会话），
> 所以刷新能恢复；换浏览器、清缓存、或页面没打开期间发生的事件都无法补回。

### 4.4 会话隔离

同一套机制在两个层面生效：

- **进程内**：`api/context.py` 用 `ContextVar` 保存 `thread_id` 与工作目录。
  每个 `asyncio.create_task` 会复制一份 context，因此并发任务天然互不串号
  （`threading.local` 在 asyncio 下会失效，全局变量会串号）。
- **磁盘上**：每个会话一个目录 —— 产物落 `output/session_{tid}/`，
  上传落 `upload/session_{tid}/`。

`thread_id` 必须匹配 `^[A-Za-z0-9_-]{1,64}$`，非法值直接 400 拒绝
（不做静默改写 —— 那会让「上传到某个目录」和「任务写到另一个目录」悄然错位）。

### 4.5 安全设计

四道防线，缺一不可：

1. **数据库只读账号**（主防线，不可绕过）
   `tanyuan_ro@localhost` 对 `pharma_db` 只有 `SELECT`。即使一条 `DROP TABLE`
   混过了应用层，数据库也会直接报错拒绝。建号见 `sql/create_readonly_user.sql`。
2. **SQL 关键字拦截**（兜底，可被绕过，但能拦下绝大多数并在日志留痕）
   `tools/mysql_tools.py` 的 `_normalize_sql` + `_reject_reason`，三重判定：
   ① 只允许单条语句（`;` 之后还有内容就拒）；② 首关键字必须落在只读白名单
   `select / show / desc / describe / explain / with` 内；③ 危险关键字黑名单兜底
   （`insert / update / delete / drop / alter / …`，防 `WITH … DELETE` 这类绕过）。
   归一化时会先摘掉注释与字符串字面量，避免 `SELECT 'DROP TABLE'` 被误伤。
3. **路径越界校验**
   `/api/files`、`/api/download` 收到的路径经 `validate_within_root()` 校验
   必须落在 `output/` 内，否则 403。`sanitize_path()` 处理 `..`、盘符注入、
   UNC 路径、Windows 保留设备名（`CON` / `NUL` / `COM1`…）。
4. **上传文件名清洗**
   先取 `basename` 去掉目录，再清洗，最后再校验一次落点在上传目录内。

前端的「越权访问已拦截」弹窗**只是视觉提示，不是安全边界** —— 真正的隔离在后端。

---

## 5. 环境要求

| 项 | 要求 | 本项目实测 |
| --- | --- | --- |
| 操作系统 | **Windows**（PDF 转换依赖本机 Microsoft Word 的 COM 接口） | Windows 11 |
| Python | ≥ 3.12 | 3.12.8 |
| Node.js | ≥ 18（Vite 需要） | v24.15.0 / npm 11.12.1 |
| MySQL | 8.x 实例 | localhost:3306 |
| Microsoft Word | **必装** —— 没有它无法生成 PDF | ✅ |

外部服务（都需要 API Key）：

- **DeepSeek**（生成模型，OpenAI 兼容协议）
- **Tavily**（联网检索）
- **BGE Embedding 模型**（本地跑，不需要密钥）
- **MySQL**（`pharma_db`，账号密码）

> **磁盘注意**：本机 C 盘只剩约 4G，装不下这套依赖（约 3.5G）。
> Python 虚拟环境放在 **`D:\venvs\tanyuan`**，`.vscode/settings.json` 的
> `python.defaultInterpreterPath` 已指向它。换机器/换盘符时改那一行即可。

---

## 6. 安装

### 6.1 Python 主环境

```bash
# 建议 venv 建在 D 盘（C 盘空间不足）
python -m venv D:/venvs/tanyuan

# 激活（Git Bash）
source /d/venvs/tanyuan/Scripts/activate
# 激活（PowerShell）
D:\venvs\tanyuan\Scripts\Activate.ps1

# 装依赖 + 以可编辑模式安装本项目（提供 tanyuan 命令）
pip install -e .
```

`pip install -e .` 是必要的：`pyproject.toml` 的 `[project.scripts]` 注册了
`tanyuan = "main:main"`，装完之后可以直接敲 `tanyuan` 启动。

> `pyproject.toml` 里显式写了 `[tool.setuptools.packages.find] include`，
> 不能去掉。本项目是「应用」而非「库」，顶层是一堆平铺目录，setuptools 的
> 自动发现会把 `output/` `upload/` `eval/` 也当成包并直接报错。

### 6.2 前端依赖

```bash
cd frontend
npm install          # 必须用 npm install，不要删 package-lock.json
```

### 6.3 数据库

```bash
# 1) 建库建表 + 灌示例数据
mysql -u root -p < sql/init.sql

# 2) 建只读账号（把 CHANGE_ME_STRONG_PASSWORD 换成强密码）
#    然后把这个密码同步填进 .env 的 MYSQL_PASSWORD
mysql -u root -p < sql/create_readonly_user.sql
```

**验证只读账号确实只读**（第三步必须报 `ERROR 1142`）：

```sql
mysql -u tanyuan_ro -p pharma_db
SELECT COUNT(*) FROM drugs;    -- ✅ 应成功
SHOW TABLES;                   -- ✅ 应成功
DROP TABLE drugs;              -- ❌ 必须被拒绝
```

### 6.4 知识库向量索引

把企业文档（`.md` / `.txt` / `.pdf` / `.docx`）放进 `knowledge_base/`，然后：

```bash
python scripts/build_knowledge_base.py
```

流程：加载 → 分块（chunk 500 / overlap 50）→ BGE 向量化 → 落盘 `vector_store/`。

脚本会**先清空旧索引再重建**，并在结束时校验「落盘向量数 == 分块数」，
不等就直接抛错。这是必要的：`Chroma.from_documents` 是**追加**语义，
不清空的话每次重建都在累积，删掉文档的旧分块会一直留在索引里被检索到。

> 重复执行是安全的，但**改了 `knowledge_base/` 里的文档就必须重跑一次**，否则检索到的还是旧内容。

### 6.5 RAG 评估环境（可选）

`eval/` 用的是一套**独立的 Python 环境**，不能和主环境合并：

```bash
python -m venv eval/.venv
eval/.venv/Scripts/python -m pip install -r eval/requirements.txt
eval/.venv/Scripts/python eval/score_ragas.py
```

**为什么必须隔离**：`ragas` 依赖 `langchain_community.chat_models.vertexai`，
该模块在 `langchain-community` 0.4.x 被移除；降级到 0.3.x 又会把
`langchain-core` 拖回 0.3，而主环境的 `langgraph` / `deepagents` 要求
`langchain-core ≥ 1.4`。两套栈无法共存。`eval/requirements.txt` 里的版本是锁死的，
不要随手升级。

`eval/score_ragas.py` 会自动 re-exec 到 `eval/.venv`，所以直接用主环境的
python 跑它也能work（它会自己切过去）。

---

## 7. 配置 .env

```bash
cp .env.example .env      # 然后填入真实值
```

`.env` 已在 `.gitignore` 中（`.env.*` 也一并忽略，但放行 `.env.example`），
**绝不可提交**。代码里一律走 `os.getenv()`，没有硬编码密钥。

| 变量 | 说明 | 默认值 |
| --- | --- | --- |
| `DEEPSEEK_API_KEY` | 大模型密钥 | — （必填） |
| `DEEPSEEK_BASE_URL` | API 端点 | `https://api.deepseek.com` |
| `DEEPSEEK_MODEL` | 模型名 | `deepseek-chat` |
| `TAVILY_API_KEY` | 联网检索密钥 | — （必填） |
| `EMBEDDING_MODEL` | Embedding 模型名 | `BAAI/bge-base-zh-v1.5` |
| `EMBEDDING_MODEL_PATH` | 本地模型路径；**填了就用它，忽略上面的模型名** | 空（走在线下载） |
| `KNOWLEDGE_BASE_DIR` | 知识库语料目录 | `./knowledge_base` |
| `VECTOR_STORE_DIR` | 向量库落盘目录 | `./vector_store` |
| `MYSQL_HOST` / `MYSQL_PORT` | 数据库地址 | `localhost` / `3306` |
| `MYSQL_USER` | **必须是只读账号**，不要填 root | `tanyuan_ro` |
| `MYSQL_PASSWORD` | 只读账号密码 | 必填 |
| `MYSQL_DATABASE` | 库名 | `pharma_db` |
| `MYSQL_CHARSET` | 字符集 | `utf8mb4` |
| `RAGAS_JUDGE_MODEL` | 仅评估用：RAGAS 裁判模型 | 空（回退 `DEEPSEEK_MODEL`） |

**两处实际配置与 `.env.example` 不一致，照抄模板会出问题：**

1. **`EMBEDDING_MODEL`**：模板写的是 `BAAI/bge-small-zh-v1.5`，
   但本机实际下载并建索引用的是 **`BAAI/bge-base-zh-v1.5`**。
   `model/` 目录下只有 base 版，照模板填 small 会让脚本去联网重新下一个模型。
2. **`EMBEDDING_MODEL_PATH`**：本机填的是指向 HuggingFace 快照目录的**绝对路径**
   （`model\models--BAAI--bge-base-zh-v1.5\snapshots\<hash>`）。
   换机器时这个 hash 目录名会变，需要重新确认，或者干脆留空让它按模型名联网下载。

另外两点：

- 若本机已经下载过模型，`EMBEDDING_MODEL_PATH` 指向 HF 缓存目录能省掉一次下载；
  但**换了机器后该路径里的 hash 目录名会变**，要么重新确认，要么留空让它在
  线拉取（首次会下载约 400MB）。
- `RAGAS_JUDGE_MODEL` 建议配成**与生成模型不同**的模型：同一个模型既生成答案
  又给自己打分，会系统性偏乐观。

---

## 8. 启动

分两个终端。**先起后端，再起前端。**

### 8.1 后端（FastAPI，:8000）

```bash
# 项目根目录
D:/venvs/tanyuan/Scripts/python.exe main.py
```

或等价的：

```bash
uvicorn api.server:app --host 127.0.0.1 --port 8000
```

启动成功会看到：

```
探渊 启动
探渊服务启动中：http://127.0.0.1:8000
INFO:     Uvicorn running on http://127.0.0.1:8000
```

> **监听地址是 `127.0.0.1`，不是 `0.0.0.0`。** TDD §16.2 写的是 `0.0.0.0`，
> 实际实现收紧到了回环地址（少一个暴露面）。需要局域网访问时改
> `api/server.py` 的 `start_server(host=...)`。

**改了后端代码必须重启进程**（`uvicorn.run(app, ...)` 没开 reload）——
否则新加的路由不会生效，请求会 404。

健康检查：

```bash
curl http://127.0.0.1:8000/          # {"status":"ok","service":"探渊"}
```

### 8.2 前端（Vite，:5173）

```bash
cd frontend
npm run dev
```

打开 <http://localhost:5173>。

开发期 `vite.config.js` 把 `/api` 和 `/ws` 代理到 `localhost:8000`，
所以前端全部用相对路径、不需要处理跨域。后端的 CORS 白名单也只放行了
`localhost:5173` / `127.0.0.1:5173`。

> `/ws` 的代理必须带 `ws: true`，否则 Vite 会按普通 HTTP 转发，
> WebSocket 握手阶段就失败。

生产构建：

```bash
npm run build      # 产物在 frontend/dist/
npm run preview    # 本地预览构建产物
```

⚠️ **构建产物目前没有被后端托管**，`api/server.py` 里没有 `StaticFiles` 挂载。
`frontend/src/api.js` 头部注释里「生产期 npm run build 后由 FastAPI 静态托管」
这句**尚未实现**。要真正部署，需要自己加挂载，或者用 nginx 把 `/api`、`/ws`
转发到 8000、静态文件指向 `frontend/dist`。

### 8.3 验证清单

依次做完，全通过说明链路没问题：

1. `curl http://127.0.0.1:8000/` → `{"status":"ok",...}`
2. 打开 <http://localhost:5173> → 页面正常，顶栏状态显示「待提交」
3. 输入框填 `列出所有抗生素类药品` → 提交 → 执行树开始滚动出事件
4. 侧栏统计卡「运行中」+1，任务完成后变「已完成」
5. 结果区 Markdown 页签出现报告，反思校验卡片 H1–H4 亮起
6. 勾上「强制生成 PDF 报告」再提交一次 → 结果区多出「PDF 预览」页签
7. 右侧「会话成果」页签能浏览 `output/session_{tid}/`，双击 `.md` 可在面板内预览
8. 上传一个 Excel → 「预览」按钮能弹出表格

---

## 9. 接口清单

| 方法 | 路径 | 请求 | 响应 |
| --- | --- | --- | --- |
| GET | `/` | — | `{"status":"ok","service":"探渊"}` |
| POST | `/api/task` | `{"query": str, "thread_id": str\|null}` | `{"status":"started","thread_id": str}` |
| POST | `/api/upload` | multipart：`files`（可重复）+ `thread_id` | `{"status":"uploaded","files":[str]}` |
| POST | `/api/cancel` | `{"thread_id": str}` | `{"status":"cancelled"\|"not_running","thread_id": str}` |
| GET | `/api/files` | `?path=<output 内绝对路径>`（空=根） | `{"files":[{name,type,path,size,mtime}]}`，mtime 倒序 |
| GET | `/api/download` | `?path=<output 内绝对路径>` | 文件流，越界 403 |
| WS | `/ws/{thread_id}` | 发任意文本 → 回 `{"type":"pong"}` | 服务端每 30s 发 `{"type":"ping"}`；编排事件按 §4.3 结构 |

`/api/files` 与 `/api/download` 的 `path` 是**绝对路径**，且必须落在 `output/` 内，
否则 403。列表接口返回的每一项自带 `path`，直接回传即可，不要自己拼——
拼出来的既可能越界被拒，也可能因斜杠/盘符差异对不上。

`/api/cancel` 已实现并测通，但**前端按钮已移除**（暂时不用该功能）。

---

## 10. 排查手册

| 现象 | 原因 | 处理 |
| --- | --- | --- |
| 提交任务后完全没有事件 | 后端没起来 / 连的是旧进程 | 看后端终端有没有请求日志；重启后端 |
| 页面一打开就显示「执行中」 | 已修复（连接状态不再等同于任务状态） | 若复现请提 issue |
| 新加的路由返回 404 | `uvicorn.run` 没开 reload，进程还是旧的 | 重启后端 |
| 每次刷新页面自动下载 PDF | 已修复（blob URL + `lazy`） | — |
| 刷新后执行日志没了 | 事件存在 localStorage；换浏览器或清缓存会丢 | 属预期行为 |
| 无联网时前端一直重连 | 已修复（仅 running/connecting 才重连） | — |
| `无法解析导入 ragas` | 编辑器用主环境解析 `eval/` 下的脚本 | 正常；`.vscode/settings.json` 已加 `extraPaths`，脚本用 `eval/.venv` 跑 |
| PDF 转换失败 | 本机没装 Microsoft Word / COM 不可用 | 装 Word；`pdf_tools` 走的是 Word COM |
| `search_knowledge_base` 返回空 | 没建索引，或索引内容过期 | 重跑 `scripts/build_knowledge_base.py` |
| `RuntimeError: Form data requires "python-multipart"` | 少装依赖 | `pip install python-multipart`（已在 `pyproject.toml`） |
| 模型联网下载卡住 | 网络问题 | 手动下好模型，把 `EMBEDDING_MODEL_PATH` 指过去 |
| 前端 `npm run build` 报 chunk > 500kB | Element Plus 全量引入，属预期 | 可忽略；要优化就换按需引入 |

---

## 11. 已知限制

这些不是 bug，是当前实现的真实边界，界面上也都有对应标注：

**事件流**

- **子智能体内部的工具调用拿不到。** `main_agent.py` 的 `astream` 用
  `stream_mode="updates"` 且未开 `subgraphs=True`，子图内部的消息进不了 chunk。
  所以执行树只有两级（主智能体 → 子智能体调度记录），没有第三级工具节点。
- **工具调用没有耗时字段，也没有结束事件。** 界面上显示的时长是
  「本事件到下一条事件的间隔」，不等于工具本身的执行耗时。
- **工具调用没有返回内容。** `tool_start` 的 `data` 只有 `{tool_name, args}`。
- **「网络检索最多 3 次」不由代码强制。** 那条规则写在网络检索助手的
  system prompt 里，由模型自觉遵守，**既不产生事件也不可校验**。
  前端只能显示「子智能体被调度了几次」，看不到它内部实际检索了几次。
- **没有进度百分比。** 后端不推进度事件，前端用的是不确定态动画。
- `cancelled` 事件是 **TDD §8.2 五种事件之外的扩展**。它纯增量 ——
  不认识它的消费者按未知事件忽略即可。取消是「尽快停」而非「立刻断」：
  已经发出的同步调用（线程池里的 LLM / 数据库请求）不会被强行中断，
  已落盘的中间产物也不会回滚。

**数据与状态**

- **后端没有「列出所有会话」接口。** 侧栏的会话列表是**本机浏览器
  localStorage 的索引**，只显示这台机器提交过的任务。「删除」只删本地记录，
  **不会删除服务端产物**（没有删除接口）。
- **「重跑」是新会话，不是续跑。** 每次 `POST /api/task` 都是一次独立执行，
  重跑会生成新的 `thread_id`。
- **同一会话多次提交，产物累积在同一目录。** 结果区显示的可能不止本次任务的
  产物，界面上有提示。
- **前端显示的 H1–H4 是事后复算的。** 后端每轮都跑 `hard_check()`，但结果
  **只写 logger、不发事件**，所以前端是用同一套算法（`frontend/src/reflect.js`）
  从最终报告重新算的，两者口径可能有出入。它也不参与后端的纠错循环。
- **反思轮数拿不到准确值。** 后端不推轮次边界，前端给的是「报告生成次数」
  作为粗略代理，标注为「约」。

**部署**

- `/api/cancel` 路由已实现并测通，但前端按钮已移除；且**需要重启后端**才会加载。
- 后端绑定 `127.0.0.1`（有意为之，非 TDD 写的 `0.0.0.0`）。
- **构建产物没有被后端托管**，见 §8.2。
- 前端构建产物约 1.37 MB（gzip 448 kB），因为 Element Plus 是全量引入。
- `prompt/prompts.yaml` 必须与 TDD §6 逐字一致。代码追加的「数据源声明规则」
  和「安全边界」都在运行时拼接，**不改动该文件**。

---

## 附：三份设计文档

| 文档 | 内容 |
| --- | --- |
| `docs/探渊-需求规格说明书（PRD）.md` | 要做什么、验收标准 |
| `docs/探渊-技术设计文档（TDD）.md` | 架构、模块、接口、提示词、安全设计 |
| `docs/探渊-开发步骤文档.md` | 阶段 0–10 的可执行步骤与验收清单 |
