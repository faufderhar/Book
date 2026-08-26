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
