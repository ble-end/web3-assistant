from datetime import date

import pytest
from sqlalchemy import func, select

from assistant.db.models import Campaign, Note, Task
from assistant.db.store import CAMPAIGN_FIELDS, MAX_CAMPAIGNS, MAX_PROJECTS, Store, StoreError

A, B = 1001, 2002


@pytest.fixture
def store(tmp_path):
    s = Store(f"sqlite:///{(tmp_path / 'test.db').as_posix()}")
    s.create_tables()
    yield s
    s.dispose()


def test_create_project_sets_it_active(store):
    p = store.create_project(A, "Moon Token")
    assert store.get_active_project(A).id == p.id


def test_duplicate_name_rejected_ignoring_case(store):
    store.create_project(A, "Moon Token")
    with pytest.raises(StoreError):
        store.create_project(A, "moon token")


def test_same_name_allowed_for_different_people(store):
    store.create_project(A, "Moon Token")
    store.create_project(B, "Moon Token")


def test_project_limit(store):
    for i in range(MAX_PROJECTS):
        store.create_project(A, f"P{i}")
    with pytest.raises(StoreError):
        store.create_project(A, "one too many")


def test_empty_and_too_long_names_rejected(store):
    with pytest.raises(StoreError):
        store.create_project(A, "   ")
    with pytest.raises(StoreError):
        store.create_project(A, "x" * 61)


def test_update_fields(store):
    p = store.create_project(A, "Moon Token")
    store.update_project_field(A, p.id, "blockchain", "Base")
    assert store.get_project(A, p.id).blockchain == "Base"
    with pytest.raises(StoreError):
        store.update_project_field(A, p.id, "owner_id", "999")  # not an editable field


def test_other_user_cannot_touch_my_project(store):
    p = store.create_project(A, "Secret Launch")
    for action in (
        lambda: store.get_project(B, p.id),
        lambda: store.set_active_project(B, p.id),
        lambda: store.update_project_field(B, p.id, "goals", "hacked"),
        lambda: store.delete_project(B, p.id),
        lambda: store.add_note(B, p.id, "fact", "hi"),
        lambda: store.list_notes(B, p.id),
    ):
        with pytest.raises(StoreError):
            action()
    assert store.get_project(A, p.id).name == "Secret Launch"  # untouched


def test_other_user_cannot_touch_my_notes(store):
    p = store.create_project(A, "Secret Launch")
    n = store.add_note(A, p.id, "fact", "launch is on Friday")
    with pytest.raises(StoreError):
        store.set_note_status(B, n.id, "outdated")
    with pytest.raises(StoreError):
        store.delete_note(B, n.id)
    assert len(store.list_notes(A, p.id)) == 1


def test_users_have_separate_project_lists(store):
    store.create_project(A, "Mine")
    store.create_project(B, "Theirs")
    assert [p.name for p in store.list_projects(A)] == ["Mine"]
    assert [p.name for p in store.list_projects(B)] == ["Theirs"]


def test_notes_kinds_and_outdating(store):
    p = store.create_project(A, "Moon Token")
    n = store.add_note(A, p.id, "assumption", "Audience is mostly in Nigeria")
    assert n.kind == "assumption" and n.status == "active"
    store.set_note_status(A, n.id, "outdated")
    assert store.list_notes(A, p.id)[0].status == "outdated"
    with pytest.raises(StoreError):
        store.add_note(A, p.id, "gossip", "not a valid kind")


def test_delete_note(store):
    p = store.create_project(A, "Moon Token")
    n = store.add_note(A, p.id, "fact", "x")
    store.delete_note(A, n.id)
    assert store.list_notes(A, p.id) == []


def test_deleting_project_deletes_its_notes_and_clears_active(store):
    p = store.create_project(A, "Moon Token")
    store.add_note(A, p.id, "fact", "one")
    store.add_note(A, p.id, "fact", "two")
    store.delete_project(A, p.id)
    assert store.get_active_project(A) is None
    with store._session() as s:
        assert s.scalar(select(func.count()).select_from(Note)) == 0


def test_user_access_add_remove_reactivate(store):
    assert not store.is_active_user(B)
    assert store.add_user(B, A) == "added"
    assert store.is_active_user(B)
    assert store.add_user(B, A) == "exists"
    assert store.remove_user(B) is True
    assert not store.is_active_user(B)
    assert store.remove_user(B) is False
    assert store.add_user(B, A) == "reactivated"


def test_removed_user_keeps_data_but_loses_access(store):
    store.add_user(B, A)
    store.create_project(B, "Friend Project")
    store.remove_user(B)
    assert not store.is_active_user(B)
    assert len(store.list_projects(B)) == 1


def test_data_survives_restart(tmp_path):
    url = f"sqlite:///{(tmp_path / 'persist.db').as_posix()}"
    first = Store(url)
    first.create_tables()
    p = first.create_project(A, "Persistent")
    first.add_note(A, p.id, "fact", "still here")
    first.dispose()

    second = Store(url)  # simulates restarting the bot
    second.create_tables()
    assert second.get_active_project(A).name == "Persistent"
    assert second.list_notes(A, p.id)[0].content == "still here"
    second.dispose()


def test_creates_missing_database_folder(tmp_path):
    url = f"sqlite:///{(tmp_path / 'newfolder' / 'x.db').as_posix()}"
    s = Store(url)
    s.create_tables()
    assert (tmp_path / "newfolder" / "x.db").exists()
    s.dispose()


# ----------------------------------------------------------------- tasks

TODAY = date(2026, 10, 15)


def test_add_and_list_tasks_soonest_deadline_first(store):
    p = store.create_project(A, "Moon")
    later = store.add_task(A, p.id, "later", date(2026, 11, 1))
    none = store.add_task(A, p.id, "no deadline")
    soon = store.add_task(A, p.id, "soon", date(2026, 10, 16))
    assert [t.id for t in store.list_tasks(A, p.id)] == [soon.id, later.id, none.id]


def test_complete_and_reopen_task(store):
    p = store.create_project(A, "Moon")
    t = store.add_task(A, p.id, "ship")
    done = store.set_task_status(A, t.id, "done")
    assert done.status == "done" and done.completed_at is not None
    assert store.list_tasks(A, p.id) == []
    assert [x.id for x in store.list_tasks(A, p.id, "done")] == [t.id]
    reopened = store.set_task_status(A, t.id, "open")
    assert reopened.status == "open" and reopened.completed_at is None


def test_task_details_can_be_set_and_cleared(store):
    p = store.create_project(A, "Moon")
    t = store.add_task(A, p.id, "ship")
    store.set_task_due(A, t.id, date(2026, 12, 1))
    store.set_task_assignee(A, t.id, "Ada")
    store.set_task_notes(A, t.id, "needs design first")
    got = store.get_task(A, t.id)
    assert (got.due_date, got.assignee, got.notes) == (date(2026, 12, 1), "Ada", "needs design first")
    store.set_task_due(A, t.id, None)
    store.set_task_assignee(A, t.id, None)
    store.set_task_notes(A, t.id, None)
    got = store.get_task(A, t.id)
    assert (got.due_date, got.assignee, got.notes) == (None, None, None)


def test_task_validation(store):
    p = store.create_project(A, "Moon")
    with pytest.raises(StoreError):
        store.add_task(A, p.id, "   ")
    with pytest.raises(StoreError):
        store.add_task(A, p.id, "x" * 201)
    t = store.add_task(A, p.id, "ok")
    with pytest.raises(StoreError):
        store.set_task_assignee(A, t.id, "x" * 101)


def test_task_counts_and_overdue(store):
    p = store.create_project(A, "Moon")
    store.add_task(A, p.id, "overdue", date(2026, 10, 1))
    store.add_task(A, p.id, "future", date(2026, 12, 1))
    finished = store.add_task(A, p.id, "finished", date(2026, 10, 1))
    store.set_task_status(A, finished.id, "done")
    assert store.task_counts(A, p.id, TODAY) == {"open": 2, "done": 1, "overdue": 1}


def test_other_user_cannot_touch_my_tasks(store):
    p = store.create_project(A, "Secret")
    t = store.add_task(A, p.id, "private task")
    for action in (
        lambda: store.add_task(B, p.id, "intruder"),
        lambda: store.list_tasks(B, p.id),
        lambda: store.get_task(B, t.id),
        lambda: store.set_task_status(B, t.id, "done"),
        lambda: store.set_task_due(B, t.id, TODAY),
        lambda: store.set_task_assignee(B, t.id, "x"),
        lambda: store.set_task_notes(B, t.id, "x"),
        lambda: store.delete_task(B, t.id),
        lambda: store.task_counts(B, p.id, TODAY),
    ):
        with pytest.raises(StoreError):
            action()
    assert store.get_task(A, t.id).status == "open"


def test_delete_task(store):
    p = store.create_project(A, "Moon")
    t = store.add_task(A, p.id, "bye")
    assert store.delete_task(A, t.id) == "bye"
    with pytest.raises(StoreError):
        store.get_task(A, t.id)


def test_deleting_project_deletes_its_tasks(store):
    p = store.create_project(A, "Moon")
    store.add_task(A, p.id, "one")
    store.add_task(A, p.id, "two")
    store.delete_project(A, p.id)
    with store._session() as s:
        assert s.scalar(select(func.count()).select_from(Task)) == 0


def test_tasks_survive_restart(tmp_path):
    url = f"sqlite:///{(tmp_path / 'tasks.db').as_posix()}"
    first = Store(url)
    first.create_tables()
    p = first.create_project(A, "Moon")
    first.add_task(A, p.id, "remember me", date(2026, 11, 1))
    first.dispose()
    second = Store(url)
    second.create_tables()
    assert second.list_tasks(A, p.id)[0].due_date == date(2026, 11, 1)
    second.dispose()


# ------------------------------------------------------------- campaigns


def test_campaign_fields_match_the_plan():
    assert list(CAMPAIGN_FIELDS) == [
        "objective", "audience", "strategy", "activities", "timeline",
        "responsibilities", "resources", "metrics", "reporting", "risks",
    ]


def test_campaign_create_fill_clear_and_list(store):
    p = store.create_project(A, "Moon")
    c = store.add_campaign(A, p.id, "Launch push")
    store.set_campaign_field(A, c.id, "objective", "Reach 3k members")
    assert store.get_campaign(A, c.id).objective == "Reach 3k members"
    store.set_campaign_field(A, c.id, "objective", None)
    assert store.get_campaign(A, c.id).objective is None
    assert [x.name for x in store.list_campaigns(A, p.id)] == ["Launch push"]


def test_campaign_validation(store):
    p = store.create_project(A, "Moon")
    with pytest.raises(StoreError):
        store.add_campaign(A, p.id, "  ")
    c = store.add_campaign(A, p.id, "ok")
    with pytest.raises(StoreError):
        store.set_campaign_field(A, c.id, "owner_id", "x")
    with pytest.raises(StoreError):
        store.set_campaign_field(A, c.id, "objective", "x" * 801)


def test_campaign_limit(store):
    p = store.create_project(A, "Moon")
    for i in range(MAX_CAMPAIGNS):
        store.add_campaign(A, p.id, f"c{i}")
    with pytest.raises(StoreError):
        store.add_campaign(A, p.id, "too many")


def test_other_user_cannot_touch_my_campaigns(store):
    p = store.create_project(A, "Secret")
    c = store.add_campaign(A, p.id, "Private plan")
    for action in (
        lambda: store.add_campaign(B, p.id, "intruder"),
        lambda: store.list_campaigns(B, p.id),
        lambda: store.get_campaign(B, c.id),
        lambda: store.set_campaign_field(B, c.id, "objective", "hacked"),
        lambda: store.delete_campaign(B, c.id),
    ):
        with pytest.raises(StoreError):
            action()
    assert store.get_campaign(A, c.id).objective is None


def test_deleting_project_deletes_its_campaigns(store):
    p = store.create_project(A, "Moon")
    store.add_campaign(A, p.id, "one")
    store.delete_project(A, p.id)
    with store._session() as s:
        assert s.scalar(select(func.count()).select_from(Campaign)) == 0


def test_delete_campaign(store):
    p = store.create_project(A, "Moon")
    c = store.add_campaign(A, p.id, "bye")
    assert store.delete_campaign(A, c.id) == "bye"
    with pytest.raises(StoreError):
        store.get_campaign(A, c.id)
