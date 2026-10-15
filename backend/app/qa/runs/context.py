from dataclasses import dataclass
from typing import Any

from redis.asyncio import Redis

from app.qa.events import EventPublisher
from app.qa.models import Project, Run
from app.qa.runs.control import RunControl
from app.qa.security.scanner import SecretScanner


@dataclass
class RunContext:
    run: Run
    project: Project
    redis: Redis
    events: EventPublisher
    control: RunControl
    scanner: SecretScanner
    checkpointer: Any | None = None

    @property
    def run_id(self) -> str:
        return str(self.run.id)
