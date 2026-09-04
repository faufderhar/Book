from __future__ import annotations

import hashlib
import re
from datetime import date, datetime, time, timedelta
from dataclasses import dataclass, field
from pathlib import Path

import yaml

PROFILE_FILENAME = "书资料.yml"
PROFILE_HEADER = "# 番茄发稿书资料。键名与作家后台表单标签对齐。创建必填项只在显式创建前检查。\n"

CHAPTER_FILENAME_RE = re.compile(r"^第0*(\d+)章[-－](.+)\.md$")
HEADING_RE = re.compile(r"^#\s+(.+)$", re.MULTILINE)
CHAPTER_PREFIX_RE = re.compile(r"^第0*\d+章\s*")
OUTLINE_REF_RE = re.compile(r"据 `([^`]+)` 撰写")
TITLE_IN_HEADING_RE = re.compile(r"《([^》]+)》")
PROTAGONIST_RE = re.compile(r"^(?:[-*]\s*)?(?:女主|男主)[:：]\s*([^\s，,（(]+)", re.MULTILINE)
BOLD_RE = re.compile(r"\*\*(.+?)\*\*")
ITALIC_RE = re.compile(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)")
LINK_RE = re.compile(r"\[([^\]]+)\]\([^)]+\)")
INLINE_CODE_RE = re.compile(r"`([^`]+)`")
ATX_HEADING_RE = re.compile(r"^#+\s+", re.MULTILINE)

VISIBILITY_DRAFT = "草稿"
VISIBILITY_PUBLISH = "立即发布"
VISIBILITY_SCHEDULE = "定时发布"
SERIAL_ONGOING = "连载"
SERIAL_FINISHED = "完结"
VISIBILITY_CHOICES = (VISIBILITY_DRAFT, VISIBILITY_PUBLISH, VISIBILITY_SCHEDULE)
SERIAL_CHOICES = (SERIAL_ONGOING, SERIAL_FINISHED)
ALLOWED_VISIBILITY = set(VISIBILITY_CHOICES)
CLOCK_RE = re.compile(r"^(\d{1,2}):(\d{2})$")
SCHEDULE_AT_FORMAT = "%Y-%m-%d %H:%M"


class QuotedClock(str):
    """YAML 1.1 会把未加引号的 08:00 读成 480 秒。写出时强制加引号。"""


def _represent_quoted_clock(dumper: yaml.SafeDumper, data: QuotedClock) -> yaml.Node:
    return dumper.represent_scalar("tag:yaml.org,2002:str", str(data), style='"')


yaml.add_representer(QuotedClock, _represent_quoted_clock, Dumper=yaml.SafeDumper)

REQUIRED_CREATE_FIELDS = ("作品名称", "频道", "分类", "简介", "封面")


class ManuscriptError(ValueError):
    """稿本或书资料不完整，发稿在本地就应停止。"""


@dataclass(frozen=True)
class Chapter:
    sequence: int
    title: str
    body: str
    path: Path

    @property
    def fingerprint(self) -> str:
        payload = f"{self.title}\n{self.body}".encode("utf-8")
        return hashlib.sha256(payload).hexdigest()[:16]


@dataclass
class ChapterCache:
    chapter_id: str = ""
    fingerprint: str = ""
    visibility: str = ""
    scheduled_at: str = ""


@dataclass
class BookProfile:
    path: Path
    book_id: str = ""
    chapter_cache: dict[int, ChapterCache] = field(default_factory=dict)
    chapter_visibility: str = VISIBILITY_DRAFT
    serial_status: str = SERIAL_ONGOING
    max_chapters_per_run: int = 20
    delay_seconds: float = 0.0
    human_wait_seconds: float = 600.0
    schedule_times: tuple[str, ...] = ()
    fields: dict[str, object] = field(default_factory=dict)

    def field_text(self, key: str) -> str:
        value = self.fields.get(key)
        if value is None:
            return ""
        if isinstance(value, list):
            return "\n".join(str(item) for item in value)
        return str(value).strip()

    def tag_list(self) -> list[str]:
        value = self.fields.get("标签")
        if isinstance(value, list):
            return [str(item).strip() for item in value if str(item).strip()]
        if isinstance(value, str) and value.strip():
            return [part.strip() for part in re.split(r"[，,]", value) if part.strip()]
        return []

    def cover_file(self, manuscript_dir: Path) -> Path | None:
        relative = self.field_text("封面")
        if not relative:
            return None
        root = manuscript_dir.resolve()
        cover = Path(relative)
        if not cover.is_absolute():
            cover = root / cover
        try:
            resolved = cover.resolve()
            resolved.relative_to(root)
        except ValueError:
            return None
        if resolved.is_file():
            return resolved
        return None

    def missing_create_fields(self, manuscript_dir: Path) -> list[str]:
        missing: list[str] = []
        for key in REQUIRED_CREATE_FIELDS:
            if key == "封面":
                if self.cover_file(manuscript_dir) is None:
                    missing.append("封面")
                continue
            if not self.field_text(key):
                missing.append(key)
        return missing

    def cache_chapter(
        self,
        sequence: int,
        chapter_id: str,
        fingerprint: str,
        visibility: str,
        scheduled_at: str = "",
    ) -> None:
        self.chapter_cache[sequence] = ChapterCache(
            chapter_id=chapter_id,
            fingerprint=fingerprint,
            visibility=visibility,
            scheduled_at=scheduled_at,
        )

    def rebind(self, book_id: str) -> bool:
        normalized = str(book_id or "").strip()
        if normalized == self.book_id.strip():
            return False
        self.book_id = normalized
        self.chapter_cache = {}
        return True

    def to_document(self) -> dict:
        chapters: dict[int, dict[str, str]] = {}
        for sequence, cached in sorted(self.chapter_cache.items()):
            row = {
                "id": cached.chapter_id,
                "正文指纹": cached.fingerprint,
                "可见性": cached.visibility,
            }
            if cached.scheduled_at:
                row["定时"] = cached.scheduled_at
            chapters[sequence] = row
        document: dict[str, object] = {
            "绑定": {
                "作品ID": self.book_id,
            },
        }
        if chapters:
            document["章缓存"] = chapters
        document.update(
            {
                "发稿": publish_document(self),
                "书资料": self.fields,
            }
        )
        return document


@dataclass(frozen=True)
class Manuscript:
    directory: Path
    chapters: tuple[Chapter, ...]
    profile: BookProfile
    memo_path: Path | None = None


def load_manuscript(directory: Path) -> Manuscript:
    manuscript_dir = directory.expanduser().resolve()
    if not manuscript_dir.is_dir():
        raise ManuscriptError(f"稿本目录不存在：{directory}")
    profile_path = manuscript_dir / PROFILE_FILENAME
    if not profile_path.is_file():
        raise ManuscriptError(f"缺少 {PROFILE_FILENAME}。先运行：python -m publish init {manuscript_dir}")
    profile = load_profile(profile_path)
    chapters = tuple(scan_chapters(manuscript_dir))
    memo_path = manuscript_dir / "00-连载备忘.md"
    return Manuscript(
        directory=manuscript_dir,
        chapters=chapters,
        profile=profile,
        memo_path=memo_path if memo_path.is_file() else None,
    )


def scan_chapters(manuscript_dir: Path) -> list[Chapter]:
    found: dict[int, Chapter] = {}
    for path in sorted(manuscript_dir.rglob("*.md")):
        match = CHAPTER_FILENAME_RE.match(path.name)
        if not match:
            continue
        sequence = int(match.group(1))
        filename_title = match.group(2).strip()
        chapter = parse_chapter_file(path, sequence, filename_title)
        if sequence in found:
            raise ManuscriptError(f"章节序号重复：第{sequence}章 {found[sequence].path} 与 {path}")
        found[sequence] = chapter
    return [found[sequence] for sequence in sorted(found)]


def parse_chapter_file(path: Path, sequence: int, filename_title: str) -> Chapter:
    raw = path.read_text(encoding="utf-8")
    heading_match = HEADING_RE.search(raw)
    if heading_match:
        heading = heading_match.group(1).strip()
        body_source = raw[heading_match.end() :]
    else:
        heading = filename_title
        body_source = raw
    title = CHAPTER_PREFIX_RE.sub("", heading).strip() or filename_title
    body = markdown_to_plain(body_source).strip()
    if not body:
        raise ManuscriptError(f"章节正文为空：{path}")
    return Chapter(sequence=sequence, title=title, body=body, path=path)


def markdown_to_plain(source: str) -> str:
    text = BOLD_RE.sub(r"\1", source)
    text = ITALIC_RE.sub(r"\1", text)
    text = LINK_RE.sub(r"\1", text)
    text = INLINE_CODE_RE.sub(r"\1", text)
    text = ATX_HEADING_RE.sub("", text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def load_profile(path: Path) -> BookProfile:
    document = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(document, dict):
        raise ManuscriptError(f"书资料不是映射：{path}")
    binding = document.get("绑定") or {}
    publish = document.get("发稿") or {}
    fields = document.get("书资料") or {}
    if not isinstance(publish, dict):
        raise ManuscriptError(f"书资料.发稿 必须是映射：{path}")
    if not isinstance(fields, dict):
        raise ManuscriptError(f"书资料.书资料 必须是映射：{path}")
    if not isinstance(binding, dict):
        raise ManuscriptError(f"书资料.绑定 必须是映射：{path}")
    parsed_publish = parse_publish_fields(publish)
    return BookProfile(
        path=path,
        book_id=str(binding.get("作品ID") or ""),
        chapter_cache=_load_chapter_cache(document, binding),
        fields=fields,
        **parsed_publish,
    )


def _load_chapter_cache(document: dict, binding: dict) -> dict[int, ChapterCache]:
    raw_chapters = document.get("章缓存")
    if not isinstance(raw_chapters, dict) or not raw_chapters:
        nested = binding.get("章节")
        raw_chapters = nested if isinstance(nested, dict) else {}
    cache: dict[int, ChapterCache] = {}
    for key, value in raw_chapters.items():
        try:
            sequence = int(key)
        except (TypeError, ValueError) as error:
            raise ManuscriptError(f"章节序号无效：{key}") from error
        if isinstance(value, dict):
            cache[sequence] = ChapterCache(
                chapter_id=str(value.get("id") or ""),
                fingerprint=str(value.get("正文指纹") or ""),
                visibility=str(value.get("可见性") or ""),
                scheduled_at=str(value.get("定时") or ""),
            )
        else:
            cache[sequence] = ChapterCache(chapter_id=str(value or ""))
    return cache


def save_profile(profile: BookProfile) -> None:
    dumped = yaml.safe_dump(
        profile.to_document(),
        allow_unicode=True,
        sort_keys=False,
        default_flow_style=False,
    )
    profile.path.write_text(PROFILE_HEADER + dumped, encoding="utf-8")


def init_profile(
    manuscript_dir: Path,
    outline_path: Path | None = None,
    force: bool = False,
) -> BookProfile:
    manuscript_dir = manuscript_dir.expanduser().resolve()
    if not manuscript_dir.is_dir():
        raise ManuscriptError(f"稿本目录不存在：{manuscript_dir}")
    profile_path = manuscript_dir / PROFILE_FILENAME
    if profile_path.exists() and not force:
        raise ManuscriptError(f"已有 {PROFILE_FILENAME}，若要重灌默认加 --force")
    chapters = scan_chapters(manuscript_dir)
    if not chapters:
        raise ManuscriptError(f"稿本里没有章节文件：{manuscript_dir}")
    memo_path = manuscript_dir / "00-连载备忘.md"
    if outline_path is None and memo_path.is_file():
        outline_path = outline_from_memo(memo_path, manuscript_dir)
    fields = default_fields(manuscript_dir, outline_path, memo_path if memo_path.is_file() else None)
    profile = BookProfile(path=profile_path, fields=fields)
    save_profile(profile)
    return profile


def create_manuscript(novel_root: Path, work_title: str) -> Path:
    name = str(work_title or "").strip()
    if not name:
        raise ManuscriptError("作品名称不能为空")
    if "/" in name or "\\" in name:
        raise ManuscriptError("作品名称不能包含路径分隔符")
    if name in {".", ".."}:
        raise ManuscriptError("稿本目录名不合法")
    base = novel_root.expanduser().resolve()
    base.mkdir(parents=True, exist_ok=True)
    manuscript_dir = (base / name).resolve()
    if manuscript_dir.parent != base:
        raise ManuscriptError("稿本目录名不合法")
    profile_path = manuscript_dir / PROFILE_FILENAME
    if manuscript_dir.exists():
        if not manuscript_dir.is_dir() or profile_path.is_file():
            raise ManuscriptError(f"已有同名稿本：{name}")
    else:
        manuscript_dir.mkdir()
    memo_path = manuscript_dir / "00-连载备忘.md"
    usable_memo = memo_path if memo_path.is_file() else None
    if usable_memo is not None or scan_chapters(manuscript_dir):
        outline_path = outline_from_memo(memo_path, manuscript_dir) if usable_memo else None
        fields = default_fields(manuscript_dir, outline_path, usable_memo)
    else:
        fields = {
            "作品名称": name,
            "频道": "",
            "分类": "",
            "子分类": "",
            "标签": [],
            "主角姓名": "",
            "封面简介": "",
            "简介": "",
            "封面": "",
        }
    profile = BookProfile(
        path=profile_path,
        fields=fields,
    )
    save_profile(profile)
    return manuscript_dir


def write_cover_file(manuscript_dir: Path, filename: str, data: bytes) -> str:
    if not data:
        raise ManuscriptError("封面文件是空的")
    name = Path(str(filename or "")).name.strip()
    if not name or name in {".", ".."} or "/" in name or "\\" in name:
        raise ManuscriptError("封面文件名不合法")
    destination = manuscript_dir / name
    destination.write_bytes(data)
    return name


def outline_from_memo(memo_path: Path, manuscript_dir: Path) -> Path | None:
    text = memo_path.read_text(encoding="utf-8")
    match = OUTLINE_REF_RE.search(text)
    if not match:
        return None
    referenced = Path(match.group(1))
    candidates = [
        referenced,
        manuscript_dir / referenced,
        manuscript_dir.parent.parent / referenced,
        repo_root() / referenced,
    ]
    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()
    return None


def default_fields(
    manuscript_dir: Path,
    outline_path: Path | None,
    memo_path: Path | None,
) -> dict[str, object]:
    title = manuscript_dir.name
    channel = ""
    category = ""
    subcategory = ""
    short_intro = ""
    long_intro = ""
    protagonist = ""
    if outline_path and outline_path.is_file():
        outline_text = outline_path.read_text(encoding="utf-8")
        heading_title = TITLE_IN_HEADING_RE.search(outline_text)
        if heading_title:
            title = heading_title.group(1).strip()
        channel = parse_channel(meta_line(outline_text, "频道"))
        category, subcategory = split_category(meta_line(outline_text, "分类"))
        short_intro = extract_heading_block(outline_text, "封面简介")
        long_intro = extract_heading_block(outline_text, "长简介")
    if memo_path and memo_path.is_file():
        memo_text = memo_path.read_text(encoding="utf-8")
        protagonist_match = PROTAGONIST_RE.search(memo_text)
        if protagonist_match:
            protagonist = protagonist_match.group(1).strip()
    cover_name = find_cover_name(manuscript_dir)
    return {
        "作品名称": title,
        "频道": channel,
        "分类": category,
        "子分类": subcategory,
        "标签": [],
        "主角姓名": protagonist,
        "封面简介": short_intro,
        "简介": long_intro or short_intro,
        "封面": cover_name,
    }


def find_cover_name(manuscript_dir: Path) -> str:
    for name in ("封面.jpg", "封面.jpeg", "封面.png", "封面.webp"):
        if (manuscript_dir / name).is_file():
            return name
    return ""


def meta_line(outline_text: str, key: str) -> str:
    pattern = re.compile(rf"^[-*]\s*{re.escape(key)}[:：]\s*(.+)$", re.MULTILINE)
    match = pattern.search(outline_text)
    return match.group(1).strip() if match else ""


def parse_channel(raw: str) -> str:
    if "女频" in raw:
        return "女频"
    if "男频" in raw:
        return "男频"
    return raw.strip()


def split_category(raw: str) -> tuple[str, str]:
    cleaned = re.sub(r"（[^）]*）", "", raw)
    cleaned = re.sub(r"\([^)]*\)", "", cleaned)
    cleaned = cleaned.split("，")[0].strip()
    for separator in ("／", "/"):
        if separator in cleaned:
            left, right = cleaned.split(separator, 1)
            return left.strip(), right.strip()
    return cleaned, ""


def extract_heading_block(markdown_text: str, heading: str) -> str:
    pattern = re.compile(
        rf"^###\s+{re.escape(heading)}[^\n]*\n+(.*?)(?=^#{{1,3}}\s+|\Z)",
        re.MULTILINE | re.DOTALL,
    )
    match = pattern.search(markdown_text)
    if not match:
        return ""
    return match.group(1).strip()


def parse_schedule_times(raw: object) -> tuple[str, ...]:
    if raw is None or raw == "":
        return ()
    if isinstance(raw, str):
        items = [part.strip() for part in re.split(r"[，,、\s]+", raw) if part.strip()]
    elif isinstance(raw, list):
        items = [normalize_schedule_item(item) for item in raw]
        items = [item for item in items if item]
    else:
        raise ManuscriptError("发稿时刻必须是时刻列表，例如 08:00、15:00")
    clocks: list[str] = []
    for item in items:
        hour, minute = parse_publish_clock(item)
        clocks.append(f"{hour:02d}:{minute:02d}")
    return tuple(clocks)


def parse_publish_clock(text: str) -> tuple[int, int]:
    match = CLOCK_RE.fullmatch(text.strip())
    if match is None:
        raise ManuscriptError(f"发稿时刻格式应为 HH:MM：{text}")
    hour = int(match.group(1))
    minute = int(match.group(2))
    if hour > 23 or minute > 59:
        raise ManuscriptError(f"发稿时刻无效：{text}")
    return hour, minute


def normalize_schedule_item(item: object) -> str:
    if isinstance(item, bool) or item is None:
        return ""
    if isinstance(item, int):
        hour, minute = divmod(item, 60)
        return f"{hour:02d}:{minute:02d}"
    return str(item).strip()


def publish_document(profile: BookProfile) -> dict[str, object]:
    document: dict[str, object] = {
        "章节可见性": profile.chapter_visibility,
        "连载状态": profile.serial_status,
        "单次章数上限": profile.max_chapters_per_run,
        "章间隔秒": profile.delay_seconds,
        "人工等待秒": profile.human_wait_seconds,
    }
    if profile.schedule_times:
        document["发稿时刻"] = [QuotedClock(clock) for clock in profile.schedule_times]
    return document


def parse_scheduled_at(text: str) -> datetime | None:
    value = (text or "").strip()
    if not value:
        return None
    try:
        return datetime.strptime(value, SCHEDULE_AT_FORMAT)
    except ValueError:
        return None


def format_scheduled_at(moment: datetime) -> str:
    return moment.strftime(SCHEDULE_AT_FORMAT)


def clock_on_day(day: date, clock: str) -> datetime:
    hour, minute = parse_publish_clock(clock)
    return datetime.combine(day, time(hour=hour, minute=minute))


def next_slot_after(moment: datetime, clocks: tuple[str, ...]) -> datetime:
    if not clocks:
        raise ManuscriptError("定时发布需要发稿时刻，例如 08:00、15:00")
    day = moment.date()
    while True:
        for clock in clocks:
            candidate = clock_on_day(day, clock)
            if candidate > moment:
                return candidate
        day = day + timedelta(days=1)


def next_first_clock(now: datetime, clocks: tuple[str, ...]) -> datetime:
    if not clocks:
        raise ManuscriptError("定时发布需要发稿时刻，例如 08:00、15:00")
    today_first = clock_on_day(now.date(), clocks[0])
    if today_first > now:
        return today_first
    return clock_on_day(now.date() + timedelta(days=1), clocks[0])


def parse_publish_fields(publish: object) -> dict[str, object]:
    if not isinstance(publish, dict):
        raise ManuscriptError("发稿必须是映射")
    visibility = str(publish.get("章节可见性") or VISIBILITY_DRAFT).strip()
    if visibility not in ALLOWED_VISIBILITY:
        raise ManuscriptError("章节可见性只能是 " + "、".join(VISIBILITY_CHOICES))
    serial_status = str(publish.get("连载状态") or SERIAL_ONGOING).strip()
    if serial_status not in SERIAL_CHOICES:
        raise ManuscriptError("连载状态只能是 " + "、".join(SERIAL_CHOICES))
    max_chapters_per_run = _parse_int(
        publish.get("单次章数上限"),
        field_name="单次章数上限",
        default=20,
        minimum=1,
    )
    delay_seconds = _parse_float(
        publish.get("章间隔秒"),
        field_name="章间隔秒",
        default=0.0,
        minimum=0,
    )
    human_wait_seconds = _parse_float(
        publish.get("人工等待秒"),
        field_name="人工等待秒",
        default=600.0,
        minimum=0,
    )
    schedule_times = parse_schedule_times(publish.get("发稿时刻"))
    if visibility == VISIBILITY_SCHEDULE and not schedule_times:
        raise ManuscriptError("定时发布需要发稿时刻，例如 08:00、15:00")
    return {
        "chapter_visibility": visibility,
        "serial_status": serial_status,
        "max_chapters_per_run": max_chapters_per_run,
        "delay_seconds": delay_seconds,
        "human_wait_seconds": human_wait_seconds,
        "schedule_times": schedule_times,
    }


def apply_publish_fields(profile: BookProfile, publish: dict) -> None:
    parsed = parse_publish_fields(publish)
    profile.chapter_visibility = str(parsed["chapter_visibility"])
    profile.serial_status = str(parsed["serial_status"])
    profile.max_chapters_per_run = int(parsed["max_chapters_per_run"])
    profile.delay_seconds = float(parsed["delay_seconds"])
    profile.human_wait_seconds = float(parsed["human_wait_seconds"])
    profile.schedule_times = tuple(str(item) for item in parsed["schedule_times"])


def _parse_int(raw: object, *, field_name: str, default: int, minimum: int) -> int:
    if raw in (None, ""):
        value = default
    else:
        try:
            value = int(raw)
        except (TypeError, ValueError) as error:
            raise ManuscriptError(f"{field_name}必须是整数") from error
    if value < minimum:
        raise ManuscriptError(f"{field_name}至少为 {minimum}")
    return value


def _parse_float(raw: object, *, field_name: str, default: float, minimum: float) -> float:
    if raw in (None, ""):
        value = default
    else:
        try:
            value = float(raw)
        except (TypeError, ValueError) as error:
            raise ManuscriptError(f"{field_name}必须是数字") from error
    if value < minimum:
        raise ManuscriptError(f"{field_name}不能为负" if minimum == 0 else f"{field_name}至少为 {minimum:g}")
    return value


def latest_occupied_slot(profile: BookProfile, skip_sequences: set[int] | None = None) -> datetime | None:
    skipped = skip_sequences or set()
    latest_sequence = -1
    latest: datetime | None = None
    for sequence, cached in profile.chapter_cache.items():
        if sequence in skipped:
            continue
        if cached.visibility != VISIBILITY_SCHEDULE:
            continue
        current = parse_scheduled_at(cached.scheduled_at)
        if current is None:
            continue
        if sequence > latest_sequence:
            latest_sequence = sequence
            latest = current
    return latest


def take_next_publish_slot(now: datetime, clocks: tuple[str, ...], occupied: datetime | None) -> datetime:
    if occupied is None:
        return next_first_clock(now, clocks)
    candidate = next_slot_after(occupied, clocks)
    while candidate <= now:
        candidate = next_slot_after(candidate, clocks)
    return candidate


def preview_publish_slots(
    profile: BookProfile,
    now: datetime | None = None,
    count: int = 4,
) -> tuple[str, ...]:
    if not profile.schedule_times or count < 1:
        return ()
    moment = now or datetime.now()
    occupied = latest_occupied_slot(profile)
    slots: list[str] = []
    current = occupied
    for _ in range(count):
        current = take_next_publish_slot(moment, profile.schedule_times, current)
        slots.append(format_scheduled_at(current))
    return tuple(slots)


def repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def content_root() -> Path:
    """稿本和作家会话落在主工作区。git worktree 里跑发稿台也读那一份。"""
    return primary_worktree_root(repo_root())


def primary_worktree_root(start: Path) -> Path:
    git_path = start / ".git"
    if not git_path.is_file():
        return start
    gitdir = None
    for line in git_path.read_text(encoding="utf-8").splitlines():
        if line.startswith("gitdir:"):
            gitdir = Path(line.split(":", 1)[1].strip())
            break
    if gitdir is None:
        return start
    if not gitdir.is_absolute():
        gitdir = (start / gitdir).resolve()
    if gitdir.parent.name != "worktrees":
        return start
    common_git = gitdir.parent.parent
    if common_git.name != ".git":
        return start
    return common_git.parent


def browser_profile_dir() -> Path:
    path = content_root() / ".local" / "fanqie-writer"
    path.mkdir(parents=True, exist_ok=True)
    return path
