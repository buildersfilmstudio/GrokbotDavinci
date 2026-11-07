"""Typed, finite edit vocabulary. No property/method/code passthrough."""

import rfc8785
import hashlib
import uuid
from typing import Annotated, Literal
from pydantic import Field, TypeAdapter, model_validator
from .protocol import StrictModel

Name = Annotated[str, Field(min_length=1, max_length=128)]
Identifier = Annotated[
    str, Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_:-]+$")
]
Frame = Annotated[int, Field(ge=0, le=100000000)]
Number = Annotated[float, Field(allow_inf_nan=False, ge=-100000, le=100000)]


class CopyTimeline(StrictModel):
    kind: Literal["copy_timeline"]
    name: Name


class CopyProject(StrictModel):
    kind: Literal["copy_project"]
    name: Name
    backup_path: str = Field(min_length=1, max_length=4096)


class RelinkMedia(StrictModel):
    kind: Literal["relink_media"]
    media_id: Identifier
    expected_old_path: str = Field(min_length=1, max_length=4096)
    new_path: str = Field(min_length=1, max_length=4096)
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    size: int = Field(ge=1, le=2147483648)
    expected_frames: int = Field(ge=1, le=100000000)
    expected_fps: float = Field(gt=0, le=240, allow_inf_nan=False)
    expected_resolution: str = Field(pattern=r"^\d{1,5}x\d{1,5}$")
    expected_codec: str = Field(min_length=1, max_length=128)


class TrackEdit(StrictModel):
    kind: Literal["track"]
    track_type: Literal["video", "audio"]
    index: int = Field(ge=1, le=128)
    action: Literal["add", "name", "enable", "lock"]
    name: Name = "Connector track"
    enabled: bool = True


class AppendClip(StrictModel):
    kind: Literal["append_clip"]
    media_id: Identifier
    start: Frame
    end: Frame
    media_type: Literal[1, 2] = 1
    record_frame: Frame | None = None
    track_index: int | None = Field(default=None, ge=1, le=128)

    @model_validator(mode="after")
    def order(self):
        if self.end <= self.start:
            raise ValueError("exclusive end must follow start")
        if (self.record_frame is None) != (self.track_index is None):
            raise ValueError("record_frame and track_index must be specified together")
        if self.record_frame is not None and self.media_type != 1:
            raise ValueError(
                "positioned placement is video-only; preserve existing audio"
            )
        return self


class Transform(StrictModel):
    kind: Literal["transform"]
    clip_id: Identifier
    pan: Number = 0.0
    tilt: Number = 0.0
    zoom_x: float = Field(default=1.0, ge=0.01, le=100, allow_inf_nan=False)
    zoom_y: float = Field(default=1.0, ge=0.01, le=100, allow_inf_nan=False)
    rotation: float = Field(default=0.0, ge=-360, le=360, allow_inf_nan=False)
    opacity: float = Field(default=100.0, ge=0, le=100, allow_inf_nan=False)


class Marker(StrictModel):
    kind: Literal["marker"]
    frame: Frame
    name: Name
    note: str = Field(default="", max_length=1024)
    color: Literal["Blue", "Green", "Red", "Yellow", "Purple", "Cyan"] = "Blue"
    duration: int = Field(default=1, ge=1, le=100000)


class Title(StrictModel):
    kind: Literal["title"]
    timecode: str = Field(pattern=r"^\d{2}:\d{2}:\d{2}[:;]\d{2}$")
    text: str = Field(min_length=1, max_length=4096)


class ImportMedia(StrictModel):
    kind: Literal["import_media"]
    paths: list[str] = Field(min_length=1, max_length=20)


class VoiceIsolation(StrictModel):
    kind: Literal["voice_isolation"]
    track: int = Field(ge=1, le=128)
    enabled: bool
    amount: int = Field(ge=0, le=100)


class FairlightPreset(StrictModel):
    kind: Literal["fairlight_preset"]
    name: Name


class CDL(StrictModel):
    kind: Literal["cdl"]
    clip_id: Identifier
    node: int = Field(ge=1, le=100)
    slope: list[Number] = Field(
        default_factory=lambda: [1.0, 1.0, 1.0], min_length=3, max_length=3
    )
    offset: list[Number] = Field(
        default_factory=lambda: [0.0, 0.0, 0.0], min_length=3, max_length=3
    )
    power: list[Number] = Field(
        default_factory=lambda: [1.0, 1.0, 1.0], min_length=3, max_length=3
    )
    saturation: float = Field(default=1.0, ge=0, le=10, allow_inf_nan=False)


class LUT(StrictModel):
    kind: Literal["lut"]
    clip_id: Identifier
    node: int = Field(ge=1, le=100)
    path: str = Field(min_length=1, max_length=4096)


# Only these built-in operators and inputs; never Loader/Saver/Custom/Fuse/Script.
NODE_INPUTS = {
    "Background": {
        "TopLeftRed",
        "TopLeftGreen",
        "TopLeftBlue",
        "TopLeftAlpha",
        "Width",
        "Height",
    },
    "TextPlus": {
        "StyledText",
        "Font",
        "Style",
        "Size",
        "Red1",
        "Green1",
        "Blue1",
        "Alpha1",
        "Center",
    },
    "Merge": {"Blend", "Center", "Size", "Angle"},
    "Transform": {"Center", "Size", "Angle"},
    "Blur": {"XBlurSize", "YBlurSize"},
    "RectangleMask": {"Center", "Width", "Height", "SoftEdge"},
    "EllipseMask": {"Center", "Width", "Height", "SoftEdge"},
    "MediaOut": set(),
}
PORTS = {
    "Merge": {"Background", "Foreground", "EffectMask"},
    "Transform": {"Input", "EffectMask"},
    "Blur": {"Input", "EffectMask"},
    "MediaOut": {"Input"},
    "Background": {"EffectMask"},
    "TextPlus": {"EffectMask"},
    "RectangleMask": set(),
    "EllipseMask": set(),
}


class Point(StrictModel):
    x: float = Field(ge=-10, le=10, allow_inf_nan=False)
    y: float = Field(ge=-10, le=10, allow_inf_nan=False)


class Key(StrictModel):
    frame: Frame
    value: Number


class Node(StrictModel):
    id: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_]{0,31}$")
    tool: Literal[
        "Background",
        "TextPlus",
        "Merge",
        "Transform",
        "Blur",
        "RectangleMask",
        "EllipseMask",
        "MediaOut",
    ]
    inputs: dict[str, float | str | Point] = Field(default_factory=dict, max_length=12)
    keyframes: dict[str, list[Key]] = Field(default_factory=dict, max_length=6)

    @model_validator(mode="after")
    def safe_inputs(self):
        for key, value in self.inputs.items():
            if key not in NODE_INPUTS[self.tool]:
                raise ValueError("input not allowed")
            if key in ("StyledText", "Font", "Style"):
                if not isinstance(value, str) or len(value) > 4096:
                    raise ValueError("invalid text")
                if key in ("Font", "Style") and any(
                    c in value for c in ("/", "\\", "\n", "\r")
                ):
                    raise ValueError("font names only")
            elif key == "Center":
                if not isinstance(value, Point):
                    raise ValueError("Center must be a point")
            elif not isinstance(value, (int, float)) or not -100000 <= value <= 100000:
                raise ValueError("invalid numeric input")
            if key in ("Width", "Height") and not 0 < float(value) <= 8192:
                raise ValueError("dimension out of bounds")
            if key in ("XBlurSize", "YBlurSize") and not 0 <= float(value) <= 100:
                raise ValueError("blur out of bounds")
        for key, frames in self.keyframes.items():
            if key not in NODE_INPUTS[self.tool] or key in (
                "StyledText",
                "Font",
                "Style",
                "Center",
                "Width",
                "Height",
            ):
                raise ValueError("only scalar animation supported")
            if not 1 <= len(frames) <= 128 or len({k.frame for k in frames}) != len(
                frames
            ):
                raise ValueError("invalid keyframes")
        return self


class Edge(StrictModel):
    source: str = Field(max_length=32)
    target: str = Field(max_length=32)
    input: str = Field(max_length=32)


class FusionBuild(StrictModel):
    kind: Literal["fusion_build"]
    clip_id: Identifier
    nodes: list[Node] = Field(min_length=1, max_length=24)
    edges: list[Edge] = Field(max_length=48)

    @model_validator(mode="after")
    def graph(self):
        nodes = {n.id: n for n in self.nodes}
        if (
            len(nodes) != len(self.nodes)
            or sum(n.tool == "MediaOut" for n in self.nodes) != 1
        ):
            raise ValueError("unique node IDs and exactly one MediaOut required")
        incoming = set()
        adj = {n: [] for n in nodes}
        for edge in self.edges:
            if edge.source not in nodes or edge.target not in nodes:
                raise ValueError("unknown node")
            if (
                edge.input not in PORTS[nodes[edge.target].tool]
                or nodes[edge.source].tool == "MediaOut"
            ):
                raise ValueError("unsafe port")
            pair = (edge.target, edge.input)
            if pair in incoming:
                raise ValueError("multiple sources for one input")
            incoming.add(pair)
            adj[edge.source].append(edge.target)
        visited = set()
        active = set()

        def visit(node):
            if node in active:
                raise ValueError("cyclic graph")
            if node in visited:
                return
            active.add(node)
            for nxt in adj[node]:
                visit(nxt)
            active.remove(node)
            visited.add(node)

        for node in nodes:
            visit(node)
        return self


class QueueRender(StrictModel):
    kind: Literal["queue_render"]
    directory: str = Field(min_length=1, max_length=4096)
    name: str = Field(pattern=r"^[A-Za-z0-9_-]{1,80}$")
    format: Literal["mov", "mp4", "wav"]
    codec: str = Field(pattern=r"^[A-Za-z0-9_ -]{1,64}$")
    export_video: bool = True
    export_audio: bool = True
    preset: Name | None = None

    @model_validator(mode="after")
    def render_mode(self):
        if not self.export_video and not self.export_audio:
            raise ValueError("at least one output stream required")
        if self.export_video and self.format == "wav":
            raise ValueError("WAV cannot contain video")
        if self.preset is None:
            self.preset = "H.264 Master" if self.export_video else "Audio Only"
        return self


class StartRender(StrictModel):
    kind: Literal["start_render"]
    job_id: Identifier


Action = Annotated[
    CopyTimeline
    | CopyProject
    | RelinkMedia
    | TrackEdit
    | AppendClip
    | Transform
    | Marker
    | Title
    | ImportMedia
    | VoiceIsolation
    | FairlightPreset
    | CDL
    | LUT
    | FusionBuild
    | QueueRender
    | StartRender,
    Field(discriminator="kind"),
]
ACTION = TypeAdapter(Action)


class Plan(StrictModel):
    command_id: str = Field(
        pattern=r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
    )
    project_id: Identifier
    timeline_id: Identifier
    action: Action


def make_plan(project_id, timeline_id, action):
    return Plan(
        command_id=str(uuid.uuid4()),
        project_id=project_id,
        timeline_id=timeline_id,
        action=action,
    )


class Preparation(StrictModel):
    project_id: Identifier
    timeline_id: Identifier
    action: Action


def prepare(payload):
    request = Preparation.model_validate(payload)
    plan = make_plan(request.project_id, request.timeline_id, request.action)
    return {
        "plan": plan.model_dump(mode="json"),
        "digest": digest(plan),
        "requires_local_approval": True,
    }


def digest(plan):
    # RFC8785 makes the same numeric/Unicode JSON hash on Python and Node.js.
    return hashlib.sha256(rfc8785.dumps(plan.model_dump(mode="json"))).hexdigest()
