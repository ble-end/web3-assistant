"""Reads deadlines typed in plain language. No AI needed. Always show the result back to the user."""
import re
from datetime import date, timedelta

WEEKDAYS = {
    "monday": 0, "mon": 0, "tuesday": 1, "tue": 1, "tues": 1, "wednesday": 2, "wed": 2,
    "thursday": 3, "thu": 3, "thur": 3, "thurs": 3, "friday": 4, "fri": 4,
    "saturday": 5, "sat": 5, "sunday": 6, "sun": 6,
}
MONTHS = {
    "jan": 1, "january": 1, "feb": 2, "february": 2, "mar": 3, "march": 3, "apr": 4,
    "april": 4, "may": 5, "jun": 6, "june": 6, "jul": 7, "july": 7, "aug": 8,
    "august": 8, "sep": 9, "sept": 9, "september": 9, "oct": 10, "october": 10,
    "nov": 11, "november": 11, "dec": 12, "december": 12,
}
_DEADLINE = re.compile(r"^(?P<title>.+)\s+(?:by|due)\s+(?P<when>[^\n]+)$", re.IGNORECASE)


def _make(year: int, month: int, day: int) -> date | None:
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _resolve(month: int, day: int, year: int | None, today: date) -> date | None:
    if year is not None:
        return _make(year, month, day)
    result = _make(today.year, month, day)
    if result is not None and result < today:
        result = _make(today.year + 1, month, day)  # no year given and already passed: next year
    return result


def parse_date(text: str, today: date) -> date | None:
    """Understands: today, tomorrow, friday, next friday, in 3 days, in 2 weeks,
    2026-10-20, 20/10/2026 (day first), 20/10, 20 oct, 20 october 2026, oct 20.
    A weekday name means the NEXT one after today. Returns None if it cannot be read."""
    t = re.sub(r"\s+", " ", (text or "").strip().lower().rstrip(".")).replace(",", "")
    if t.startswith("next "):
        t = t[5:]
    if not t:
        return None
    if t == "today":
        return today
    if t == "tomorrow":
        return today + timedelta(days=1)
    if t in WEEKDAYS:
        return today + timedelta(days=(WEEKDAYS[t] - today.weekday()) % 7 or 7)
    m = re.fullmatch(r"in (\d{1,3}) (day|days|week|weeks)", t)
    if m:
        return today + timedelta(days=int(m[1]) * (7 if m[2].startswith("week") else 1))
    m = re.fullmatch(r"(\d{4})-(\d{1,2})-(\d{1,2})", t)
    if m:
        return _make(int(m[1]), int(m[2]), int(m[3]))
    m = re.fullmatch(r"(\d{1,2})[/-](\d{1,2})(?:[/-](\d{4}))?", t)
    if m:  # day first, as is usual in Nigeria and the UK
        return _resolve(int(m[2]), int(m[1]), int(m[3]) if m[3] else None, today)
    m = re.fullmatch(r"(\d{1,2})(?:st|nd|rd|th)? ([a-z]+)(?: (\d{4}))?", t)
    if m and m[2] in MONTHS:
        return _resolve(MONTHS[m[2]], int(m[1]), int(m[3]) if m[3] else None, today)
    m = re.fullmatch(r"([a-z]+) (\d{1,2})(?:st|nd|rd|th)?(?: (\d{4}))?", t)
    if m and m[1] in MONTHS:
        return _resolve(MONTHS[m[1]], int(m[2]), int(m[3]) if m[3] else None, today)
    return None


def format_due(due: date, today: date) -> str:
    label = f"{due.strftime('%a')} {due.day} {due.strftime('%b %Y')}"
    delta = (due - today).days
    if delta == 0:
        return f"{label} (today)"
    if delta == 1:
        return f"{label} (tomorrow)"
    if delta > 1:
        return f"{label} (in {delta} days)"
    if delta == -1:
        return f"{label} (OVERDUE by 1 day)"
    return f"{label} (OVERDUE by {-delta} days)"


def parse_task_input(text: str, today: date) -> tuple[str, date | None, str | None]:
    """Splits 'Post launch thread by friday' into (title, deadline, warning)."""
    text = text.strip()
    m = _DEADLINE.match(text)
    if not m:
        return text, None, None
    when = m["when"].strip()
    due = parse_date(when, today)
    if due is not None:
        return m["title"].strip(), due, None
    if len(when.split()) > 3:  # probably just part of the sentence, not a deadline
        return text, None, None
    return text, None, f'I could not read the deadline "{when}", so I saved the task without one.'
