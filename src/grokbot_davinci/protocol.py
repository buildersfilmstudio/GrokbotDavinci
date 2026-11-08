"""A closed read-only protocol; never transmit Python code or filesystem paths."""

from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator

Operation = Literal[
    "version", "project", "timeline", "media", "inspect", "edit", "prepare"
]


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
    payload: dict = Field(default_factory=dict)

    @model_validator(mode="after")
    def payload_schema(self):
        if self.operation == "edit":
            from .edits import Plan

            Plan.model_validate(self.payload)
        elif self.operation == "prepare":
            from .edits import Preparation

            Preparation.model_validate(self.payload)
        elif self.operation == "inspect":
            Inspection.model_validate(self.payload)
        elif self.payload:
            raise ValueError("Read operation does not accept parameters")
        return self


class Completion(StrictModel):
    worker_id: str = Field(min_length=1, max_length=64)
    lease: str = Field(min_length=32, max_length=128)
    ok: bool
    result: dict


MAX_BODY = 65536
MAX_RESULT = 49152


class Inspection(StrictModel):
    domain: Literal["capabilities", "inventory", "audio", "fusion", "render"]
    clip_id: str | None = Field(
        default=None, max_length=128, pattern=r"^[A-Za-z0-9_:-]+$"
    )
