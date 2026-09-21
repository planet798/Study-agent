"""Phase 5 测试辅助（非 test_ 前缀，不参与收集）。"""

from __future__ import annotations

from types import SimpleNamespace


def add_topic(env, project, route_id, name):
    topic = env.plan_repo.find_topic_by_name_in_route(route_id, name)
    assert topic is not None, f"topic not found: {name}"
    env.service.add_topic(project["id"], topic.id)
    return topic


def add_output(env, project, output_type, title, uri=None, details=None):
    return env.service.add_output(
        project["id"], output_type, title, uri=uri, details=details
    )


def complete(env, project):
    env.service.set_status(project["id"], "completed")
    return env.service.projects.get(project["id"])


def lora_topic(env):
    return env.plan_repo.find_topic_by_name_in_route(env.r2.id, "LoRA / QLoRA")


def ready_lora(env, name="Qwen LoRA 微调"):
    """标准场景 A：completed project + LoRA topic + repo/bench/ckpt/readme。"""
    project = env.service.create_project(
        name, "llm_training", route_ids=[env.r1.id, env.r2.id, env.r3.id]
    )
    lora = add_topic(env, project, env.r2.id, "LoRA / QLoRA")
    repo = add_output(env, project, "repository", "GitHub repo",
                      uri="https://github.com/x/qwen-lora")
    bench = add_output(env, project, "benchmark", "Benchmark",
                       details={"mmlu": 0.71})
    ckpt = add_output(env, project, "checkpoint", "Adapter ckpt",
                      uri="hf://qwen-lora")
    readme = add_output(env, project, "readme", "README",
                        uri="https://x/readme")
    complete(env, project)
    return SimpleNamespace(
        project=env.service.projects.get(project["id"]),
        lora=lora, repo=repo, bench=bench, ckpt=ckpt, readme=readme,
    )


def create_evidence(env, project, topic, output_ids, usage="真实使用", confirmed=True):
    return env.pc.create_project_topic_evidence(
        project["id"], topic.id, output_ids, usage, confirmed=confirmed,
    )


# ============================================================
# Phase 6 helpers
# ============================================================

DEFAULT_PLAN_DATE = "2026-09-15"


def current_phase_topics(env, route_id, plan_date=DEFAULT_PLAN_DATE):
    phase = env.sps_for(int(route_id)).get_current_phase(plan_date)
    return list(getattr(phase, "topics", None) or []) if phase else []


def topic_in_current_phase(env, route_id, name, plan_date=DEFAULT_PLAN_DATE):
    for t in current_phase_topics(env, route_id, plan_date):
        if t.name == name:
            return t
    return None


def ensure_capability(env, topic_id, level):
    """确保 topic 有 kp，并写入指定等级 evidence（测试用捷径，不走 UI）。"""
    topic = env.plan_repo.get_topic(int(topic_id))
    route_id = env.plan_repo.get_route_id_for_topic(int(topic_id))
    kp = env.arepo.get_or_create_knowledge_point_for_topic(
        int(topic_id), topic.name, route_id=route_id
    )
    kp_id = int(kp["id"])
    if int(level) > 0:
        env.cap.repo.create_or_update_by_key(
            knowledge_point_id=kp_id,
            capability_level=int(level),
            evidence_type="assessment",
            evidence_key=f"test:kp:{kp_id}:{level}",
            description="test evidence",
        )
    return kp_id


def make_requirement_project(
    env, route_id, topic_names, *, status="in_progress",
    targets=None, name="Qwen LoRA 微调", plan_date=DEFAULT_PLAN_DATE,
):
    """创建项目 + 关联（当前阶段内的）Topic + 设置 requirement。

    topic_names 中不在 current phase 的 Topic 用 find_topic_by_name_in_route 兜底
    （适合测试 topic_not_currently_available）。
    """
    project = env.service.create_project(
        name, "other", route_ids=[int(route_id)]
    )
    topics = []
    for tn in topic_names:
        t = topic_in_current_phase(env, route_id, tn, plan_date)
        if t is None:
            t = env.plan_repo.find_topic_by_name_in_route(int(route_id), tn)
        assert t is not None, f"topic not found: {tn}"
        env.service.add_topic(project["id"], t.id)
        topics.append(t)
    if status == "in_progress":
        env.service.set_status(project["id"], "in_progress")
    elif status == "completed":
        env.service.set_status(project["id"], "completed")
    elif status == "archived":
        env.service.archive_project(project["id"])
    for t in topics:
        target = (targets or {}).get(t.name, 3)
        env.readiness.set_requirement(project["id"], t.id, target)
    return project, topics


def mark_component_done(env, topic_id, component_id, plan_date="2026-01-01"):
    task = env.repo.create(
        title="completed component", scheduled_date=plan_date,
        topic_id=int(topic_id), component_id=int(component_id),
        source="generated", task_type="new",
    )
    env.repo.set_status(task.id, "done")
    return task


def complete_required_components(env, topic_id, except_kinds=()):
    """把所有 required 且 enabled 的 component 标记为 done（可排除某些 activity）。"""
    done = []
    for c in env.tl.get_components(int(topic_id)):
        if not (c["enabled"] and c["required"]):
            continue
        if c["activity_kind"] in except_kinds:
            continue
        mark_component_done(env, topic_id, c["id"])
        done.append(c["activity_kind"])
    return done


def has_activity(env, topic_id, kind):
    return any(
        c["activity_kind"] == kind and c["enabled"] and c["required"]
        for c in env.tl.get_components(int(topic_id))
    )


def add_same_day_task(env, topic_id, plan_date, status="active", component_id=None):
    task = env.repo.create(
        title="same day task", scheduled_date=plan_date,
        topic_id=int(topic_id), component_id=component_id,
        source="manual", task_type="new",
    )
    if status != "active":
        env.repo.set_status(task.id, status)
    return task
