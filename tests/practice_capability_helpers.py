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
