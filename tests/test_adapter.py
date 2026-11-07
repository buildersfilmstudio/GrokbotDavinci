from unittest.mock import Mock
import pytest
from grokbot_davinci.adapter import ResolveAdapter


def test_read_methods_only():
    r = Mock()
    r.GetProductName.return_value = "Studio"
    r.GetVersionString.return_value = "21"
    a = ResolveAdapter(r)
    assert a.execute("version") == {"product": "Studio", "version": "21"}
    p = r.GetProjectManager.return_value.GetCurrentProject.return_value
    p.GetName.return_value = "Violin"
    p.GetTimelineCount.return_value = 2
    assert a.execute("project")["name"] == "Violin"
    t = p.GetCurrentTimeline.return_value
    t.GetName.return_value = "Twinkle"
    t.GetStartFrame.return_value = 0
    t.GetEndFrame.return_value = 100
    t.GetTrackCount.return_value = 2
    assert a.execute("timeline")["end_frame"] == 100
    f = p.GetMediaPool.return_value.GetCurrentFolder.return_value
    f.GetName.return_value = "Bin"
    c = Mock()
    c.GetName.return_value = "clip"
    f.GetClipList.return_value = [c] * 101
    result = a.execute("media")
    assert result["truncated"] and len(result["clips"]) == 100
    methods = {call[0].split(".")[-1] for call in r.mock_calls}
    assert all(name.startswith("Get") for name in methods)
    with pytest.raises(ValueError):
        a.execute("delete_project")


def test_no_current_project():
    r = Mock()
    r.GetProjectManager.return_value.GetCurrentProject.return_value = None
    assert ResolveAdapter(r).execute("timeline") == {"available": False}
