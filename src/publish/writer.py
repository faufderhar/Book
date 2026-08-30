from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from publish.manuscript import (
    SERIAL_FINISHED,
    VISIBILITY_PUBLISH,
    BookProfile,
    Chapter,
    Manuscript,
    browser_profile_dir,
    save_profile,
)

if TYPE_CHECKING:
    from playwright.sync_api import Locator, Page

WRITER_HOMES = (
    "https://writer.muyewx.com/",
    "https://fanqienovel.com/main/writer/book-manage",
    "https://fanqienovel.com/writer/zone/",
)

LOGGED_IN_HINTS = ("作品管理", "创建新书", "创建作品", "工作台", "章节管理")
CHALLENGE_HINTS = ("安全验证", "请完成验证", "验证码", "滑动验证")
CREATE_BOOK_BUTTONS = ("创建新书", "创建作品", "新建作品")
SUBMIT_BOOK_BUTTONS = ("立即创建", "提交并创建", "确认创建")
CREATE_CHAPTER_BUTTONS = ("创建章节", "新建章节", "写新章节")
DRAFT_BUTTONS = ("保存草稿", "存草稿")
PUBLISH_BUTTONS = ("发布", "立即发布")
AUTO_DISMISS_BUTTONS = ("我知道了", "知道了")
BOOK_MANAGE_BUTTONS = ("作品管理", "作品列表")
SETTINGS_BUTTONS = ("作品信息", "作品设置", "编辑作品")

FIELD_ALIASES: dict[str, tuple[str, ...]] = {
    "作品名称": ("作品名称", "书名", "作品名"),
    "频道": ("频道", "目标读者", "男女频", "读者性别"),
    "分类": ("分类", "作品分类", "一级分类"),
    "子分类": ("子分类", "二级分类", "细分类"),
    "标签": ("标签", "作品标签"),
    "主角姓名": ("主角姓名", "主角名", "主角"),
    "简介": ("简介", "作品简介", "长简介"),
    "封面简介": ("封面简介", "短简介", "一句话简介", "推荐语"),
    "封面": ("封面", "上传封面", "作品封面"),
    "连载状态": ("连载状态", "写作状态", "作品状态"),
}

DISCOVERY_NOISE = {
    "确定",
    "取消",
    "保存",
    "提交",
    "创建",
    "返回",
    "关闭",
    "我知道了",
    "删除",
    "搜索",
    "筛选",
}

BOOK_ID_RE = re.compile(r"[?&](?:bookId|book_id|novel_id)=(\d+)", re.I)
BOOK_PATH_RE = re.compile(r"/book(?:Id)?/(\d+)", re.I)
CHAPTER_ID_RE = re.compile(r"(?:chapterId|chapter_id|item_id|itemId)=(\d+)", re.I)
CHAPTER_TITLE_RE = re.compile(r"第0*(\d+)章")


class PublishHalt(RuntimeError):
    """登录、风控或后台表单缺字段，发稿应停并保留进度。"""


@dataclass
class RemoteChapter:
    title: str
    published: bool
    chapter_id: str = ""


@dataclass
class PublishReport:
    created_book: bool = False
    created_sequences: list[int] = field(default_factory=list)
    updated_sequences: list[int] = field(default_factory=list)
    skipped_sequences: list[int] = field(default_factory=list)
    published_mismatches: list[str] = field(default_factory=list)
    locked_fields: list[str] = field(default_factory=list)
    discovered_fields: list[str] = field(default_factory=list)
    missing_fields: list[str] = field(default_factory=list)
    halted: str | None = None
    dry_run: bool = False

    def print_report(self) -> None:
        mode = "干跑" if self.dry_run else "发稿"
        print(f"== {mode}报告 ==", flush=True)
        if self.created_book:
            print("已创建平台作品", flush=True)
        if self.created_sequences:
            print("新建章节：" + comma_sequences(self.created_sequences), flush=True)
        if self.updated_sequences:
            print("更新草稿：" + comma_sequences(self.updated_sequences), flush=True)
        if self.skipped_sequences:
            print("已对齐跳过：" + comma_sequences(self.skipped_sequences), flush=True)
        for line in self.published_mismatches:
            print(f"已发布不一致：{line}", flush=True)
        if self.locked_fields:
            print("锁定字段：" + "、".join(self.locked_fields), flush=True)
        if self.discovered_fields:
            print("发现表单：" + "、".join(self.discovered_fields), flush=True)
        if self.missing_fields:
            print("缺书资料：" + "、".join(self.missing_fields), flush=True)
        if self.halted:
            print(f"已停止：{self.halted}", flush=True)


def comma_sequences(sequences: list[int]) -> str:
    return "、".join(f"第{sequence}章" for sequence in sequences)


def run_publish(manuscript: Manuscript, dry_run: bool = False, discover_only: bool = False) -> PublishReport:
    from playwright.sync_api import sync_playwright

    report = PublishReport(dry_run=dry_run or discover_only)
    missing = manuscript.profile.missing_create_fields(manuscript.directory)
    if missing and not manuscript.profile.book_id and not discover_only:
        report.missing_fields = missing
        report.halted = "创建平台作品前书资料不完整"
        report.print_report()
        return report

    with sync_playwright() as playwright:
        context = playwright.chromium.launch_persistent_context(
            user_data_dir=str(browser_profile_dir()),
            headless=False,
            viewport={"width": 1440, "height": 960},
            locale="zh-CN",
        )
        try:
            page = context.pages[0] if context.pages else context.new_page()
            page.set_default_timeout(15_000)
            open_writer_home(page, manuscript.profile)
            if discover_only:
                discover_form_fields(page, manuscript, report)
            else:
                publish_manuscript(page, manuscript, report, dry_run=dry_run)
        except PublishHalt as halted:
            report.halted = str(halted)
        finally:
            save_profile(manuscript.profile)
            context.close()
    report.print_report()
    return report


def open_writer_home(page: Page, profile: BookProfile) -> None:
    last_error = ""
    for url in WRITER_HOMES:
        try:
            page.goto(url, wait_until="domcontentloaded")
            wait_until_logged_in(page, profile)
            return
        except PublishHalt as halted:
            raise halted
        except Exception as error:
            last_error = str(error)
            continue
    raise PublishHalt(f"打不开作家后台：{last_error}")


def wait_until_logged_in(page: Page, profile: BookProfile) -> None:
    timeout_ms = int(profile.human_wait_seconds * 1000)
    deadline = time.monotonic() + timeout_ms / 1000
    announced_login = False
    announced_challenge = False
    while time.monotonic() < deadline:
        dismiss_popups(page)
        if challenge_visible(page):
            if not announced_challenge:
                print("请在浏览器里完成验证码或安全验证。", flush=True)
                announced_challenge = True
            page.wait_for_timeout(1500)
            continue
        if logged_in(page):
            return
        if not announced_login:
            print("请在弹出的浏览器里登录番茄作家账号（通常是扫码）。", flush=True)
            announced_login = True
        page.wait_for_timeout(1500)
    raise PublishHalt("登录或验证等待超时，发稿进度已保留")


def logged_in(page: Page) -> bool:
    return any_text_visible(page, LOGGED_IN_HINTS)


def challenge_visible(page: Page) -> bool:
    return any_text_visible(page, CHALLENGE_HINTS)


def any_text_visible(page: Page, texts: tuple[str, ...]) -> bool:
    for text in texts:
        locator = page.get_by_text(text, exact=False)
        try:
            if locator.count() and locator.first.is_visible():
                return True
        except Exception:
            continue
    return False


def dismiss_popups(page: Page) -> None:
    for name in AUTO_DISMISS_BUTTONS:
        click_button_if_visible(page, name)


def click_button_if_visible(page: Page, name: str) -> bool:
    locator = page.get_by_role("button", name=name)
    try:
        if locator.count() and locator.first.is_visible():
            locator.first.click()
            return True
    except Exception:
        return False
    return False


def click_first_visible_name(page: Page, names: tuple[str, ...]) -> bool:
    for name in names:
        if click_button_if_visible(page, name):
            return True
        text_locator = page.get_by_text(name, exact=True)
        try:
            if text_locator.count() and text_locator.first.is_visible():
                text_locator.first.click()
                return True
        except Exception:
            continue
    return False


def publish_manuscript(page: Page, manuscript: Manuscript, report: PublishReport, dry_run: bool) -> None:
    ensure_book(page, manuscript, report, dry_run=dry_run)
    if report.halted:
        return
    if dry_run and not manuscript.profile.book_id:
        print("干跑：尚未绑定平台作品，不对章节。", flush=True)
        return
    if not dry_run:
        rewrite_book_fields(page, manuscript, report)
    remote_chapters = list_remote_chapters(page)
    align_chapters(page, manuscript, remote_chapters, report, dry_run=dry_run)


def ensure_book(page: Page, manuscript: Manuscript, report: PublishReport, dry_run: bool) -> None:
    profile = manuscript.profile
    title = profile.field_text("作品名称")
    if profile.book_id:
        if not open_book_by_id_or_title(page, profile.book_id, title):
            raise PublishHalt(f"找不到已绑定作品 {profile.book_id}《{title}》")
        return
    if open_book_by_id_or_title(page, "", title):
        profile.book_id = extract_id(page.url, BOOK_ID_RE, BOOK_PATH_RE)
        save_profile(profile)
        print(f"按书名绑定已有平台作品 {profile.book_id}", flush=True)
        return
    missing = profile.missing_create_fields(manuscript.directory)
    if missing:
        report.missing_fields = missing
        raise PublishHalt("创建平台作品前书资料不完整")
    if dry_run:
        print(f"干跑：将创建平台作品《{title}》", flush=True)
        return
    if not click_first_visible_name(page, CREATE_BOOK_BUTTONS):
        raise PublishHalt("找不到「创建新书」")
    page.wait_for_timeout(800)
    fill_book_form(page, manuscript, report, creating=True)
    if report.missing_fields:
        save_profile(profile)
        raise PublishHalt("创建页还有未填的必填项，已写回书资料")
    if not click_first_visible_name(page, SUBMIT_BOOK_BUTTONS):
        raise PublishHalt("找不到创建提交按钮")
    click_button_if_visible(page, "确定")
    dismiss_popups(page)
    page.wait_for_timeout(1500)
    wait_until_logged_in(page, profile)
    profile.book_id = extract_id(page.url, BOOK_ID_RE, BOOK_PATH_RE)
    if not profile.book_id:
        open_book_by_id_or_title(page, "", title)
        profile.book_id = extract_id(page.url, BOOK_ID_RE, BOOK_PATH_RE)
    if not profile.book_id:
        raise PublishHalt("创建后读不到作品 ID，请打开作品管理确认后把 ID 填进书资料")
    report.created_book = True
    save_profile(profile)
    print(f"已创建平台作品 {profile.book_id}", flush=True)


def open_book_by_id_or_title(page: Page, book_id: str, title: str) -> bool:
    click_first_visible_name(page, BOOK_MANAGE_BUTTONS)
    page.wait_for_timeout(600)
    if title:
        search = page.get_by_placeholder(re.compile("搜索|书名|作品"))
        if search.count():
            search.first.fill(title)
            page.keyboard.press("Enter")
            page.wait_for_timeout(600)
        title_link = page.get_by_text(title, exact=True)
        if title_link.count() and title_link.first.is_visible():
            title_link.first.click()
            page.wait_for_timeout(800)
            return True
    if book_id:
        id_text = page.get_by_text(book_id, exact=False)
        if id_text.count() and id_text.first.is_visible():
            id_text.first.click()
            page.wait_for_timeout(800)
            return True
    return False


def rewrite_book_fields(page: Page, manuscript: Manuscript, report: PublishReport) -> None:
    if not click_first_visible_name(page, SETTINGS_BUTTONS):
        return
    page.wait_for_timeout(800)
    fill_book_form(page, manuscript, report, creating=False)
    click_first_visible_name(page, ("保存", "提交", "确定"))
    dismiss_popups(page)
    apply_serial_status(page, manuscript.profile, report)


def fill_book_form(page: Page, manuscript: Manuscript, report: PublishReport, creating: bool) -> None:
    profile = manuscript.profile
    discovered = discover_labels(page)
    merged = merge_discovered_fields(profile, discovered)
    if merged:
        report.discovered_fields.extend(merged)
        save_profile(profile)
    for key, aliases in FIELD_ALIASES.items():
        if key == "封面":
            cover = profile.cover_file(manuscript.directory)
            if cover is not None:
                upload_cover(page, aliases, cover, report)
            elif creating:
                report.missing_fields.append("封面")
            continue
        if key == "标签":
            fill_tags(page, aliases, profile.tag_list(), report)
            if creating and not profile.tag_list() and any(
                label in discovered for label in ("标签", "作品标签")
            ):
                report.missing_fields.append("标签")
            continue
        if key in {"频道", "分类", "子分类", "连载状态"}:
            value = profile.serial_status if key == "连载状态" else profile.field_text(key)
            if value:
                select_or_fill(page, aliases, value, report)
            continue
        value = profile.field_text(key)
        if value:
            fill_text_field(page, aliases, value, report)
    extra_keys = [key for key in profile.fields if key not in FIELD_ALIASES]
    for key in extra_keys:
        value = profile.field_text(key)
        if value:
            fill_text_field(page, (key,), value, report)
        elif creating and key in discovered:
            report.missing_fields.append(key)


def merge_discovered_fields(profile: BookProfile, discovered: list[str]) -> list[str]:
    added: list[str] = []
    known = set(profile.fields) | set(FIELD_ALIASES) | DISCOVERY_NOISE
    for label in discovered:
        if label in known or len(label) > 16:
            continue
        profile.fields[label] = profile.fields.get(label, "")
        added.append(label)
        known.add(label)
    return added


def discover_form_fields(page: Page, manuscript: Manuscript, report: PublishReport) -> None:
    if manuscript.profile.book_id:
        open_book_by_id_or_title(page, manuscript.profile.book_id, manuscript.profile.field_text("作品名称"))
        click_first_visible_name(page, SETTINGS_BUTTONS)
    else:
        if not click_first_visible_name(page, CREATE_BOOK_BUTTONS):
            raise PublishHalt("找不到「创建新书」，无法对照表单")
    page.wait_for_timeout(800)
    discovered = discover_labels(page)
    added = merge_discovered_fields(manuscript.profile, discovered)
    report.discovered_fields = discovered
    if added:
        save_profile(manuscript.profile)
        print("已把页面标签补进书资料，空着的请填完再 run。", flush=True)


def discover_labels(page: Page) -> list[str]:
    raw = page.evaluate(
        """() => {
          const labels = [];
          const seen = new Set();
          const add = (value) => {
            const text = (value || "").replace(/\\s+/g, " ").replace(/\\*/g, "").trim();
            if (!text || text.length > 16 || seen.has(text)) return;
            seen.add(text);
            labels.push(text);
          };
          document.querySelectorAll("label").forEach((el) => add(el.innerText));
          document.querySelectorAll('[class*="label"], [class*="Label"], [class*="form-item"]').forEach((el) => {
            add((el.innerText || "").split("\\n")[0]);
          });
          return labels;
        }"""
    )
    return [str(item) for item in raw if str(item)]


def fill_text_field(page: Page, aliases: tuple[str, ...], value: str, report: PublishReport) -> None:
    locator = locate_input(page, aliases)
    if locator is None:
        return
    if input_locked(locator):
        report.locked_fields.append(aliases[0])
        return
    locator.fill(value)


def select_or_fill(page: Page, aliases: tuple[str, ...], value: str, report: PublishReport) -> None:
    for alias in aliases:
        trigger = page.get_by_text(alias, exact=True)
        try:
            if trigger.count() and trigger.first.is_visible():
                if input_locked(trigger.first):
                    report.locked_fields.append(alias)
                    return
                trigger.first.click()
                page.wait_for_timeout(300)
                option = page.get_by_role("option", name=value)
                if option.count():
                    option.first.click()
                    return
                option_text = page.get_by_text(value, exact=True)
                if option_text.count():
                    option_text.first.click()
                    return
                page.keyboard.press("Escape")
                report.missing_fields.append(f"{alias}={value}")
                return
        except Exception:
            continue
    fill_text_field(page, aliases, value, report)


def fill_tags(page: Page, aliases: tuple[str, ...], tags: list[str], report: PublishReport) -> None:
    if not tags:
        return
    click_first_visible_name(page, aliases)
    for tag in tags:
        option = page.get_by_text(tag, exact=True)
        try:
            if option.count():
                option.first.click()
                continue
        except Exception:
            pass
        report.missing_fields.append(f"标签={tag}")


def upload_cover(page: Page, aliases: tuple[str, ...], cover: Path, report: PublishReport) -> None:
    file_input = page.locator('input[type="file"]')
    try:
        if file_input.count():
            file_input.first.set_input_files(str(cover))
            return
    except Exception:
        pass
    for alias in aliases:
        trigger = page.get_by_text(alias, exact=False)
        try:
            if trigger.count() and trigger.first.is_visible():
                with page.expect_file_chooser() as chooser_info:
                    trigger.first.click()
                chooser_info.value.set_files(str(cover))
                return
        except Exception:
            continue
    report.missing_fields.append("封面")


def locate_input(page: Page, aliases: tuple[str, ...]) -> Locator | None:
    for alias in aliases:
        by_label = page.get_by_label(alias, exact=False)
        if by_label.count():
            return by_label.first
        by_placeholder = page.get_by_placeholder(re.compile(re.escape(alias)))
        if by_placeholder.count():
            return by_placeholder.first
        label = page.get_by_text(alias, exact=True)
        if label.count():
            following = label.first.locator("xpath=following::input[1] | following::textarea[1]")
            if following.count():
                return following.first
    return None


def input_locked(locator: Locator) -> bool:
    try:
        if locator.is_disabled():
            return True
        readonly = locator.get_attribute("readonly")
        aria = locator.get_attribute("aria-disabled")
        return readonly is not None or aria == "true"
    except Exception:
        return False


def apply_serial_status(page: Page, profile: BookProfile, report: PublishReport) -> None:
    if profile.serial_status != SERIAL_FINISHED:
        return
    select_or_fill(page, FIELD_ALIASES["连载状态"], SERIAL_FINISHED, report)


def list_remote_chapters(page: Page) -> list[RemoteChapter]:
    click_first_visible_name(page, ("章节管理", "目录", "章节列表"))
    page.wait_for_timeout(800)
    scroll_until_stable(page)
    payload = page.evaluate(
        """() => {
          const rows = [];
          const nodes = Array.from(document.querySelectorAll("a, tr, li, div")).filter((el) =>
            /第\\d+章/.test(el.innerText || "")
          );
          const seen = new Set();
          for (const el of nodes) {
            const text = (el.innerText || "").replace(/\\s+/g, " ").trim();
            const titleLine = text.split(" ").slice(0, 8).join(" ");
            if (seen.has(titleLine) || titleLine.length > 80) continue;
            seen.add(titleLine);
            const href = el.href || (el.querySelector && el.querySelector("a") && el.querySelector("a").href) || "";
            rows.push({
              text,
              href,
              published: /已发布|已上线/.test(text),
            });
          }
          return rows;
        }"""
    )
    remotes: list[RemoteChapter] = []
    for row in payload:
        text = str(row.get("text") or "")
        href = str(row.get("href") or "")
        title = compact_chapter_title(text)
        if not title:
            continue
        remotes.append(
            RemoteChapter(
                title=title,
                published=bool(row.get("published")),
                chapter_id=extract_id(href, CHAPTER_ID_RE) or "",
            )
        )
    return remotes


def compact_chapter_title(text: str) -> str:
    match = re.search(r"第0*\d+章[^\n]*", text)
    if match:
        line = match.group(0)
        line = re.split(r"草稿|已发布|已上线|字", line)[0].strip()
        return line
    return ""


def scroll_until_stable(page: Page) -> None:
    previous = -1
    for _ in range(40):
        current = page.get_by_text(re.compile(r"第\d+章")).count()
        if current <= previous:
            return
        previous = current
        page.mouse.wheel(0, 2400)
        page.wait_for_timeout(350)


def align_chapters(
    page: Page,
    manuscript: Manuscript,
    remotes: list[RemoteChapter],
    report: PublishReport,
    dry_run: bool,
) -> None:
    profile = manuscript.profile
    budget = profile.max_chapters_per_run
    used = 0
    for chapter in manuscript.chapters:
        if challenge_visible(page):
            wait_until_logged_in(page, profile)
        remote = match_remote_chapter(chapter, remotes, profile)
        binding = profile.chapter_bindings.get(chapter.sequence)
        already_aligned = (
            binding is not None
            and binding.fingerprint == chapter.fingerprint
            and binding.visibility == profile.chapter_visibility
        )
        if remote and remote.published:
            local_title = chapter.title
            if local_title not in remote.title and remote.title not in local_title:
                report.published_mismatches.append(
                    f"第{chapter.sequence}章 本地《{chapter.title}》 / 远端「{remote.title}」"
                )
            else:
                report.skipped_sequences.append(chapter.sequence)
            continue
        if already_aligned and remote is not None:
            report.skipped_sequences.append(chapter.sequence)
            continue
        action = "新建" if remote is None else "更新草稿"
        if dry_run:
            print(f"干跑：{action}第{chapter.sequence}章《{chapter.title}》", flush=True)
            used += 1
            if used >= budget:
                print(f"干跑：已达单次上限 {budget}", flush=True)
                return
            continue
        write_chapter(page, chapter, remote, profile, report)
        used += 1
        save_profile(profile)
        if used >= budget:
            print(f"已达单次上限 {budget}，下次从剩余章节续。", flush=True)
            return
        page.wait_for_timeout(int(profile.delay_seconds * 1000))


def match_remote_chapter(chapter: Chapter, remotes: list[RemoteChapter], profile: BookProfile) -> RemoteChapter | None:
    binding = profile.chapter_bindings.get(chapter.sequence)
    if binding and binding.chapter_id:
        for remote in remotes:
            if remote.chapter_id == binding.chapter_id:
                return remote
    for remote in remotes:
        if chapter.title and chapter.title in remote.title:
            return remote
        numbered = CHAPTER_TITLE_RE.search(remote.title)
        if numbered and int(numbered.group(1)) == chapter.sequence:
            return remote
    return None


def write_chapter(
    page: Page,
    chapter: Chapter,
    remote: RemoteChapter | None,
    profile: BookProfile,
    report: PublishReport,
) -> None:
    if remote is None:
        if not click_first_visible_name(page, CREATE_CHAPTER_BUTTONS):
            raise PublishHalt("找不到「创建章节」")
        report.created_sequences.append(chapter.sequence)
    else:
        open_remote_chapter(page, remote)
        report.updated_sequences.append(chapter.sequence)
    page.wait_for_timeout(600)
    fill_chapter_title(page, chapter.title)
    fill_chapter_body(page, chapter.body)
    if profile.chapter_visibility == VISIBILITY_PUBLISH:
        if not click_first_visible_name(page, PUBLISH_BUTTONS):
            raise PublishHalt("找不到「发布」")
    else:
        if not click_first_visible_name(page, DRAFT_BUTTONS):
            if not click_first_visible_name(page, ("保存",)):
                raise PublishHalt("找不到「保存草稿」")
    click_button_if_visible(page, "确定")
    click_button_if_visible(page, "确认")
    dismiss_popups(page)
    page.wait_for_timeout(800)
    chapter_id = extract_id(page.url, CHAPTER_ID_RE) or (remote.chapter_id if remote else "")
    profile.set_binding(chapter.sequence, chapter_id, chapter.fingerprint, profile.chapter_visibility)
    print(f"已写入第{chapter.sequence}章《{chapter.title}》", flush=True)
    click_first_visible_name(page, ("返回", "章节管理", "目录"))


def open_remote_chapter(page: Page, remote: RemoteChapter) -> None:
    target = page.get_by_text(remote.title, exact=False)
    if target.count():
        target.first.click()
        return
    raise PublishHalt(f"打不开远端章节「{remote.title}」")


def fill_chapter_title(page: Page, title: str) -> None:
    locator = locate_input(page, ("章节标题", "标题", "章节名"))
    if locator is None:
        raise PublishHalt("找不到章节标题输入框")
    locator.fill(title)


def fill_chapter_body(page: Page, body: str) -> None:
    editor = locate_input(page, ("正文", "章节内容", "内容"))
    if editor is None:
        editable = page.locator("[contenteditable='true']")
        if editable.count():
            editor = editable.first
        else:
            textbox = page.get_by_role("textbox")
            if textbox.count() > 1:
                editor = textbox.nth(1)
            elif textbox.count() == 1:
                editor = textbox.first
    if editor is None:
        raise PublishHalt("找不到正文编辑器")
    try:
        editor.click()
        editor.fill(body)
        return
    except Exception:
        pass
    editor.click()
    page.keyboard.press("Meta+A")
    page.keyboard.insert_text(body)


def extract_id(url: str, *patterns: re.Pattern[str]) -> str:
    for pattern in patterns:
        match = pattern.search(url or "")
        if match:
            return match.group(1)
    return ""
