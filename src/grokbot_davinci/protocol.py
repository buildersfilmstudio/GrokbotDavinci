"""A closed read-only protocol; never transmit Python code or filesystem paths."""

from typing import Literal
from pydantic import BaseModel, ConfigDict, Field

Operation = Literal["version", "project", "timeline", "media"]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Claim(StrictModel):
    worker_id: str = Field(min_length=1, max_length=64, pattern=r"^[a-zA-Z0-9_-]+$")


class Job(StrictModel):
    id: str = Field(
        pattern=r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
    )
    operation: Operation
    lease: str = Field(min_length=32, max_length=128)
    expires_at: float = Field(allow_inf_nan=False)


class Completion(StrictModel):
    worker_id: str = Field(min_length=1, max_length=64)
    lease: str = Field(min_length=32, max_length=128)
    ok: bool
    result: dict


MAX_BODY = 65536
MAX_RESULT = 49152
