"""Timezone-aware schedule calculations, independent of database state."""

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from croniter import croniter
from dateutil.relativedelta import relativedelta

RECURRENCES = ("Once", "Hourly", "Daily", "Weekly", "Monthly", "Custom")


def schedule_start(value, zone):
    local = datetime.fromisoformat(str(value))
    if local.tzinfo is not None:
        raise ValueError("Enter a local time without an offset and select a timezone.")
    aware = local.replace(tzinfo=ZoneInfo(zone))
    round_trip = aware.astimezone(timezone.utc).astimezone(aware.tzinfo)
    if round_trip.replace(tzinfo=None) != local:
        raise ValueError("That time does not exist in this timezone because clocks change.")
    return aware


def next_occurrence(start, recurrence, expression=None, after=None):
    """Skip missed occurrences; calendar repeats retain the original local time."""
    if recurrence not in RECURRENCES:
        raise ValueError("Choose a valid repeat interval.")
    if recurrence == "Custom":
        if len((expression or "").split()) != 5 or not croniter.is_valid(expression):
            raise ValueError("Enter a valid five-field cron expression.")
        lower_bound = start.astimezone(timezone.utc) - timedelta(microseconds=1)
        base = max(lower_bound, after or lower_bound)
        return croniter(expression, base.astimezone(start.tzinfo)).get_next(datetime)
    if after is None or start.astimezone(timezone.utc) > after:
        return start
    if recurrence == "Once":
        return None
    if recurrence == "Hourly":
        anchor = start.astimezone(timezone.utc)
        hours = int((after - anchor).total_seconds() // 3600) + 1
        return (anchor + timedelta(hours=hours)).astimezone(start.tzinfo)
    local_after = after.astimezone(start.tzinfo)
    if recurrence == "Monthly":
        count = max(0, (local_after.year - start.year) * 12 + local_after.month - start.month)
        candidate = start + relativedelta(months=count)
        while candidate.astimezone(timezone.utc) <= after:
            count += 1
            candidate = start + relativedelta(months=count)
    else:
        days = 7 if recurrence == "Weekly" else 1
        count = max(0, (local_after.date() - start.date()).days // days)
        candidate = start + timedelta(days=count * days)
        while candidate.astimezone(timezone.utc) <= after:
            count += 1
            candidate = start + timedelta(days=count * days)
    return candidate.astimezone(timezone.utc).astimezone(start.tzinfo)
