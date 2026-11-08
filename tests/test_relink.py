import copy
import hashlib
from pathlib import Path
from unittest.mock import Mock
import pytest
from grokbot_davinci.approval import ApprovalStore
from grokbot_davinci.edits import ACTION, make_plan
from grokbot_davinci.editor import Editor, EditPolicy
from grokbot_davinci.relink import file_hash


@pytest.fixture
def relink_env(tmp_path):
    source = tmp_path / "inputs"
    source.mkdir()
    output = tmp_path / "backups"
    output.mkdir()
    asset = source / "Title_00.mov"
    asset.write_bytes(b"verified-original-media")
    old = str(tmp_path / "missing" / asset.name)
    props = {
        "File Path": old,
        "Frames": "139",
        "FPS": 23.976,
        "Resolution": "1920x1080",
        "Video Codec": "Apple ProRes 4444",
    }
    clip = Mock()
    clip.GetMediaId.return_value = "media1"
    clip.GetClipProperty.side_effect = lambda key=None: (
        props.get(key) if key else dict(props)
    )
    folder = Mock()
    folder.GetClipList.return_value = [clip]
    folder.GetSubFolderList.return_value = []
    timeline = Mock()
    timeline.GetUniqueId.return_value = "timeline1"
    timeline.GetName.return_value = "Timeline"
    timeline.GetStartFrame.return_value = 0
    timeline.GetEndFrame.return_value = 100
    timeline.GetTrackCount.return_value = 0
    project = Mock()
    project.GetUniqueId.return_value = "source"
    project.GetName.return_value = "Original"
    project.GetCurrentTimeline.return_value = timeline
    project.GetTimelineCount.return_value = 1
    project.GetTimelineByIndex.return_value = timeline
    pool = project.GetMediaPool.return_value
    pool.GetRootFolder.return_value = folder

    def relink(clips, parent):
        assert clips == [clip]
        props["File Path"] = str(Path(parent) / asset.name)
        return True

    pool.RelinkClips.side_effect = relink
    resolve = Mock()
    pm = resolve.GetProjectManager.return_value
    pm.GetCurrentProject.return_value = project
    pm.SaveProject.return_value = True
    pm.GetProjectListInCurrentFolder.return_value = ["Original"]

    def export(name, path):
        Path(path).write_bytes(b"protected-project-backup")
        return True

    pm.ExportProject.side_effect = export
    pm.ImportProject.return_value = True
    # Separate project and media objects: no shared pool with original.
    cloned = copy.deepcopy(project)
    cloned.GetUniqueId.return_value = "isolated"
    cloned.GetName.return_value = "Protected"

    def load(name):
        pm.GetCurrentProject.return_value = cloned
        return cloned

    pm.LoadProject.side_effect = load
    state = tmp_path / "approvals.db"
    store = ApprovalStore(state)
    editor = Editor(
        resolve, EditPolicy(True, str(state), (str(source),), (str(output),))
    )
    action = dict(
        kind="relink_media",
        media_id="media1",
        expected_old_path=old,
        new_path=str(asset),
        sha256=file_hash(asset),
        size=asset.stat().st_size,
        expected_frames=139,
        expected_fps=23.976,
        expected_resolution="1920x1080",
        expected_codec="Apple ProRes 4444",
    )
    env = dict(
        editor=editor,
        store=store,
        project=project,
        copy=cloned,
        pool=pool,
        pm=pm,
        asset=asset,
        old=old,
        action=action,
        output=output,
        props=props,
        tmp=tmp_path,
    )
    yield env
    store.close()


def execute(e, action, project="source"):
    plan = make_plan(project, "timeline1", ACTION.validate_python(action))
    e["store"].approve(plan)
    return e["editor"].execute(plan), plan


def protect(e):
    backup = e["output"] / "unique.drp"
    e["store"].own_project(
        "source",
        "different-original",
        str(backup),
        hashlib.sha256(b"backup").hexdigest(),
    )
    backup.write_bytes(b"backup")


def test_relink_requires_isolated_project_not_timeline_copy(relink_env):
    e = relink_env
    e["store"].own_copy("source", "timeline1")
    with pytest.raises(PermissionError, match="isolated_project"):
        execute(e, e["action"])
    e["pool"].RelinkClips.assert_not_called()


def test_relink_success_and_no_replay(relink_env):
    e = relink_env
    protect(e)
    result, plan = execute(e, e["action"])
    assert result["missing_before"] == 1 and result["missing_after"] == 0
    assert result["saved"] and e["props"]["File Path"] == str(e["asset"])
    with pytest.raises(Exception):
        e["editor"].execute(plan)
    assert e["pool"].RelinkClips.call_count == 1


@pytest.mark.parametrize(
    "field,value,error",
    [
        ("sha256", "0" * 64, "fingerprint"),
        ("size", 1, "fingerprint"),
        ("expected_old_path", "/wrong/Title_00.mov", "offline_path"),
        ("expected_frames", 140, "metadata"),
        ("expected_resolution", "640x480", "metadata"),
        ("media_id", "other", "identity"),
    ],
)
def test_reject_mismatched_original(relink_env, field, value, error):
    e = relink_env
    protect(e)
    a = dict(e["action"], **{field: value})
    with pytest.raises(PermissionError, match=error):
        execute(e, a)
    e["pool"].RelinkClips.assert_not_called()


def test_reject_ambiguous_filename(relink_env):
    e = relink_env
    protect(e)
    (e["asset"].parent / "title_00.MOV").write_bytes(b"different")
    with pytest.raises(PermissionError, match="ambiguous_original"):
        execute(e, e["action"])
    e["pool"].RelinkClips.assert_not_called()


def test_reject_symlink_and_outside_root(relink_env):
    e = relink_env
    protect(e)
    f = e["tmp"] / "Title_00.mov"
    f.write_bytes(e["asset"].read_bytes())
    with pytest.raises(PermissionError, match="outside_allowed"):
        execute(e, dict(e["action"], new_path=str(f)))
    e["asset"].unlink()
    e["asset"].symlink_to(f)
    with pytest.raises(PermissionError):
        execute(e, e["action"])
    e["pool"].RelinkClips.assert_not_called()


def test_reject_changed_backup(relink_env):
    e = relink_env
    protect(e)
    (e["output"] / "unique.drp").write_bytes(b"corrupt")
    with pytest.raises(PermissionError, match="backup_changed"):
        execute(e, e["action"])
    e["pool"].RelinkClips.assert_not_called()


def test_project_copy_backup_distinct_identity_and_scope(relink_env):
    e = relink_env
    result, _ = execute(
        e,
        dict(
            kind="copy_project",
            name="Protected",
            backup_path=str(e["output"] / "safe.drp"),
        ),
    )
    assert result["working_project_id"] == "isolated" and result["structure_verified"]
    assert e["store"].require_project("isolated")[0] == "source"
    with pytest.raises(PermissionError):
        e["store"].require_project("source")
    assert (e["output"] / "safe.drp").read_bytes() == b"protected-project-backup"
    # No original relinking performed by copying.
    e["pool"].RelinkClips.assert_not_called()


@pytest.mark.parametrize("existing", ["name", "backup"])
def test_copy_refuses_overwrite(relink_env, existing):
    e = relink_env
    b = e["output"] / "safe.drp"
    if existing == "name":
        e["pm"].GetProjectListInCurrentFolder.return_value = ["Protected"]
    else:
        b.write_bytes(b"existing")
    with pytest.raises(PermissionError):
        execute(e, dict(kind="copy_project", name="Protected", backup_path=str(b)))
    e["pm"].ExportProject.assert_not_called()


def test_copy_identity_collision_never_registered(relink_env):
    e = relink_env
    e["copy"].GetUniqueId.return_value = "source"
    with pytest.raises(RuntimeError, match="distinct_project"):
        execute(
            e,
            dict(
                kind="copy_project",
                name="Protected",
                backup_path=str(e["output"] / "safe.drp"),
            ),
        )
    with pytest.raises(PermissionError):
        e["store"].require_project("source")
