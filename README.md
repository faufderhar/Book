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

边界见 [docs/requirements.md](docs/requirements.md)。

# 番茄发稿

把 `novel/` 下一部稿本对齐到番茄作家后台。风向标不登录；发稿用本机可见浏览器，首次扫码，会话在 `.local/fanqie-writer/`。

```
.venv/bin/pip install -e .
.venv/bin/python -m playwright install chromium
.venv/bin/python -m publish init novel/工牌不认婚约
# 默认按作品名称认领已有平台作品；封面键为空但目录里有 封面.jpg 仍会上传并写回
.venv/bin/python -m publish discover novel/工牌不认婚约
.venv/bin/python -m publish run --dry-run novel/工牌不认婚约
.venv/bin/python -m publish run novel/工牌不认婚约
# 只有搜索 0 命中时才建书：.venv/bin/python -m publish run --create novel/工牌不认婚约
```

默认认领、只存草稿，单次最多 20 章。创建必须显式 `--create`。词汇见 [src/publish/CONTEXT.md](src/publish/CONTEXT.md)。
