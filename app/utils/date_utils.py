from datetime import date, datetime, timedelta, timezone


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
    now = utcnow()
    return now.replace(hour=0, minute=0, second=0, microsecond=0)
