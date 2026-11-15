from .domain import (
    ForgeEvent,
    QuestStatus,
    QuestVersionConflict,
    now_iso,
    validate_quest_transition,
)


class QuestService:
    def __init__(self, store, *, completion_gate=None):
        self.store = store
        self.completion_gate = completion_gate

    def complete(self, quest_id: str, *, expected_version: int | None = None):
        """Internal completion path; the evidence gate and CAS share one transaction."""
        from .evidence import CompletionGate

        with self.store.unit_of_work() as work:
            record = work.get_quest(quest_id)
            if record is None:
                raise KeyError(quest_id)
            if expected_version is not None and record["version"] != expected_version:
                raise QuestVersionConflict("Quest version conflict")
            current = QuestStatus(record["status"])
            validate_quest_transition(current, QuestStatus.COMPLETED)
            gate = self.completion_gate or CompletionGate(self.store)
            gate.check(work, record)
            cursor = work.connection.execute(
                "UPDATE quests SET status=?,updated_at=?,version=version+1 WHERE id=? AND status=? AND version=?",
                (
                    QuestStatus.COMPLETED.value,
                    now_iso(),
                    quest_id,
                    current.value,
                    record["version"],
                ),
            )
            if cursor.rowcount != 1:
                raise QuestVersionConflict("Quest version conflict during completion")
            work.append_event(
                ForgeEvent(
                    event_type="quest.status_changed",
                    quest_id=quest_id,
                    conversation_id=record["conversation_id"],
                    payload={
                        "from": current.value,
                        "to": QuestStatus.COMPLETED.value,
                        "version": record["version"] + 1,
                    },
                )
            )
            return work.get_quest(quest_id)

    def transition(
        self, quest_id: str, target: QuestStatus, *, expected_version: int | None = None
    ):
        with self.store.unit_of_work() as work:
            record = work.get_quest(quest_id)
            if record is None:
                raise KeyError(quest_id)
            if expected_version is not None and record["version"] != expected_version:
                raise QuestVersionConflict(
                    f"Quest version conflict: expected {expected_version}, current {record['version']}"
                )
            current = QuestStatus(record["status"])
            validate_quest_transition(current, target)
            if not work.transition_quest(
                quest_id,
                target.value,
                now_iso(),
                expected_version=record["version"],
                expected_status=current.value,
            ):
                raise QuestVersionConflict("Quest version conflict during transition")
            work.append_event(
                ForgeEvent(
                    event_type="quest.status_changed",
                    conversation_id=record["conversation_id"],
                    quest_id=quest_id,
                    payload={
                        "from": current.value,
                        "to": target.value,
                        "version": record["version"] + 1,
                    },
                )
            )
            return work.get_quest(quest_id)
