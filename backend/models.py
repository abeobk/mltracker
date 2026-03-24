"""Pure dataclass helpers — no ORM."""
from dataclasses import dataclass
from typing import Optional


@dataclass
class UserRecord:
    id: int
    google_id: str
    email: str
    name: Optional[str]
    picture: Optional[str]
    api_key: str
    created_at: float


@dataclass
class ProjectRecord:
    id: int
    user_id: int
    name: str
    created_at: float


@dataclass
class RunRecord:
    id: int
    project_id: int
    name: str
    status: str
    config: Optional[str]
    created_at: float
    finished_at: Optional[float]


@dataclass
class MetricRow:
    run_id: int
    step: int
    key: str
    value: float
    ts: float


@dataclass
class ImageRow:
    run_id: int
    step: int
    key: str
    path: str
    ts: float
