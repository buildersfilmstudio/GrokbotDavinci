"""Finite project-isolated relinking. No generic API, file writing or path RPC."""

import hashlib
import json
import math
from pathlib import Path
from .editor import method, success, uid, MEDIA_EXTENSIONS


def file_hash(path):
    before = path.stat()
    if not path.is_file() or path.is_symlink() or before.st_size > 2147483648:
        raise PermissionError("unsupported_fingerprint_file")
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    after = path.stat()
    if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (
        after.st_dev,
        after.st_ino,
        after.st_size,
        after.st_mtime_ns,
    ):
        raise PermissionError("source_changed_during_fingerprint")
    return h.hexdigest()


def media(project):
    result = []
    folders = 0

    def walk(folder, depth=0):
        nonlocal folders
        folders += 1
        if folders > 1500 or depth > 24:
            raise ValueError("media_inventory_too_large")
        result.extend(folder.GetClipList() or [])
        if len(result) > 20000:
            raise ValueError("media_inventory_too_large")
        for child in folder.GetSubFolderList() or []:
            walk(child, depth + 1)

    walk(project.GetMediaPool().GetRootFolder())
    return result


def missing(project):
    return sorted(
        c.GetClipProperty("File Path")
        for c in media(project)
        if c.GetClipProperty("File Path")
        and not Path(c.GetClipProperty("File Path")).is_file()
    )


def structure(project):
    """Bounded read-only edit/offset/property/version snapshot, excluding remapped UUIDs.

    DRP backup protects grade contents not exposed by these read methods. This is
    not a claim of byte-for-byte grading graph verification.
    """
    count = project.GetTimelineCount()
    if count > 128:
        raise ValueError("timeline_inventory_too_large")
    timelines = []
    total = 0
    for n in range(1, count + 1):
        t = project.GetTimelineByIndex(n)
        tracks = []
        for kind in ("video", "audio", "subtitle"):
            track_count = t.GetTrackCount(kind) or 0
            if track_count > 128:
                raise ValueError("track_inventory_too_large")
            for i in range(1, track_count + 1):
                items = []
                for c in t.GetItemListInTrack(kind, i) or []:
                    total += 1
                    if total > 20000:
                        raise ValueError("clip_inventory_too_large")
                    items.append(
                        [
                            c.GetName(),
                            c.GetStart(),
                            c.GetEnd(),
                            c.GetLeftOffset(),
                            c.GetRightOffset(),
                            c.GetProperty(),
                            c.GetCurrentVersion(),
                        ]
                    )
                tracks.append(
                    [kind, t.GetTrackName(kind, i), t.GetIsTrackEnabled(kind, i), items]
                )
        timelines.append([t.GetName(), t.GetStartFrame(), t.GetEndFrame(), tracks])
    return hashlib.sha256(
        json.dumps(timelines, sort_keys=True, allow_nan=False).encode()
    ).hexdigest()


def backup_target(editor, raw):
    p = Path(raw)
    if not p.is_absolute() or p.suffix != ".drp" or p.exists() or p.is_symlink():
        raise PermissionError("new_unique_drp_backup_required")
    parent = editor.policy.checked_path(str(p.parent), output=True)
    if any(q.is_symlink() for q in [p.parent, *p.parent.parents]):
        raise PermissionError("backup_symlinks_forbidden")
    return parent / p.name


def require_backup(store, project_id, policy):
    source, raw, fingerprint = store.require_project(project_id)
    p = Path(raw)
    policy.checked_path(str(p.parent), output=True)
    if any(q.is_symlink() for q in [p, *p.parents]) or file_hash(p) != fingerprint:
        raise PermissionError("protected_project_backup_changed_or_missing")
    return source


def validate_relink(editor, project, action):
    candidates = [c for c in media(project) if c.GetMediaId() == action.media_id]
    if len(candidates) != 1:
        raise PermissionError("media_identity_missing_or_ambiguous")
    clip = candidates[0]
    props = clip.GetClipProperty()
    old = props.get("File Path")
    if (
        old != action.expected_old_path
        or not Path(old).is_absolute()
        or Path(old).exists()
    ):
        raise PermissionError("expected_original_offline_path_mismatch")
    new = editor.policy.checked_path(action.new_path)
    if any(
        q.is_symlink() for q in [Path(action.new_path), *Path(action.new_path).parents]
    ):
        raise PermissionError("relink_symlinks_forbidden")
    if new.suffix.lower() not in MEDIA_EXTENSIONS or new.name != Path(old).name:
        raise PermissionError("original_filename_mismatch")
    aliases = [
        p for p in new.parent.iterdir() if p.name.casefold() == new.name.casefold()
    ]
    if len(aliases) != 1:
        raise PermissionError("ambiguous_original_filename")
    if new.stat().st_size != action.size or file_hash(new) != action.sha256:
        raise PermissionError("original_fingerprint_mismatch")
    if (
        str(props.get("Frames")) != str(action.expected_frames)
        or not math.isclose(
            float(props.get("FPS", 0)), action.expected_fps, abs_tol=0.0001
        )
        or props.get("Resolution") != action.expected_resolution
        or props.get("Video Codec") != action.expected_codec
    ):
        raise PermissionError("original_metadata_mismatch")
    return clip, new


def preflight(editor, project, action):
    if action.kind == "copy_project":
        pm = editor.resolve.GetProjectManager()
        for name in ("ExportProject", "ImportProject", "LoadProject", "SaveProject"):
            method(pm, name)
        backup_target(editor, action.backup_path)
        if (
            action.name in pm.GetProjectListInCurrentFolder()
            or action.name == project.GetName()
        ):
            raise PermissionError("project_copy_name_exists")
    else:
        method(project.GetMediaPool(), "RelinkClips")
        validate_relink(editor, project, action)


def apply(editor, project, action, store):
    pm = editor.resolve.GetProjectManager()
    if action.kind == "copy_project":
        target = backup_target(editor, action.backup_path)
        before = structure(project)
        original_id = uid(project)
        # Saves only existing current edits before backing them up; no timeline edits.
        success(pm.SaveProject())
        success(pm.ExportProject(project.GetName(), str(target)))
        if not target.is_file() or target.stat().st_size == 0:
            raise RuntimeError("backup_not_created")
        fingerprint = file_hash(target)
        if structure(project) != before:
            raise RuntimeError("source_project_changed_during_backup")
        success(pm.ImportProject(str(target), action.name))
        copy = pm.LoadProject(action.name)
        if not copy or uid(copy) == original_id or copy.GetName() != action.name:
            raise RuntimeError("distinct_project_copy_not_verified")
        if structure(copy) != before:
            raise RuntimeError("project_copy_structure_mismatch_do_not_retry")
        store.own_project(uid(copy), original_id, str(target), fingerprint)
        return {
            "working_project_id": uid(copy),
            "working_project_name": copy.GetName(),
            "working_timeline_id": uid(copy.GetCurrentTimeline()),
            "backup_path": str(target),
            "backup_sha256": fingerprint,
            "structure_verified": True,
        }
    # Re-check source identity immediately before mutation; approval is already consumed.
    require_backup(store, uid(project), editor.policy)
    clip, new = validate_relink(editor, project, action)
    before = structure(project)
    offline = missing(project)
    identity = clip.GetMediaId()
    success(project.GetMediaPool().RelinkClips([clip], str(new.parent)))
    if (
        clip.GetMediaId() != identity
        or Path(clip.GetClipProperty("File Path")).resolve() != new
    ):
        raise RuntimeError("relink_readback_mismatch_do_not_retry")
    after = missing(project)
    expected = list(offline)
    expected.remove(action.expected_old_path)
    if after != expected or structure(project) != before:
        raise RuntimeError("relink_changed_unexpected_scope_do_not_retry")
    success(pm.SaveProject())
    return {
        "media_id": identity,
        "old_path": action.expected_old_path,
        "new_path": str(new),
        "missing_before": len(offline),
        "missing_after": len(after),
        "edit_structure_unchanged": True,
        "saved": True,
    }
