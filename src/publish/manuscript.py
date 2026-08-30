from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

PROFILE_FILENAME = "书资料.yml"
PROFILE_HEADER = "# 番茄发稿书资料。键名与作家后台表单标签对齐。缺必填项则发稿停止。\n"

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
SERIAL_ONGOING = "连载"
SERIAL_FINISHED = "完结"

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
class ChapterBinding:
    chapter_id: str = ""
    fingerprint: str = ""
    visibility: str = ""


@dataclass
class BookProfile:
    path: Path
    book_id: str = ""
    chapter_bindings: dict[int, ChapterBinding] = field(default_factory=dict)
    chapter_visibility: str = VISIBILITY_DRAFT
    serial_status: str = SERIAL_ONGOING
    max_chapters_per_run: int = 20
    delay_seconds: float = 4.0
    human_wait_seconds: float = 600.0
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
        cover = Path(relative)
        if not cover.is_absolute():
            cover = manuscript_dir / cover
        if cover.is_file():
            return cover
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

    def set_binding(self, sequence: int, chapter_id: str, fingerprint: str, visibility: str) -> None:
        self.chapter_bindings[sequence] = ChapterBinding(
            chapter_id=chapter_id,
            fingerprint=fingerprint,
            visibility=visibility,
        )

    def to_document(self) -> dict:
        chapters: dict[int, dict[str, str]] = {}
        for sequence, binding in sorted(self.chapter_bindings.items()):
            chapters[sequence] = {
                "id": binding.chapter_id,
                "正文指纹": binding.fingerprint,
                "可见性": binding.visibility,
            }
        return {
            "绑定": {
                "作品ID": self.book_id,
                "章节": chapters,
            },
            "发稿": {
                "章节可见性": self.chapter_visibility,
                "连载状态": self.serial_status,
                "单次章数上限": self.max_chapters_per_run,
                "章间隔秒": self.delay_seconds,
                "人工等待秒": self.human_wait_seconds,
            },
            "书资料": self.fields,
        }


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
    if not chapters:
        raise ManuscriptError(f"稿本里没有章节文件：{manuscript_dir}")
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
    if not isinstance(fields, dict):
        raise ManuscriptError(f"书资料.书资料 必须是映射：{path}")
    chapter_bindings: dict[int, ChapterBinding] = {}
    raw_chapters = binding.get("章节") or {}
    if isinstance(raw_chapters, dict):
        for key, value in raw_chapters.items():
            sequence = int(key)
            if isinstance(value, dict):
                chapter_bindings[sequence] = ChapterBinding(
                    chapter_id=str(value.get("id") or ""),
                    fingerprint=str(value.get("正文指纹") or ""),
                    visibility=str(value.get("可见性") or ""),
                )
            else:
                chapter_bindings[sequence] = ChapterBinding(chapter_id=str(value or ""))
    visibility = str(publish.get("章节可见性") or VISIBILITY_DRAFT)
    if visibility not in {VISIBILITY_DRAFT, VISIBILITY_PUBLISH}:
        raise ManuscriptError(f"章节可见性只能是 {VISIBILITY_DRAFT} 或 {VISIBILITY_PUBLISH}")
    serial_status = str(publish.get("连载状态") or SERIAL_ONGOING)
    if serial_status not in {SERIAL_ONGOING, SERIAL_FINISHED}:
        raise ManuscriptError(f"连载状态只能是 {SERIAL_ONGOING} 或 {SERIAL_FINISHED}")
    return BookProfile(
        path=path,
        book_id=str(binding.get("作品ID") or ""),
        chapter_bindings=chapter_bindings,
        chapter_visibility=visibility,
        serial_status=serial_status,
        max_chapters_per_run=int(publish.get("单次章数上限") or 20),
        delay_seconds=float(publish.get("章间隔秒") or 4),
        human_wait_seconds=float(publish.get("人工等待秒") or 600),
        fields=fields,
    )


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


def repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def browser_profile_dir() -> Path:
    path = repo_root() / ".local" / "fanqie-writer"
    path.mkdir(parents=True, exist_ok=True)
    return path
