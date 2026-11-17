"""Host acceptance gates: programmatic fixtures, no Provider execution."""

import json

import pytest
from test_verify_superagent_capability import driver


def test_final_handback_requires_reviewed_completion_event_in_original_router_message():
    state = {
        "quests": [{"id": "q1", "status": "completed"}, {"id": "q2", "status": "running"}],
        "events": [
            {"id": "claim", "quest_id": "q1", "event_type": "worker.result", "payload": "{}"},
            {
                "id": "done",
                "quest_id": "q1",
                "event_type": "quest.status_changed",
                "payload": json.dumps({"to": "completed"}),
            },
            {"id": "other", "quest_id": "q2", "event_type": "quest.completed", "payload": "{}"},
        ],
        "messages": [],
    }

    def notice(event_id, *, role="assistant", source="router_notice", batch=None):
        return {
            "role": role,
            "metadata": json.dumps(
                {
                    "source": source,
                    "event_id": event_id,
                    **({"source_event_ids": batch} if batch else {}),
                }
            ),
        }

    state["messages"] = [
        notice("claim"),
        notice("other"),
        notice("done", role="user"),
        notice("done", source="worker"),
    ]
    assert driver.reviewed_notices_for_tasks(state) == set()
    state["messages"].append(notice("claim", batch=["claim", "done"]))
    assert driver.reviewed_notices_for_tasks(state) == {"q1"}


def test_operator_model_window_wins_without_changing_execution_budget():
    result = driver.resolve_context_declaration(
        {"context_length": 1000000, "max_input_tokens": 1000000, "max_output_tokens": 128000},
        operator_window=100000000,
    )
    assert result["source"] == "operator_declared"
    assert result["context_window"] == 100000000
    assert result["fields"]["context_length"] == 1000000
    assert result["declaration_conflict_retained"]
    assert not result["execution_budget_grant"] and not result["independently_measured"]
    assert (
        driver.resolve_context_declaration({"context_length": 1000000})["context_window"] == 1000000
    )


@pytest.mark.parametrize("value", [True, 0, -1, "100M"])
def test_operator_window_is_validated(value):
    with pytest.raises(ValueError):
        driver.resolve_context_declaration({"context_length": 1000000}, value)


def test_two_projects_gate_checks_task_identity_not_just_number_of_roots(tmp_path, monkeypatch):
    alpha, beta = tmp_path / "alpha", tmp_path / "beta"
    descriptors = {"L2": {"project_root": str(beta)}, "L3": {"project_root": str(alpha)}}
    links = [
        {"quest_id": "research", "root": str(alpha)},
        {"quest_id": "coding", "root": str(beta)},
    ]
    monkeypatch.setattr(driver, "rows", lambda *_args: links)
    result = driver.verify_project_links("unused", {"L2": "research", "L3": "coding"}, descriptors)
    assert result["two_detected_project_roots"]
    assert not result["correct_task_project_associations"]
    links[0]["root"], links[1]["root"] = str(beta), str(alpha)
    assert all(
        driver.verify_project_links(
            "unused", {"L2": "research", "L3": "coding"}, descriptors
        ).values()
    )


def test_parallel_workers_of_same_task_are_not_independent_task_parallelism():
    intervals = [
        {"run_id": "w1", "started_at": 0, "ended_at": 5},
        {"run_id": "w2", "started_at": 1, "ended_at": 6},
        {"run_id": "w3", "started_at": 6, "ended_at": 8},
    ]
    workers = [
        {"id": "w1", "quest_id": "q1"},
        {"id": "w2", "quest_id": "q1"},
        {"id": "w3", "quest_id": "q2"},
    ]
    assert driver.parallel_task_ownership(intervals, workers) == 1
    intervals[-1]["started_at"] = 4
    assert driver.parallel_task_ownership(intervals, workers) == 2
