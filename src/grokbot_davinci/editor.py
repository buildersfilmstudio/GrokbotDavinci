"""Scoped implementations of documented Resolve/Fusion methods, not raw API RPC."""

from dataclasses import dataclass
from pathlib import Path
import math
from .approval import ApprovalStore
from .edits import Point

MEDIA_EXTENSIONS = {
    ".mov",
    ".mp4",
    ".mxf",
    ".wav",
    ".aif",
    ".aiff",
    ".mp3",
    ".flac",
    ".png",
    ".jpg",
    ".jpeg",
    ".tif",
    ".tiff",
    ".exr",
}


@dataclass(frozen=True)
class EditPolicy:
    enabled: bool = False
    state: str = ""
    import_roots: tuple[str, ...] = ()
    output_roots: tuple[str, ...] = ()

    def checked_path(self, raw, output=False):
        p = Path(raw)
        if not p.is_absolute() or len(raw) > 4096:
            raise PermissionError("absolute_path_required")
        resolved = p.resolve(strict=True)
        roots = self.output_roots if output else self.import_roots
        if not roots:
            raise PermissionError("no_allowed_roots_configured")
        if not any(
            Path(root).resolve() != Path("/")
            and resolved.is_relative_to(Path(root).resolve(strict=True))
            for root in roots
        ):
            raise PermissionError("outside_allowed_roots")
        if output and (
            not resolved.is_dir() or any(x.is_symlink() for x in [p, *p.parents])
        ):
            raise PermissionError("output_parent_must_be_real_directory")
        if not output and not resolved.is_file():
            raise PermissionError("regular_file_required")
        return resolved


def method(obj, name):
    # name is only supplied by fixed application code below, never by a request.
    function = getattr(obj, name, None)
    if not callable(function):
        raise RuntimeError("unsupported_api:" + name)
    return function


def success(value):
    if value is not True:
        raise RuntimeError("api_rejected_or_unverified")


def input_matches(actual, expected):
    if isinstance(expected, dict):
        return isinstance(actual, dict) and all(
            key in actual and input_matches(actual[key], value)
            for key, value in expected.items()
        )
    if isinstance(expected, (float, int)) and not isinstance(expected, bool):
        return isinstance(actual, (float, int)) and math.isclose(
            actual, expected, rel_tol=1e-6, abs_tol=1e-7
        )
    return actual == expected


def set_fusion_input(node, key, value):
    # Fusion SetInput is void, unlike Resolve's boolean setters. Verify readback.
    node.SetInput(key, value)
    if not input_matches(node.GetInput(key), value):
        raise RuntimeError("fusion_input_readback_mismatch")


def connect_fusion_input(target, key, source):
    # Fusion ConnectInput is void. Verify the actual documented Link connection.
    target.ConnectInput(key, source)
    for port in (target.GetInputList() or {}).values():
        if port.GetAttrs().get("INPS_ID") == key:
            output = port.GetConnectedOutput()
            upstream = output.GetTool() if output else None
            expected_name = source.GetAttrs().get("TOOLS_Name")
            if (
                isinstance(expected_name, str)
                and expected_name
                and upstream
                and upstream.GetAttrs().get("TOOLS_Name") == expected_name
            ):
                return
    raise RuntimeError("fusion_connection_readback_mismatch")


def uid(obj):
    return str(method(obj, "GetUniqueId")())


class Editor:
    def __init__(self, resolve, policy):
        self.resolve, self.policy = resolve, policy

    def scope(self, plan):
        p = self.resolve.GetProjectManager().GetCurrentProject()
        if not p or uid(p) != plan.project_id:
            raise PermissionError("current_project_changed")
        t = p.GetCurrentTimeline()
        if not t or uid(t) != plan.timeline_id:
            raise PermissionError("current_timeline_changed")
        return p, t

    @staticmethod
    def clip(timeline, clip_id):
        for kind in ("video", "audio"):
            count = timeline.GetTrackCount(kind)
            if count > 128:
                raise ValueError("track_inventory_too_large")
            for index in range(1, count + 1):
                items = timeline.GetItemListInTrack(kind, index) or []
                if len(items) > 2000:
                    raise ValueError("clip_inventory_too_large")
                for clip in items:
                    if uid(clip) == clip_id:
                        return clip
        raise ValueError("clip_not_in_scoped_timeline")

    def execute(self, plan):
        if not self.policy.enabled or not self.policy.state:
            raise PermissionError("worker_edits_disabled")
        p, t = self.scope(plan)
        action = plan.action
        store = ApprovalStore(self.policy.state)
        try:
            if action.kind != "copy_timeline":
                store.require_copy(plan.project_id, plan.timeline_id)
            self.preflight(p, t, action)
            # Atomic durable consumption BEFORE first mutation. Errors never rearm it.
            store.consume(plan)
            # Scope check again immediately before modifying; Resolve itself is not transactional.
            p, t = self.scope(plan)
            result = self.apply(p, t, action, plan, store)
            return {
                "status": "api_reported_applied",
                "independent_verification": False,
                **result,
            }
        finally:
            store.close()

    def preflight(self, p, t, a):
        required = {
            "copy_timeline": (t, "DuplicateTimeline"),
            "track": (
                t,
                {
                    "add": "AddTrack",
                    "name": "SetTrackName",
                    "enable": "SetTrackEnable",
                    "lock": "SetTrackLock",
                }.get(getattr(a, "action", None), "AddTrack"),
            ),
            "append_clip": (p.GetMediaPool(), "AppendToTimeline"),
            "transform": (self.clip(t, a.clip_id), "SetProperty")
            if a.kind == "transform"
            else (t, "GetName"),
            "marker": (t, "AddMarker"),
            "title": (t, "InsertFusionTitleIntoTimeline"),
            "import_media": (p.GetMediaPool(), "ImportMedia"),
            "voice_isolation": (t, "SetVoiceIsolationState"),
            "fairlight_preset": (p, "ApplyFairlightPresetToCurrentTimeline"),
            "cdl": (t, "GetName"),
            "lut": (t, "GetName"),
            "fusion_build": (t, "GetName"),
            "queue_render": (p, "AddRenderJob"),
            "start_render": (p, "StartRendering"),
        }
        method(*required[a.kind])
        if a.kind in ("cdl", "lut", "fusion_build"):
            item = self.clip(t, a.clip_id)
            method(
                item,
                {"cdl": "SetCDL", "lut": "SetLUT", "fusion_build": "AddFusionComp"}[
                    a.kind
                ],
            )
            if a.kind in ("cdl", "lut"):
                method(item, "AddVersion")
                method(item, "LoadVersionByName")
        if a.kind == "import_media":
            for path in a.paths:
                if (
                    self.policy.checked_path(path).suffix.lower()
                    not in MEDIA_EXTENSIONS
                ):
                    raise PermissionError("unsupported_media_extension")
        if a.kind == "lut":
            if self.policy.checked_path(a.path).suffix.lower() != ".cube":
                raise PermissionError("only_cube_luts_allowed")
        if a.kind == "queue_render":
            self.policy.checked_path(a.directory, True)
            method(p, "LoadRenderPreset")
            if a.preset not in (method(p, "GetRenderPresetList")() or []):
                raise ValueError("render_preset_not_available")
            catalog = "GetRenderCodecs" if a.export_video else "GetAudioRenderCodecs"
            codecs = method(p, catalog)(a.format) or {}
            if a.codec not in codecs.values():
                raise ValueError("codec_not_available_for_format")
        if a.kind == "voice_isolation":
            if tuple(self.resolve.GetVersion()[:3]) < (20, 1, 0):
                raise RuntimeError("requires_resolve_20_1")
        if a.kind == "fairlight_preset":
            if tuple(self.resolve.GetVersion()[:3]) < (20, 2, 2):
                raise RuntimeError("requires_resolve_20_2_2")
            presets = method(self.resolve, "GetFairlightPresets")() or []
            if a.name not in presets:
                raise ValueError("unknown_fairlight_preset")
        if a.kind in ("voice_isolation", "track"):
            kind = "audio" if a.kind == "voice_isolation" else a.track_type
            index = a.track if a.kind == "voice_isolation" else a.index
            count = t.GetTrackCount(kind)
            if a.kind == "track" and a.action == "add":
                if index != count + 1:
                    raise ValueError("new_track_index_must_be_next")
            elif index > count:
                raise ValueError("track_not_found")

    def apply(self, p, t, a, plan, store):
        kind = a.kind
        if kind == "copy_timeline":
            # Do not overwrite existing named timelines.
            if any(
                p.GetTimelineByIndex(i).GetName() == a.name
                for i in range(1, p.GetTimelineCount() + 1)
            ):
                raise ValueError("timeline_name_exists")
            copy = t.DuplicateTimeline(a.name)
            if not copy:
                raise RuntimeError("duplicate_failed")
            new_id = uid(copy)
            if new_id == plan.timeline_id:
                raise RuntimeError("duplicate_identity_not_distinct")
            store.own_copy(plan.project_id, new_id)
            success(p.SetCurrentTimeline(copy))
            return {"working_timeline_id": new_id, "name": copy.GetName()}
        if kind == "track":
            if a.action == "add":
                success(
                    t.AddTrack(a.track_type, "stereo")
                    if a.track_type == "audio"
                    else t.AddTrack("video")
                )
            elif a.action == "name":
                success(t.SetTrackName(a.track_type, a.index, a.name))
            elif a.action == "enable":
                success(t.SetTrackEnable(a.track_type, a.index, a.enabled))
            else:
                success(t.SetTrackLock(a.track_type, a.index, a.enabled))
            return {"track_count": t.GetTrackCount(a.track_type)}
        if kind == "append_clip":
            media = p.GetMediaPool().GetCurrentFolder().GetClipList() or []
            clip = next((c for c in media if c.GetMediaId() == a.media_id), None)
            if not clip:
                raise ValueError("media_not_in_current_bin")
            result = p.GetMediaPool().AppendToTimeline(
                [
                    {
                        "mediaPoolItem": clip,
                        "startFrame": a.start,
                        "endFrame": a.end,
                        "mediaType": a.media_type,
                    }
                ]
            )
            if not result:
                raise RuntimeError("append_failed")
            return {"clip_ids": [uid(c) for c in result]}
        if kind == "transform":
            clip = self.clip(t, a.clip_id)
            values = {
                "Pan": a.pan,
                "Tilt": a.tilt,
                "ZoomX": a.zoom_x,
                "ZoomY": a.zoom_y,
                "RotationAngle": a.rotation,
                "Opacity": a.opacity,
            }
            for key, value in values.items():
                success(clip.SetProperty(key, value))
            return {"readback": {key: clip.GetProperty(key) for key in values}}
        if kind == "marker":
            if a.frame in (t.GetMarkers() or {}):
                raise ValueError("marker_already_present")
            success(
                t.AddMarker(
                    a.frame,
                    a.color,
                    a.name,
                    a.note,
                    a.duration,
                    "GrokbotDavinci:" + plan.command_id,
                )
            )
            return {"frame": a.frame}
        if kind == "title":
            success(t.SetCurrentTimecode(a.timecode))
            item = t.InsertFusionTitleIntoTimeline("Text+")
            if not item:
                raise RuntimeError("title_insert_failed_check_selected_video_track")
            comp = item.GetFusionCompByIndex(1)
            tool = comp.FindToolByID("TextPlus")
            if not tool:
                raise RuntimeError("textplus_not_found")
            set_fusion_input(tool, "StyledText", a.text)
            return {
                "clip_id": uid(item),
                "text_readback": str(tool.GetInput("StyledText"))[:4096],
                "warning": "Insertion track/duration follow Resolve UI defaults; inspect before render.",
            }
        if kind == "import_media":
            paths = [str(self.policy.checked_path(path)) for path in a.paths]
            clips = p.GetMediaPool().ImportMedia(paths)
            if not clips:
                raise RuntimeError("import_failed")
            return {"media_ids": [str(c.GetMediaId()) for c in clips]}
        if kind == "voice_isolation":
            success(
                t.SetVoiceIsolationState(
                    a.track, {"isEnabled": a.enabled, "amount": a.amount}
                )
            )
            return {"readback": method(t, "GetVoiceIsolationState")(a.track)}
        if kind == "fairlight_preset":
            success(p.ApplyFairlightPresetToCurrentTimeline(a.name))
            return {"preset": a.name}
        if kind in ("cdl", "lut"):
            clip = self.clip(t, a.clip_id)
            # Keep a separate LOCAL grade version before changing grade on the working copy.
            success(clip.AddVersion("GBD_" + plan.command_id, 0))
            success(method(clip, "LoadVersionByName")("GBD_" + plan.command_id, 0))
            if kind == "cdl":
                data = {
                    key: " ".join(str(v) for v in value)
                    for key, value in [
                        ("Slope", a.slope),
                        ("Offset", a.offset),
                        ("Power", a.power),
                    ]
                }
                success(
                    clip.SetCDL(
                        {
                            "NodeIndex": str(a.node),
                            **data,
                            "Saturation": str(a.saturation),
                        }
                    )
                )
            else:
                success(clip.SetLUT(a.node, str(self.policy.checked_path(a.path))))
            return {"local_grade_version": "GBD_" + plan.command_id}
        if kind == "fusion_build":
            return self.fusion(self.clip(t, a.clip_id), a)
        if kind == "queue_render":
            parent = self.policy.checked_path(a.directory, True)
            # A fresh command-owned directory avoids existing files and future overwrite.
            output = parent / ("GBD_" + plan.command_id)
            output.mkdir(mode=0o700, exist_ok=False)
            # Explicitly pin a known preset. Resolve21 can silently retain Audio Only
            # state even when ExportVideo=True and queue readbacks claim video.
            success(p.LoadRenderPreset(a.preset))
            if a.export_video:
                success(p.SetCurrentRenderFormatAndCodec(a.format, a.codec))
            audio = (
                {"AudioFormat": a.format, "AudioCodec": a.codec}
                if not a.export_video
                else {}
            )
            success(
                p.SetRenderSettings(
                    {
                        "TargetDir": str(output),
                        "CustomName": a.name,
                        "SelectAllFrames": True,
                        "ExportVideo": a.export_video,
                        "ExportAudio": a.export_audio,
                        **audio,
                    }
                )
            )
            job = p.AddRenderJob()
            if not job:
                raise RuntimeError("render_queue_failed")
            store.own_render(plan.project_id, plan.timeline_id, str(job))
            return {
                "job_id": str(job),
                "output_directory_name": output.name,
                "preset_pinned": a.preset,
                "warning": "A completed job is not proof of output streams. Independently probe rendered media before delivery.",
            }
        if kind == "start_render":
            store.claim_render_once(plan.project_id, plan.timeline_id, a.job_id)
            # Never start all queued jobs. This call starts only the approved owned job.
            success(p.StartRendering([a.job_id], False))
            return {"job_id": a.job_id, "started": True}
        raise ValueError("unsupported_action")

    @staticmethod
    def fusion(clip, action):
        # Build in a NEW comp; never import .comp/.setting scripts or mutate existing user nodes.
        comp = clip.AddFusionComp()
        if not comp:
            raise RuntimeError("fusion_comp_create_failed")
        created = {}
        for spec in action.nodes:
            node = (
                comp.FindToolByID("MediaOut")
                if spec.tool == "MediaOut"
                else comp.AddTool(spec.tool)
            )
            if not node:
                raise RuntimeError("fusion_node_create_failed")
            created[spec.id] = node
            for key, value in spec.inputs.items():
                if isinstance(value, Point):
                    value = {1: value.x, 2: value.y}
                set_fusion_input(node, key, value)
            for key, frames in spec.keyframes.items():
                # Public Fusion scalar animation API, no expressions or code evaluated.
                node[key] = comp.BezierSpline()
                for frame in frames:
                    node[key][frame.frame] = frame.value
                for frame in frames:
                    if not input_matches(node.GetInput(key, frame.frame), frame.value):
                        raise RuntimeError("fusion_keyframe_readback_mismatch")
        for edge in action.edges:
            connect_fusion_input(created[edge.target], edge.input, created[edge.source])
        return {
            "new_composition": True,
            "nodes": list(created),
            "keyframes": sum(
                len(keys) for n in action.nodes for keys in n.keyframes.values()
            ),
            "warning": "Native graph/timing requires visual review; original composition is not modified.",
        }
