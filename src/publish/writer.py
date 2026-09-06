from __future__ import annotations

import re
import sys
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from book.jobs import TaskProgress
from publish.manuscript import (
    SERIAL_FINISHED,
    VISIBILITY_DRAFT,
    VISIBILITY_PUBLISH,
    VISIBILITY_SCHEDULE,
    BookProfile,
    Chapter,
    Manuscript,
    browser_profile_dir,
    platform_chapter_title,
    save_profile,
)
from publish.plan import (
    ACTION_CREATE_DRAFT,
    ACTION_PUBLISHED_MISMATCH,
    ACTION_SKIP,
    ACTION_UPDATE_DRAFT,
    ACTION_UPDATE_VISIBILITY,
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
REVIEW_CHOICE_HINTS = ("请选择内容检测方式", "内容检测方式")
TYPO_CONFIRM_BUTTONS = ("提交", "确定", "确认", "确认提交", "继续提交", "仍要提交", "仍然提交")
TYPO_OVERLAY_HINTS = ("发布提示", "错别字未修改", "是否确定提交")
TITLE_TOO_SHORT_HINTS = ("章节名字数小于",)
AUTO_DISMISS_BUTTONS = ("我知道了", "知道了")
TOUR_SELECTORS = (".publish-tour-guide", ".reactour__helper", ".reactour__mask")
TOUR_BUTTON_CLASS = "guide-card-footer-btn"
TOUR_SKIP_BUTTONS = ("跳过", "我知道了", "知道了", "完成", "下一步")
CATALOG_TAB_WAIT_SECONDS = 12.0
CATALOG_ROW_WAIT_SECONDS = 8.0
CATALOG_SWITCH_POLLS = 20
CATALOG_SCROLL_STEPS = 60
CATALOG_SCROLL_IDLE_STEPS = 3
CATALOG_PAGE_POLLS = 20
CATALOG_VOLUME_POLLS = 10
CREATE_CHAPTER_POLLS = 20
CREATE_CHAPTER_MISSING = ""
CREATE_CHAPTER_STUCK = "stuck"
CREATE_CHAPTER_OK = "ok"
CATALOG_ROW_RE = re.compile(r"第\d+章")
CARD_OPEN_BUTTONS = ("章节管理", "作品设置")
SETTINGS_BUTTONS = ("作品信息", "作品设置", "编辑作品")
NAVIGATION_TIMEOUT_MS = 30_000
STEP_INTERVAL_MS = 2_000
OVERLAY_CLICK_TIMEOUT_MS = 800
OVERLAY_POLL_MS = 150
OVERLAY_SETTLE_MS = 800
NAVIGATION_ATTEMPTS = 2
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


def _task_progress(progress: TaskProgress | None) -> TaskProgress:
    return progress or TaskProgress([])


def _executable_chapter_count(plan: PublishPlan) -> int:
    return sum(
        1
        for action in plan.chapter_actions
        if action.action not in {ACTION_SKIP, ACTION_PUBLISHED_MISMATCH}
    )


def _catalog_halt(progress: TaskProgress | None, reason: str) -> None:
    _task_progress(progress).fail("catalog", note=reason)
    raise PublishHalt(reason)


def _finish_catalog(progress: TaskProgress, remotes: Sequence[RemoteChapter]) -> None:
    pages = 0
    for item in progress.snapshot():
        if item["key"] == "catalog":
            pages = item["total"]
            break
    progress.finish("catalog", note=f"{pages}/{pages} 页 · {len(remotes)} 章")


def run_publish(
    manuscript: Manuscript,
    dry_run: bool = False,
    discover_only: bool = False,
    allow_create: bool = False,
    progress: TaskProgress | None = None,
) -> PublishReport:
    from playwright.sync_api import sync_playwright

    progress = _task_progress(progress)
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
            page.set_default_navigation_timeout(NAVIGATION_TIMEOUT_MS)
            progress.begin("login")
            open_writer_home(page, manuscript.profile)
            progress.finish("login")
            execute_planned_publish(page, manuscript, mode, report, progress)
        except PublishHalt as halted:
            report.halted = str(halted)
        except Exception as error:
            report.halted = str(error) or type(error).__name__
        finally:
            save_profile(manuscript.profile)
            context.close()
    report.print_report()
    return report


def run_list_platform_books(
    profile: BookProfile,
    progress: TaskProgress | None = None,
) -> tuple[SearchHit, ...]:
    progress = _task_progress(progress)
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
            page.set_default_navigation_timeout(NAVIGATION_TIMEOUT_MS)
            progress.begin("login")
            open_writer_home(page, profile)
            progress.finish("login")
            progress.begin("books")
            hits = list_platform_books(page, profile)
            progress.finish("books", note=f"{len(hits)} 本")
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
    # 三个入口轮着试，但「请扫码」只对人说一次。
    announced: set[str] = set()
    for url in WRITER_HOMES:
        try:
            page.goto(url, wait_until="domcontentloaded")
            wait_until_logged_in(page, profile, announced=announced)
            return
        except PublishHalt as halted:
            raise halted
        except Exception as error:
            last_error = str(error)
            continue
    raise PublishHalt(f"打不开作家后台：{last_error}")


def wait_until_logged_in(
    page: Page,
    profile: BookProfile,
    announced: set[str] | None = None,
) -> None:
    timeout_ms = int(profile.human_wait_seconds * 1000)
    deadline = time.monotonic() + max(timeout_ms, 0) / 1000
    said = announced if announced is not None else set()
    while True:
        dismiss_popups(page)
        if challenge_visible(page):
            if time.monotonic() >= deadline:
                raise PublishHalt("登录或验证等待超时，发稿进度已保留")
            if "challenge" not in said:
                print("请在浏览器里完成验证码或安全验证。", flush=True)
                said.add("challenge")
            page.wait_for_timeout(1500)
            continue
        if logged_in(page):
            return
        if time.monotonic() >= deadline:
            raise PublishHalt("登录或验证等待超时，发稿进度已保留")
        if "login" not in said:
            print("请在弹出的浏览器里登录番茄作家账号（通常是扫码）。", flush=True)
            said.add("login")
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
    if dismiss_tour_guide(page):
        return
    for name in AUTO_DISMISS_BUTTONS:
        if click_button_if_visible(page, name):
            page.wait_for_timeout(300)
            return
    if click_overlay_name(page, AUTO_DISMISS_BUTTONS, page_wide=False):
        page.wait_for_timeout(300)


def dismiss_tour_guide(page: Page) -> bool:
    """新手引导（reactour）带整屏遮罩，会把真按钮的点击吃掉，自己还有个「下一步」。先走完它。"""
    dismissed = False
    for _ in range(6):
        guide = visible_tour_guide(page)
        if guide is None:
            return dismissed
        if not click_tour_button(guide):
            # 点不动就把它摘掉，别让遮罩一直挡着发稿。
            return remove_tour_guide(page) or dismissed
        dismissed = True
        page.wait_for_timeout(400)
    return dismissed


def visible_tour_guide(page: Page) -> Locator | None:
    for selector in TOUR_SELECTORS:
        locator = page.locator(selector)
        try:
            if locator.count() and locator.first.is_visible():
                return locator.first
        except Exception:
            continue
    return None


def click_tour_button(guide: Locator) -> bool:
    button = guide.locator(f"button.{TOUR_BUTTON_CLASS}")
    try:
        if button.count() and button.last.is_visible() and button.last.is_enabled():
            button.last.click()
            return True
    except Exception:
        pass
    for name in TOUR_SKIP_BUTTONS:
        target = guide.get_by_role("button", name=name, exact=True)
        try:
            if target.count() and target.first.is_visible():
                target.first.click()
                return True
        except Exception:
            continue
    return False


def remove_tour_guide(page: Page) -> bool:
    try:
        return bool(
            page.evaluate(
                """(selectors) => {
                  let removed = 0;
                  for (const selector of selectors) {
                    for (const el of Array.from(document.querySelectorAll(selector))) {
                      el.remove();
                      removed += 1;
                    }
                  }
                  return removed > 0;
                }""",
                list(TOUR_SELECTORS),
            )
        )
    except Exception:
        return False


def wait_step(page: Page) -> None:
    page.wait_for_timeout(STEP_INTERVAL_MS)


def _publish_timing(mark: str, started: float) -> None:
    elapsed_ms = int((time.monotonic() - started) * 1000)
    print(f"发稿计时：{mark} +{elapsed_ms}ms", flush=True)


def click_locator_now(locator: Locator, timeout_ms: int = OVERLAY_CLICK_TIMEOUT_MS) -> bool:
    """弹层按钮常常看起来可点、实际还被动画挡住。默认 15 秒 actionability 会把一章卡住一轮。"""
    try:
        if not locator.count():
            return False
        target = locator.first
        if not target.is_visible() or not target.is_enabled():
            return False
        target.click(timeout=timeout_ms)
        return True
    except Exception:
        return False


def click_button_if_visible(
    page: Page,
    name: str,
    timeout_ms: int | None = None,
) -> bool:
    locator = page.get_by_role("button", name=name)
    if timeout_ms is None:
        try:
            if locator.count() and locator.first.is_visible() and locator.first.is_enabled():
                locator.first.click()
                return True
        except Exception:
            return False
        return False
    return click_locator_now(locator, timeout_ms)


def click_first_visible_name(
    page: Page,
    names: tuple[str, ...],
    timeout_ms: int | None = None,
) -> bool:
    for name in names:
        if click_button_if_visible(page, name, timeout_ms=timeout_ms):
            return True
        text_locator = page.get_by_text(name, exact=True)
        try:
            if text_locator.count() and text_locator.first.is_visible():
                if timeout_ms is None:
                    text_locator.first.click()
                else:
                    text_locator.first.click(timeout=timeout_ms)
                return True
        except Exception:
            continue
    return False


def execute_planned_publish(
    page: Page,
    manuscript: Manuscript,
    mode: CommandMode,
    report: PublishReport,
    progress: TaskProgress | None = None,
) -> None:
    progress = _task_progress(progress)
    progress.begin("claim")
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
        progress.fail("claim", note=claim_plan.halt_reason)
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
        already_open = bool(manuscript.profile.book_id) and bound_openable
        if not already_open and not open_claimed_book(page, manuscript, claim_plan, hits):
            title = manuscript.profile.field_text("作品名称")
            raise PublishHalt(f"找不到已认领的平台作品 {claim_plan.book_id}《{title}》")
        if not manuscript.profile.book_id:
            manuscript.profile.book_id = extract_book_id(page.url) or claim_plan.book_id
            save_profile(manuscript.profile)
        report.claimed_book_id = manuscript.profile.book_id
        print(f"认领平台作品 {manuscript.profile.book_id}", flush=True)
    progress.finish(
        "claim",
        note=manuscript.profile.book_id or report.claimed_book_id or claim_plan.book_id,
    )
    if mode.kind == MODE_DISCOVER:
        discover_claimed_settings(page, manuscript, report)
        return
    remotes: tuple[RemoteChapter, ...] = ()
    catalog_ready = not (report.dry_run and created_this_run and not manuscript.profile.book_id)
    if catalog_ready:
        remotes = tuple(
            list_remote_chapters(
                page,
                manuscript.profile.book_id,
                drafts_required=manuscript.profile.has_created_chapters(),
                progress=progress,
            )
        )
    full_plan = plan_publish(
        manuscript,
        mode,
        RemoteObservation(
            search_hits=hits,
            bound_book_openable=True,
            remote_chapters=remotes,
            catalog_observed=True,
            # 逐页遍历读完整本才走到这里：任何一页读不出来 list_remote_chapters
            # 都已经停机了，所以读到这一步就是读全了。
            catalog_complete=catalog_ready,
            created_this_run=created_this_run,
        ),
    )
    apply_plan_report(full_plan, report)
    if report.dry_run:
        preview_chapter_plan(full_plan, manuscript, progress)
        if full_plan.halt_reason:
            report.halted = full_plan.halt_reason
        return
    if full_plan.halt_reason:
        report.halted = full_plan.halt_reason
        return
    backfill_chapter_ids(manuscript, full_plan)
    execute_chapter_actions(page, manuscript, full_plan, report, remotes, progress)


def backfill_chapter_ids(manuscript: Manuscript, plan: PublishPlan) -> int:
    """把目录里读到、章缓存却没记住的章 ID 落回书资料。

    先落盘再动笔：这一趟即使后面停机，这些 ID 也不会再丢一次。
    """
    changed = 0
    for sequence, chapter_id in plan.chapter_ids_to_cache:
        if manuscript.profile.backfill_chapter_id(sequence, chapter_id):
            changed += 1
    if changed:
        save_profile(manuscript.profile)
        print(f"按后台回填 {changed} 章的章 ID", flush=True)
    return changed


def observe_claim_state(page: Page, manuscript: Manuscript) -> tuple[tuple[SearchHit, ...], bool]:
    profile = manuscript.profile
    if profile.book_id:
        opened = open_bound_book(page, profile.book_id, profile)
        return (), opened
    return collect_search_hits(page, profile), True


def open_book_manage(page: Page, profile: BookProfile) -> None:
    same_tab_goto(page, BOOK_MANAGE_URL)
    wait_until_logged_in(page, profile)
    try:
        page.wait_for_selector(BOOK_CARD_SELECTOR, timeout=8000)
    except Exception:
        wait_step(page)


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
            wait_step(page)
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
                        wait_step(page)
                        return True
        except Exception:
            pass
        id_text = page.get_by_text(hit.book_id, exact=False)
        try:
            if id_text.count() and id_text.first.is_visible():
                id_text.first.click()
                wait_step(page)
                return True
        except Exception:
            pass
    if hit.row_text:
        locator = page.get_by_text(hit.row_text, exact=False)
        try:
            if locator.count() and locator.first.is_visible():
                locator.first.click()
                wait_step(page)
                return True
        except Exception:
            pass
    return False


def create_platform_book(page: Page, manuscript: Manuscript, plan: PublishPlan, report: PublishReport) -> None:
    opened = click_first_visible_name(page, CREATE_BOOK_BUTTONS)
    submitted = False
    if opened:
        wait_step(page)
        apply_planned_settings(page, manuscript, plan, report, creating=True)
        if not report.missing_fields:
            submitted = click_first_visible_name(page, SUBMIT_BOOK_BUTTONS)
            if submitted:
                click_button_if_visible(page, "确定")
                dismiss_popups(page)
                wait_step(page)
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
        elif report.dry_run and action.action in {ACTION_UPDATE_DRAFT, ACTION_UPDATE_VISIBILITY}:
            report.updated_sequences.append(action.sequence)


def preview_chapter_plan(
    plan: PublishPlan,
    manuscript: Manuscript,
    progress: TaskProgress | None = None,
) -> None:
    progress = _task_progress(progress)
    executable = _executable_chapter_count(plan)
    progress.begin("chapters", total=executable)
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
        elif action.action == ACTION_UPDATE_VISIBILITY:
            print(f"干跑：改可见性第{action.sequence}章《{title}》{schedule}", flush=True)
    if plan.halt_reason:
        print(f"干跑：{plan.halt_reason}", flush=True)
    progress.finish("chapters", note=f"预演 {executable} 章")


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
    remotes: Sequence[RemoteChapter],
    progress: TaskProgress | None = None,
) -> None:
    """按计划逐章执行。远端观察由调用方传入：一次发稿只读一次后台目录，
    计划和执行看同一份事实，否则第二次读少了草稿就会把整批动作判成「找不到」。"""
    progress = _task_progress(progress)
    progress.begin("chapters", total=_executable_chapter_count(plan))
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
                progress.advance("chapters")
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
        progress.advance(
            "chapters",
            step=0,
            note=f"正在写 第{action.sequence}章《{chapter.title}》",
        )
        write_chapter(
            page,
            chapter,
            remote,
            manuscript.profile,
            report,
            action.scheduled_at,
            visibility_only=action.action == ACTION_UPDATE_VISIBILITY,
        )
        progress.advance("chapters")
        if manuscript.profile.delay_seconds > 0:
            page.wait_for_timeout(int(manuscript.profile.delay_seconds * 1000))
    progress.finish("chapters")


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
                wait_step(page)
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


def list_remote_chapters(
    page: Page,
    book_id: str = "",
    *,
    drafts_required: bool = True,
    progress: TaskProgress | None = None,
) -> list[RemoteChapter]:
    progress = _task_progress(progress)
    progress.begin("catalog")
    if book_id:
        return_to_chapter_catalog(page, book_id)
    remotes: list[RemoteChapter] = []
    if not click_catalog_tab(page, ("章节管理",)):
        _catalog_halt(progress, "打不开章节目录，未确认整本目录")
    try:
        halt_on_multiple_volumes(page)
    except PublishHalt as halted:
        _catalog_halt(progress, str(halted))
    remotes.extend(
        collect_paged_catalog_rows(page, "章节管理", progress=progress)
    )
    # 草稿箱和章节管理都是必要来源。点不开就静默当成「没有草稿」，
    # 会让计划里的改可见性动作在执行时全部落空。空书没有东西可丢，才允许跳过。
    before = catalog_row_signature(page)
    if not click_catalog_tab(page, ("草稿箱",)):
        if drafts_required or remotes:
            _catalog_halt(progress, "打不开草稿箱，未确认整本目录")
        remotes = unique_remote_chapters(remotes)
        _finish_catalog(progress, remotes)
        return remotes
    wait_for_catalog_switch(page, before)
    remotes.extend(
        collect_paged_catalog_rows(
            page,
            "草稿箱",
            published=False,
            visibility=VISIBILITY_DRAFT,
            progress=progress,
        )
    )
    click_catalog_tab(page, ("章节管理",))
    remotes = unique_remote_chapters(remotes)
    _finish_catalog(progress, remotes)
    return remotes


COLLECT_CHAPTER_ROWS_JS = """() => {
  const rows = [];
  // 只要叶子行。跨多章的祖先容器也含「第N章」，把它当成一行会串状态：
  // 标题取容器里第一个章号，而「已发布」是在整块文本里搜的。
  const nodes = Array.from(document.querySelectorAll("a, tr, li, div")).filter(
    (el) => ((el.innerText || "").match(/第\\d+章/g) || []).length === 1
  );
  const seen = new Set();
  for (const el of nodes) {
    const box = el.getBoundingClientRect();
    if (box.width <= 0 || box.height <= 0) continue;
    // 切走的那个标签并没有从页面上消失：它的面板被挪到视口左侧之外，
    // 仍然是显示状态、仍然有宽高。只按宽高判可见，就会把「章节管理」当前页的行
    // 收进草稿箱那一趟，还被强制标成草稿。按水平位置把视口外的整行排除。
    if (box.right <= 0 || box.left >= window.innerWidth) continue;
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


ACTIVE_CATALOG_ROOT_JS = """function activeCatalogRoot() {
  const panes = Array.from(
    document.querySelectorAll(".arco-tabs-pane, [role='tabpanel']")
  );
  if (!panes.length) return document;
  const viewWidth = window.innerWidth;
  let best = null;
  let bestVisible = -1;
  for (const pane of panes) {
    const box = pane.getBoundingClientRect();
    if (box.width <= 0 || box.height <= 0) continue;
    if (box.left < 0 || box.left >= viewWidth) continue;
    const visible = Math.min(box.right, viewWidth) - box.left;
    if (visible > bestVisible) {
      bestVisible = visible;
      best = pane;
    }
  }
    return best || document;
}"""


CATALOG_PAGE_NUMBERS_JS = """() => {
  // 两个标签的面板长期共存。切走的那个停在 x 为负处，左侧目录行已经出视口，
  // 但底部分页靠右，页码仍可能露在视口里。必须先锁定当前活动面板，再在
  // 面板内收页码；扫整页会把「章节管理」的 7 页当成草稿箱的页去翻。
""" + ACTIVE_CATALOG_ROOT_JS + """
  const items = Array.from(activeCatalogRoot().querySelectorAll("li[aria-label]"));
  const pages = [];
  for (const el of items) {
    const label = el.getAttribute("aria-label") || "";
    const matched = label.match(/^第\\s*(\\d+)\\s*页$/);
    if (!matched) continue;
    const box = el.getBoundingClientRect();
    if (box.width <= 0 || box.height <= 0) continue;
    if (box.right <= 0 || box.left >= window.innerWidth) continue;
    pages.push({
      number: parseInt(matched[1], 10),
      active: (el.className || "").indexOf("active") >= 0
    });
  }
  return pages;
}"""


CLICK_CATALOG_PAGE_JS = """/* click-catalog-page */
(number) => {
""" + ACTIVE_CATALOG_ROOT_JS + """
  const items = Array.from(activeCatalogRoot().querySelectorAll("li[aria-label]"));
  for (const el of items) {
    const label = el.getAttribute("aria-label") || "";
    const matched = label.match(/^第\\s*(\\d+)\\s*页$/);
    if (!matched || parseInt(matched[1], 10) !== number) continue;
    el.click();
    return true;
  }
  return false;
}"""


def catalog_page_numbers(page: Page) -> list[int]:
    """当前活动标签面板里这一刻读到的页码。

    空列表只表示此刻没读到分页控件，不表示只有一页。
    另一标签停在屏外的分页控件即使仍露在视口里，也不算。
    """
    payload = page.evaluate(CATALOG_PAGE_NUMBERS_JS)
    rows = payload if isinstance(payload, list) else []
    numbers = sorted(
        {int(row["number"]) for row in rows if isinstance(row, dict) and "number" in row}
    )
    return numbers


def wait_for_catalog_pages(page: Page) -> list[int]:
    """等分页控件出现；等满窗口仍没有，才当成只有一页。

    「这一刻没读到分页控件」和「确认只有一页」不是一回事。控件晚渲染一拍就读成空，
    这一趟只看到第一页却会被当成整本——后面「以后台为准」就会拿一份缺了几十章的
    目录去判后台缺哪些章，把后台已有的章重新建一遍。所以必须等满窗口。
    """
    numbers: list[int] = []
    for _ in range(CATALOG_PAGE_POLLS):
        numbers = catalog_page_numbers(page)
        if numbers:
            return numbers
        page.wait_for_timeout(300)
    return numbers


def active_catalog_page(page: Page) -> int:
    payload = page.evaluate(CATALOG_PAGE_NUMBERS_JS)
    rows = payload if isinstance(payload, list) else []
    for row in rows:
        if isinstance(row, dict) and row.get("active"):
            return int(row.get("number") or 0)
    return 0


def catalog_page_selector(number: int) -> str:
    # 页码是 <li aria-label="第 N 页">，不是表单控件。get_by_label 主要面向
    # 有 <label> 或 aria-label 的表单元素，对纯 li 不保证命中；命中不了的话
    # 任何多页目录都会在第二页停机。直接用属性选择器，不赌。
    return f'li[aria-label="第 {number} 页"]'


def click_catalog_page(page: Page, number: int) -> bool:
    """翻到指定页。翻页不改变地址，只能点当前活动标签面板里的页码。

    不能用整页 locator：两个标签的分页控件同时挂在 DOM 上，
    `.first` 会点到另一标签里那个同名页码。草稿箱没有那么多页时，
    活动页永远到不了目标，就会报「草稿箱翻不到第 6 页」。
    """
    try:
        clicked = page.evaluate(CLICK_CATALOG_PAGE_JS, number)
    except Exception:
        return False
    if not clicked:
        return False
    for _ in range(CATALOG_PAGE_POLLS):
        if active_catalog_page(page) == number:
            return True
        page.wait_for_timeout(300)
    return active_catalog_page(page) == number


def wait_for_catalog_page_rows(
    page: Page,
    previous: tuple[str, ...],
    label: str,
    number: int,
    progress: TaskProgress | None = None,
) -> None:
    """翻页不改地址，页码亮了不等于新行已经渲染出来。

    只等「有行」会把上一页还挂着的行再收一遍，真正这一页的章就丢了。
    必须等到行的内容换成这一页的；等不到就停机并指名页码。
    """
    for _ in range(CATALOG_PAGE_POLLS):
        current = catalog_row_signature(page)
        if current and current != previous:
            return
        page.wait_for_timeout(300)
    _catalog_halt(
        progress,
        f"{label}第{number}页的行在等待窗口内没有渲染出来，未确认整本目录",
    )


def collect_paged_catalog_rows(
    page: Page,
    label: str,
    *,
    published: bool | None = None,
    visibility: str = "",
    progress: TaskProgress | None = None,
) -> list[RemoteChapter]:
    """把当前标签的每一页都走一遍，行并起来。

    目录是分页表格，不是虚拟滚动：同一时刻页面上只有当前那一页的行，
    一页之内的行全部挂在 DOM 上。所以读全整本靠翻页，不靠滚动。
    任何一页读不出来都停机——把没读到的页当成"那几页没有章"，
    就会把后台已经有的章当成缺口重新建一遍。

    单个标签为空是合法的：章可以全在草稿箱里，章节管理就空着；没有草稿时
    草稿箱也空着。两个标签都空、而章缓存记着建过章，才是异常，那由
    `list_remote_chapters` 合起来判。

    页码控件会省略中间页。第 1 页常见形态是「1 2 3 4 5 … 9」，可见按钮
    不是完整页列表。末页号始终露着，按 1..末页依次翻；走到附近时被藏住
    的页码会自己露出来。只点这一刻看得见的按钮，倒序目录的中段会丢，
    下游就会把漏读的章判成中间缺口而停机。

    分页控件晚渲染一拍、只读到第一页仍然是这里最大的残余风险。它被下游兜住了：
    目录按章号倒序，第一页是最高的那些章，所以水位取自第一页，漏掉的章必然
    落在水位之下——`plan.py` 会把它们判成中间缺口而停机，进不了补建路径。
    """
    progress = _task_progress(progress)
    first = collect_catalog_rows(page, published=published, visibility=visibility)
    numbers = wait_for_catalog_pages(page)
    if not numbers:
        # 等满窗口都没有分页控件。分页表格总是连着行一起渲染出来，
        # 所以「有行、没页码」就是真的只有一页。
        progress.add_total("catalog", 1)
        progress.advance("catalog", note=f"{label}第1页")
        return first
    if not first:
        _catalog_halt(
            progress,
            f"{label}有分页控件却一章都没读到，未确认整本目录。"
            "分页说明这个标签下有内容，读不到就是没渲染出来。",
        )
    last_page = max(numbers)
    progress.add_total("catalog", last_page)
    current = active_catalog_page(page) or min(numbers)
    counts = {current: len(first)}
    remotes = list(first)
    progress.advance("catalog", note=f"{label}第{current}页")
    for number in range(1, last_page + 1):
        if number == current:
            continue
        previous = catalog_row_signature(page)
        if not click_catalog_page(page, number):
            _catalog_halt(progress, f"{label}翻不到第{number}页，未确认整本目录")
        wait_for_catalog_page_rows(page, previous, label, number, progress=progress)
        rows = collect_catalog_rows(page, published=published, visibility=visibility)
        if not rows:
            # 分页控件报着有这一页，这一页却一章都读不出来。多半是没渲染出来；
            # 也可能整页都是「未命名草稿」那种读不出章号的行。两者分不开，
            # 按没读全处理——放过去就要拿不完整的目录去判后台缺哪些章。
            _catalog_halt(
                progress,
                f"{label}第{number}页一章都没读到，未确认整本目录。"
                "请确认这一页是渲染慢了，还是整页都是读不出章号的行。",
            )
        counts[number] = len(rows)
        remotes.extend(rows)
        progress.advance("catalog", note=f"{label}第{number}页")
    halt_on_short_page(label, counts, progress=progress)
    return remotes


def halt_on_short_page(
    label: str,
    counts: dict[int, int],
    progress: TaskProgress | None = None,
) -> None:
    """除了最后一页，每页的行数都该等于页容量。少了就是那一页没读全。

    页容量不写死，取各页里最多的那个——后台改每页条数也不会误报。
    只有最末一页可以不满，那是余数。
    """
    if len(counts) <= 1:
        return
    page_size = max(counts.values())
    last = max(counts)
    short = sorted(
        number
        for number, count in counts.items()
        if number != last and count < page_size
    )
    if not short:
        return
    listed = "、".join(f"第{number}页" for number in short)
    _catalog_halt(
        progress,
        f"{label}的 {listed} 只读到不满一页的行（每页 {page_size} 行），"
        "未确认整本目录。这几页多半没渲染完就被数了。",
    )


CATALOG_VOLUME_OPTIONS_JS = """() => {
  const popup = document.querySelector(".byte-select-popup");
  if (!popup) return null;
  return Array.from(popup.querySelectorAll("li")).map(
    (el) => (el.innerText || "").replace(/\\s+/g, " ").trim()
  );
}"""


def catalog_volume_names(page: Page) -> list[str]:
    """后台目录还能按卷过滤，一次只显示一卷。返回卷名，读不到选择器时返回空。"""
    trigger = page.locator(".serial-select").first
    try:
        if not trigger.count():
            return []
        trigger.click()
    except Exception:
        return []
    names: list[str] = []
    for _ in range(CATALOG_VOLUME_POLLS):
        payload = page.evaluate(CATALOG_VOLUME_OPTIONS_JS)
        if isinstance(payload, list) and payload:
            names = [str(name) for name in payload if str(name).strip()]
            break
        page.wait_for_timeout(200)
    # 浮层留着会盖住页码，翻页就点不动了。实测 Escape 关不掉它，
    # 得再点一次触发器把它收回去，Escape 只作兜底。
    try:
        trigger.click()
    except Exception:
        pass
    try:
        page.keyboard.press("Escape")
    except Exception:
        pass
    return names


def halt_on_multiple_volumes(page: Page) -> None:
    names = catalog_volume_names(page)
    if len(names) <= 1:
        return
    listed = "、".join(names[:5])
    raise PublishHalt(
        f"这本书在后台分了 {len(names)} 卷（{listed}），本版本只支持单卷作品。"
        "目录一次只显示一卷，只读当前这一卷就不是整本，"
        "后面「以后台为准」的判断都不成立。"
    )


def click_catalog_tab(page: Page, names: tuple[str, ...], seconds: float | None = None) -> bool:
    """目录页是异步渲染的，标签要等。试一次就判「打不开章节目录」会误伤。"""
    deadline = time.monotonic() + (CATALOG_TAB_WAIT_SECONDS if seconds is None else seconds)
    while True:
        for name in names:
            tab = page.get_by_role("tab", name=name, exact=True)
            try:
                if tab.count() and tab.first.is_visible():
                    tab.first.click()
                    wait_step(page)
                    return True
            except Exception:
                continue
        if time.monotonic() >= deadline:
            return False
        dismiss_popups(page)
        page.wait_for_timeout(500)


def collect_catalog_rows(
    page: Page,
    *,
    published: bool | None = None,
    visibility: str = "",
) -> list[RemoteChapter]:
    payload = scroll_and_collect(page)
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


def catalog_row_count(page: Page) -> int:
    return page.get_by_text(CATALOG_ROW_RE).count()


def catalog_row_signature(page: Page) -> tuple[str, ...]:
    return tuple(str(row.get("text") or "") for row in catalog_rows_now(page))


def wait_for_catalog_switch(page: Page, previous: tuple[str, ...]) -> None:
    """等上一个标签的行让位，并且等新标签自己的行出来。

    切到草稿箱时，章节管理那几十行通常还挂在 DOM 上，行数立刻大于 0，
    只等「有行」等于没等——抓到的还是旧行，草稿箱等于白读。
    但「变了」也不等于「加载完了」：旧行清空、新行还没渲染的那一瞬间同样是变了，
    这时候数到的是 0。所以要等到既不同于旧行、又不是空的那一刻。
    真的空草稿箱等不到，就走完轮询按空处理。
    """
    for _ in range(CATALOG_SWITCH_POLLS):
        current = catalog_row_signature(page)
        if current and current != previous:
            return
        page.wait_for_timeout(300)
    print("等不到草稿箱的行，这次可能没读全。", flush=True)


def wait_for_catalog_rows(page: Page) -> None:
    """目录行是异步渲染的。第一行还没出来就开始数，会得到 0。"""
    if catalog_row_count(page):
        return
    try:
        page.get_by_text(CATALOG_ROW_RE).first.wait_for(
            state="visible",
            timeout=int(CATALOG_ROW_WAIT_SECONDS * 1000),
        )
    except Exception:
        return


def scroll_and_collect(page: Page) -> list[dict]:
    """边滚边收，把每一步看到的行并起来。

    长目录是虚拟滚动的：滚出视口的行会被移出 DOM，而 COLLECT_CHAPTER_ROWS_JS
    只认当前渲染出来的行。滚到底再取一次快照，拿到的只是最后那一屏——
    93 章的书会读成几章，缺口检测就会把早已发布的章判成缺口重发一遍。
    """
    wait_for_catalog_rows(page)
    seen: dict[str, dict] = {}
    previous = -1
    idle = 0
    for _ in range(CATALOG_SCROLL_STEPS):
        for row in catalog_rows_now(page):
            text = str(row.get("text") or "")
            if text and text not in seen:
                seen[text] = row
        current = len(seen)
        if current <= previous:
            idle += 1
            if idle >= CATALOG_SCROLL_IDLE_STEPS:
                break
        else:
            idle = 0
        previous = current
        page.mouse.wheel(0, 2400)
        page.wait_for_timeout(350)
    return list(seen.values())


def catalog_rows_now(page: Page) -> list[dict]:
    payload = page.evaluate(COLLECT_CHAPTER_ROWS_JS)
    rows = payload if isinstance(payload, list) else []
    return [row for row in rows if isinstance(row, dict)]


def write_chapter(
    page: Page,
    chapter: Chapter,
    remote: RemoteChapter | None,
    profile: BookProfile,
    report: PublishReport,
    scheduled_at: str = "",
    visibility_only: bool = False,
) -> None:
    if remote is not None and remote.published:
        report.published_mismatches.append(
            f"第{chapter.sequence}章 本地《{chapter.title}》 / 远端「{remote.title}」"
        )
        return
    if remote is None:
        open_create_chapter(page, profile.book_id)
    else:
        open_remote_chapter(page, remote, profile.book_id)
    wait_for_chapter_editor(page)
    if remote is None:
        # 新章页的地址不带章 ID。带了就说明还停在别的章的编辑器上，
        # 再写下去就是拿这一章的正文覆盖那一章。
        stranded = extract_chapter_id(page.url)
        if stranded:
            raise PublishHalt(
                f"新建第{chapter.sequence}章《{chapter.title}》时没有进入新章页，"
                f"仍停在章节 {stranded} 的编辑器上。已停止，避免覆盖那一章。"
            )
        report.created_sequences.append(chapter.sequence)
    else:
        report.updated_sequences.append(chapter.sequence)
    # 章 ID 要在还留在编辑页时取：定时发布和立即发布提交完就跳走了。
    # 新建章节进来时地址是 publish/?enter_from=newchapter，还没有章 ID；
    # 后台第一次云端存稿才分配并写回地址栏。所以每一步之后都补取一次。
    editor_chapter_id = extract_chapter_id(page.url)
    if not visibility_only:
        fill_chapter_number(page, chapter.sequence)
        filled_title = platform_chapter_title(chapter.title)
        if filled_title != chapter.title:
            print(
                f"第{chapter.sequence}章标题不足5字，后台写成《{filled_title}》",
                flush=True,
            )
        fill_chapter_title(page, filled_title)
        fill_chapter_body(page, chapter.body)
        wait_for_cloud_save(page)
        editor_chapter_id = extract_chapter_id(page.url) or editor_chapter_id
    dismiss_popups(page)
    editor_chapter_id = extract_chapter_id(page.url) or editor_chapter_id
    submit_written_chapter(page, profile, scheduled_at)
    chapter_id = (
        extract_chapter_id(page.url)
        or editor_chapter_id
        or (remote.chapter_id if remote else "")
    )
    cached = profile.chapter_cache.get(chapter.sequence)
    fingerprint = chapter.fingerprint if not visibility_only else (cached.fingerprint if cached else chapter.fingerprint)
    profile.cache_chapter(
        chapter.sequence,
        chapter_id,
        fingerprint,
        profile.chapter_visibility,
        scheduled_at,
    )
    # 这一章后台已经收下了。先落盘再回目录：回目录的导航一超时，
    # 这次写入就会连章 ID 一起丢掉，下次发稿又把它当没建过。
    save_profile(profile)
    stamp = f" 定时 {scheduled_at}" if scheduled_at else ""
    verb = "已改可见性" if visibility_only else "已写入"
    print(f"{verb}第{chapter.sequence}章《{chapter.title}》{stamp}", flush=True)
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
        wait_step(page)
        return
    if profile.chapter_visibility not in {VISIBILITY_PUBLISH, VISIBILITY_SCHEDULE}:
        raise PublishHalt(f"不支持的章节可见性：{profile.chapter_visibility}")
    submit_publish_settings(
        page,
        scheduled_at if profile.chapter_visibility == VISIBILITY_SCHEDULE else "",
        profile,
    )


def submit_publish_settings(page: Page, scheduled_at: str, profile: BookProfile | None = None) -> None:
    click_next_step(page)
    started = time.monotonic()
    _publish_timing("已点下一步", started)
    wait_until_publish_settings(page, profile, started=started)
    choose_not_using_ai(page)
    if scheduled_at:
        enable_timed_publish(page, scheduled_at)
    if not click_first_visible_name(page, CONFIRM_PUBLISH_BUTTONS):
        raise PublishHalt("找不到「确认发布」")
    _publish_timing("已点确认发布", started)
    wait_while_overlay(page, TYPO_OVERLAY_HINTS + REVIEW_CHOICE_HINTS)
    dismiss_popups(page)
    _publish_timing("发布设置提交完毕", started)


def click_next_step(page: Page) -> None:
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        dismiss_popups(page)
        if any_text_visible(page, TITLE_TOO_SHORT_HINTS):
            raise PublishHalt("章节名字数小于 5 个字，无法提交")
        next_button = page.locator("button.auto-editor-next, button.publish-button")
        try:
            if next_button.count() and next_button.first.is_visible() and next_button.first.is_enabled():
                next_button.first.click()
                return
        except Exception:
            pass
        if click_editor_next_step(page):
            return
        page.wait_for_timeout(400)
    if not click_first_visible_name(page, PUBLISH_BUTTONS):
        raise PublishHalt("找不到「下一步」")


def click_editor_next_step(page: Page) -> bool:
    """页面上可能有两个「下一步」：编辑器的和新手引导的。只点不属于引导的那个。"""
    try:
        locator = page.locator(f"button:not(.{TOUR_BUTTON_CLASS}):has-text('下一步')")
        if locator.count() and locator.first.is_visible() and locator.first.is_enabled():
            locator.first.click()
            return True
        return False
    except Exception:
        return click_button_if_visible(page, "下一步")


def wait_until_publish_settings(
    page: Page,
    profile: BookProfile | None = None,
    started: float | None = None,
) -> None:
    typo_seen = advance_to_publish_settings(page, seconds=25, started=started)
    if typo_seen is None:
        return
    # 弹层还在。浏览器是可见的，按 ADR 0009 让人自己点一下，别把整轮发稿丢掉。
    human_seconds = profile.human_wait_seconds if profile is not None else 0
    if human_seconds > 0:
        print(
            f"错别字提示自动点不掉，请在浏览器里点掉它（最多等 {int(human_seconds)} 秒）：{typo_seen}",
            flush=True,
        )
        if advance_to_publish_settings(page, seconds=human_seconds, started=started) is None:
            return
    if typo_seen:
        raise PublishHalt(f"错别字提示点不掉：{typo_seen}")
    raise PublishHalt("找不到发布设置")


def advance_to_publish_settings(
    page: Page,
    seconds: float,
    started: float | None = None,
) -> str | None:
    """走到「发布设置」返回 None；超时则返回挡路弹层的文字，没有弹层则返回空串。"""
    deadline = time.monotonic() + seconds
    blocking = ""
    clock = started if started is not None else time.monotonic()
    logged: set[str] = set()
    while time.monotonic() < deadline:
        dismiss_popups(page)
        if any_text_visible(page, ("发布设置",)):
            _publish_timing("到达发布设置", clock)
            page.wait_for_timeout(OVERLAY_POLL_MS)
            return None
        if any(overlay_contains(page, hint) for hint in TYPO_OVERLAY_HINTS):
            blocking = overlay_summary(page) or "错别字提示"
            if "typo-seen" not in logged:
                _publish_timing("看到错别字弹窗", clock)
                logged.add("typo-seen")
            # 弹层挂上的瞬间按钮可能还不可点，等下一轮再试，不要一次没点着就停手。
            clicked = click_overlay_name(page, TYPO_CONFIRM_BUTTONS) or click_overlay_primary(
                page, TYPO_OVERLAY_HINTS
            )
            if "typo-click" not in logged:
                _publish_timing("已点错别字弹窗", clock)
                logged.add("typo-click")
            if clicked:
                wait_while_overlay(page, TYPO_OVERLAY_HINTS)
            else:
                page.wait_for_timeout(OVERLAY_POLL_MS)
            continue
        if any(overlay_contains(page, hint) for hint in REVIEW_CHOICE_HINTS) or any_text_visible(
            page, ("仅基础检测",)
        ):
            blocking = overlay_summary(page) or "内容检测方式"
            if "review-seen" not in logged:
                _publish_timing("看到内容检测弹窗", clock)
                logged.add("review-seen")
            # 「全面检测」每章只有两次，不替作者花掉，一律走不限次数的基础检测。
            clicked = click_overlay_name(page, BASIC_REVIEW_BUTTONS) or click_first_visible_name(
                page, BASIC_REVIEW_BUTTONS, timeout_ms=OVERLAY_CLICK_TIMEOUT_MS
            )
            if "review-click" not in logged:
                _publish_timing("已点仅基础检测", clock)
                logged.add("review-click")
            if clicked:
                wait_while_overlay(page, REVIEW_CHOICE_HINTS)
            else:
                page.wait_for_timeout(OVERLAY_POLL_MS)
            continue
        page.wait_for_timeout(OVERLAY_POLL_MS)
    return blocking


def overlay_summary(page: Page) -> str:
    for selector in REVIEW_OVERLAY_SELECTORS:
        locator = page.locator(selector)
        try:
            if not locator.count():
                continue
            target = locator.last
            if not target.is_visible():
                continue
            text = " ".join((target.inner_text() or "").split())
        except Exception:
            continue
        if text:
            return text[:120]
    return ""


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
            if click_locator_now(primary):
                return True
        for name in names:
            button = overlay.get_by_role("button", name=name, exact=True)
            if click_locator_now(button):
                return True
            text_locator = overlay.get_by_text(name, exact=True)
            if click_locator_now(text_locator):
                return True
    if page_wide:
        return click_first_visible_name(page, names, timeout_ms=OVERLAY_CLICK_TIMEOUT_MS)
    return False


def click_overlay_primary(page: Page, must_contain: tuple[str, ...]) -> bool:
    """按钮名都没命中时，退到弹层自己的主按钮。只点文字对得上的那个弹层，别乱确认别的。"""
    for selector in REVIEW_OVERLAY_SELECTORS:
        locator = page.locator(selector)
        try:
            if not locator.count():
                continue
            overlay = locator.last
            if not overlay.is_visible():
                continue
            text = overlay.inner_text() or ""
            if not any(needle in text for needle in must_contain):
                continue
            primary = overlay.locator("button.arco-btn-primary")
            if click_locator_now(primary):
                return True
        except Exception:
            continue
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


def wait_while_overlay(
    page: Page,
    hints: tuple[str, ...],
    timeout_ms: int = OVERLAY_SETTLE_MS,
) -> None:
    """点完弹层后等到它消失或已经到了发布设置，不再死等 1–2 秒。"""
    deadline = time.monotonic() + timeout_ms / 1000
    while time.monotonic() < deadline:
        if any_text_visible(page, ("发布设置",)):
            return
        if not any(overlay_contains(page, hint) for hint in hints):
            return
        page.wait_for_timeout(OVERLAY_POLL_MS)


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
        try:
            same_tab_goto(page, href)
            return
        except PublishHalt:
            if click_create_chapter_button(page) == CREATE_CHAPTER_OK:
                return
            raise
    outcome = click_create_chapter_button(page)
    if outcome == CREATE_CHAPTER_OK:
        return
    if outcome == CREATE_CHAPTER_STUCK:
        raise PublishHalt("点了「创建章节」但没进入新章页")
    raise PublishHalt("找不到「创建章节」")


def click_create_chapter_button(page: Page) -> str:
    """点「创建章节」之后要确认真的进了新章页。

    新章页的地址里没有章 ID。点击没生效时页面还停在上一章的编辑器上，
    那里同样有标题框、地址同样含 /publish/，接着写就会把这一章的正文
    覆盖到上一章里——章缓存会出现两章共用一个章 ID。

    「没找到按钮」和「点了没跳」要分开报，所以这里返回三态而不是真假。
    事后回头数按钮判断不出来：候选按钮名有三个，点中的未必是第一个；
    点击真的触发了跳转时按钮还会从 DOM 里消失。
    """
    if not click_first_visible_name(page, CREATE_CHAPTER_BUTTONS):
        return CREATE_CHAPTER_MISSING
    for _ in range(CREATE_CHAPTER_POLLS):
        if on_new_chapter_page(page):
            return CREATE_CHAPTER_OK
        page.wait_for_timeout(300)
    return CREATE_CHAPTER_OK if on_new_chapter_page(page) else CREATE_CHAPTER_STUCK


def on_new_chapter_page(page: Page) -> bool:
    """新章页是「不带章 ID 的 publish 页」。

    只判「没有章 ID」不够——章节目录页同样没有章 ID，点击没生效时会被当成进了新章页。
    """
    url = page.url or ""
    return bool(BARE_PUBLISH_RE.search(url)) and not extract_chapter_id(url)


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
    dismiss_popups(page)
    last_error: Exception | None = None
    for attempt in range(NAVIGATION_ATTEMPTS):
        try:
            page.goto(href, wait_until="domcontentloaded", timeout=NAVIGATION_TIMEOUT_MS)
            return
        except PublishHalt:
            raise
        except Exception as error:
            last_error = error
            if navigation_path(page.url) == navigation_path(href):
                return
            if attempt + 1 < NAVIGATION_ATTEMPTS:
                page.wait_for_timeout(1000)
    detail = str(last_error) if last_error else href
    raise PublishHalt(f"打不开页面 {href}：{detail}") from last_error


def navigation_path(url: str) -> str:
    return (url or "").split("#", 1)[0].split("?", 1)[0].rstrip("/")


def chapter_catalog_url(book_id: str) -> str:
    return f"https://fanqienovel.com/main/writer/chapter-manage/{book_id}?type=1"


def open_book_settings(page: Page, book_id: str) -> bool:
    """打开作品设置页：有作品 ID 时直达 book-info，否则再点页面入口。"""
    if book_id:
        same_tab_goto(page, f"https://fanqienovel.com/main/writer/book-info/{book_id}?type=2")
        wait_step(page)
        return True
    if click_first_visible_name(page, SETTINGS_BUTTONS):
        wait_step(page)
        return True
    return False


def return_to_chapter_catalog(page: Page, book_id: str) -> None:
    if book_id:
        same_tab_goto(page, chapter_catalog_url(book_id))
        wait_step(page)
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
