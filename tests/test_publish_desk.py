from __future__ import annotations

import threading
import tempfile
import time
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from book.web.app import create_app
from publish.desk import (
    JOB_DONE,
    JOB_FAILED,
    KIND_BIND,
    add_desk_manuscript,
    bind_manuscript,
    get_job,
    list_desk_rows,
    remove_desk_manuscript,
    reset_jobs,
    resolve_manuscript_dir,
    save_desk_publish_settings,
    start_bind_job,
    start_publish_job,
)
from publish.manuscript import ManuscriptError, load_manuscript, load_profile, save_profile
from publish.web import attach_publish_desk
from publish.writer import PublishReport
from publish.plan import SearchHit
from tests.test_publish_plan import write_manuscript


def wait_for_job(job_id: str, timeout: float = 2.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        job = get_job(job_id)
        if job is not None and job.status in {JOB_DONE, JOB_FAILED}:
            return job
        time.sleep(0.01)
    raise AssertionError("发稿任务没有在时限内结束")


def fake_runner(manuscript, dry_run=False, discover_only=False, allow_create=False):
    report = PublishReport(dry_run=dry_run, claimed_book_id="claimed-1")
    report.print_report()
    return report


def prepare_root(temp_dir: str) -> Path:
    root = Path(temp_dir)
    manuscript_dir = root / "novel" / "工牌不认婚约"
    manuscript_dir.mkdir(parents=True)
    write_manuscript(manuscript_dir, cover_name="封面.jpg")
    return root


class PublishDeskTest(unittest.TestCase):
    def setUp(self) -> None:
        reset_jobs()

    def tearDown(self) -> None:
        reset_jobs()

    def test_lists_manuscript_and_skips_dirs_without_profile(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = prepare_root(temp_dir)
            (root / "novel" / "草稿未灌").mkdir()
            (root / "novel" / "草稿未灌" / "readme.txt").write_text("no", encoding="utf-8")
            rows = list_desk_rows(root)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0].title, "工牌不认婚约")
            self.assertFalse(rows[0].bound)
            self.assertTrue(rows[0].cover_ready)
            self.assertEqual(rows[0].chapter_count, 1)

    def test_rejects_path_traversal(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "novel").mkdir()
            with self.assertRaises(ManuscriptError):
                resolve_manuscript_dir("../secret", root=root)

    def test_job_records_claim_report(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = prepare_root(temp_dir)
            job = start_publish_job(
                "工牌不认婚约",
                dry_run=True,
                root=root,
                runner=fake_runner,
            )
            finished = wait_for_job(job.job_id)
            self.assertEqual(finished.status, JOB_DONE)
            self.assertEqual(finished.claimed_book_id, "claimed-1")
            self.assertTrue(any("认领" in line for line in finished.lines))

    def test_second_job_rejected_while_running(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = prepare_root(temp_dir)
            started = threading.Event()
            release = threading.Event()

            def blocking_runner(manuscript, dry_run=False, discover_only=False, allow_create=False):
                started.set()
                release.wait(timeout=2)
                return PublishReport(dry_run=dry_run)

            start_publish_job("工牌不认婚约", root=root, runner=blocking_runner)
            self.assertTrue(started.wait(timeout=2))
            try:
                with self.assertRaises(ManuscriptError):
                    start_publish_job("工牌不认婚约", root=root, runner=fake_runner)
            finally:
                release.set()

    def test_lists_empty_manuscript_and_can_start_job(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            add_desk_manuscript("空书", root=root)
            rows = list_desk_rows(root)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0].title, "空书")
            self.assertEqual(rows[0].chapter_count, 0)
            self.assertEqual(rows[0].load_error, "")
            manuscript = load_manuscript(root / "novel" / "空书")
            self.assertEqual(manuscript.chapters, ())
            job = start_publish_job("空书", dry_run=True, root=root, runner=fake_runner)
            finished = wait_for_job(job.job_id)
            self.assertEqual(finished.status, JOB_DONE)


    def test_remove_empty_manuscript_and_keep_chapters(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            add_desk_manuscript("空书", root=root)
            remove_desk_manuscript("空书", root=root)
            self.assertEqual(list_desk_rows(root), [])
            self.assertFalse((root / "novel" / "空书").exists())
            filled = prepare_root(temp_dir)
            with self.assertRaises(ManuscriptError):
                remove_desk_manuscript("工牌不认婚约", root=filled)
            self.assertTrue((filled / "novel" / "工牌不认婚约" / "书资料.yml").is_file())

    def test_add_rejects_existing_work_title(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = prepare_root(temp_dir)
            profile = load_profile(root / "novel" / "工牌不认婚约" / "书资料.yml")
            profile.fields["作品名称"] = "婚约不许我升职"
            save_profile(profile)
            with self.assertRaises(ManuscriptError):
                add_desk_manuscript("婚约不许我升职", root=root)
            self.assertEqual(len(list_desk_rows(root)), 1)

    def test_default_novel_root_is_main_worktree(self) -> None:
        from publish.desk import novel_root
        from publish.manuscript import content_root

        self.assertEqual(novel_root(), content_root() / "novel")


class PublishDeskWebTest(unittest.TestCase):
    def setUp(self) -> None:
        reset_jobs()

    def tearDown(self) -> None:
        reset_jobs()

    def test_publish_page_lists_manuscript_and_actions(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = prepare_root(temp_dir)
            app = create_app()
            attach_publish_desk(app, project_root=root)
            client = TestClient(app)
            home = client.get("/")
            self.assertEqual(home.status_code, 200)
            self.assertIn("发稿", home.text)
            self.assertIn("本机工作台", home.text)
            page = client.get("/publish")
            self.assertEqual(page.status_code, 200)
            self.assertIn("本机工作台", page.text)
            self.assertIn("题材进掉", page.text)
            self.assertIn("工牌不认婚约", page.text)
            self.assertIn("干跑", page.text)
            self.assertIn("发稿", page.text)
            self.assertIn("创建平台作品", page.text)
            self.assertNotIn("搜不到再创建", page.text)
            self.assertIn("添加稿本", page.text)
            self.assertIn("设置", page.text)
            self.assertIn("绑定", page.text)
            self.assertIn("/bind-jobs", page.text)

    def test_foreign_origin_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = prepare_root(temp_dir)
            app = create_app()
            attach_publish_desk(app, project_root=root)
            client = TestClient(app)
            response = client.post(
                "/publish/工牌不认婚约/jobs",
                params={"dry_run": True},
                headers={"origin": "https://evil.example"},
            )
            self.assertEqual(response.status_code, 403)

    def test_post_dry_run_redirects_to_job(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = prepare_root(temp_dir)
            app = create_app()
            attach_publish_desk(app, project_root=root)

            from publish import desk as desk_module

            original = desk_module.run_publish
            desk_module.run_publish = fake_runner
            try:
                client = TestClient(app)
                response = client.post(
                    "/publish/工牌不认婚约/jobs",
                    params={"dry_run": True},
                    follow_redirects=False,
                )
                self.assertEqual(response.status_code, 303)
                location = response.headers["location"]
                self.assertTrue(location.startswith("/publish/jobs/"))
                job_id = location.rsplit("/", 1)[-1]
                finished = wait_for_job(job_id)
                self.assertEqual(finished.status, JOB_DONE)
                job_page = client.get(location)
                self.assertEqual(job_page.status_code, 200)
                self.assertIn("干跑", job_page.text)
                self.assertIn("本机工作台", job_page.text)
                self.assertNotIn('http-equiv="refresh"', job_page.text)
            finally:
                desk_module.run_publish = original

    def test_add_manuscript_creates_profile_and_returns_to_desk(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            app = create_app()
            attach_publish_desk(app, project_root=root)
            client = TestClient(app)
            response = client.post(
                "/publish/manuscripts",
                data={"work_title": "新书"},
                follow_redirects=False,
            )
            self.assertEqual(response.status_code, 303)
            self.assertEqual(response.headers["location"], "/publish")
            profile = load_profile(root / "novel" / "新书" / "书资料.yml")
            self.assertEqual(profile.field_text("作品名称"), "新书")
            page = client.get("/publish")
            self.assertIn("新书", page.text)
            self.assertIn("0 章", page.text)

    def test_add_manuscript_rejects_blank_path_and_duplicate(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            app = create_app()
            attach_publish_desk(app, project_root=root)
            client = TestClient(app)
            created = client.post("/publish/manuscripts", data={"work_title": "新书"})
            self.assertIn(created.status_code, {200, 303})
            blank = client.post("/publish/manuscripts", data={"work_title": "  "})
            self.assertEqual(blank.status_code, 400)
            slash = client.post("/publish/manuscripts", data={"work_title": "a/b"})
            self.assertEqual(slash.status_code, 400)
            duplicate = client.post("/publish/manuscripts", data={"work_title": "新书"})
            self.assertEqual(duplicate.status_code, 400)
            self.assertEqual(len(list_desk_rows(root)), 1)


    def test_remove_manuscript_from_desk(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            app = create_app()
            attach_publish_desk(app, project_root=root)
            client = TestClient(app)
            created = client.post("/publish/manuscripts", data={"work_title": "空书"})
            self.assertIn(created.status_code, {200, 303})
            page = client.get("/publish")
            self.assertIn("删除稿本", page.text)
            response = client.post(
                "/publish/空书/remove",
                follow_redirects=False,
            )
            self.assertEqual(response.status_code, 303)
            self.assertEqual(response.headers["location"], "/publish")
            self.assertEqual(list_desk_rows(root), [])
            filled = prepare_root(temp_dir)
            blocked = client.post("/publish/工牌不认婚约/remove")
            self.assertEqual(blocked.status_code, 400)
            self.assertTrue((filled / "novel" / "工牌不认婚约" / "书资料.yml").is_file())

    def test_create_button_starts_allow_create_job(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = prepare_root(temp_dir)
            app = create_app()
            attach_publish_desk(app, project_root=root)
            captured: dict[str, bool] = {}

            def recording_runner(manuscript, dry_run=False, discover_only=False, allow_create=False):
                captured["allow_create"] = allow_create
                return fake_runner(manuscript, dry_run=dry_run, allow_create=allow_create)

            from publish import desk as desk_module

            original = desk_module.run_publish
            desk_module.run_publish = recording_runner
            try:
                client = TestClient(app)
                response = client.post(
                    "/publish/工牌不认婚约/jobs",
                    params={"allow_create": True},
                    follow_redirects=False,
                )
                self.assertEqual(response.status_code, 303)
                job_id = response.headers["location"].rsplit("/", 1)[-1]
                finished = wait_for_job(job_id)
                self.assertEqual(finished.status, JOB_DONE)
                self.assertTrue(finished.allow_create)
                self.assertTrue(captured["allow_create"])
                job_page = client.get(response.headers["location"])
                self.assertIn("创建平台作品", job_page.text)
            finally:
                desk_module.run_publish = original


SETTINGS_FORM = {
    "book_id": "",
    "work_title": "工牌不认婚约",
    "channel": "女频",
    "category": "现代言情",
    "intro": "长简介一段。",
    "chapter_visibility": "定时发布",
    "serial_status": "连载",
    "max_chapters_per_run": "10",
    "delay_seconds": "2",
    "human_wait_seconds": "30",
    "schedule_times": "08:00、15:00",
}


class PublishSettingsWebTest(unittest.TestCase):
    def setUp(self) -> None:
        reset_jobs()

    def tearDown(self) -> None:
        reset_jobs()

    def test_settings_page_shows_current_values(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = prepare_root(temp_dir)
            app = create_app()
            attach_publish_desk(app, project_root=root)
            client = TestClient(app)
            page = client.get("/publish/工牌不认婚约/settings")
            self.assertEqual(page.status_code, 200)
            self.assertIn("发稿设置", page.text)
            self.assertIn("发稿时刻", page.text)
            self.assertIn("章节可见性", page.text)
            self.assertIn("单次章数上限", page.text)
            self.assertIn("作品 ID", page.text)
            self.assertIn("草稿", page.text)
            self.assertIn("作品名称", page.text)
            self.assertIn("频道", page.text)
            self.assertIn("分类", page.text)
            self.assertIn("简介", page.text)
            self.assertIn("封面", page.text)

    def test_post_settings_writes_profile_and_keeps_binding(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = prepare_root(temp_dir)
            profile_path = root / "novel" / "工牌不认婚约" / "书资料.yml"
            profile = load_profile(profile_path)
            profile.book_id = "bound-1"
            save_profile(profile)
            app = create_app()
            attach_publish_desk(app, project_root=root)
            client = TestClient(app)
            response = client.post(
                "/publish/工牌不认婚约/settings",
                data={**SETTINGS_FORM, "book_id": "bound-1"},
                follow_redirects=False,
            )
            self.assertEqual(response.status_code, 303)
            self.assertIn("/settings?saved=1", response.headers["location"])
            saved = load_profile(profile_path)
            self.assertEqual(saved.book_id, "bound-1")
            self.assertEqual(saved.chapter_visibility, "定时发布")
            self.assertEqual(saved.schedule_times, ("08:00", "15:00"))
            self.assertEqual(saved.max_chapters_per_run, 10)
            self.assertEqual(saved.delay_seconds, 2.0)
            desk = client.get("/publish")
            self.assertIn("08:00、15:00", desk.text)
            saved_page = client.get(response.headers["location"])
            self.assertEqual(saved_page.status_code, 200)
            self.assertIn("已写入书资料", saved_page.text)
            self.assertIn("08:00、15:00", saved_page.text)

    def test_invalid_schedule_keeps_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = prepare_root(temp_dir)
            profile_path = root / "novel" / "工牌不认婚约" / "书资料.yml"
            before = profile_path.read_text(encoding="utf-8")
            app = create_app()
            attach_publish_desk(app, project_root=root)
            client = TestClient(app)
            bad = dict(SETTINGS_FORM)
            bad["schedule_times"] = ""
            response = client.post("/publish/工牌不认婚约/settings", data=bad)
            self.assertEqual(response.status_code, 400)
            self.assertIn("定时发布需要发稿时刻", response.text)
            self.assertEqual(profile_path.read_text(encoding="utf-8"), before)

    def test_rejects_settings_while_same_book_is_publishing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = prepare_root(temp_dir)
            started = threading.Event()
            release = threading.Event()

            def blocking_runner(manuscript, dry_run=False, discover_only=False, allow_create=False):
                started.set()
                release.wait(timeout=2)
                return PublishReport(dry_run=dry_run)

            start_publish_job("工牌不认婚约", root=root, runner=blocking_runner)
            self.assertTrue(started.wait(timeout=2))
            try:
                with self.assertRaises(ManuscriptError):
                    save_desk_publish_settings(
                        "工牌不认婚约",
                        book_id="",
                        chapter_visibility="草稿",
                        serial_status="连载",
                        max_chapters_per_run="20",
                        delay_seconds="4",
                        human_wait_seconds="600",
                        schedule_times="",
                        root=root,
                    )
            finally:
                release.set()

    def test_rebind_clears_chapter_cache(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = prepare_root(temp_dir)
            profile_path = root / "novel" / "工牌不认婚约" / "书资料.yml"
            profile = load_profile(profile_path)
            profile.book_id = "bound-1"
            profile.set_binding(1, "c1", "abc", "草稿")
            save_profile(profile)
            saved = save_desk_publish_settings(
                "工牌不认婚约",
                book_id="bound-2",
                chapter_visibility="草稿",
                serial_status="连载",
                max_chapters_per_run="20",
                delay_seconds="4",
                human_wait_seconds="600",
                schedule_times="",
                root=root,
            )
            self.assertEqual(saved.book_id, "bound-2")
            self.assertEqual(saved.chapter_bindings, {})

    def test_same_book_id_keeps_chapter_cache(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = prepare_root(temp_dir)
            profile_path = root / "novel" / "工牌不认婚约" / "书资料.yml"
            profile = load_profile(profile_path)
            profile.book_id = "bound-1"
            profile.set_binding(1, "c1", "abc", "草稿")
            save_profile(profile)
            saved = save_desk_publish_settings(
                "工牌不认婚约",
                book_id="bound-1",
                chapter_visibility="草稿",
                serial_status="连载",
                max_chapters_per_run="20",
                delay_seconds="4",
                human_wait_seconds="600",
                schedule_times="",
                root=root,
            )
            self.assertEqual(saved.book_id, "bound-1")
            self.assertEqual(saved.chapter_bindings[1].chapter_id, "c1")

    def test_settings_refuses_book_owned_by_other_manuscript(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            first = root / "novel" / "工牌不认婚约"
            second = root / "novel" / "认罪会传染"
            first.mkdir(parents=True)
            second.mkdir(parents=True)
            write_manuscript(first, cover_name="封面.jpg")
            write_manuscript(second, title="认罪会传染")
            other = load_profile(second / "书资料.yml")
            other.book_id = "shared-1"
            save_profile(other)
            with self.assertRaisesRegex(ManuscriptError, "已绑定稿本"):
                save_desk_publish_settings(
                    "工牌不认婚约",
                    book_id="shared-1",
                    chapter_visibility="草稿",
                    serial_status="连载",
                    max_chapters_per_run="20",
                    delay_seconds="4",
                    human_wait_seconds="600",
                    schedule_times="",
                    root=root,
                )

    def test_settings_writes_create_fields_and_cover_keeps_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = prepare_root(temp_dir)
            manuscript_dir = root / "novel" / "工牌不认婚约"
            app = create_app()
            attach_publish_desk(app, project_root=root)
            client = TestClient(app)
            response = client.post(
                "/publish/工牌不认婚约/settings",
                data={
                    **SETTINGS_FORM,
                    "work_title": "空书新名",
                    "channel": "男频",
                    "category": "都市",
                    "intro": "新简介",
                },
                files={"cover": ("新封面.jpg", b"jpeg-bytes", "image/jpeg")},
                follow_redirects=False,
            )
            self.assertEqual(response.status_code, 303)
            saved = load_profile(manuscript_dir / "书资料.yml")
            self.assertEqual(saved.field_text("作品名称"), "空书新名")
            self.assertEqual(saved.field_text("频道"), "男频")
            self.assertEqual(saved.field_text("分类"), "都市")
            self.assertEqual(saved.field_text("简介"), "新简介")
            self.assertEqual(saved.field_text("封面"), "新封面.jpg")
            self.assertTrue((manuscript_dir / "新封面.jpg").is_file())
            self.assertTrue(manuscript_dir.is_dir())
            desk = client.get("/publish")
            self.assertIn("空书新名", desk.text)


def fake_list_books(profile):
    del profile
    print("作品管理 1 本", flush=True)
    return (
        SearchHit(
            book_id="99",
            row_text="婚约不许我升职",
            work_name="婚约不许我升职",
        ),
    )


class PublishBindTest(unittest.TestCase):
    def setUp(self) -> None:
        reset_jobs()

    def tearDown(self) -> None:
        reset_jobs()

    def test_bind_manuscript_writes_id_and_clears_cache(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = prepare_root(temp_dir)
            profile_path = root / "novel" / "工牌不认婚约" / "书资料.yml"
            profile = load_profile(profile_path)
            profile.book_id = "bound-1"
            profile.set_binding(1, "c1", "abc", "草稿")
            save_profile(profile)
            saved = bind_manuscript("工牌不认婚约", "bound-2", root=root)
            self.assertEqual(saved.book_id, "bound-2")
            self.assertEqual(saved.chapter_bindings, {})

    def test_bind_refuses_book_owned_by_other_manuscript(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            first = root / "novel" / "工牌不认婚约"
            second = root / "novel" / "认罪会传染"
            first.mkdir(parents=True)
            second.mkdir(parents=True)
            write_manuscript(first, cover_name="封面.jpg")
            write_manuscript(second, title="认罪会传染")
            other = load_profile(second / "书资料.yml")
            other.book_id = "shared-1"
            save_profile(other)
            with self.assertRaisesRegex(ManuscriptError, "已绑定稿本"):
                bind_manuscript("工牌不认婚约", "shared-1", root=root)

    def test_bind_job_records_candidates(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = prepare_root(temp_dir)
            job = start_bind_job("工牌不认婚约", root=root, runner=fake_list_books)
            finished = wait_for_job(job.job_id)
            self.assertEqual(finished.status, JOB_DONE)
            self.assertEqual(finished.kind, KIND_BIND)
            self.assertEqual(finished.candidates[0].book_id, "99")
            self.assertTrue(any("作品管理" in line for line in finished.lines))


class PublishBindWebTest(unittest.TestCase):
    def setUp(self) -> None:
        reset_jobs()

    def tearDown(self) -> None:
        reset_jobs()

    def test_bind_job_page_lists_candidates_and_post_writes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = prepare_root(temp_dir)
            app = create_app()
            attach_publish_desk(app, project_root=root)

            from publish import desk as desk_module

            original = desk_module.run_list_platform_books
            desk_module.run_list_platform_books = fake_list_books
            try:
                client = TestClient(app)
                response = client.post(
                    "/publish/工牌不认婚约/bind-jobs",
                    follow_redirects=False,
                )
                self.assertEqual(response.status_code, 303)
                location = response.headers["location"]
                self.assertTrue(location.startswith("/publish/jobs/"))
                job_id = location.rsplit("/", 1)[-1]
                finished = wait_for_job(job_id)
                self.assertEqual(finished.status, JOB_DONE)
                page = client.get(location)
                self.assertEqual(page.status_code, 200)
                self.assertIn("婚约不许我升职", page.text)
                self.assertIn("绑定这本", page.text)
                self.assertIn("99", page.text)
                bound = client.post(
                    "/publish/工牌不认婚约/bind",
                    data={"book_id": "99"},
                    follow_redirects=False,
                )
                self.assertEqual(bound.status_code, 303)
                self.assertEqual(bound.headers["location"], "/publish")
                saved = load_profile(root / "novel" / "工牌不认婚约" / "书资料.yml")
                self.assertEqual(saved.book_id, "99")
                desk = client.get("/publish")
                self.assertIn("已绑定", desk.text)
                self.assertIn("改绑", desk.text)
            finally:
                desk_module.run_list_platform_books = original

    def test_foreign_origin_is_rejected_on_bind(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = prepare_root(temp_dir)
            app = create_app()
            attach_publish_desk(app, project_root=root)
            client = TestClient(app)
            response = client.post(
                "/publish/工牌不认婚约/bind-jobs",
                headers={"origin": "https://evil.example"},
            )
            self.assertEqual(response.status_code, 403)

    def test_bound_desk_shows_rebind_label(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = prepare_root(temp_dir)
            profile_path = root / "novel" / "工牌不认婚约" / "书资料.yml"
            profile = load_profile(profile_path)
            profile.book_id = "bound-1"
            save_profile(profile)
            app = create_app()
            attach_publish_desk(app, project_root=root)
            client = TestClient(app)
            page = client.get("/publish")
            self.assertIn("改绑", page.text)
            self.assertNotIn(">绑定<", page.text)


if __name__ == "__main__":
    unittest.main()
