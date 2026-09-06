# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 语言

代码标识符用英文，面向人的文字（文档、CLI 帮助、报告、模板、错误信息、提交信息）一律中文。领域词汇必须照 `CONTEXT.md` / `src/publish/CONTEXT.md` 里的定义用，那些文件每条词条底下的 `_Avoid_` 是明令禁止的同义词——改名之前先改 CONTEXT。

## 常用命令

```bash
python3.11 -m venv .venv
.venv/bin/pip install -e .
.venv/bin/python -m playwright install chromium   # 只有发稿需要

.venv/bin/python -m pytest tests/test_publish_plan.py -q         # 改完先跑单文件
.venv/bin/python -m pytest tests/test_publish_plan.py::PlanTest::test_x -q  # 单个用例
.venv/bin/python -m pytest -q                                   # 全量；只在交接手 / 用户要求时跑

.venv/bin/python -m book.cli crawl fanqie      # 采番茄公开榜，退出码 2 = 被风控停采
.venv/bin/python -m book.cli serve             # 本机看板 + 发稿台，只绑 127.0.0.1:8765

.venv/bin/python -m publish init novel/<稿本>       # 从大纲灌书资料.yml
.venv/bin/python -m publish discover novel/<稿本>   # 认领后读设置页标签补空键
.venv/bin/python -m publish run --dry-run novel/<稿本>
.venv/bin/python -m publish run novel/<稿本>        # 加 --create 才允许建书
```

没有 linter / formatter 配置，不要引入。`WINDVANE_DATA` 可改 sqlite 落盘目录。

## 两个上下文，一个仓库

`CONTEXT-MAP.md` 定的边界，写代码时不要跨：

- **风向标**（`src/book/`）：采公开榜，算题材进 / 掉。不登录、不碰正文、不读 `novel/`。
- **发稿**（`src/publish/`）：把 `novel/` 下稿本发到番茄作家后台。必须登录、不写快照库。

两边的「作品」不是同一个概念（榜单条目 vs 作家后台的平台作品），永不合并身份；限速与失败策略各自成立。唯一的物理耦合是 `book.cli serve` 起的那个 FastAPI 应用同时挂了两边的路由（`publish.web.attach_publish_desk`），且 `src/publish/web.py` 复用了 `src/book/web/templates/base.html`。

## 风向标数据流

`cli crawl` → `platforms/fanqie.FanqieCrawler` → `fetch.PoliteClient`（限速、403/风控抛 `PlatformHalted`）→ 榜单页 HTML 取 `__INITIAL_STATE__` 和 woff 字体 → `fonts.decode_text` 用 `data/sourcehan_gb_bitmaps.npz` 位图比对还原被字体替换的书名 / 作者 → `store.Store.replace_snapshot` 整份覆盖当日快照 → `heat` 算占位 / 新进 / 掉出 → `board.build_day_board` → `web/app.py` 渲染。

- 快照契约见 `docs/requirements.md`：一日一榜一份、最多 100 条、只整份覆盖。失败的榜写 `SNAPSHOT_MISSING` 并在看板标红，绝不用昨日数据顶上、不显示成零。
- `config/fanqie_lists.json` 是榜单目录，采集时会按页面实际分类刷新回写。
- `sync.py` 让看板「同步榜单」按钮在后台线程跑同一个 `crawl`；`capture_stdout` 按线程隔离日志，同一时刻只允许一个任务。

## 发稿三段式

严格分层，改动请落在对应层：

1. **`manuscript.py`** — 纯本地。扫 `第{序号}章-{标题}.md`、markdown 转纯文本、读写稿本目录里的 `书资料.yml`（绑定的作品 ID、章缓存、发稿时刻档位、可见性、单次上限）。发稿时刻的排档算法（`next_slot_after` / `take_next_publish_slot`）也在这里。
2. **`plan.py`** — 纯函数决策，无 IO、无 Playwright。输入 `Manuscript` + `CommandMode` + `RemoteObservation`（远端观察结果），输出 `PublishPlan`。认领 / 创建、设置写哪些键、每章该新建草稿还是改可见性还是只报告，全在 `decide_claim` / `decide_settings` / `decide_chapters` 里。**新规则优先加在这里并配单测**。
3. **`writer.py`** — 只做 Playwright 操作：登录等人、观察远端凑出 `RemoteObservation`、执行 `PublishPlan`、写回章缓存。

`desk.py` + `web.py` 是发稿台：把上面的流程包成后台单任务（`start_publish_job` / `start_bind_job`），并做绑定占用校验、设置表单读写。

### 不可违反的发稿规则（ADR）

`docs/adr/` 是决策记录，改行为前先读；`0006` 已被 `0011` 取代。

- 默认按**作品名称认领**已有平台作品；创建只在显式 `--create` 且搜索 0 命中时发生（ADR 0010）。
- 绑定只存作品 ID，改绑即丢章缓存。发稿不回写作品设置（ADR 0011）。
- 一次发稿**只读一次**后台目录，计划和执行共用同一份远端观察。「章节管理」和「草稿箱」都是必要来源，任一个打不开就停机，不得静默当成「没有草稿」。章写成功后先落盘再回目录；新建章节必须确认真的进了新章页（新章页地址不带章 ID），否则会把这一章写进上一章（ADR 0012）。
- 目录是**每页 15 行、按章号倒序的分页表格**，不是虚拟滚动。要**逐页遍历**读全整本；页数读不出、某页翻不过去或行渲染不出来，一律停机并指名第几页，不得当成「那几页没有章」（ADR 0013）。
- 整本读全之后**以后台为准**：后台有而章缓存缺 ID 的回填；本地有而后台没有的**末尾缺口**补建（不再受"只补水位之后"限制）；**中间缺口**只报告不补，因为后台只能末尾追加，补进去顺序会乱。没读全时退回旧兜底「水位够不到章缓存里最大已建章就停机」（ADR 0013）。
- **只支持单卷作品**：卷选择器里多于一个卷直接停机，不得只读当前那一卷。
- 收目录行要按**视口水平位置**过滤：两个标签的面板长期共存于 DOM，切走的那个停在 `x` 为负处但仍有宽高，只判宽高会把它的行串进来。
- 已发布章节不改正文，不一致只进报告（ADR 0008）。
- 走真实表单，不打私有接口（ADR 0007）。可见浏览器、登录和验证码等人工处理（ADR 0009）。会话持久化在 `.local/fanqie-writer/`。
- 「空键」（书资料里有键但值为空）不得提交成清空远端已有值。

## 测试约定

`unittest` 写法、pytest 跑。测试必须离线且不开浏览器：

- `plan.py` / `manuscript.py` / `heat.py` / `store.py` 用临时目录和夹具直接测。
- `writer.py` 用 `tests/test_publish_writer.py` 里的 `FakeLocator` / `FakePage` 手写替身模拟页面，不要引入真 Playwright。
- 采集用 `tests/test_book_fanqie.py` 的固定 HTML / JSON 夹具。

`novel/`、`outline/`、`data/*.sqlite` 是本机真实内容，测试不得读写它们。

改完先跑被改模块对应的单文件（`tests/test_publish_writer.py` 对 `writer.py`，以此类推）。全量 `.venv/bin/python -m pytest -q` 只在准备交接手、或用户明确要求时跑一次，禁止每改几行就全量。

## Agent 用量

控制上下文体积和 token。四条都要遵守。

### 模型与 effort

- 改字符串、补测试、跑 pytest、写文档：保持默认 effort，不要 `/effort max`，不要主动切到 Opus。
- 只有定位根因、判断页面或发稿行为、或用户明确要求时，才用 Opus 或 `/effort max`。
- 子代理（Explore / 复核）指定 Sonnet，或宿主里更便宜的模型；不要继承主会话的 Opus + max。

### 子代理

- 同一份改动最多一个会扫仓库的 Explore。
- 子代理 prompt 必须写明只读哪些路径（例如 `src/publish/` 与对应 `tests/test_publish_*.py`），禁止把整个 publish 再爬一遍。
- 主会话已经能看的 diff，不要交给子代理重读。

### 读写文件

- 读代码用 Read / Grep；本仓库有 CodeGraph 时先用它。
- 改代码用 Edit / Write。
- 禁止用 Bash 当编辑器：`python3 <<'PY'`、`cat >file <<EOF`、`sed -i`、`python3 -c` 读写改仓库文件。
- Bash 只用于 pytest、git、`python -m publish`、`python -m book`。
- 搜索不要把 `grep` / `rg` 放进 Bash。
