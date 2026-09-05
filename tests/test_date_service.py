"""date_service 日期切换逻辑测试。"""

from __future__ import annotations

from app.database.schema import STATUS_ACTIVE, STATUS_NOT_DONE
from app.services.date_service import LAST_PROCESSED_DATE_KEY


class TestFirstEntry:
    def test_first_entry_new_date(self, date_service):
        """第一次进入一个新日期：记录基准，不做搬运。"""
        res = date_service.process_date_transition("2026-01-05")
        assert res["processed"] is True
        assert res["reason"] == "first_run"
        assert date_service.get_last_processed_date() == "2026-01-05"


class TestReprocessSameDay:
    def test_repeat_same_day_is_noop(self, date_service):
        """同一天重复启动：不得重复执行。"""
        date_service.process_date_transition("2026-01-05")
        res = date_service.process_date_transition("2026-01-05")
        assert res["processed"] is False
        assert res["reason"] == "already_processed"

    def test_repeat_does_not_duplicate_tasks(self, date_service, task_service):
        """重复处理同一天不能造成任务重复。"""
        date_service.process_date_transition("2026-01-05")
        date_service.process_date_transition("2026-01-06")

        t = task_service.create_task("任务", scheduled_date="2026-01-06")
        task_service.mark_not_done(t.id, "没时间")
        task_service.postpone_task(t.id)  # -> 2026-01-07

        # 连续多次进入 2026-01-07，不影响任务数量
        for _ in range(3):
            date_service.process_date_transition("2026-01-07")

        today = task_service.get_active_tasks_by_date("2026-01-07")
        assert len(today) == 1


class TestPostponedTaskEntry:
    def test_postponed_task_enters_next_day(self, date_service, task_service):
        """延期任务自动进入第二天。"""
        # 01-05 创建并延期到 01-06
        date_service.process_date_transition("2026-01-05")
        t = task_service.create_task("背书", scheduled_date="2026-01-05")
        task_service.mark_not_done(t.id, "还没背熟")
        task_service.postpone_task(t.id)  # scheduled_date -> 2026-01-06

        # 打开日期变成 01-06
        date_service.process_date_transition("2026-01-06")

        today = task_service.get_active_tasks_by_date("2026-01-06")
        assert len(today) == 1
        assert today[0].id == t.id
        assert today[0].status == STATUS_ACTIVE

    def test_postpone_count_preserved_through_transition(
        self, date_service, task_service
    ):
        """跨天后 preserve postpone_count。"""
        date_service.process_date_transition("2026-01-05")
        t = task_service.create_task("反复延期", scheduled_date="2026-01-05")

        task_service.mark_not_done(t.id, "a")
        task_service.postpone_task(t.id)  # count=1, 01-06
        task_service.mark_not_done(t.id, "b")
        task_service.postpone_task(t.id)  # count=2, 01-07

        date_service.process_date_transition("2026-01-06")
        date_service.process_date_transition("2026-01-07")

        got = task_service.get_task(t.id)
        assert got.postpone_count == 2
        assert got.scheduled_date == "2026-01-07"
        assert got.status == STATUS_ACTIVE


class TestNonPostponedTask:
    def test_unfinished_not_postponed_does_not_enter_next_day(
        self, date_service, task_service
    ):
        """未完成且未延期的任务，不会自动进入下一天。"""
        date_service.process_date_transition("2026-01-05")
        t = task_service.create_task("没完成也没延期", scheduled_date="2026-01-05")

        res = date_service.process_date_transition("2026-01-06")

        # 遗留任务被自动归档（不进入 01-06 待办）
        assert t.id in res["archived"]
        got = task_service.get_task(t.id)
        assert got.scheduled_date == "2026-01-05"  # 日期不动
        assert got.status == STATUS_NOT_DONE        # 状态变为 not_done
        today = task_service.get_active_tasks_by_date("2026-01-06")
        assert today == []

    def test_done_task_not_archived(self, date_service, task_service):
        """已完成任务在日期切换后保持 done。"""
        date_service.process_date_transition("2026-01-05")
        t = task_service.create_task("已完成", scheduled_date="2026-01-05")
        task_service.complete_task(t.id)

        res = date_service.process_date_transition("2026-01-06")
        assert res["archived"] == []
        assert task_service.get_status(t.id) == "done"


class TestMultiDayGap:
    def test_gap_processes_all_stale_tasks_once(self, date_service, task_service):
        """跨多天未打开：所有过期任务只归档一次，不重复生成。"""
        date_service.process_date_transition("2026-01-05")
        t1 = task_service.create_task("A", scheduled_date="2026-01-05")
        t2 = task_service.create_task("B", scheduled_date="2026-01-06")
        t3 = task_service.create_task("C", scheduled_date="2026-01-07")

        # 直接跳到 01-08
        res = date_service.process_date_transition("2026-01-08")

        assert t1.id in res["archived"]
        assert t2.id in res["archived"]
        assert t3.id in res["archived"]

        # 再次同一天处理，幂等
        res2 = date_service.process_date_transition("2026-01-08")
        assert res2["processed"] is False

        for tid in (t1.id, t2.id, t3.id):
            assert task_service.get_status(tid) == STATUS_NOT_DONE


class TestMeta:
    def test_meta_persisted(self, conn, date_service):
        """last_processed_date 持久化到数据库。"""
        date_service.process_date_transition("2026-01-05")
        date_service.process_date_transition("2026-01-06")
        # 模拟重启：用同一 connection 重新读取
        stored = conn.execute(
            "SELECT value FROM app_meta WHERE key = ?",
            (LAST_PROCESSED_DATE_KEY,),
        ).fetchone()
        assert stored["value"] == "2026-01-06"
