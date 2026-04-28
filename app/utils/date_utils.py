from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo


BOGOTA_TZ = ZoneInfo("America/Bogota")


def utcnow() -> datetime:
    return datetime.now(tz=timezone.utc)


def week_boundaries(reference: date | None = None) -> tuple[date, date]:
    """Retorna (lunes, domingo) de la semana que contiene 'reference'."""
    ref = reference or date.today()
    monday = ref - timedelta(days=ref.weekday())
    sunday = monday + timedelta(days=6)
    return monday, sunday


def hours_since(dt: datetime) -> float:
    """Horas transcurridas desde 'dt' hasta ahora (UTC)."""
    now = utcnow()
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return (now - dt).total_seconds() / 3600


def is_older_than_hours(dt: datetime, hours: float) -> bool:
    return hours_since(dt) >= hours


def start_of_today() -> datetime:
    now_local = utcnow().astimezone(BOGOTA_TZ)
    local_start = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
    return local_start.astimezone(timezone.utc)


def count_days_excluding_sundays(start: date, end: date) -> int:
    """Cuenta días entre start y end excluyendo domingos (weekday==6)."""
    if end <= start:
        return 0
    total = (end - start).days
    # Contar cuántos domingos caen en el rango [start, end)
    # El primer domingo >= start
    days_to_first_sunday = (6 - start.weekday()) % 7
    if days_to_first_sunday >= total:
        return total
    sundays = (total - days_to_first_sunday + 6) // 7
    return total - sundays
