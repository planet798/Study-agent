"""跨日未确认正式任务补确认测试。"""

from __future__ import annotations

from PySide6.QtWidgets import QDialog

from app.database.assessment_repository import AssessmentRepository
from app.database.schema import (
    STATUS_ACTIVE,
    STATUS_DONE,
    STATUS_NOT_DONE,
)
from app.services.date_service import DateService
from app.services.past_task_service import (
    DECISION_DONE,
    DECISION_NOT_DONE,
    PastTaskConfirmationService,
)
from app.services.skill_service import SkillService
from app.services.study_plan_service import StudyPlanService
from app.database.skill_repository import SkillRepository
from app.ui.past_task_dialog import PastTaskConfirmationDialog
from app.ui.task_widget import TaskWidget

YESTERDAY = "2026-09-14"
DAY_BEFORE = "2026-09-13"
TODAY = "2026-09-15"


def _mk(repo, title, date, task_type="new", source="generated", topic_id=None,
        kp=None):
    return repo.create(title=title, scheduled_date=date, source=source,
                       task_type=task_type, topic_id=topic_id,
                       knowledge_point_id=kp)


def _svc(repo, task_service):
    return PastTaskConfirmationService(repo, task_service)


# ================= 1~9：查询条件 =================

class TestFindUnresolved:
    def test_yesterday_active_new_found(self, repo, task_service):
        t = _mk(repo, "A", YESTERDAY)
        found = _svc(repo, task_service).find_unresolved(TODAY)
        assert [x.id for x in found] == [t.id]

    def test_done_not_found(self, repo, task_service):
        t = _mk(repo, "A", YESTERDAY)
        repo.mark_done(t.id)
        assert _svc(repo, task_service).find_unresolved(TODAY) == []

    def test_not_done_not_found(self, repo, task_service):
        t = _mk(repo, "A", YESTERDAY)
        repo.mark_not_done(t.id, "忘了")
        assert _svc(repo, task_service).find_unresolved(TODAY) == []

    def test_review_not_found(self, repo, task_service):
        _mk(repo, "R", YESTERDAY, task_type="review", source="review")
        assert _svc(repo, task_service).find_unresolved(TODAY) == []

    def test_legacy_extra_not_found(self, repo, task_service):
        # 额外学习功能已移除：legacy extra 不再阻塞昨日补确认。
        _mk(repo, "E", YESTERDAY, task_type="extra", source="extra")
        assert _svc(repo, task_service).find_unresolved(TODAY) == []

    def test_manual_activity_found(self, repo, task_service):
        # Phase A：手动手动学习活动（task_type=manual）需要补确认。
        t = _mk(repo, "M", YESTERDAY, task_type="manual", source="manual")
        found = _svc(repo, task_service).find_unresolved(TODAY)
        assert [x.id for x in found] == [t.id]

    def test_manual_knowledge_found(self, repo, task_service):
        # Phase A：手动正式知识任务（source=manual, task_type=new）需要补确认。
        t = _mk(repo, "K", YESTERDAY, source="manual")
        found = _svc(repo, task_service).find_unresolved(TODAY)
        assert [x.id for x in found] == [t.id]

    def test_cancelled_not_found(self, repo, task_service):
        # Phase A：cancelled 是“用户主动移除”，不再弹昨日确认。
        t = _mk(repo, "C", YESTERDAY)
        repo.cancel(t.id)
        assert _svc(repo, task_service).find_unresolved(TODAY) == []

    def test_today_active_not_found(self, repo, task_service):
        _mk(repo, "T", TODAY)
        assert _svc(repo, task_service).find_unresolved(TODAY) == []

    def test_multiple_all_listed(self, repo, task_service):
        a = _mk(repo, "A", YESTERDAY)
        b = _mk(repo, "B", YESTERDAY)
        ids = {t.id for t in _svc(repo, task_service).find_unresolved(TODAY)}
        assert ids == {a.id, b.id}

    def test_cross_multiple_days_found(self, repo, task_service):
        old = _mk(repo, "old", DAY_BEFORE)
        new = _mk(repo, "new", YESTERDAY)
        found = _svc(repo, task_service).find_unresolved(TODAY)
        assert [t.id for t in found] == [old.id, new.id]  # 日期从旧到新

    def test_sorted_by_date(self, repo, task_service):
        _mk(repo, "b", YESTERDAY)
        _mk(repo, "a", DAY_BEFORE)
        dates = [t.scheduled_date for t in
                 _svc(repo, task_service).find_unresolved(TODAY)]
        assert dates == [DAY_BEFORE, YESTERDAY]


# ================= 10~11、24：Dialog 行为 =================

class TestDialog:
    def _dialog(self, qtbot, repo, task_service):
        _mk(repo, "A", YESTERDAY)
        _mk(repo, "B", YESTERDAY)
        tasks = _svc(repo, task_service).find_unresolved(TODAY)
        dlg = PastTaskConfirmationDialog(tasks)
        qtbot.addWidget(dlg)
        return dlg

    def test_submit_disabled_until_all_answered(self, qtbot, repo, task_service):
        dlg = self._dialog(qtbot, repo, task_service)
        assert dlg.submit_btn.isEnabled() is False
        ids = list(dlg._radios)
        dlg._radios[ids[0]][0].setChecked(True)  # 只选一个
        assert dlg.submit_btn.isEnabled() is False
        dlg._radios[ids[1]][1].setChecked(True)
        assert dlg.submit_btn.isEnabled() is True

    def test_decisions_mapping(self, qtbot, repo, task_service):
        dlg = self._dialog(qtbot, repo, task_service)
        ids = list(dlg._radios)
        dlg._radios[ids[0]][0].setChecked(True)   # 已完成
        dlg._radios[ids[1]][1].setChecked(True)   # 未完成
        decisions = dlg.result_decisions()
        assert decisions[ids[0]] == DECISION_DONE
        assert decisions[ids[1]] == DECISION_NOT_DONE

    def test_reject_leaves_no_decisions(self, qtbot, repo, task_service):
        dlg = self._dialog(qtbot, repo, task_service)
        dlg.reject()
        assert dlg.result_decisions() == {}
        # 数据未变
        assert len(_svc(repo, task_service).find_unresolved(TODAY)) == 2

    def test_escape_rejects(self, qtbot, repo, task_service):
        from PySide6.QtCore import Qt
        from PySide6.QtGui import QKeyEvent

        dlg = self._dialog(qtbot, repo, task_service)
        dlg.keyPressEvent(QKeyEvent(QKeyEvent.Type.KeyPress,
                                    Qt.Key.Key_Escape, Qt.KeyboardModifier.NoModifier))
        assert dlg.result() == QDialog.DialogCode.Rejected


# ================= 12~17、26~27：提交复用现有逻辑 =================

class TestApply:
    def test_done_reuses_complete_task(self, repo, task_service, monkeypatch):
        t = _mk(repo, "A", YESTERDAY)
        calls = []
        real = task_service.complete_task

        def spy(tid):
            calls.append(tid)
            return real(tid)

        monkeypatch.setattr(task_service, "complete_task", spy)
        _svc(repo, task_service).apply_decisions({t.id: DECISION_DONE})
        assert calls == [t.id]
        assert repo.get(t.id).status == STATUS_DONE

    def test_not_done_reuses_mark_not_done(self, repo, task_service,
                                           monkeypatch):
        t = _mk(repo, "A", YESTERDAY)
        calls = []
        real = task_service.mark_not_done

        def spy(tid, reason):
            calls.append((tid, reason))
            return real(tid, reason)

        monkeypatch.setattr(task_service, "mark_not_done", spy)
        _svc(repo, task_service).apply_decisions({t.id: DECISION_NOT_DONE})
        assert calls and calls[0][0] == t.id
        task = repo.get(t.id)
        assert task.status == STATUS_NOT_DONE
        assert task.reason

    def test_multiple_decisions(self, repo, task_service):
        a = _mk(repo, "A", YESTERDAY)
        b = _mk(repo, "B", YESTERDAY)
        c = _mk(repo, "C", DAY_BEFORE)
        _svc(repo, task_service).apply_decisions({
            a.id: DECISION_DONE,
            b.id: DECISION_NOT_DONE,
            c.id: DECISION_DONE,
        })
        assert repo.get(a.id).status == STATUS_DONE
        assert repo.get(b.id).status == STATUS_NOT_DONE
        assert repo.get(c.id).status == STATUS_DONE

    def test_no_mastery_no_assessment(self, repo, task_service, conn):
        arepo = AssessmentRepository(conn)
        kp = arepo.create_knowledge_point("kp")
        t = _mk(repo, "A", YESTERDAY, kp=kp["id"])
        _svc(repo, task_service).apply_decisions({t.id: DECISION_DONE})
        assert arepo.get_knowledge_point(kp["id"])["mastery_estimate"] == 0.0
        assert arepo.list_attempts() == []

    def test_scheduled_date_preserved(self, repo, task_service):
        t = _mk(repo, "A", YESTERDAY)
        _svc(repo, task_service).apply_decisions({t.id: DECISION_DONE})
        assert repo.get(t.id).scheduled_date == YESTERDAY

    def test_failure_raises_and_stops(self, repo, task_service, monkeypatch):
        a = _mk(repo, "A", YESTERDAY)
        b = _mk(repo, "B", YESTERDAY)

        def boom(tid):
            raise RuntimeError("db 坏了")

        monkeypatch.setattr(task_service, "complete_task", boom)
        # 先校验全部合法，再执行；第一项即失败
        try:
            _svc(repo, task_service).apply_decisions({
                a.id: DECISION_DONE, b.id: DECISION_DONE})
            raised = False
        except RuntimeError:
            raised = True
        assert raised

    def test_illegal_transition_rejected_before_apply(self, repo, task_service):
        t = _mk(repo, "A", YESTERDAY)
        repo.mark_done(t.id)  # done 是终态
        try:
            _svc(repo, task_service).apply_decisions({t.id: DECISION_DONE})
            raised = False
        except ValueError:
            raised = True
        assert raised

    def test_done_still_assessable(self, qtbot, repo, task_service, conn):
        arepo = AssessmentRepository(conn)
        kp = arepo.create_knowledge_point("kp")
        t = _mk(repo, "A", YESTERDAY, kp=kp["id"])
        _svc(repo, task_service).apply_decisions({t.id: DECISION_DONE})
        w = TaskWidget(repo.get(t.id))
        qtbot.addWidget(w)
        assert w._can_assess(repo.get(t.id)) is True
        assert hasattr(w, "assessment_btn")


# ================= 18~23、25：与启动 / Planner 的关系 =================

def _fake_dialog(monkeypatch, accepted, decisions=None):
    from app.ui import past_task_dialog as mod

    class FakeDlg:
        def __init__(self, tasks, parent=None, route_names=None):
            self.tasks = tasks

        def exec(self):
            return (QDialog.DialogCode.Accepted if accepted
                    else QDialog.DialogCode.Rejected)

        def result_decisions(self):
            if decisions is not None:
                return decisions
            return {t.id: DECISION_DONE for t in self.tasks}

    monkeypatch.setattr(mod, "PastTaskConfirmationDialog", FakeDlg)


def _preflight(repo, task_service):
    import app.main as main_mod

    return main_mod._run_past_task_preflight(repo, task_service, TODAY)


class TestStartupGate:
    def test_reject_blocks_and_leaves_data(self, repo, task_service, monkeypatch):
        t = _mk(repo, "A", YESTERDAY)
        _fake_dialog(monkeypatch, accepted=False)
        assert _preflight(repo, task_service) is False
        assert repo.get(t.id).status == STATUS_ACTIVE
        assert repo.list_by_date(TODAY) == []
        assert repo.get_meta("last_processed_date") is None

    def test_no_planner_decision_before_confirm(self, repo, task_service,
                                                monkeypatch, conn):
        _mk(repo, "A", YESTERDAY)
        _fake_dialog(monkeypatch, accepted=False)
        assert _preflight(repo, task_service) is False
        n = conn.execute("SELECT COUNT(*) FROM planner_decisions").fetchone()[0]
        assert n == 0

    def test_confirm_then_today_generated(self, repo, plan_repo, task_service,
                                          conn, monkeypatch):
        arepo = AssessmentRepository(conn)
        sps = StudyPlanService(repo, plan_repo, assessment_repo=arepo)
        sps.ensure_default_plan()
        t = _mk(repo, "A", YESTERDAY)
        _fake_dialog(monkeypatch, accepted=True)
        assert _preflight(repo, task_service) is True
        assert repo.get(t.id).status == STATUS_DONE
        # 补确认完成后，主流程才执行跨日 + 生成今天任务
        ds = DateService(repo, study_plan_service=sps)
        ds.process_date_transition(TODAY)
        assert repo.list_by_date(TODAY)

    def test_second_start_no_prompt(self, repo, task_service, monkeypatch):
        _mk(repo, "A", YESTERDAY)
        _fake_dialog(monkeypatch, accepted=True)
        assert _preflight(repo, task_service) is True
        assert _svc(repo, task_service).find_unresolved(TODAY) == []

    def test_no_history_no_dialog(self, repo, task_service):
        # 无历史任务 → 直接放行（不会弹空窗）
        assert _preflight(repo, task_service) is True

    def test_apply_failure_does_not_continue(self, repo, task_service,
                                             monkeypatch):
        t = _mk(repo, "A", YESTERDAY)
        _fake_dialog(monkeypatch, accepted=True)
        monkeypatch.setattr(
            task_service, "complete_task",
            lambda tid: (_ for _ in ()).throw(RuntimeError("boom")),
        )
        monkeypatch.setattr("app.ui.dialogs.show_warning",
                            lambda *a, **k: None)
        assert _preflight(repo, task_service) is False
        assert repo.get(t.id).status == STATUS_ACTIVE

    def test_completion_affects_coverage(self, repo, plan_repo, task_service,
                                         conn, monkeypatch):
        arepo = AssessmentRepository(conn)
        sr = SkillRepository(conn)
        ss = SkillService(sr, plan_repo=plan_repo, assessment_repo=arepo)
        sps = StudyPlanService(repo, plan_repo, assessment_repo=arepo,
                               skill_service=ss)
        sps.ensure_default_plan()
        plan = plan_repo.get_active_plan()
        phase = plan_repo.list_phases(plan.id)[0]
        topic = plan_repo.list_topics(phase.id)[0]
        sr.upsert_by_name("TopicSkill")
        ss.link_topic_by_name("TopicSkill", topic.name)
        t = _mk(repo, "A", YESTERDAY, topic_id=topic.id)
        _fake_dialog(monkeypatch, accepted=True)
        assert _preflight(repo, task_service) is True
        assert "TopicSkill" in ss.refresh_coverage()

    def test_not_done_postpone_no_duplicate_topic(self, repo, plan_repo,
                                                  task_service, conn,
                                                  monkeypatch):
        arepo = AssessmentRepository(conn)
        sps = StudyPlanService(repo, plan_repo, assessment_repo=arepo)
        sps.ensure_default_plan()
        plan = plan_repo.get_active_plan()
        phase = plan_repo.list_phases(plan.id)[0]
        topic = plan_repo.list_topics(phase.id)[0]
        t = _mk(repo, "A", YESTERDAY, topic_id=topic.id)
        _fake_dialog(monkeypatch, accepted=True,
                     decisions={t.id: DECISION_NOT_DONE})
        assert _preflight(repo, task_service) is True
        assert repo.get(t.id).status == STATUS_NOT_DONE
        # 现有延期逻辑：not_done -> 今天待办
        task_service.postpone_task(t.id)
        assert repo.get(t.id).scheduled_date == TODAY
        # 今日规划不重复生成同 topic
        res = sps.generate_daily_tasks(TODAY)
        assert topic.id not in res["selected"]
