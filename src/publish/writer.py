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
from publish.plan import (
    ACTION_CREATE_DRAFT,
    ACTION_PUBLISHED_MISMATCH,
    ACTION_SKIP,
    ACTION_UPDATE_DRAFT,
    MODE_DISCOVER,
    MODE_DRY_RUN,
    MODE_PUBLISH,
    CommandMode,
    PublishPlan,
    RemoteChapter,
    RemoteObservation,
    SearchHit,
    is_exact_work_row,
    plan_publish,
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


class PublishHalt(RuntimeError):
    """登录、风控或后台表单缺字段，发稿应停并保留进度。"""



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
    claimed_book_id: str = ""
    extra_remote_chapters: list[str] = field(default_factory=list)

    def print_report(self) -> None:
        mode = "干跑" if self.dry_run else "发稿"
        print(f"== {mode}报告 ==", flush=True)
        if self.claimed_book_id and not self.created_book:
            print(f"认领平台作品 {self.claimed_book_id}", flush=True)
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
        for title in self.extra_remote_chapters:
            print(f"远端多余章：{title}", flush=True)
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


def run_publish(
    manuscript: Manuscript,
    dry_run: bool = False,
    discover_only: bool = False,
    allow_create: bool = False,
) -> PublishReport:
    from playwright.sync_api import sync_playwright

    mode = writer_command_mode(discover_only=discover_only, dry_run=dry_run, allow_create=allow_create)
    report = PublishReport(dry_run=dry_run or discover_only)
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
            execute_planned_publish(page, manuscript, mode, report)
        except PublishHalt as halted:
            report.halted = str(halted)
        finally:
            save_profile(manuscript.profile)
            context.close()
    report.print_report()
    return report


def writer_command_mode(*, discover_only: bool, dry_run: bool, allow_create: bool) -> CommandMode:
    if discover_only:
        return CommandMode(MODE_DISCOVER, allow_create=False)
    if dry_run:
        return CommandMode(MODE_DRY_RUN, allow_create=allow_create)
    return CommandMode(MODE_PUBLISH, allow_create=allow_create)


def execute_planned_publish(
    page: Page,
    manuscript: Manuscript,
    mode: CommandMode,
    report: PublishReport,
) -> None:
    hits, bound_openable = observe_claim_state(page, manuscript)
    claim_plan = plan_publish(
        manuscript,
        mode,
        RemoteObservation(search_hits=hits, bound_book_openable=bound_openable),
    )
    report.missing_fields = list(claim_plan.missing_fields)
    if claim_plan.halt_reason:
        report.halted = claim_plan.halt_reason
        if claim_plan.candidates:
            print("候选：" + "、".join(hit.row_text for hit in claim_plan.candidates), flush=True)
        return
    created_this_run = False
    if claim_plan.create:
        if report.dry_run:
            print(f"干跑：将创建平台作品《{manuscript.profile.field_text('作品名称')}》", flush=True)
            created_this_run = True
        else:
            create_platform_book(page, manuscript, claim_plan, report)
            created_this_run = True
    else:
        if not open_claimed_book(page, manuscript, claim_plan, hits):
            title = manuscript.profile.field_text("作品名称")
            raise PublishHalt(f"找不到已认领作品 {claim_plan.book_id}《{title}》")
        if not manuscript.profile.book_id:
            manuscript.profile.book_id = extract_id(page.url, BOOK_ID_RE, BOOK_PATH_RE) or claim_plan.book_id
            save_profile(manuscript.profile)
        report.claimed_book_id = manuscript.profile.book_id
        print(f"认领平台作品 {manuscript.profile.book_id}", flush=True)
    if mode.kind == MODE_DISCOVER:
        discover_claimed_settings(page, manuscript, report)
        return
    remotes: tuple[RemoteChapter, ...] = ()
    form_labels: tuple[str, ...] = ()
    catalog_ready = not (report.dry_run and created_this_run and not manuscript.profile.book_id)
    if catalog_ready:
        remotes = tuple(list_remote_chapters(page))
        if click_first_visible_name(page, SETTINGS_BUTTONS):
            page.wait_for_timeout(800)
            form_labels = tuple(discover_labels(page))
    full_plan = plan_publish(
        manuscript,
        mode,
        RemoteObservation(
            search_hits=hits,
            bound_book_openable=True,
            remote_chapters=remotes,
            form_labels=form_labels,
            catalog_observed=True,
            created_this_run=created_this_run,
        ),
    )
    apply_plan_report(full_plan, report)
    if report.dry_run:
        preview_chapter_plan(full_plan, manuscript)
        if full_plan.halt_reason:
            report.halted = full_plan.halt_reason
        return
    apply_planned_settings(page, manuscript, full_plan, report, creating=False)
    if full_plan.halt_reason:
        report.halted = full_plan.halt_reason
        return
    execute_chapter_actions(page, manuscript, full_plan, report)


def observe_claim_state(page: Page, manuscript: Manuscript) -> tuple[tuple[SearchHit, ...], bool]:
    profile = manuscript.profile
    title = profile.field_text("作品名称")
    if profile.book_id:
        opened = open_book_by_id_or_title(page, profile.book_id, title)
        return (), opened
    return collect_search_hits(page, title), True


def collect_search_hits(page: Page, title: str) -> tuple[SearchHit, ...]:
    click_first_visible_name(page, BOOK_MANAGE_BUTTONS)
    page.wait_for_timeout(600)
    if title:
        search = page.get_by_placeholder(re.compile("搜索|书名|作品"))
        if search.count():
            search.first.fill(title)
            page.keyboard.press("Enter")
            page.wait_for_timeout(600)
    payload = page.evaluate(
        """(workTitle) => {
          const rows = [];
          const seen = new Set();
          const nodes = Array.from(document.querySelectorAll("a, tr, li, div"));
          for (const el of nodes) {
            const text = (el.innerText || "").replace(/\s+/g, " ").trim();
            if (!text || text.length > 80 || !text.includes(workTitle) || seen.has(text)) continue;
            seen.add(text);
            const href = el.href || (el.querySelector && el.querySelector("a") && el.querySelector("a").href) || "";
            const idMatch = href.match(/[?&](?:bookId|book_id|novel_id)=(\d+)/i)
              || href.match(/\/book(?:Id)?\/(\d+)/i);
            rows.push({ text, book_id: idMatch ? idMatch[1] : "" });
          }
          return rows;
        }""",
        title,
    )
    return tuple(
        SearchHit(book_id=str(row.get("book_id") or ""), row_text=str(row.get("text") or ""))
        for row in payload
    )


def open_claimed_book(
    page: Page,
    manuscript: Manuscript,
    plan: PublishPlan,
    hits: tuple[SearchHit, ...],
) -> bool:
    title = manuscript.profile.field_text("作品名称")
    if plan.book_id:
        hit = next((item for item in hits if item.book_id == plan.book_id), None)
        if hit and open_search_hit(page, hit):
            return True
        return open_book_by_id_or_title(page, plan.book_id, title)
    claimed = next((item for item in hits if is_exact_work_row(item.row_text, title)), None)
    if claimed:
        return open_search_hit(page, claimed)
    return open_book_by_id_or_title(page, "", title)


def open_search_hit(page: Page, hit: SearchHit) -> bool:
    if hit.book_id:
        id_text = page.get_by_text(hit.book_id, exact=False)
        try:
            if id_text.count() and id_text.first.is_visible():
                id_text.first.click()
                page.wait_for_timeout(800)
                return True
        except Exception:
            pass
    if hit.row_text:
        locator = page.get_by_text(hit.row_text, exact=False)
        try:
            if locator.count() and locator.first.is_visible():
                locator.first.click()
                page.wait_for_timeout(800)
                return True
        except Exception:
            pass
    return False


def create_platform_book(page: Page, manuscript: Manuscript, plan: PublishPlan, report: PublishReport) -> None:
    if not click_first_visible_name(page, CREATE_BOOK_BUTTONS):
        raise PublishHalt("找不到「创建新书」")
    page.wait_for_timeout(800)
    apply_planned_settings(page, manuscript, plan, report, creating=True)
    if report.missing_fields:
        save_profile(manuscript.profile)
        raise PublishHalt("创建页还有未填的必填项，已写回书资料")
    if not click_first_visible_name(page, SUBMIT_BOOK_BUTTONS):
        raise PublishHalt("找不到创建提交按钮")
    click_button_if_visible(page, "确定")
    dismiss_popups(page)
    page.wait_for_timeout(1500)
    wait_until_logged_in(page, manuscript.profile)
    title = manuscript.profile.field_text("作品名称")
    manuscript.profile.book_id = extract_id(page.url, BOOK_ID_RE, BOOK_PATH_RE)
    if not manuscript.profile.book_id:
        open_book_by_id_or_title(page, "", title)
        manuscript.profile.book_id = extract_id(page.url, BOOK_ID_RE, BOOK_PATH_RE)
    if not manuscript.profile.book_id:
        raise PublishHalt("创建后读不到作品 ID，请打开作品管理确认后把 ID 填进书资料")
    report.created_book = True
    save_profile(manuscript.profile)
    print(f"已创建平台作品 {manuscript.profile.book_id}", flush=True)


def discover_claimed_settings(page: Page, manuscript: Manuscript, report: PublishReport) -> None:
    if not click_first_visible_name(page, SETTINGS_BUTTONS):
        raise PublishHalt("找不到作品设置页")
    page.wait_for_timeout(800)
    discovered = discover_labels(page)
    added = merge_discovered_fields(manuscript.profile, discovered)
    report.discovered_fields = discovered
    if added:
        save_profile(manuscript.profile)
        print("已把页面标签补进书资料，空着的请填完再 run。", flush=True)


def apply_plan_report(plan: PublishPlan, report: PublishReport) -> None:
    report.locked_fields = list(plan.locked_fields)
    if plan.missing_fields:
        report.missing_fields = list(plan.missing_fields)
    report.extra_remote_chapters = [item.title for item in plan.extra_remote_chapters]
    for action in plan.chapter_actions:
        if action.action == ACTION_SKIP:
            report.skipped_sequences.append(action.sequence)
        elif action.action == ACTION_PUBLISHED_MISMATCH:
            report.published_mismatches.append(action.reason or f"第{action.sequence}章")
        elif report.dry_run and action.action == ACTION_CREATE_DRAFT:
            report.created_sequences.append(action.sequence)
        elif report.dry_run and action.action == ACTION_UPDATE_DRAFT:
            report.updated_sequences.append(action.sequence)


def preview_chapter_plan(plan: PublishPlan, manuscript: Manuscript) -> None:
    chapters = {chapter.sequence: chapter for chapter in manuscript.chapters}
    for action in plan.chapter_actions:
        chapter = chapters.get(action.sequence)
        title = chapter.title if chapter is not None else ""
        if action.action == ACTION_CREATE_DRAFT:
            print(f"干跑：新建第{action.sequence}章《{title}》", flush=True)
        elif action.action == ACTION_UPDATE_DRAFT:
            print(f"干跑：更新草稿第{action.sequence}章《{title}》", flush=True)
    if plan.halt_reason:
        print(f"干跑：{plan.halt_reason}", flush=True)


def apply_planned_settings(
    page: Page,
    manuscript: Manuscript,
    plan: PublishPlan,
    report: PublishReport,
    creating: bool,
) -> None:
    if not creating:
        if not click_first_visible_name(page, SETTINGS_BUTTONS):
            return
        page.wait_for_timeout(800)
    for key in plan.empty_keys_to_add:
        if key not in manuscript.profile.fields:
            manuscript.profile.fields[key] = ""
    if plan.cover_to_upload:
        cover_path = manuscript.directory / plan.cover_to_upload
        if cover_path.is_file():
            upload_cover(page, FIELD_ALIASES["封面"], cover_path, report)
            if not manuscript.profile.field_text("封面"):
                manuscript.profile.fields["封面"] = plan.cover_to_upload
    for key, value in plan.fields_to_write.items():
        if key in plan.locked_fields:
            continue
        aliases = FIELD_ALIASES.get(key, (key,))
        if key == "标签":
            tags = list(value) if isinstance(value, list) else [str(value)]
            fill_tags(page, aliases, [str(tag) for tag in tags], report)
            continue
        if key in {"频道", "分类", "子分类", "连载状态"}:
            select_or_fill(page, aliases, str(value), report)
            continue
        fill_text_field(page, aliases, str(value), report)
    if not creating:
        click_first_visible_name(page, ("保存", "提交", "确定"))
        dismiss_popups(page)
        if "连载状态" in plan.fields_to_write:
            apply_serial_status(page, manuscript.profile, report)
    save_profile(manuscript.profile)
    report.locked_fields = list(dict.fromkeys([*report.locked_fields, *plan.locked_fields]))


def execute_chapter_actions(
    page: Page,
    manuscript: Manuscript,
    plan: PublishPlan,
    report: PublishReport,
) -> None:
    remotes = list_remote_chapters(page)
    remote_by_id = {item.chapter_id: item for item in remotes if item.chapter_id}
    chapters = {chapter.sequence: chapter for chapter in manuscript.chapters}
    for action in plan.chapter_actions:
        if action.action in {ACTION_SKIP, ACTION_PUBLISHED_MISMATCH}:
            continue
        chapter = chapters[action.sequence]
        if challenge_visible(page):
            wait_until_logged_in(page, manuscript.profile)
        remote = None
        if action.action != ACTION_CREATE_DRAFT:
            remote = remote_by_id.get(action.chapter_id)
            if remote is None:
                remote = next((item for item in remotes if chapter.title and chapter.title in item.title), None)
            if remote is None:
                raise PublishHalt(f"找不到要更新的草稿第{action.sequence}章《{chapter.title}》")
        write_chapter(page, chapter, remote, manuscript.profile, report)
        save_profile(manuscript.profile)
        page.wait_for_timeout(int(manuscript.profile.delay_seconds * 1000))


def open_book_by_id_or_title(page: Page, book_id: str, title: str) -> bool:
    click_first_visible_name(page, BOOK_MANAGE_BUTTONS)
    page.wait_for_timeout(600)
    if title:
        search = page.get_by_placeholder(re.compile("搜索|书名|作品"))
        if search.count():
            search.first.fill(title)
            page.keyboard.press("Enter")
            page.wait_for_timeout(600)
        if click_exact_work_row(page, title):
            return True
    if book_id:
        id_text = page.get_by_text(book_id, exact=False)
        if id_text.count() and id_text.first.is_visible():
            id_text.first.click()
            page.wait_for_timeout(800)
            return True
    return False


def click_exact_work_row(page: Page, title: str) -> bool:
    texts = page.evaluate(
        """() => Array.from(document.querySelectorAll("a, tr, li, div"))
            .map((el) => (el.innerText || "").replace(/\\s+/g, " ").trim())
            .filter((text) => text && text.length <= 80)"""
    )
    for text in texts:
        row_text = str(text)
        if not is_exact_work_row(row_text, title):
            continue
        locator = page.get_by_text(row_text, exact=False)
        try:
            if locator.count() and locator.first.is_visible():
                locator.first.click()
                page.wait_for_timeout(800)
                return True
        except Exception:
            continue
    return False


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
