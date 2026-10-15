import time

from app.qa.agents.spec import Budget


class BudgetExceeded(Exception):
    def __init__(self, which: str) -> None:
        super().__init__(f"budget exceeded: {which}")
        self.which = which


class BudgetTracker:
    def __init__(self, budget: Budget) -> None:
        self.budget = budget
        self.steps = 0
        self.tokens = 0
        self.cost_usd = 0.0
        self._started = time.monotonic()

    @property
    def elapsed(self) -> float:
        return time.monotonic() - self._started

    def check(self) -> None:
        b = self.budget
        if self.steps >= b.max_steps:
            raise BudgetExceeded("steps")
        if self.tokens >= b.max_tokens:
            raise BudgetExceeded("tokens")
        if self.elapsed >= b.max_seconds:
            raise BudgetExceeded("seconds")
        if b.max_cost_usd is not None and self.cost_usd >= b.max_cost_usd:
            raise BudgetExceeded("cost")

    def step(self) -> None:
        """Account one tool step; raises before the step runs if a cap is already hit."""
        self.check()
        self.steps += 1

    def add_tokens(self, input_tokens: int, output_tokens: int) -> None:
        self.tokens += input_tokens + output_tokens

    def snapshot(self) -> dict[str, float | int]:
        return {
            "steps": self.steps,
            "tokens": self.tokens,
            "seconds": round(self.elapsed, 1),
            "cost_usd": round(self.cost_usd, 6),
        }
