"""Regenerate public relay schemas from the worker's single typed vocabulary."""

import json
from pathlib import Path
from grokbot_davinci.edits import Preparation, Plan

plan = Plan.model_json_schema()
definitions = plan.pop("$defs")
definitions["Plan"] = plan
execution = {
    "type": "object",
    "additionalProperties": False,
    "required": ["plan", "confirmation"],
    "properties": {
        "plan": {"$ref": "#/$defs/Plan"},
        "confirmation": {"type": "string", "maxLength": 80},
    },
    "$defs": definitions,
}
path = Path(__file__).resolve().parents[1] / "neon-relay/src/schemas.json"
path.write_text(
    json.dumps(
        {"preparation": Preparation.model_json_schema(), "execution": execution},
        indent=2,
    )
    + "\n"
)
