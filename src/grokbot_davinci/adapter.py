"""Only documented read methods on the current Resolve project; no file access.

Import the SDK installed by Blackmagic via its documented PYTHONPATH configuration.
No project is loaded, switched, saved or mutated. No media paths or metadata returned.
"""


class ResolveAdapter:
    def __init__(self, resolve):
        self.resolve = resolve

    @classmethod
    def connect(cls):
        import DaVinciResolveScript

        resolve = DaVinciResolveScript.scriptapp("Resolve")
        if resolve is None:
            raise RuntimeError("resolve_unavailable")
        return cls(resolve)

    @staticmethod
    def text(value):
        return str(value or "")[:256]

    def execute(self, operation):
        r = self.resolve
        if operation == "version":
            return dict(
                product=self.text(r.GetProductName()),
                version=self.text(r.GetVersionString()),
            )
        if operation not in ("project", "timeline", "media"):
            raise ValueError("unsupported_operation")
        project = r.GetProjectManager().GetCurrentProject()
        if not project:
            return dict(available=False)
        if operation == "project":
            return dict(
                available=True,
                name=self.text(project.GetName()),
                timeline_count=project.GetTimelineCount(),
            )
        if operation == "timeline":
            timeline = project.GetCurrentTimeline()
            if not timeline:
                return dict(available=False)
            return dict(
                available=True,
                name=self.text(timeline.GetName()),
                start_frame=timeline.GetStartFrame(),
                end_frame=timeline.GetEndFrame(),
                video_tracks=timeline.GetTrackCount("video"),
                audio_tracks=timeline.GetTrackCount("audio"),
            )
        folder = project.GetMediaPool().GetCurrentFolder()
        clips = folder.GetClipList() or []
        return dict(
            bin=self.text(folder.GetName()),
            total=len(clips),
            truncated=len(clips) > 100,
            clips=[dict(name=self.text(c.GetName())) for c in clips[:100]],
        )

    def inspect(self, payload):
        from .protocol import Inspection
        from .editor import uid, Editor

        request = Inspection.model_validate(payload)
        r = self.resolve
        p = r.GetProjectManager().GetCurrentProject()
        if request.domain == "capabilities":
            return {
                "version": self.text(r.GetVersionString()),
                "read_domains": ["inventory", "audio", "fusion", "render"],
                "edit_kinds": [
                    "copy_timeline",
                    "track",
                    "append_clip",
                    "transform",
                    "marker",
                    "title",
                    "import_media",
                    "voice_isolation",
                    "fairlight_preset",
                    "cdl",
                    "lut",
                    "fusion_build",
                    "queue_render",
                    "start_render",
                ],
                "minimum_versions": {
                    "voice_isolation": "20.1",
                    "fairlight_preset": "20.2.2",
                },
                "note": "Availability is checked again at execution; this is not GUI parity or a live feature test.",
            }
        if not p:
            return {"available": False}
        t = p.GetCurrentTimeline()
        if request.domain == "render":
            return {
                "formats": p.GetRenderFormats(),
                "rendering": p.IsRenderingInProgress(),
                "codecs": {
                    fmt: p.GetRenderCodecs(fmt) for fmt in ("mov", "mp4", "wav")
                },
            }
        if not t:
            return {"available": False, "project_id": uid(p)}
        if request.domain == "inventory":
            tracks = []
            for kind in ("video", "audio"):
                for index in range(1, min(t.GetTrackCount(kind), 16) + 1):
                    items = t.GetItemListInTrack(kind, index) or []
                    tracks.append(
                        {
                            "type": kind,
                            "index": index,
                            "name": self.text(t.GetTrackName(kind, index)),
                            "clips": [
                                {
                                    "id": uid(c),
                                    "name": self.text(c.GetName()),
                                    "start": c.GetStart(),
                                    "end": c.GetEnd(),
                                }
                                for c in items[:50]
                            ],
                            "truncated": len(items) > 50,
                        }
                    )
            media = p.GetMediaPool().GetCurrentFolder().GetClipList() or []
            return {
                "project_id": uid(p),
                "timeline_id": uid(t),
                "tracks": tracks,
                "media": [
                    {"id": str(c.GetMediaId()), "name": self.text(c.GetName())}
                    for c in media[:100]
                ],
                "limits": "16 tracks/type; 50 clips/track; 100 current-bin media entries; output size capped",
            }
        if request.domain == "audio":
            states = []
            voice = getattr(t, "GetVoiceIsolationState", None)
            for index in range(1, min(t.GetTrackCount("audio"), 32) + 1):
                states.append(
                    {
                        "index": index,
                        "name": self.text(t.GetTrackName("audio", index)),
                        "enabled": t.GetIsTrackEnabled("audio", index),
                        "voice_isolation": voice(index)
                        if callable(voice)
                        else {"unsupported": True},
                    }
                )
            presets = getattr(r, "GetFairlightPresets", None)
            return {
                "tracks": states,
                "fairlight_presets": list(presets() or [])[:100]
                if callable(presets)
                else [],
                "unsupported": "Individual faders/pan/EQ/FairlightFX automation not exposed by this connector/API.",
            }
        if not request.clip_id:
            raise ValueError("clip_id required for Fusion inspection")
        clip = Editor.clip(t, request.clip_id)
        count = clip.GetFusionCompCount()
        comps = []
        for index in range(1, min(count, 8) + 1):
            comp = clip.GetFusionCompByIndex(index)
            nodes = comp.GetToolList(False) or {}
            comps.append(
                {
                    "index": index,
                    "nodes": [
                        {
                            "name": self.text(n.GetAttrs().get("TOOLS_Name")),
                            "tool": self.text(n.GetAttrs().get("TOOLS_RegID")),
                        }
                        for n in list(nodes.values())[:100]
                    ],
                }
            )
        return {"compositions": comps, "truncated": count > 8}
