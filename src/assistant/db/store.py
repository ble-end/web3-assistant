"""All database operations live here.

Every project or note operation requires the owner's Telegram ID and checks it,
so one person can never read or change another person's data.
"""
from datetime import date
from pathlib import Path

from sqlalchemy import create_engine, select
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from assistant.db.models import Base, Campaign, Note, Project, Task, User, utcnow

MAX_PROJECTS = 10
MAX_NOTES = 200
NOTE_MAX_CHARS = 1000
NAME_MAX_CHARS = 60
MAX_OPEN_TASKS = 200
MAX_CAMPAIGNS = 20
CAMPAIGN_NAME_MAX = 100
CAMPAIGN_FIELD_MAX = 800
# field -> (label, question asked when filling it in). Order matters.
CAMPAIGN_FIELDS = {
    "objective": ("Objective", "What do you want this campaign to achieve? Be specific, for example: grow Telegram members from 1,000 to 3,000 in 30 days."),
    "audience": ("Target audience", "Who exactly are you trying to reach?"),
    "strategy": ("Strategy", "What is the main approach, and why should it work for this audience?"),
    "activities": ("Activities", "What will you actually do (posts, threads, AMAs, giveaways, partnerships...)?"),
    "timeline": ("Timeline", "Key dates and phases, from start to finish."),
    "responsibilities": ("Responsibilities", "Who is responsible for what?"),
    "resources": ("Resources needed", "Budget, tools, people and assets you need."),
    "metrics": ("Success metrics", "How will you measure success? Include numbers (impressions, engagement rate, follower or member growth, link clicks, conversions) AND quality (community sentiment, discussion quality, trust). Impressions alone do not prove growth."),
    "reporting": ("Reporting schedule", "How often will you review results, and with whom?"),
    "risks": ("Risks and contingencies", "What could go wrong, and what is the plan if it does?"),
}
TASK_TITLE_MAX = 200
TASK_ASSIGNEE_MAX = 100
TASK_NOTES_MAX = 500
TASK_STATUSES = ("open", "done")
NOTE_KINDS = ("fact", "assumption", "decision", "question")
NOTE_STATUSES = ("active", "outdated")
# editable project field -> maximum length
PROJECT_FIELDS = {
    "blockchain": 100,
    "website": 200,
    "audience": 300,
    "voice": 300,
    "description": 1000,
    "goals": 1000,
}


class StoreError(Exception):
    """A problem that is safe to show to the user as-is."""


def _clean(text: str | None, limit: int, label: str) -> str:
    text = (text or "").strip()
    if not text:
        raise StoreError(f"{label} cannot be empty.")
    if len(text) > limit:
        raise StoreError(f"{label} is too long (maximum {limit} characters).")
    return text


class Store:
    def __init__(self, url: str):
        self._prepare_sqlite_folder(url)
        self.engine = create_engine(url, pool_pre_ping=True)
        self._session = sessionmaker(self.engine, expire_on_commit=False)

    @staticmethod
    def _prepare_sqlite_folder(url: str) -> None:
        parsed = make_url(url)
        if parsed.drivername.startswith("sqlite") and parsed.database not in (None, "", ":memory:"):
            Path(parsed.database).parent.mkdir(parents=True, exist_ok=True)

    @property
    def dialect(self) -> str:
        return self.engine.dialect.name

    def create_tables(self) -> None:
        Base.metadata.create_all(self.engine)

    def dispose(self) -> None:
        self.engine.dispose()

    # ---------------------------------------------------------------- users

    def ensure_user(self, telegram_id: int, added_by: int | None = None) -> None:
        try:
            with self._session.begin() as s:
                if s.get(User, telegram_id) is None:
                    s.add(User(telegram_id=telegram_id, added_by=added_by))
        except IntegrityError:
            pass  # another request created the same row at the same moment

    def add_user(self, telegram_id: int, added_by: int) -> str:
        """Returns 'added', 'reactivated' or 'exists'."""
        with self._session.begin() as s:
            user = s.get(User, telegram_id)
            if user is None:
                s.add(User(telegram_id=telegram_id, added_by=added_by))
                return "added"
            if not user.is_active:
                user.is_active = True
                return "reactivated"
            return "exists"

    def remove_user(self, telegram_id: int) -> bool:
        """Turns access off. Their data is kept. Returns False if they had no access."""
        with self._session.begin() as s:
            user = s.get(User, telegram_id)
            if user is None or not user.is_active:
                return False
            user.is_active = False
            return True

    def is_active_user(self, telegram_id: int) -> bool:
        with self._session() as s:
            user = s.get(User, telegram_id)
            return bool(user and user.is_active)

    def list_users(self) -> list[User]:
        with self._session() as s:
            return list(
                s.scalars(select(User).where(User.is_active.is_(True)).order_by(User.created_at))
            )

    # ------------------------------------------------------------- projects

    @staticmethod
    def _owned_project(s, owner_id: int, project_id: int) -> Project:
        project = s.get(Project, project_id)
        if project is None or project.owner_id != owner_id:
            # Same message whether it does not exist or belongs to someone else.
            raise StoreError("I could not find that project.")
        return project

    def create_project(self, owner_id: int, name: str) -> Project:
        name = _clean(name, NAME_MAX_CHARS, "The project name")
        with self._session.begin() as s:
            user = s.get(User, owner_id)
            if user is None:
                user = User(telegram_id=owner_id)
                s.add(user)
                s.flush()
            existing = list(s.scalars(select(Project).where(Project.owner_id == owner_id)))
            if len(existing) >= MAX_PROJECTS:
                raise StoreError(
                    f"You already have {MAX_PROJECTS} projects, which is the limit. "
                    "Delete one with /deleteproject first."
                )
            for other in existing:
                if other.name.lower() == name.lower():
                    raise StoreError(f'You already have a project called "{other.name}".')
            project = Project(owner_id=owner_id, name=name)
            s.add(project)
            s.flush()
            user.active_project_id = project.id
            return project

    def list_projects(self, owner_id: int) -> list[Project]:
        with self._session() as s:
            return list(
                s.scalars(
                    select(Project).where(Project.owner_id == owner_id).order_by(Project.id)
                )
            )

    def get_project(self, owner_id: int, project_id: int) -> Project:
        with self._session() as s:
            return self._owned_project(s, owner_id, project_id)

    def get_active_project(self, owner_id: int) -> Project | None:
        with self._session() as s:
            user = s.get(User, owner_id)
            if user is None or user.active_project_id is None:
                return None
            project = s.get(Project, user.active_project_id)
            if project is None or project.owner_id != owner_id:
                return None
            return project

    def set_active_project(self, owner_id: int, project_id: int) -> Project:
        with self._session.begin() as s:
            project = self._owned_project(s, owner_id, project_id)
            user = s.get(User, owner_id)
            if user is None:
                user = User(telegram_id=owner_id)
                s.add(user)
            user.active_project_id = project.id
            return project

    def update_project_field(self, owner_id: int, project_id: int, field: str, value: str) -> Project:
        if field not in PROJECT_FIELDS:
            raise StoreError("That field cannot be changed.")
        value = _clean(value, PROJECT_FIELDS[field], f"The {field}")
        with self._session.begin() as s:
            project = self._owned_project(s, owner_id, project_id)
            setattr(project, field, value)
            return project

    def delete_project(self, owner_id: int, project_id: int) -> str:
        """Deletes the project and all of its notes. Returns the project's name."""
        with self._session.begin() as s:
            project = self._owned_project(s, owner_id, project_id)
            name = project.name
            user = s.get(User, owner_id)
            if user is not None and user.active_project_id == project_id:
                user.active_project_id = None
            s.delete(project)
            return name

    # ---------------------------------------------------------------- notes

    @staticmethod
    def _owned_note(s, owner_id: int, note_id: int) -> Note:
        note = s.get(Note, note_id)
        if note is None or note.owner_id != owner_id:
            raise StoreError("I could not find that note.")
        return note

    def add_note(self, owner_id: int, project_id: int, kind: str, content: str) -> Note:
        if kind not in NOTE_KINDS:
            raise StoreError(f"A note must be one of: {', '.join(NOTE_KINDS)}.")
        content = _clean(content, NOTE_MAX_CHARS, "The note")
        with self._session.begin() as s:
            self._owned_project(s, owner_id, project_id)
            count = len(list(s.scalars(select(Note.id).where(Note.project_id == project_id))))
            if count >= MAX_NOTES:
                raise StoreError(f"This project already has {MAX_NOTES} notes. Delete some first.")
            note = Note(project_id=project_id, owner_id=owner_id, kind=kind, content=content)
            s.add(note)
            s.flush()
            return note

    def list_notes(self, owner_id: int, project_id: int) -> list[Note]:
        with self._session() as s:
            self._owned_project(s, owner_id, project_id)
            return list(
                s.scalars(select(Note).where(Note.project_id == project_id).order_by(Note.id))
            )

    def set_note_status(self, owner_id: int, note_id: int, status: str) -> Note:
        if status not in NOTE_STATUSES:
            raise StoreError("Unknown note status.")
        with self._session.begin() as s:
            note = self._owned_note(s, owner_id, note_id)
            note.status = status
            return note

    def delete_note(self, owner_id: int, note_id: int) -> None:
        with self._session.begin() as s:
            s.delete(self._owned_note(s, owner_id, note_id))

    # ---------------------------------------------------------------- tasks

    @staticmethod
    def _owned_task(s, owner_id: int, task_id: int) -> Task:
        task = s.get(Task, task_id)
        if task is None or task.owner_id != owner_id:
            raise StoreError("I could not find that task.")
        return task

    def add_task(self, owner_id: int, project_id: int, title: str, due_date: date | None = None) -> Task:
        title = _clean(title, TASK_TITLE_MAX, "The task")
        with self._session.begin() as s:
            self._owned_project(s, owner_id, project_id)
            open_count = len(
                list(
                    s.scalars(
                        select(Task.id).where(Task.project_id == project_id, Task.status == "open")
                    )
                )
            )
            if open_count >= MAX_OPEN_TASKS:
                raise StoreError(f"This project already has {MAX_OPEN_TASKS} open tasks. Finish or delete some first.")
            task = Task(project_id=project_id, owner_id=owner_id, title=title, due_date=due_date)
            s.add(task)
            s.flush()
            return task

    def list_tasks(self, owner_id: int, project_id: int, status: str = "open") -> list[Task]:
        """Open tasks come soonest-deadline first (no deadline last). Done tasks newest first."""
        if status not in TASK_STATUSES:
            raise StoreError("Unknown task status.")
        with self._session() as s:
            self._owned_project(s, owner_id, project_id)
            tasks = list(
                s.scalars(select(Task).where(Task.project_id == project_id, Task.status == status))
            )
        if status == "open":
            return sorted(tasks, key=lambda t: (t.due_date is None, t.due_date or date.max, t.id))
        return sorted(tasks, key=lambda t: t.id, reverse=True)

    def get_task(self, owner_id: int, task_id: int) -> Task:
        with self._session() as s:
            return self._owned_task(s, owner_id, task_id)

    def set_task_status(self, owner_id: int, task_id: int, status: str) -> Task:
        if status not in TASK_STATUSES:
            raise StoreError("Unknown task status.")
        with self._session.begin() as s:
            task = self._owned_task(s, owner_id, task_id)
            task.status = status
            task.completed_at = utcnow() if status == "done" else None
            return task

    def set_task_due(self, owner_id: int, task_id: int, due_date: date | None) -> Task:
        with self._session.begin() as s:
            task = self._owned_task(s, owner_id, task_id)
            task.due_date = due_date
            return task

    def set_task_assignee(self, owner_id: int, task_id: int, assignee: str | None) -> Task:
        if assignee is not None:
            assignee = _clean(assignee, TASK_ASSIGNEE_MAX, "The name")
        with self._session.begin() as s:
            task = self._owned_task(s, owner_id, task_id)
            task.assignee = assignee
            return task

    def set_task_notes(self, owner_id: int, task_id: int, notes: str | None) -> Task:
        if notes is not None:
            notes = _clean(notes, TASK_NOTES_MAX, "The task note")
        with self._session.begin() as s:
            task = self._owned_task(s, owner_id, task_id)
            task.notes = notes
            return task

    def delete_task(self, owner_id: int, task_id: int) -> str:
        with self._session.begin() as s:
            task = self._owned_task(s, owner_id, task_id)
            title = task.title
            s.delete(task)
            return title

    def task_counts(self, owner_id: int, project_id: int, today: date) -> dict:
        with self._session() as s:
            self._owned_project(s, owner_id, project_id)
            tasks = list(s.scalars(select(Task).where(Task.project_id == project_id)))
        open_tasks = [t for t in tasks if t.status == "open"]
        return {
            "open": len(open_tasks),
            "done": len(tasks) - len(open_tasks),
            "overdue": sum(1 for t in open_tasks if t.due_date and t.due_date < today),
        }

    # ------------------------------------------------------------ campaigns

    @staticmethod
    def _owned_campaign(s, owner_id: int, campaign_id: int) -> Campaign:
        campaign = s.get(Campaign, campaign_id)
        if campaign is None or campaign.owner_id != owner_id:
            raise StoreError("I could not find that campaign.")
        return campaign

    def add_campaign(self, owner_id: int, project_id: int, name: str) -> Campaign:
        name = _clean(name, CAMPAIGN_NAME_MAX, "The campaign name")
        with self._session.begin() as s:
            self._owned_project(s, owner_id, project_id)
            existing = list(s.scalars(select(Campaign.id).where(Campaign.project_id == project_id)))
            if len(existing) >= MAX_CAMPAIGNS:
                raise StoreError(f"This project already has {MAX_CAMPAIGNS} campaigns. Delete one first.")
            campaign = Campaign(project_id=project_id, owner_id=owner_id, name=name)
            s.add(campaign)
            s.flush()
            return campaign

    def list_campaigns(self, owner_id: int, project_id: int) -> list[Campaign]:
        with self._session() as s:
            self._owned_project(s, owner_id, project_id)
            return list(
                s.scalars(select(Campaign).where(Campaign.project_id == project_id).order_by(Campaign.id))
            )

    def get_campaign(self, owner_id: int, campaign_id: int) -> Campaign:
        with self._session() as s:
            return self._owned_campaign(s, owner_id, campaign_id)

    def set_campaign_field(self, owner_id: int, campaign_id: int, field: str, value: str | None) -> Campaign:
        if field not in CAMPAIGN_FIELDS:
            raise StoreError("That campaign field does not exist.")
        if value is not None:
            value = _clean(value, CAMPAIGN_FIELD_MAX, CAMPAIGN_FIELDS[field][0])
        with self._session.begin() as s:
            campaign = self._owned_campaign(s, owner_id, campaign_id)
            setattr(campaign, field, value)
            return campaign

    def delete_campaign(self, owner_id: int, campaign_id: int) -> str:
        with self._session.begin() as s:
            campaign = self._owned_campaign(s, owner_id, campaign_id)
            name = campaign.name
            s.delete(campaign)
            return name
