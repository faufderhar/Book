from __future__ import annotations

import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from publish.manuscript import (
    SERIAL_FINISHED,
    VISIBILITY_DRAFT,
    VISIBILITY_PUBLISH,
    VISIBILITY_SCHEDULE,
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
    CHAPTER_NUMBER_RE,
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

BOOK_MANAGE_URL = "https://fanqienovel.com/main/writer/book-manage?enter_from=book_detail"
WRITER_HOMES = (
    BOOK_MANAGE_URL,
    "https://writer.muyewx.com/",
    "https://fanqienovel.com/writer/zone/",
)

LOGGED_IN_HINTS = ("作品管理", "创建新书", "创建作品", "工作台", "章节管理")
CHALLENGE_HINTS = ("安全验证", "请完成验证", "验证码", "滑动验证")
CREATE_BOOK_BUTTONS = ("创建新书", "创建作品", "新建作品")
SUBMIT_BOOK_BUTTONS = ("立即创建", "提交并创建", "确认创建")
CREATE_CHAPTER_BUTTONS = ("创建章节", "新建章节", "写新章节")
DRAFT_BUTTONS = ("存草稿", "保存草稿")
PUBLISH_BUTTONS = ("发布", "立即发布")
NEXT_STEP_BUTTONS = ("下一步",)
CONFIRM_PUBLISH_BUTTONS = ("确认发布",)
BASIC_REVIEW_BUTTONS = ("仅基础检测",)
TYPO_CONFIRM_BUTTONS = ("提交", "确定", "确认提交", "继续提交")
TYPO_OVERLAY_HINTS = ("发布提示", "错别字未修改", "是否确定提交")
AUTO_DISMISS_BUTTONS = ("我知道了", "知道了")
CARD_OPEN_BUTTONS = ("章节管理", "作品设置")
SETTINGS_BUTTONS = ("作品信息", "作品设置", "编辑作品")
REVIEW_OVERLAY_SELECTORS = (
    ".auto-editor-error-modal",
    ".publish-modal-confirm",
    ".arco-modal",
    "[role='dialog']",
    ".ant-modal",
    ".semi-modal",
    ".publish-confirm-container-new",
)

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
WRITER_BOOK_PATH_RE = re.compile(r"/writer/(?:book-info|chapter-manage|preview)/(\d+)", re.I)
WRITER_PUBLISH_PATH_RE = re.compile(r"/main/writer/(\d+)/publish", re.I)
CHAPTER_ID_RE = re.compile(r"(?:chapterId|chapter_id|item_id|itemId)=(\d+)", re.I)
CHAPTER_PATH_RE = re.compile(r"/publish/(\d+)")
CHAPTER_TITLE_PLACEHOLDER = re.compile(r"标题")
BARE_PUBLISH_RE = re.compile(r"/publish/?(\?|$)")
BOOK_ID_PATTERNS = (BOOK_ID_RE, BOOK_PATH_RE, WRITER_BOOK_PATH_RE, WRITER_PUBLISH_PATH_RE)
CHAPTER_ID_PATTERNS = (CHAPTER_PATH_RE, CHAPTER_ID_RE)
BOOK_CARD_SELECTOR = '[id^="long-article-table-item-"]'
COLLECT_BOOK_CARDS_JS = """() => {
  const items = Array.from(document.querySelectorAll('[id^="long-article-table-item-"]'));
  return items.map((el) => {
    const idMatch = (el.id || "").match(/long-article-table-item-(\\d+)/);
    const titleNode = el.querySelector(".info-content-title .hoverup")
      || el.querySelector(".info-content-title");
    const workName = (titleNode ? titleNode.innerText : "")
      .replace(/\\s+/g, " ")
      .replace(/\\s*置顶\\s*/g, " ")
      .trim();
    let bookId = idMatch ? idMatch[1] : "";
    if (!bookId) {
      const hrefs = Array.from(el.querySelectorAll("a")).map((a) => a.href || "").join(" ");
      const hrefMatch = hrefs.match(/\\/writer\\/(?:book-info|chapter-manage|preview)\\/(\\d+)/)
        || hrefs.match(/\\/main\\/writer\\/(\\d+)\\/publish/);
      bookId = hrefMatch ? hrefMatch[1] : "";
    }
    return { book_id: bookId, work_name: workName, text: workName };
  }).filter((row) => row.book_id || row.work_name);
}"""


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
    watermark: int | None = None
    anchor_scheduled_at: str = ""

    def print_report(self) -> None:
        mode = "干跑" if self.dry_run else "发稿"
        print(f"== {mode}报告 ==", flush=True)
        if self.claimed_book_id and not self.created_book:
            print(f"认领平台作品 {self.claimed_book_id}", flush=True)
        if self.created_book:
            print("已创建平台作品", flush=True)
        if self.watermark is not None:
            print(
                f"后台水位：第{self.watermark}章，本次从第{self.watermark + 1}章起",
                flush=True,
            )
        if self.anchor_scheduled_at:
            print(
                f"目录最后定时：{self.anchor_scheduled_at}，其后按发稿时刻顺延",
                flush=True,
            )
        if self.created_sequences:
            print("新建章节：" + comma_sequences(self.created_sequences), flush=True)
        if self.updated_sequences:
            print("更新草稿：" + comma_sequences(self.updated_sequences), flush=True)
        if self.skipped_sequences:
            print("已发布跳过：" + comma_sequences(self.skipped_sequences), flush=True)
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


def run_list_platform_books(profile: BookProfile) -> tuple[SearchHit, ...]:
    from playwright.sync_api import sync_playwright

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
            open_writer_home(page, profile)
            hits = list_platform_books(page, profile)
            print(f"作品管理 {len(hits)} 本", flush=True)
            for hit in hits:
                name = hit.work_name or hit.row_text
                print(f"{hit.book_id} {name}".strip(), flush=True)
            return hits
        finally:
            context.close()


def writer_command_mode(*, discover_only: bool, dry_run: bool, allow_create: bool) -> CommandMode:
    if discover_only:
        return CommandMode(MODE_DISCOVER, allow_create=False)
    if dry_run:
        return CommandMode(MODE_DRY_RUN, allow_create=allow_create)
    return CommandMode(MODE_PUBLISH, allow_create=allow_create)


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
    deadline = time.monotonic() + max(timeout_ms, 0) / 1000
    announced_login = False
    announced_challenge = False
    while True:
        dismiss_popups(page)
        if challenge_visible(page):
            if time.monotonic() >= deadline:
                raise PublishHalt("登录或验证等待超时，发稿进度已保留")
            if not announced_challenge:
                print("请在浏览器里完成验证码或安全验证。", flush=True)
                announced_challenge = True
            page.wait_for_timeout(1500)
            continue
        if logged_in(page):
            return
        if time.monotonic() >= deadline:
            raise PublishHalt("登录或验证等待超时，发稿进度已保留")
        if not announced_login:
            print("请在弹出的浏览器里登录番茄作家账号（通常是扫码）。", flush=True)
            announced_login = True
        page.wait_for_timeout(1500)


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
        if click_button_if_visible(page, name):
            page.wait_for_timeout(300)
            return
    if click_overlay_name(page, AUTO_DISMISS_BUTTONS, page_wide=False):
        page.wait_for_timeout(300)


def click_button_if_visible(page: Page, name: str) -> bool:
    locator = page.get_by_role("button", name=name)
    try:
        if locator.count() and locator.first.is_visible() and locator.first.is_enabled():
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
            manuscript.profile.book_id = extract_book_id(page.url) or claim_plan.book_id
            save_profile(manuscript.profile)
        report.claimed_book_id = manuscript.profile.book_id
        print(f"认领平台作品 {manuscript.profile.book_id}", flush=True)
    if mode.kind == MODE_DISCOVER:
        discover_claimed_settings(page, manuscript, report)
        return
    remotes: tuple[RemoteChapter, ...] = ()
    catalog_ready = not (report.dry_run and created_this_run and not manuscript.profile.book_id)
    if catalog_ready:
        remotes = tuple(list_remote_chapters(page, manuscript.profile.book_id))
    full_plan = plan_publish(
        manuscript,
        mode,
        RemoteObservation(
            search_hits=hits,
            bound_book_openable=True,
            remote_chapters=remotes,
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
    if full_plan.halt_reason:
        report.halted = full_plan.halt_reason
        return
    execute_chapter_actions(page, manuscript, full_plan, report)


def observe_claim_state(page: Page, manuscript: Manuscript) -> tuple[tuple[SearchHit, ...], bool]:
    profile = manuscript.profile
    if profile.book_id:
        opened = open_bound_book(page, profile.book_id, profile)
        return (), opened
    return collect_search_hits(page, profile), True


def open_book_manage(page: Page, profile: BookProfile) -> None:
    page.goto(BOOK_MANAGE_URL, wait_until="domcontentloaded")
    wait_until_logged_in(page, profile)
    try:
        page.wait_for_selector(BOOK_CARD_SELECTOR, timeout=8000)
    except Exception:
        page.wait_for_timeout(800)


def list_platform_books(page: Page, profile: BookProfile) -> tuple[SearchHit, ...]:
    open_book_manage(page, profile)
    return _hits_from_cards(page)


def collect_search_hits(
    page: Page, profile: BookProfile, query: str | None = None
) -> tuple[SearchHit, ...]:
    open_book_manage(page, profile)
    needle = profile.field_text("作品名称") if query is None else query
    if needle:
        submit_work_search(page, needle)
    return _hits_from_cards(page)


def _hits_from_cards(page: Page) -> tuple[SearchHit, ...]:
    payload = page.evaluate(COLLECT_BOOK_CARDS_JS)
    rows = payload if isinstance(payload, list) else []
    return tuple(
        SearchHit(
            book_id=str(row.get("book_id") or ""),
            row_text=str(row.get("text") or row.get("work_name") or ""),
            work_name=str(row.get("work_name") or ""),
        )
        for row in rows
        if isinstance(row, dict)
    )


def submit_work_search(page: Page, query: str) -> None:
    box = page.get_by_placeholder(re.compile("搜索"))
    try:
        if box.count() and box.first.is_visible():
            box.first.fill(query)
            page.keyboard.press("Enter")
            page.wait_for_timeout(800)
    except Exception:
        return


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
        return open_bound_book(page, plan.book_id, manuscript.profile)
    claimed = next(
        (
            item
            for item in hits
            if item.work_name == title or is_exact_work_row(item.row_text, title)
        ),
        None,
    )
    if claimed:
        return open_search_hit(page, claimed)
    return open_book_by_id_or_title(page, "", title, manuscript.profile)


def open_search_hit(page: Page, hit: SearchHit) -> bool:
    if hit.book_id:
        card = page.locator(f"#long-article-table-item-{hit.book_id}")
        try:
            if card.count():
                for name in CARD_OPEN_BUTTONS:
                    target = card.get_by_text(name, exact=True)
                    if target.count() and target.first.is_visible():
                        target.first.click()
                        page.wait_for_timeout(800)
                        return True
        except Exception:
            pass
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
    opened = click_first_visible_name(page, CREATE_BOOK_BUTTONS)
    submitted = False
    if opened:
        page.wait_for_timeout(800)
        apply_planned_settings(page, manuscript, plan, report, creating=True)
        if not report.missing_fields:
            submitted = click_first_visible_name(page, SUBMIT_BOOK_BUTTONS)
            if submitted:
                click_button_if_visible(page, "确定")
                dismiss_popups(page)
                page.wait_for_timeout(1500)
                wait_until_logged_in(page, manuscript.profile)
    book_id = extract_book_id(page.url)
    if not book_id and submitted:
        title = manuscript.profile.field_text("作品名称")
        open_book_by_id_or_title(page, "", title, manuscript.profile)
        book_id = extract_book_id(page.url)
    if not book_id:
        book_id = wait_for_created_book_id(page, manuscript)
    manuscript.profile.book_id = book_id
    report.created_book = True
    report.claimed_book_id = book_id
    save_profile(manuscript.profile)
    print(f"已创建平台作品 {book_id}", flush=True)


def wait_for_created_book_id(page: Page, manuscript: Manuscript) -> str:
    print("自动创建走不通，请在打开的创建页手工建完。程序会回读作品 ID。", flush=True)
    profile = manuscript.profile
    timeout_ms = int(profile.human_wait_seconds * 1000)
    deadline = time.monotonic() + max(timeout_ms, 0) / 1000
    while True:
        book_id = extract_book_id(page.url)
        if book_id:
            return book_id
        if time.monotonic() >= deadline:
            raise PublishHalt("手工创建等待超时，请打开作品管理确认后把作品 ID 填进设置")
        page.wait_for_timeout(1500)


def discover_claimed_settings(page: Page, manuscript: Manuscript, report: PublishReport) -> None:
    if not open_book_settings(page, manuscript.profile.book_id):
        raise PublishHalt("找不到作品设置页")
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
    report.watermark = plan.watermark
    report.anchor_scheduled_at = plan.anchor_scheduled_at
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
    print(
        f"干跑：后台水位第{plan.watermark}章，本次从第{plan.watermark + 1}章起",
        flush=True,
    )
    if plan.anchor_scheduled_at:
        print(
            f"干跑：目录最后定时 {plan.anchor_scheduled_at}，其后按发稿时刻顺延",
            flush=True,
        )
    chapters = {chapter.sequence: chapter for chapter in manuscript.chapters}
    for action in plan.chapter_actions:
        chapter = chapters.get(action.sequence)
        title = chapter.title if chapter is not None else ""
        schedule = f" 定时 {action.scheduled_at}" if action.scheduled_at else ""
        if action.action == ACTION_CREATE_DRAFT:
            print(f"干跑：新建第{action.sequence}章《{title}》{schedule}", flush=True)
        elif action.action == ACTION_UPDATE_DRAFT:
            print(f"干跑：更新草稿第{action.sequence}章《{title}》{schedule}", flush=True)
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
        book_id = manuscript.profile.book_id or plan.book_id
        if not open_book_settings(page, book_id):
            if plan.fields_to_write or plan.cover_to_upload:
                raise PublishHalt("找不到作品设置页")
            return
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
    remotes = list_remote_chapters(page, manuscript.profile.book_id)
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
            if remote.published:
                report.published_mismatches.append(
                    action.reason or f"第{action.sequence}章 本地《{chapter.title}》 / 远端「{remote.title}」"
                )
                continue
            if action.chapter_id and not remote.chapter_id:
                remote = RemoteChapter(
                    title=remote.title,
                    chapter_id=action.chapter_id,
                    published=remote.published,
                    fingerprint=remote.fingerprint,
                    visibility=remote.visibility,
                    scheduled_at=remote.scheduled_at,
                )
        write_chapter(page, chapter, remote, manuscript.profile, report, action.scheduled_at)
        save_profile(manuscript.profile)
        if manuscript.profile.delay_seconds > 0:
            page.wait_for_timeout(int(manuscript.profile.delay_seconds * 1000))


def open_bound_book(page: Page, book_id: str, profile: BookProfile) -> bool:
    if not book_id:
        return False
    hits = collect_search_hits(page, profile, query=book_id)
    hit = next((item for item in hits if item.book_id == book_id), None)
    if hit and open_search_hit(page, hit):
        return True
    return False


def open_book_by_id_or_title(page: Page, book_id: str, title: str, profile: BookProfile) -> bool:
    hits = collect_search_hits(page, profile)
    if book_id:
        hit = next((item for item in hits if item.book_id == book_id), None)
        if hit and open_search_hit(page, hit):
            return True
    if title:
        hit = next(
            (
                item
                for item in hits
                if item.work_name == title or is_exact_work_row(item.row_text, title)
            ),
            None,
        )
        if hit and open_search_hit(page, hit):
            return True
        if click_exact_work_row(page, title):
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


def list_remote_chapters(page: Page, book_id: str = "") -> list[RemoteChapter]:
    if book_id:
        return_to_chapter_catalog(page, book_id)
    remotes: list[RemoteChapter] = []
    if not click_catalog_tab(page, ("章节管理",)):
        raise PublishHalt("打不开章节目录，未确认水位")
    remotes.extend(collect_catalog_rows(page))
    if click_catalog_tab(page, ("草稿箱",)):
        remotes.extend(collect_catalog_rows(page, published=False, visibility=VISIBILITY_DRAFT))
    click_catalog_tab(page, ("章节管理",))
    return unique_remote_chapters(remotes)


COLLECT_CHAPTER_ROWS_JS = """() => {
  const rows = [];
  const nodes = Array.from(document.querySelectorAll("a, tr, li, div")).filter((el) =>
    /第\\d+章/.test(el.innerText || "")
  );
  const seen = new Set();
  for (const el of nodes) {
    const box = el.getBoundingClientRect();
    if (box.width <= 0 || box.height <= 0) continue;
    const text = (el.innerText || "").replace(/\\s+/g, " ").trim();
    const titleLine = text.split(" ").slice(0, 8).join(" ");
    if (seen.has(titleLine) || titleLine.length > 80) continue;
    seen.add(titleLine);
    const publishLink = el.querySelector && el.querySelector('a[href*="/publish/"]');
    const anyLink = el.querySelector && el.querySelector("a");
    const href = el.href || (publishLink && publishLink.href) || (anyLink && anyLink.href) || "";
    rows.push({ text, href });
  }
  return rows;
}"""


def click_catalog_tab(page: Page, names: tuple[str, ...]) -> bool:
    for name in names:
        tab = page.get_by_role("tab", name=name, exact=True)
        try:
            if tab.count() and tab.first.is_visible():
                tab.first.click()
                page.wait_for_timeout(500)
                return True
        except Exception:
            continue
    return False


def collect_catalog_rows(
    page: Page,
    *,
    published: bool | None = None,
    visibility: str = "",
) -> list[RemoteChapter]:
    scroll_until_stable(page)
    payload = page.evaluate(COLLECT_CHAPTER_ROWS_JS)
    remotes: list[RemoteChapter] = []
    for row in payload:
        text = str(row.get("text") or "")
        href = str(row.get("href") or "")
        title = compact_chapter_title(text)
        if not title:
            continue
        is_published, row_visibility = catalog_row_status(text)
        if published is not None:
            is_published = published
        if visibility:
            row_visibility = visibility
        remotes.append(
            RemoteChapter(
                title=title,
                published=is_published,
                visibility=row_visibility,
                chapter_id=extract_chapter_id(href),
                scheduled_at="" if row_visibility == VISIBILITY_DRAFT else catalog_row_scheduled_at(text),
            )
        )
    return remotes


SCHEDULE_STAMP_RE = re.compile(r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2})")


def catalog_row_scheduled_at(text: str) -> str:
    match = SCHEDULE_STAMP_RE.search(text)
    return match.group(1) if match else ""


def catalog_row_status(text: str) -> tuple[bool, str]:
    if re.search(r"已发布|已上线", text):
        return True, "已发布"
    if "定时发布" in text or "待发布" in text:
        return False, VISIBILITY_SCHEDULE
    if re.search(r"草稿|未发布", text):
        return False, VISIBILITY_DRAFT
    return False, ""


def compact_chapter_title(text: str) -> str:
    match = re.search(r"第0*\d+章[^\n]*", text)
    if match:
        line = match.group(0)
        line = re.split(r"草稿|已发布|已上线|定时发布|待发布|字", line)[0].strip()
        return line
    return ""


def unique_remote_chapters(remotes: list[RemoteChapter]) -> list[RemoteChapter]:
    best: dict[int, RemoteChapter] = {}
    leftovers: list[RemoteChapter] = []
    for remote in remotes:
        numbered = CHAPTER_NUMBER_RE.search(remote.title)
        if numbered is None:
            leftovers.append(remote)
            continue
        sequence = int(numbered.group(1))
        current = best.get(sequence)
        if current is None:
            best[sequence] = remote
            continue
        if catalog_rank(remote) > catalog_rank(current):
            best[sequence] = remote
            continue
        if catalog_rank(remote) < catalog_rank(current):
            continue
        if remote.chapter_id and not current.chapter_id:
            best[sequence] = remote
            continue
        if current.chapter_id and not remote.chapter_id:
            continue
        if remote.scheduled_at and not current.scheduled_at:
            best[sequence] = remote
            continue
        if len(remote.title) < len(current.title):
            best[sequence] = remote
    return [best[sequence] for sequence in sorted(best)] + leftovers


def catalog_rank(remote: RemoteChapter) -> int:
    if remote.published:
        return 2
    if remote.visibility == VISIBILITY_SCHEDULE:
        return 1
    return 0


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
    scheduled_at: str = "",
) -> None:
    if remote is not None and remote.published:
        report.published_mismatches.append(
            f"第{chapter.sequence}章 本地《{chapter.title}》 / 远端「{remote.title}」"
        )
        return
    if remote is None:
        open_create_chapter(page, profile.book_id)
        report.created_sequences.append(chapter.sequence)
    else:
        open_remote_chapter(page, remote, profile.book_id)
        report.updated_sequences.append(chapter.sequence)
    wait_for_chapter_editor(page)
    binding = profile.chapter_bindings.get(chapter.sequence)
    body_already_aligned = binding is not None and binding.fingerprint == chapter.fingerprint
    if not body_already_aligned:
        fill_chapter_number(page, chapter.sequence)
        fill_chapter_title(page, chapter.title)
        fill_chapter_body(page, chapter.body)
        wait_for_cloud_save(page)
    dismiss_popups(page)
    submit_written_chapter(page, profile, scheduled_at)
    chapter_id = extract_chapter_id(page.url) or (remote.chapter_id if remote else "")
    profile.set_binding(
        chapter.sequence,
        chapter_id,
        chapter.fingerprint,
        profile.chapter_visibility,
        scheduled_at,
    )
    stamp = f" 定时 {scheduled_at}" if scheduled_at else ""
    print(f"已写入第{chapter.sequence}章《{chapter.title}》{stamp}", flush=True)
    return_to_chapter_catalog(page, profile.book_id)


def wait_for_cloud_save(page: Page) -> None:
    try:
        page.get_by_text("已保存到云端", exact=False).first.wait_for(state="visible", timeout=15_000)
    except Exception:
        page.wait_for_timeout(1500)


def submit_written_chapter(page: Page, profile: BookProfile, scheduled_at: str) -> None:
    if profile.chapter_visibility == VISIBILITY_DRAFT:
        save_chapter_draft(page)
        click_button_if_visible(page, "确定")
        click_button_if_visible(page, "确认")
        dismiss_popups(page)
        page.wait_for_timeout(800)
        return
    if profile.chapter_visibility not in {VISIBILITY_PUBLISH, VISIBILITY_SCHEDULE}:
        raise PublishHalt(f"不支持的章节可见性：{profile.chapter_visibility}")
    submit_publish_settings(page, scheduled_at if profile.chapter_visibility == VISIBILITY_SCHEDULE else "")


def submit_publish_settings(page: Page, scheduled_at: str) -> None:
    click_next_step(page)
    page.wait_for_timeout(800)
    wait_until_publish_settings(page)
    choose_not_using_ai(page)
    if scheduled_at:
        enable_timed_publish(page, scheduled_at)
    if not click_first_visible_name(page, CONFIRM_PUBLISH_BUTTONS):
        raise PublishHalt("找不到「确认发布」")
    page.wait_for_timeout(1500)
    dismiss_popups(page)


def click_next_step(page: Page) -> None:
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        dismiss_popups(page)
        next_button = page.locator("button.auto-editor-next, button.publish-button")
        try:
            if next_button.count() and next_button.first.is_visible() and next_button.first.is_enabled():
                next_button.first.click()
                return
        except Exception:
            pass
        if click_button_if_visible(page, "下一步"):
            return
        page.wait_for_timeout(400)
    if not click_first_visible_name(page, PUBLISH_BUTTONS):
        raise PublishHalt("找不到「下一步」")


def wait_until_publish_settings(page: Page) -> None:
    deadline = time.monotonic() + 25
    while time.monotonic() < deadline:
        dismiss_popups(page)
        if any_text_visible(page, ("发布设置",)):
            page.wait_for_timeout(300)
            return
        if any(overlay_contains(page, hint) for hint in TYPO_OVERLAY_HINTS):
            if not click_overlay_name(page, TYPO_CONFIRM_BUTTONS):
                raise PublishHalt("找不到错别字确认「提交」")
            page.wait_for_timeout(800)
            continue
        if any_text_visible(page, ("仅基础检测",)):
            click_first_visible_name(page, BASIC_REVIEW_BUTTONS)
            page.wait_for_timeout(800)
            continue
        page.wait_for_timeout(400)
    raise PublishHalt("找不到发布设置")


def click_overlay_name(page: Page, names: tuple[str, ...], *, page_wide: bool = True) -> bool:
    for selector in REVIEW_OVERLAY_SELECTORS:
        locator = page.locator(selector)
        try:
            if not locator.count():
                continue
            overlay = locator.last
            if not overlay.is_visible():
                continue
        except Exception:
            continue
        if selector in {".auto-editor-error-modal", ".publish-modal-confirm"}:
            primary = overlay.locator("button.arco-btn-primary")
            try:
                if primary.count() and primary.first.is_visible() and primary.first.is_enabled():
                    primary.first.click()
                    return True
            except Exception:
                pass
        for name in names:
            button = overlay.get_by_role("button", name=name, exact=True)
            try:
                if button.count() and button.first.is_visible() and button.first.is_enabled():
                    button.first.click()
                    return True
            except Exception:
                pass
            text_locator = overlay.get_by_text(name, exact=True)
            try:
                if text_locator.count() and text_locator.first.is_visible():
                    text_locator.first.click()
                    return True
            except Exception:
                continue
    if page_wide:
        return click_first_visible_name(page, names)
    return False


def overlay_contains(page: Page, needle: str) -> bool:
    for selector in REVIEW_OVERLAY_SELECTORS:
        locator = page.locator(selector)
        try:
            if not locator.count():
                continue
            target = locator.last
            if not target.is_visible():
                continue
            text = target.inner_text() or ""
        except Exception:
            continue
        if needle in text:
            return True
    return False


def choose_not_using_ai(page: Page) -> None:
    modal = publish_settings_modal(page)
    no_option = modal.get_by_text("否", exact=True)
    try:
        if no_option.count() and no_option.first.is_visible():
            no_option.first.click()
            page.wait_for_timeout(200)
    except Exception:
        return


def enable_timed_publish(page: Page, scheduled_at: str) -> None:
    date_text, time_text = split_schedule_stamp(scheduled_at)
    modal = publish_settings_modal(page)
    switch = modal.locator("button[role='switch']")
    try:
        if not switch.count():
            raise PublishHalt("找不到定时发布开关")
        checked = switch.first.get_attribute("aria-checked")
        if checked != "true":
            switch.first.click()
            page.wait_for_timeout(400)
    except PublishHalt:
        raise
    except Exception as error:
        raise PublishHalt("找不到定时发布开关") from error
    fill_picker_input(page, "请选择日期", date_text)
    fill_picker_input(page, "请选择时间", time_text)


def split_schedule_stamp(scheduled_at: str) -> tuple[str, str]:
    parts = scheduled_at.strip().split(" ", 1)
    if len(parts) != 2:
        raise PublishHalt(f"定时时刻格式无效：{scheduled_at}")
    return parts[0], parts[1]


def fill_picker_input(page: Page, placeholder: str, value: str) -> None:
    locator = page.get_by_placeholder(placeholder)
    if not locator.count():
        raise PublishHalt(f"找不到{placeholder}")
    target = locator.first
    target.click()
    page.wait_for_timeout(200)
    target.fill(value)
    page.keyboard.press("Enter")
    page.wait_for_timeout(200)
    current = target.input_value() if hasattr(target, "input_value") else ""
    if value not in (current or ""):
        page.evaluate(
            """({placeholder, value}) => {
              const input = Array.from(document.querySelectorAll("input")).find(
                (el) => (el.placeholder || "") === placeholder
              );
              if (!input) return;
              const proto = Object.getPrototypeOf(input);
              const descriptor = Object.getOwnPropertyDescriptor(proto, "value")
                || Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value");
              if (descriptor && descriptor.set) {
                descriptor.set.call(input, value);
              } else {
                input.value = value;
              }
              input.dispatchEvent(new Event("input", { bubbles: true }));
              input.dispatchEvent(new Event("change", { bubbles: true }));
            }""",
            {"placeholder": placeholder, "value": value},
        )


def publish_settings_modal(page: Page) -> Locator:
    return page.locator(".publish-confirm-container-new, [role='dialog']").last


def open_create_chapter(page: Page, book_id: str) -> None:
    href = create_chapter_href(page)
    if not href and book_id:
        href = f"https://fanqienovel.com/main/writer/{book_id}/publish/?enter_from=newchapter"
    if href:
        same_tab_goto(page, href)
        return
    if not click_first_visible_name(page, CREATE_CHAPTER_BUTTONS):
        raise PublishHalt("找不到「创建章节」")


def create_chapter_href(page: Page) -> str:
    payload = page.evaluate(
        """() => Array.from(document.querySelectorAll('a[href*="/publish/"]')).map((a) => a.href || "")"""
    )
    hrefs = [str(item) for item in payload] if isinstance(payload, list) else []
    for href in hrefs:
        if "newchapter" in href or "newdraft" in href:
            return href
        if BARE_PUBLISH_RE.search(href) and not CHAPTER_PATH_RE.search(href):
            return href
    return ""


def catalog_tab_for_remote(remote: RemoteChapter) -> str:
    if remote.visibility == VISIBILITY_DRAFT:
        return "草稿箱"
    return "章节管理"


def open_remote_chapter(page: Page, remote: RemoteChapter, book_id: str = "") -> None:
    if remote.chapter_id and book_id:
        enter_from = "modifychapter" if remote.published else "modifydraft"
        same_tab_goto(
            page,
            f"https://fanqienovel.com/main/writer/{book_id}/publish/{remote.chapter_id}/?enter_from={enter_from}",
        )
        return
    if book_id:
        return_to_chapter_catalog(page, book_id)
        click_catalog_tab(page, (catalog_tab_for_remote(remote),))
    target = page.get_by_text(remote.title, exact=False)
    try:
        if target.count() and target.first.is_visible():
            target.first.click()
            return
    except Exception:
        pass
    raise PublishHalt(f"打不开远端章节「{remote.title}」")


def wait_for_chapter_editor(page: Page) -> None:
    try:
        page.wait_for_url(re.compile(r"/publish/"), timeout=15_000)
    except Exception:
        pass
    dismiss_popups(page)
    locator = page.get_by_placeholder(CHAPTER_TITLE_PLACEHOLDER)
    try:
        locator.first.wait_for(state="visible", timeout=15_000)
    except Exception:
        raise PublishHalt("找不到章节标题输入框")


def fill_chapter_number(page: Page, sequence: int) -> None:
    number = page.locator("input.serial-input:not(.serial-editor-input-hint-area)")
    try:
        if number.count() and number.first.is_visible():
            number.first.fill(str(sequence))
    except Exception:
        return


def fill_chapter_title(page: Page, title: str) -> None:
    locator = page.get_by_placeholder(CHAPTER_TITLE_PLACEHOLDER)
    if locator.count():
        locator.first.fill(title)
        return
    located = locate_input(page, ("章节标题", "标题", "章节名"))
    if located is None:
        raise PublishHalt("找不到章节标题输入框")
    located.fill(title)


def same_tab_goto(page: Page, href: str) -> None:
    page.goto(href, wait_until="domcontentloaded")


def chapter_catalog_url(book_id: str) -> str:
    return f"https://fanqienovel.com/main/writer/chapter-manage/{book_id}?type=1"


def open_book_settings(page: Page, book_id: str) -> bool:
    """打开作品设置页：有作品 ID 时直达 book-info，否则再点页面入口。"""
    if book_id:
        same_tab_goto(page, f"https://fanqienovel.com/main/writer/book-info/{book_id}?type=2")
        page.wait_for_timeout(800)
        return True
    if click_first_visible_name(page, SETTINGS_BUTTONS):
        page.wait_for_timeout(800)
        return True
    return False


def return_to_chapter_catalog(page: Page, book_id: str) -> None:
    if book_id:
        same_tab_goto(page, chapter_catalog_url(book_id))
        page.wait_for_timeout(800)
        return
    click_first_visible_name(page, ("返回", "章节管理", "目录"))


def fill_chapter_body(page: Page, body: str) -> None:
    prose = page.locator(".ProseMirror[contenteditable='true']")
    editor = prose.first if prose.count() else None
    if editor is None:
        editable = page.locator("[contenteditable='true']")
        editor = editable.first if editable.count() else None
    if editor is None:
        raise PublishHalt("找不到正文编辑器")
    editor.click()
    page.wait_for_timeout(200)
    page.keyboard.press(select_all_key())
    page.keyboard.insert_text(body)
    snippet = body[:12].replace("\n", "")
    if snippet and snippet not in (editor.inner_text() or "").replace("\n", ""):
        page.evaluate(
            """(text) => {
              const el = document.querySelector(".ProseMirror[contenteditable='true']");
              if (!el) return;
              el.focus();
              document.execCommand("selectAll", false);
              document.execCommand("insertText", false, text);
            }""",
            body,
        )


def save_chapter_draft(page: Page) -> None:
    try:
        page.wait_for_function(
            """() => {
              const buttons = Array.from(document.querySelectorAll("button"));
              const target = buttons.find((el) => (el.innerText || "").trim() === "存草稿");
              return Boolean(target && !target.disabled);
            }""",
            timeout=20_000,
        )
    except Exception as error:
        raise PublishHalt("找不到「保存草稿」") from error
    if not click_first_visible_name(page, DRAFT_BUTTONS):
        raise PublishHalt("找不到「保存草稿」")


def select_all_key() -> str:
    return "Meta+A" if sys.platform == "darwin" else "Control+A"


def extract_book_id(url: str) -> str:
    return extract_id(url, *BOOK_ID_PATTERNS)


def extract_chapter_id(url: str) -> str:
    return extract_id(url, *CHAPTER_ID_PATTERNS)


def extract_id(url: str, *patterns: re.Pattern[str]) -> str:
    for pattern in patterns:
        match = pattern.search(url or "")
        if match:
            return match.group(1)
    return ""
