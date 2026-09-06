# 任务进度

三个后台任务——发稿台的**发稿**、**绑定**，看板的**同步榜单**——现在点一下就跳到一张几乎不动的页面。本文是给这三个任务加进度显示的开发文档。

## 为什么

根因不只是没有进度条，是**运行期间根本没有输出**。

`src/publish/desk.py:299-306` 和 `src/book/sync.py:123-125` 都是这个形状：

```python
with capture_stdout() as buffer:
    report = runner(...)                                    # 十几分钟卡在这里
job.lines = [l for l in buffer.getvalue().splitlines() if l]  # 跑完才赋值
```

跑的过程里 `job.lines` 只有开头那一句固定提示。而 `job.html:6` / `sync.html:6` 的 `<meta http-equiv="refresh" content="2">` 每 2 秒重载的，正是这张永远不变的页。发一本几十章的书要十几分钟，中间只能盯着「正在打开作家后台」。

再就是没有阶段、没有分母、没有已用时间：`PublishJob` / `CrawlJob` 没有 progress 字段，`run_publish()` / `FanqieCrawler.crawl()` 没有进度回调，`execute_chapter_actions` 遍历章节连 `enumerate` 都没有。

## 做成什么样

每个任务页显示**阶段 + 该阶段的分母 + 进度条 + 已用时间**，日志实时滚出来，每秒轮询刷新而不是整页重载。

```
┌─ 发稿：《长夜行》 ────────────────┐
│ 状态 运行中                      │
│                                  │
│ ① 登录后台        ✓              │
│ ② 认领平台作品    ✓ 7276...1234  │
│ ③ 读后台目录      ✓ 6/6 页 · 87章│
│ ④ 写入章节        ▶              │
│   ███████░░░░░░░░░░░  3/8 章     │
│   正在写 第 91 章《雪落时》       │
│                                  │
│ 已用 04:12                       │
└──────────────────────────────────┘
```

**不做跨阶段的总百分比。** 登录要等人扫码，写章要等后台云端存稿，两者时长差几个数量级，合成一个总数是撒谎。进度条只画在**当前阶段之内**；算不出分母的阶段（登录、认领）画不确定态的呼吸条。

---

## 用词：新加「任务进度」，不要碰「发稿进度」

`src/publish/CONTEXT.md` 已有 **发稿进度**：

> 因写操作上限或风控停下后，下一次仍先确认水位再续。
> _Avoid_: 断点, 游标, 任务队列, 指纹对齐进度

那是**跨次续写**的能力，跟界面上显示到哪一步是两回事，**不得复用这个词**。

- `CONTEXT-MAP.md` 新加一节，定义 **任务进度**：后台任务当前在哪个阶段、该阶段已完成多少 / 共多少、已用多久。只服务界面展示，不参与任何决策；两个上下文共用同一套展示结构。`_Avoid_`: 发稿进度, 断点, 完成率预测。
- 放 `CONTEXT-MAP.md` 而不是两个 `CONTEXT.md`：它是基础设施词，不属于任何一边的领域。
- `src/publish/CONTEXT.md` 的 **发稿进度** 词条 `_Avoid_` 补上 `任务进度（界面）`。

## 边界

- **`src/publish/plan.py` 一行不动。** 它是纯决策、无 IO；进度是 IO 和展示。CLAUDE.md 说「新规则优先加在 plan.py」——进度不是规则。
- **不改任何发稿行为。** 所有 `progress.*` 调用都是纯记录，不影响 ADR 0008 / 0010 / 0011 / 0012 / 0013 的任何判断。分母取自本来就在手上的值，不为了显示进度额外读一次页面（那会直接违反 0012）。
- **不合并两个上下文的任务身份。** `PublishJob` 和 `CrawlJob` 各自保留，共用的只有进度值对象和展示层——跟 `capture_stdout` 已被 `desk.py:9` 从 `book.sync` 复用，是同一性质的物理耦合。
- `src/publish/writer.py` 工作区有 324 行未提交改动（ADR 0013 的分页读目录）。埋点落在这份工作区之上，改完必须跑 `tests/test_publish_writer.py`。

---

## 1. 新模块 `src/book/jobs.py`

上下文中立的进度原语。放 `book/` 下沿用既有先例；单开一个模块而不是塞进 `sync.py`，是为了让「进度」和「同步榜单」分开。

```python
PHASE_PENDING = "pending"
PHASE_RUNNING = "running"
PHASE_DONE    = "done"
PHASE_FAILED  = "failed"

@dataclass
class Phase:
    key: str
    label: str
    state: str = PHASE_PENDING
    note: str = ""
    done: int = 0
    total: int = 0        # 0 = 不确定态：只画呼吸条，不画百分比

@dataclass
class TaskProgress:
    phases: list[Phase]
    started_at: float = field(default_factory=time.monotonic)

    def begin(key, *, total=0, note="")      # 标 running
    def add_total(key, count)                # 分母运行中才知道时追加
    def advance(key, *, step=1, note="")     # step=0 只改 note
    def finish(key, *, note="")              # 标 done，done 补满到 total
    def fail(key, *, note="")
    def snapshot() -> list[dict]             # 加锁取冻结视图，给模板和 JSON
    def elapsed_seconds() -> float
```

要点：

- 内部一把 `threading.Lock`，`snapshot()` 在锁里浅拷贝。写在任务线程，读在 Web 线程。
- **未知 key 全部静默忽略。** 这样 `TaskProgress(phases=[])` 天然就是空对象，被调用方写 `progress = progress or TaskProgress([])`，callee 里不用到处判 `None`。每次分配新实例，不用共享单例（避免跨线程互相污染）。
- 百分比只在 `total > 0` 时算，模板负责判断。
- `add_total` 而不是只有 `begin(total=...)`：读目录时「章节管理」和「草稿箱」各出一批页数，分母要能相加。
- 顺带把 `buffer_lines(buffer) -> list[str]`（读 `StringIO` 并滤掉空行）放这里，三处 `_run_job` 共用。

## 2. 实时日志

这是根因，跟进度条同等重要。`desk.py`（`_run_job` 295、`_run_bind_job` 319）和 `sync.py`（`_run_job` 119）：把 buffer 挂到 job 上，让 Web 线程中途能读。

```python
@dataclass
class PublishJob:          # CrawlJob 同样处理
    ...
    progress: TaskProgress | None = None
    buffer: io.StringIO | None = None

    def log_lines(self) -> list[str]:
        # 任务线程正卡在 runner 里，只能由这边直接读它的缓冲区。
        # 单用户本机工具，GIL 下 StringIO 的读写不会撕裂。
        if self.buffer is not None:
            live = buffer_lines(self.buffer)
            if live:
                return live
        return list(self.lines)
```

`with capture_stdout() as buffer:` 之后立刻 `job.buffer = buffer`；收尾时 `job.lines = buffer_lines(buffer)` 再 `job.buffer = None`。模板和 JSON 一律改成读 `job.log_lines()`。

## 3. 三个任务的阶段表

阶段在 `start_*_job` 里**建线程之前**就装好，这样 303 重定向过去的第一屏就有骨架，不是空白。

**发稿**（干跑时第 4 阶段标签换成「预演章节」）

| key | 标签 | 分母来源 |
|---|---|---|
| `login` | 打开作家后台 | 0（等人扫码，不确定态） |
| `claim` | 认领平台作品 | 0，done 时 note 写 book_id |
| `catalog` | 读后台目录 | 页数，运行中由 `wait_for_catalog_pages` 得出；note 写「6/6 页 · 87 章」 |
| `chapters` | 写入章节 | 计划里非 SKIP / PUBLISHED_MISMATCH 的动作数 |

**绑定**

| key | 标签 | 分母来源 |
|---|---|---|
| `login` | 打开作家后台 | 0 |
| `books` | 读作品管理 | 0，done 时 note 写「N 本」 |

**同步榜单**

| key | 标签 | 分母来源 |
|---|---|---|
| `catalog` | 取榜单目录与字体 | 0 |
| `lists` | 采各榜 | `len(selected)` |

## 4. 埋点

约 12 处，全是纯记录，不改任何控制流。

### `src/publish/writer.py`

| 位置 | 埋点 |
|---|---|
| `run_publish` 217 | 签名加 `progress=None`；`progress = progress or TaskProgress([])` 后往下传 |
| `open_writer_home` 238 前后 | `begin("login")` / `finish("login")` |
| `execute_planned_publish` 450 | 多收一个 `progress` 参数 |
| 456 | `begin("claim")` |
| 474 创建后 / 485 认领后 | `finish("claim", note=book_id)` |
| 463-467 早退 | `fail("claim", note=claim_plan.halt_reason)` |
| `list_remote_chapters` 1036 | 签名加 `progress=None`；开头 `begin("catalog")` |
| 1068 | `finish("catalog", note=f"{页数}/{页数} 页 · {len(remotes)} 章")` |
| `collect_paged_catalog_rows` 1246 | 也收 `progress`。拿到 `numbers`（1269）后 `add_total("catalog", len(numbers) or 1)`；每翻完一页 `advance("catalog", note=f"{label}第{number}页")`。两个标签各调一次，分母天然相加 |
| 1241 / 1287 / 1294 每处 `raise PublishHalt` 之前 | `fail("catalog", note=原因)` |
| `execute_chapter_actions` 794 | 进循环前 `begin("chapters", total=可执行动作数)`（见下） |
| 832 `write_chapter` 前后 | 前 `advance(step=0, note=f"正在写 第{sequence}章《{title}》")`；后 `advance()` |
| 822 `remote.published` 的 `continue` | **也要 `advance()`** |
| `preview_chapter_plan` 726 | 干跑不执行，直接 `begin` + `finish("chapters", note=f"预演 {n} 章")` |
| `run_list_platform_books` 251 | 签名加 `progress=None`，绑定的两个阶段 |

分母：

```python
total = len([
    a for a in plan.chapter_actions
    if a.action not in {ACTION_SKIP, ACTION_PUBLISHED_MISMATCH}
])
```

`ACTION_SKIP` / `ACTION_PUBLISHED_MISMATCH` 在 806 行就 `continue` 了，不算进分母。但 822 行 `remote.published` 那条 `continue` 在分母**之内**——它已经计入 `total` 却不会走到 `write_chapter`，不补一次 `advance()`，进度条就永远差几格到不了满。

### `src/book/platforms/fanqie.py`

| 位置 | 埋点 |
|---|---|
| `crawl` 51 | 签名加 `progress=None` |
| 56 | `begin("catalog")` |
| 62 | `finish("catalog")` |
| 63-72 两个 except 臂 | `fail("catalog", note=halted_reason)` |
| 79 `for` 之前 | `begin("lists", total=len(selected))` |
| 85 / 98 / 107 / 112 | **四个出口都要 `advance("lists", note=rank_list.category)`** |

四个出口分别是：halted 后跳过（85）、成功（98）、被风控（107）、其它异常（112）。漏掉任何一个，失败的榜就会卡住分子，进度条停在半路。

`sync.py::run_fanqie_crawl` 和 `desk.py` 的三个 `_run_*` 负责把 `job.progress` 传进去。

## 5. JSON 端点 + 轮询脚本

### 路由

加在 `src/publish/web.py:231` 和 `src/book/web/app.py:92` 旁边：

- `GET /publish/jobs/{job_id}/progress`
- `GET /sync/{job_id}/progress`

用 `/progress` 这个独立路径段，**不要**用 `{job_id}.json`——后者会被既有的 `{job_id}` 规则先吃掉，得靠声明顺序兜，脆。

返回体：

```json
{"status":"running","finished":false,"halted":"","elapsed":252,
 "phases":[{"key":"catalog","label":"读后台目录","state":"done",
            "note":"6/6 页 · 87 章","done":6,"total":6}],
 "lines":["认领平台作品 7276...","已写入第89章《残灯》"]}
```

### `src/book/web/static/job.js`

新文件，约 30 行，无构建步骤（`/static` 已经挂好了，`app.py:49`）。**这是仓里第一段 JavaScript**，代价是明知的：换来的是进度条不闪、日志滚到哪停在哪、浏览器标签不再每 2 秒转圈。

- 从进度卡片的 `data-progress-url` 读地址，`setInterval` 每 1000ms 拉一次。
- 只做三件事：改阶段行的 `data-state` 和 note；改 `.progress-fill` 的 `style.width`；按 `lines.length` **只追加**新增日志行。已用时间本地每秒自增，拉到新值就校准。
- `finished` 为真时 `clearInterval` 然后 `location.reload()` 一次——绑定任务结束后要靠服务端渲染 `job.candidates` 那块卡片（`job.html:39-64`）。
- 关掉 JS 的兜底：`{% block extra_head %}` 里保留 `<noscript><meta http-equiv="refresh" content="2"></noscript>`。`head` 里的 `noscript` 允许放 `meta`，退化成现在的行为。

## 6. 模板与样式

- 新建 **`src/book/web/templates/_progress.html`** 一份，`job.html` 和 `sync.html` 都 `{% include %}`。放 `book/web/templates` 是因为 `publish/web.py:38-44` 已经把这个目录也加进了 Jinja 搜索路径——两边共用 `base.html` 走的就是这条路。
- 卡片结构：阶段编号 + 标签 + 状态标记 + note，运行中的阶段下面挂一条 `.progress-track` / `.progress-fill`，底部一行「已用 04:12」。
- `board.css` 在 `/* 任务日志 */`（678 行）之后新开 `/* 任务进度 */` 一节：`.progress`、`.progress-step[data-state=...]`、`.progress-track`、`.progress-fill`、`.progress-fill.is-indeterminate`。颜色只用现成 token（`--ink` / `--enter` / `--leave` / `--hairline`），不新增色值。不确定态那条呼吸动画照 `.stamp-run` 的 `stamp-pulse`（437-449）的写法，包在 `prefers-reduced-motion: no-preference` 里；文件末尾 829 行那个 reduce 块会一并兜住。

### 顺带补一个缺口

`summary.html:9-10` 已经有「正在同步。看进度」的横幅并禁用同步按钮，**发稿台没有对应的东西**：`web.py:53-60` 的 `publish_desk` 只传 `rows` / `error` / `work_title`，不查 `running_job()`，所以稿本列表页对「有任务在跑」毫无感知。

- `publish_desk` 加上 `running_job()`。
- `desk.html` 照 `summary.html` 的样子加一条横幅。
- 把那四个会起任务的按钮置灰：发稿、干跑（80-85）、创建平台作品（97-100）、绑定 / 改绑（91-94）。

---

## 测试

离线、不开浏览器、不碰 `novel/`（CLAUDE.md 测试约定）。

- **`tests/test_book_jobs.py`**（新）：`begin` / `add_total` / `advance` / `finish` / `fail` 的状态迁移；未知 key 被忽略；`total=0` 时不产出百分比；`snapshot()` 与后续改动隔离。
- **`tests/test_publish_desk.py`**：起任务后阶段骨架立刻就位；fake runner 推进阶段后 `get_job` 读得到；**跑到一半时 `job.log_lines()` 能读到缓冲区里的行**——这份文件已经有 `blocking_runner`（104、423）正是干这个的替身，照着扩。
- **`tests/test_publish_writer.py`**：用现有 `FakePage` / `FakeLocator` 跑 `run_publish`，断言 catalog 阶段的分母等于假分页控件的页数、chapters 阶段的分母等于计划里的可执行动作数、已发布跳过的那一章也把分子推进了；干跑只设不写。
- **`tests/test_book_fanqie.py`**：用现成的固定夹具跑 `crawl(progress=...)`，断言分母 = 选中榜单数，且失败的榜同样推进分子。
- 两个 `/progress` 端点各加一个用例，断言 `finished` 与 `phases` 形状。

## 验证

```bash
.venv/bin/python -m pytest tests/test_book_jobs.py -q
.venv/bin/python -m pytest tests/test_publish_desk.py -q
.venv/bin/python -m pytest tests/test_publish_writer.py -q
.venv/bin/python -m pytest tests/test_book_fanqie.py -q
.venv/bin/python -m pytest -q                      # 最后一次全量
```

真机走一遍：

1. `.venv/bin/python -m book.cli serve`
2. 开 `http://127.0.0.1:8765/` 点「同步榜单」——看 `lists` 阶段的分子逐张榜往上走，日志实时滚出来，不闪。
3. 开 `http://127.0.0.1:8765/publish` 对一个已绑定的稿本点「干跑」——看 `login → claim → catalog（N/N 页）→ 预演章节` 四段依次亮，已用时间每秒跳。
4. 浏览器关掉 JS 再刷一次，确认 `<noscript>` 的 meta refresh 仍然把页面刷出来（只是回到 2 秒一闪）。
