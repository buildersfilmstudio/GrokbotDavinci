import json
import time
from unittest.mock import Mock
import pytest
from grokbot_davinci.edits import ACTION, Plan, make_plan, digest
from grokbot_davinci.approval import ApprovalStore
from grokbot_davinci.editor import Editor, EditPolicy
from grokbot_davinci.worker import Worker
from grokbot_davinci.protocol import Job
import httpx


class Node:
    def __init__(self):
        self.inputs = {}
        self.connections = []

    def SetInput(self, key, value):
        self.inputs[key] = value
        return None  # Native Fusion void return

    def GetInput(self, key, time=None):
        value = self.inputs.get(key)
        return value.get(time) if time is not None else value

    def ConnectInput(self, key, source):
        self.connections.append((key, source))
        return None

    def GetAttrs(self):
        return {"TOOLS_Name": str(id(self))}

    def GetInputList(self):
        return {
            i: MockPort(key, source) for i, (key, source) in enumerate(self.connections)
        }

    def __setitem__(self, key, value):
        self.inputs[key] = value

    def __getitem__(self, key):
        return self.inputs[key]


class MockPort:
    def __init__(self, key, source):
        self.key, self.source = key, source

    def GetAttrs(self):
        return {"INPS_ID": self.key}

    def GetConnectedOutput(self):
        return self

    def GetTool(self):
        return self.source


class Comp:
    def __init__(self):
        self.added = []
        self.output = Node()
        self.text = Node()

    def AddTool(self, tool):
        node = Node()
        self.added.append((tool, node))
        return node

    def FindToolByID(self, tool):
        return self.text if tool == "TextPlus" else self.output

    def BezierSpline(self):
        return {}


@pytest.fixture
def env(tmp_path):
    state = tmp_path / "approval.db"
    store = ApprovalStore(state)
    methods = [
        "GetUniqueId",
        "GetName",
        "GetTrackCount",
        "GetItemListInTrack",
        "DuplicateTimeline",
        "AddTrack",
        "SetTrackName",
        "SetTrackEnable",
        "SetTrackLock",
        "GetMarkers",
        "AddMarker",
        "SetCurrentTimecode",
        "InsertFusionTitleIntoTimeline",
        "SetVoiceIsolationState",
        "GetVoiceIsolationState",
    ]
    timeline = Mock(spec=methods)
    timeline.GetUniqueId.return_value = "copy"
    timeline.GetName.return_value = "Working"
    timeline.GetTrackCount.return_value = 2
    timeline.GetMarkers.return_value = {}
    copy = Mock(spec=["GetUniqueId", "GetName"])
    copy.GetUniqueId.return_value = "newcopy"
    copy.GetName.return_value = "Copy"
    timeline.DuplicateTimeline.return_value = copy
    comp = Comp()
    clip = Mock(
        spec=[
            "GetUniqueId",
            "SetProperty",
            "GetProperty",
            "AddVersion",
            "LoadVersionByName",
            "SetCDL",
            "SetLUT",
            "AddFusionComp",
            "GetFusionCompByIndex",
        ]
    )
    clip.GetUniqueId.return_value = "clip1"
    clip.AddFusionComp.return_value = comp
    clip.GetFusionCompByIndex.return_value = comp
    values = {}
    clip.SetProperty.side_effect = lambda k, v: values.__setitem__(k, v) or True
    clip.GetProperty.side_effect = lambda k: values[k]
    for name in ("AddVersion", "LoadVersionByName", "SetCDL", "SetLUT"):
        getattr(clip, name).return_value = True
    timeline.GetItemListInTrack.return_value = [clip]
    timeline.InsertFusionTitleIntoTimeline.return_value = clip
    for name in (
        "AddTrack",
        "SetTrackName",
        "SetTrackEnable",
        "SetTrackLock",
        "AddMarker",
        "SetCurrentTimecode",
        "SetVoiceIsolationState",
    ):
        getattr(timeline, name).return_value = True
    timeline.GetVoiceIsolationState.return_value = {"isEnabled": True, "amount": 50}
    media = Mock(spec=["GetMediaId"])
    media.GetMediaId.return_value = "media1"
    folder = Mock(spec=["GetClipList"])
    folder.GetClipList.return_value = [media]
    pool = Mock(spec=["GetCurrentFolder", "AppendToTimeline", "ImportMedia"])
    pool.GetCurrentFolder.return_value = folder
    pool.AppendToTimeline.return_value = [clip]
    pool.ImportMedia.return_value = [media]
    p = Mock(
        spec=[
            "GetUniqueId",
            "GetCurrentTimeline",
            "GetMediaPool",
            "GetTimelineCount",
            "GetTimelineByIndex",
            "SetCurrentTimeline",
            "ApplyFairlightPresetToCurrentTimeline",
            "GetRenderCodecs",
            "SetCurrentRenderFormatAndCodec",
            "SetRenderSettings",
            "AddRenderJob",
            "StartRendering",
        ]
    )
    p.GetUniqueId.return_value = "project"
    p.GetCurrentTimeline.return_value = timeline
    p.GetMediaPool.return_value = pool
    p.GetTimelineCount.return_value = 1
    p.GetTimelineByIndex.return_value = timeline
    p.GetRenderCodecs.return_value = {"H.264": "H264"}
    p.AddRenderJob.return_value = "render1"
    for name in (
        "SetCurrentTimeline",
        "ApplyFairlightPresetToCurrentTimeline",
        "SetCurrentRenderFormatAndCodec",
        "SetRenderSettings",
        "StartRendering",
    ):
        getattr(p, name).return_value = True
    r = Mock(spec=["GetProjectManager", "GetVersion", "GetFairlightPresets"])
    r.GetProjectManager.return_value.GetCurrentProject.return_value = p
    r.GetVersion.return_value = [21, 1, 0, 17, ""]
    r.GetFairlightPresets.return_value = ["Mix"]
    store.own_copy("project", "copy")
    output = tmp_path / "output"
    output.mkdir(mode=0o700)
    media_path = tmp_path / "violin.wav"
    media_path.write_bytes(b"mock-only")
    lut = tmp_path / "look.cube"
    lut.write_text("mock-only")
    policy = EditPolicy(True, str(state), (str(tmp_path),), (str(output),))
    yield dict(
        store=store,
        editor=Editor(r, policy),
        policy=policy,
        p=p,
        t=timeline,
        clip=clip,
        comp=comp,
        r=r,
        output=output,
        media=media_path,
        lut=lut,
    )
    store.close()


def run(env, action):
    plan = make_plan("project", "copy", ACTION.validate_python(action))
    env["store"].approve(plan)
    return env["editor"].execute(plan), plan


@pytest.mark.parametrize(
    "action",
    [
        {"kind": "copy_timeline", "name": "Copy"},
        {"kind": "track", "track_type": "audio", "index": 3, "action": "add"},
        {
            "kind": "track",
            "track_type": "video",
            "index": 1,
            "action": "name",
            "name": "Score",
        },
        {
            "kind": "track",
            "track_type": "audio",
            "index": 1,
            "action": "enable",
            "enabled": False,
        },
        {
            "kind": "track",
            "track_type": "video",
            "index": 1,
            "action": "lock",
            "enabled": True,
        },
        {
            "kind": "append_clip",
            "media_id": "media1",
            "start": 0,
            "end": 48,
            "media_type": 2,
        },
        {"kind": "transform", "clip_id": "clip1", "pan": 10.0, "opacity": 60.0},
        {"kind": "marker", "frame": 24, "name": "First note"},
        {"kind": "title", "timecode": "01:00:02:00", "text": "Twinkle Twinkle"},
        {"kind": "voice_isolation", "track": 1, "enabled": True, "amount": 50},
        {"kind": "fairlight_preset", "name": "Mix"},
        {"kind": "cdl", "clip_id": "clip1", "node": 1},
    ],
)
def test_each_documented_edit_dispatch_and_replay(env, action):
    result, plan = run(env, action)
    assert result["status"] == "api_reported_applied"
    with pytest.raises(PermissionError, match="consumed"):
        env["editor"].execute(plan)


def test_import_lut_and_render_paths(env):
    result, _ = run(env, {"kind": "import_media", "paths": [str(env["media"])]})
    assert result["media_ids"] == ["media1"]
    run(env, {"kind": "lut", "clip_id": "clip1", "node": 1, "path": str(env["lut"])})
    result, _ = run(
        env,
        {
            "kind": "queue_render",
            "directory": str(env["output"]),
            "name": "violin",
            "format": "mp4",
            "codec": "H264",
        },
    )
    assert (
        result["job_id"] == "render1"
        and (env["output"] / result["output_directory_name"]).is_dir()
    )
    run(env, {"kind": "start_render", "job_id": "render1"})
    with pytest.raises(PermissionError, match="already_started"):
        run(env, {"kind": "start_render", "job_id": "render1"})
    assert env["p"].StartRendering.call_count == 1
    assert env["p"].StartRendering.call_args.args == (["render1"], False)


def test_real_fusion_graph_dispatch_numeric_keyframes(env):
    result, _ = run(
        env,
        {
            "kind": "fusion_build",
            "clip_id": "clip1",
            "nodes": [
                {
                    "id": "score",
                    "tool": "TextPlus",
                    "inputs": {"StyledText": "♩ ♪", "Center": {"x": 0.5, "y": 0.25}},
                    "keyframes": {
                        "Red1": [
                            {"frame": 0, "value": 1.0},
                            {"frame": 24, "value": 0.0},
                        ]
                    },
                },
                {"id": "output", "tool": "MediaOut"},
            ],
            "edges": [{"source": "score", "target": "output", "input": "Input"}],
        },
    )
    node = env["comp"].added[0][1]
    assert node.inputs["Red1"] == {0: 1.0, 24: 0.0}
    assert node.inputs["Center"] == {1: 0.5, 2: 0.25}
    assert env["comp"].output.connections[0][0] == "Input"
    assert result["new_composition"]


def test_worker_default_readonly_rejects_edit_without_api_call():
    plan = make_plan(
        "project",
        "copy",
        ACTION.validate_python({"kind": "marker", "frame": 1, "name": "note"}),
    )
    completions = []

    def handler(request):
        if request.url.path.endswith("/claim"):
            return httpx.Response(
                200,
                json={
                    "job": {
                        "id": "12345678-1234-4234-8234-123456789abc",
                        "operation": "edit",
                        "lease": "l" * 40,
                        "expires_at": time.time() + 15,
                        "payload": plan.model_dump(mode="json"),
                    }
                },
            )
        completions.append(json.loads(request.content))
        return httpx.Response(200, json={"status": "completed"})

    with httpx.Client(
        transport=httpx.MockTransport(handler), base_url="https://relay.example"
    ) as c:
        assert Worker(
            c,
            "mac",
            reader=lambda *_: pytest.fail("default worker must not call native edit"),
        ).run_once()
    assert (
        completions[0]["ok"] is False
        and completions[0]["result"]["error"] == "worker_edits_disabled"
    )


def test_scope_missing_approval_and_disabled_policy(env):
    plan = make_plan(
        "project",
        "copy",
        ACTION.validate_python({"kind": "marker", "frame": 1, "name": "note"}),
    )
    with pytest.raises(PermissionError, match="approval"):
        env["editor"].execute(plan)
    env["store"].approve(plan)
    with pytest.raises(PermissionError, match="disabled"):
        Editor(env["r"], EditPolicy()).execute(plan)
    env["p"].GetUniqueId.return_value = "other"
    with pytest.raises(PermissionError, match="project_changed"):
        env["editor"].execute(plan)
    env["t"].AddMarker.assert_not_called()


def test_only_working_copies_mutate(env):
    env["t"].GetUniqueId.return_value = "original"
    plan = make_plan(
        "project",
        "original",
        ACTION.validate_python({"kind": "marker", "frame": 1, "name": "note"}),
    )
    env["store"].approve(plan)
    with pytest.raises(PermissionError, match="working_copies"):
        env["editor"].execute(plan)
    env["t"].AddMarker.assert_not_called()


def test_failed_native_edit_consumes_approval(env):
    env["clip"].SetProperty.side_effect = None
    env["clip"].SetProperty.return_value = False
    plan = make_plan(
        "project",
        "copy",
        ACTION.validate_python({"kind": "transform", "clip_id": "clip1"}),
    )
    env["store"].approve(plan)
    with pytest.raises(RuntimeError):
        env["editor"].execute(plan)
    with pytest.raises(PermissionError, match="consumed"):
        env["editor"].execute(plan)
    assert env["clip"].SetProperty.call_count == 1


def test_version_gates_and_unavailable_api(env):
    env["r"].GetVersion.return_value = [19, 1, 0, 1, ""]
    with pytest.raises(RuntimeError, match="20_1"):
        run(env, {"kind": "voice_isolation", "track": 1, "enabled": True, "amount": 50})
    with pytest.raises(RuntimeError, match="20_2_2"):
        run(env, {"kind": "fairlight_preset", "name": "Mix"})
    env["t"].SetVoiceIsolationState = None
    with pytest.raises(RuntimeError, match="unsupported_api"):
        run(env, {"kind": "voice_isolation", "track": 1, "enabled": True, "amount": 50})


def test_path_escape_and_symlink_and_script_imports(env, tmp_path):
    outside = tmp_path.parent / "outside-source.wav"
    outside.write_bytes(b"test")
    allowed = tmp_path / "only"
    allowed.mkdir(mode=0o700)
    link = allowed / "escape.wav"
    link.symlink_to(outside)
    policy = EditPolicy(
        True, str(tmp_path / "approval.db"), (str(allowed),), (str(allowed),)
    )
    with pytest.raises(PermissionError, match="outside"):
        policy.checked_path(str(link))
    output_link = allowed / "out"
    output_link.symlink_to(allowed, target_is_directory=True)
    with pytest.raises(PermissionError, match="real_directory"):
        policy.checked_path(str(output_link), True)
    bad = tmp_path / "evil.comp"
    bad.write_text("not executed")
    with pytest.raises(PermissionError, match="extension"):
        run(env, {"kind": "import_media", "paths": [str(bad)]})


def test_local_approval_expiry_and_persistence(env):
    plan = make_plan(
        "project",
        "copy",
        ACTION.validate_python({"kind": "marker", "frame": 1, "name": "note"}),
    )
    env["store"].approve(plan, seconds=-1)
    with pytest.raises(PermissionError):
        env["editor"].execute(plan)
    another = make_plan(
        "project",
        "copy",
        ACTION.validate_python({"kind": "marker", "frame": 2, "name": "other"}),
    )
    env["store"].approve(another)
    env["store"].consume(another)
    reopened = ApprovalStore(env["policy"].state)
    with pytest.raises(PermissionError):
        reopened.consume(another)
    reopened.close()


@pytest.mark.parametrize(
    "action",
    [
        {"kind": "delete_project", "name": "anything"},
        {"kind": "transform", "clip_id": "clip1", "Volume": 0},
        {
            "kind": "fusion_build",
            "clip_id": "clip1",
            "nodes": [{"id": "evil", "tool": "Loader"}],
            "edges": [],
        },
        {
            "kind": "fusion_build",
            "clip_id": "clip1",
            "nodes": [
                {
                    "id": "text",
                    "tool": "TextPlus",
                    "inputs": {"Expression": "os.execute()"},
                },
                {"id": "output", "tool": "MediaOut"},
            ],
            "edges": [],
        },
        {
            "kind": "fusion_build",
            "clip_id": "clip1",
            "nodes": [
                {"id": "a", "tool": "Merge"},
                {"id": "b", "tool": "Merge"},
                {"id": "output", "tool": "MediaOut"},
            ],
            "edges": [
                {"source": "a", "target": "b", "input": "Background"},
                {"source": "b", "target": "a", "input": "Background"},
            ],
        },
        {"kind": "append_clip", "media_id": "media1", "start": 50, "end": 1},
    ],
)
def test_unsafe_or_unknown_edit_schema_rejected(action):
    with pytest.raises(ValueError):
        ACTION.validate_python(action)


def test_plan_digest_bound_to_command_and_payload():
    plan = make_plan(
        "p", "t", ACTION.validate_python({"kind": "marker", "frame": 1, "name": "note"})
    )
    changed = Plan.model_validate({**plan.model_dump(), "timeline_id": "different"})
    assert digest(plan) != digest(changed)
    with pytest.raises(ValueError):
        Job(
            id="12345678-1234-4234-8234-123456789abc",
            operation="edit",
            lease="l" * 40,
            expires_at=float(time.time() + 15),
            payload={**plan.model_dump(), "eval": "bad"},
        )


@pytest.mark.asyncio
async def test_mcp_prepare_confirm_local_approve_execute_and_replay(env, tmp_path):
    import asyncio
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client
    from grokbot_davinci.relay import create_app, Config

    a = create_app(
        Config(
            "c" * 40, "w" * 40, "mac", ("testserver",), str(tmp_path / "relay.db"), 2
        )
    )
    transport = httpx.ASGITransport(app=a)
    async with a.app.router.lifespan_context(a.app):
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://testserver",
            headers={"Authorization": "Bearer " + "c" * 40},
        ) as c:
            async with streamable_http_client(
                "http://testserver/mcp", http_client=c
            ) as (r, w, _):
                async with ClientSession(r, w) as session:
                    await session.initialize()
                    prepared = await session.call_tool(
                        "prepare_edit",
                        {
                            "project_id": "project",
                            "timeline_id": "copy",
                            "action": {"kind": "marker", "frame": 10, "name": "A note"},
                        },
                    )
                    assert not prepared.isError
                    response = json.loads(prepared.content[0].text)
                    plan = Plan.model_validate(response["plan"])
                    assert response["digest"] == digest(plan)
                    # Preparing cannot mutate or grant an approval.
                    env["t"].AddMarker.assert_not_called()
                    wrong = await session.call_tool(
                        "execute_edit",
                        {"plan": response["plan"], "confirmation": "yes"},
                    )
                    assert wrong.isError and a.state.queue.claim("mac") is None
                    env["store"].approve(plan)

                    async def worker():
                        for _ in range(100):
                            job = a.state.queue.claim("mac")
                            if job:
                                wire = Job.model_validate(job)
                                outcome = env["editor"].execute(
                                    Plan.model_validate(wire.payload)
                                )
                                a.state.queue.complete(
                                    wire.id, "mac", wire.lease, True, outcome
                                )
                                return
                            await asyncio.sleep(0.01)
                        raise AssertionError("job not received")

                    task = asyncio.create_task(worker())
                    executed = await session.call_tool(
                        "execute_edit",
                        {
                            "plan": response["plan"],
                            "confirmation": "EXECUTE " + response["digest"],
                        },
                    )
                    await task
                    assert not executed.isError and env["t"].AddMarker.call_count == 1
                    with pytest.raises(PermissionError, match="consumed"):
                        env["editor"].execute(plan)


def test_claimed_mutation_expiry_never_rearms_local_approval(env, tmp_path):
    from grokbot_davinci.queue import Queue, QueueError

    plan = make_plan(
        "project",
        "copy",
        ACTION.validate_python({"kind": "marker", "frame": 1, "name": "note"}),
    )
    env["store"].approve(plan)
    now = [100.0]
    q = Queue(str(tmp_path / "relay-expiry.db"), "mac", ttl=2, clock=lambda: now[0])
    id = q.enqueue("edit", plan.model_dump(mode="json"))
    job = q.claim("mac")
    env["editor"].execute(Plan.model_validate(job["payload"]))
    now[0] += 3
    with pytest.raises(QueueError):
        q.complete(id, "mac", job["lease"], True, {})
    assert q.claim("mac") is None
    with pytest.raises(PermissionError, match="consumed"):
        env["editor"].execute(plan)
    q.close()


def test_mutation_worker_receipt_retry_never_reexecutes(env):
    plan = make_plan(
        "project",
        "copy",
        ACTION.validate_python({"kind": "marker", "frame": 1, "name": "note"}),
    )
    env["store"].approve(plan)
    receipts = []

    def handler(request):
        if request.url.path.endswith("/claim"):
            return httpx.Response(
                200,
                json={
                    "job": {
                        "id": "12345678-1234-4234-8234-123456789abc",
                        "operation": "edit",
                        "lease": "l" * 40,
                        "expires_at": time.time() + 15,
                        "payload": plan.model_dump(mode="json"),
                    }
                },
            )
        receipts.append(json.loads(request.content))
        if len(receipts) == 1:
            raise httpx.ConnectError("lost acknowledgement")
        return httpx.Response(200, json={"status": "duplicate"})

    def reader(op, timeout, payload, policy):
        return {
            "ok": True,
            "result": env["editor"].execute(Plan.model_validate(payload)),
        }

    with httpx.Client(
        transport=httpx.MockTransport(handler), base_url="https://relay.example"
    ) as c:
        assert Worker(c, "mac", reader=reader, policy=env["policy"]).run_once()
    assert receipts[0] == receipts[1] and env["t"].AddMarker.call_count == 1


@pytest.mark.parametrize("crash", [False, True])
def test_mutation_child_timeout_or_crash_reports_unknown(monkeypatch, crash):
    import grokbot_davinci.worker as module

    receiver = Mock()
    receiver.poll.return_value = crash
    receiver.recv.side_effect = EOFError
    sender = Mock()
    process = Mock()
    process.is_alive.side_effect = [True, False]
    ctx = Mock()
    ctx.Pipe.return_value = (receiver, sender)
    ctx.Process.return_value = process
    monkeypatch.setattr(module.multiprocessing, "get_context", lambda _: ctx)
    result = module.read_bounded("edit", 0.01, {}, EditPolicy(True, "unused"))
    assert (
        not result["ok"]
        and result["result"]["mutation_outcome"] == "unknown_or_partial_do_not_retry"
    )
    process.terminate.assert_called_once()
    receiver.close.assert_called_once()


def test_prepare_is_strict_pure_and_does_not_connect_to_resolve(monkeypatch):
    from grokbot_davinci.worker import _read
    from grokbot_davinci.edits import prepare
    import grokbot_davinci.worker as module

    monkeypatch.setattr(
        module.ResolveAdapter,
        "connect",
        lambda: pytest.fail("prepare must not connect to Resolve"),
    )
    sender = Mock()
    payload = {
        "project_id": "project",
        "timeline_id": "original",
        "action": {"kind": "copy_timeline", "name": "Working"},
    }
    _read("prepare", sender, payload, EditPolicy())
    result = sender.send.call_args.args[0]
    assert result["ok"] and result["result"]["requires_local_approval"]
    plan = Plan.model_validate(result["result"]["plan"])
    assert result["result"]["digest"] == digest(plan)
    with pytest.raises(ValueError):
        prepare({**payload, "arbitrary_code": "bad"})


def test_fusion_void_setter_requires_real_readback():
    from grokbot_davinci.editor import set_fusion_input, connect_fusion_input

    node = Node()
    set_fusion_input(node, "Size", 0.1)
    broken = Mock()
    broken.GetInput.return_value = None
    with pytest.raises(RuntimeError, match="readback"):
        set_fusion_input(broken, "Size", 0.1)
    connect_fusion_input(node, "Input", Node())
    broken.GetInputList.return_value = {}
    with pytest.raises(RuntimeError, match="connection_readback"):
        connect_fusion_input(broken, "Input", Node())


def test_neon_published_schemas_match_python_validation():
    from pathlib import Path
    from grokbot_davinci.edits import Preparation

    schema = json.loads(
        (Path(__file__).parents[1] / "neon-relay/src/schemas.json").read_text()
    )
    assert schema["preparation"] == Preparation.model_json_schema()
    plan = Plan.model_json_schema()
    definitions = plan.pop("$defs")
    assert schema["execution"]["$defs"]["Plan"] == plan
    for name, definition in definitions.items():
        assert schema["execution"]["$defs"][name] == definition


def test_fusion_connection_missing_names_cannot_verify():
    from grokbot_davinci.editor import connect_fusion_input

    target, source = Node(), Node()
    source.GetAttrs = lambda: {}
    with pytest.raises(RuntimeError, match="connection_readback"):
        connect_fusion_input(target, "Input", source)
