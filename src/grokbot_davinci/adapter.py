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
