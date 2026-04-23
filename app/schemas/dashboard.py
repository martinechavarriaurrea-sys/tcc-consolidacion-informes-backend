from datetime import datetime

from pydantic import BaseModel


class StatusBreakdown(BaseModel):
    status: str
    count: int


class AdvisorBreakdown(BaseModel):
    advisor_name: str
    total: int
    active: int
    delivered: int


class DashboardSummary(BaseModel):
    total_active: int
    total_delivered_today: int
    total_with_issues: int
    total_no_movement_72h: int
    status_breakdown: list[StatusBreakdown]
    advisor_breakdown: list[AdvisorBreakdown]
    last_tracking_run: datetime | None
    as_of: datetime
