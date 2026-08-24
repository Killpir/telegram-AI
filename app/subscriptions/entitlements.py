from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class SubscriptionEntitlements:
    duration_days: int
    requests_limit: int
    smart_requests_limit: int
    input_tokens_limit: int
    output_tokens_limit: int

    def validate(self) -> None:
        if self.duration_days <= 0:
            raise ValueError("duration_days must be positive")
        if self.requests_limit < 0 or self.smart_requests_limit < 0:
            raise ValueError("request limits must be non-negative")
        if self.input_tokens_limit <= 0 or self.output_tokens_limit <= 0:
            raise ValueError("token limits must be positive")
