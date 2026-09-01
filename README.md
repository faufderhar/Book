# 网文风向标

本机内部工具：礼貌采集番茄公开榜单，按日存快照，看板看题材进 / 掉。

```
python3.11 -m venv .venv
.venv/bin/pip install -e .
.venv/bin/python -m book.cli crawl fanqie
.venv/bin/python -m book.cli serve
```

看板只绑 `127.0.0.1:8765`。番茄每天 15:30 之后采昨日榜：

```
30 15 * * * cd /Users/guxiaobin/project/Book && .venv/bin/python -m book.cli crawl fanqie
```

看板顶栏「题材进掉」会显示当前同步的榜单日期，也可点「同步榜单」当场采一次。

边界见 [docs/requirements.md](docs/requirements.md)。

# 番茄发稿

把 `novel/` 下一部稿本发到番茄作家后台。未绑定则按作品名称认领；已绑定则确认后台目录水位，再增量补后面的章。风向标不登录；发稿用本机可见浏览器，首次扫码，会话在 `.local/fanqie-writer/`。

```
.venv/bin/pip install -e .
.venv/bin/python -m playwright install chromium
.venv/bin/python -m publish init novel/工牌不认婚约
# 默认按作品名称认领已有平台作品。封面只在显式创建时上传
.venv/bin/python -m publish discover novel/工牌不认婚约
.venv/bin/python -m publish run --dry-run novel/工牌不认婚约
.venv/bin/python -m publish run novel/工牌不认婚约
# 只有搜索 0 命中时才建书：.venv/bin/python -m publish run --create novel/工牌不认婚约
```

本机工作台顶栏切「发稿」：打开 http://127.0.0.1:8765/publish ，对稿本点「干跑」或「发稿」，效果与上面的 CLI 相同。点「设置」改作品 ID（改绑会丢掉章缓存）、发稿时刻、章节可见性、单次章数上限。

默认认领、只存草稿，单次最多 20 章。创建必须显式 `--create`。词汇见 [src/publish/CONTEXT.md](src/publish/CONTEXT.md)。
