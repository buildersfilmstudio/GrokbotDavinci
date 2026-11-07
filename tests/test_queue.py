import pytest
from grokbot_davinci.queue import Queue, QueueError


@pytest.fixture
def q(tmp_path):
    now = [100.0]
    queue = Queue(str(tmp_path / "queue.db"), "mac", ttl=10, clock=lambda: now[0])
    yield queue, now
    queue.close()


def test_ownership_claim_once_completion_and_duplicates(q):
    queue, _ = q
    id = queue.enqueue("version")
    with pytest.raises(QueueError):
        queue.claim("wrong")
    job = queue.claim("mac")
    assert job["id"] == id
    assert queue.claim("mac") is None
    with pytest.raises(QueueError):
        queue.complete(id, "other", job["lease"], True, {})
    with pytest.raises(QueueError):
        queue.complete(id, "mac", "x" * 40, True, {})
    assert (
        queue.complete(id, "mac", job["lease"], True, {"version": "21"}) == "completed"
    )
    assert (
        queue.complete(id, "mac", job["lease"], True, {"version": "21"}) == "duplicate"
    )
    with pytest.raises(QueueError):
        queue.complete(id, "mac", job["lease"], True, {"version": "22"})
    assert queue.result(id)["result"] == {"version": "21"}


def test_expiry_never_requeues_and_rejects_late_completion(q):
    queue, now = q
    id = queue.enqueue("project")
    job = queue.claim("mac")
    now[0] += 11
    with pytest.raises(QueueError):
        queue.complete(id, "mac", job["lease"], True, {})
    assert queue.claim("mac") is None
    with pytest.raises(QueueError):
        queue.result(id)


def test_offline_expiry(q):
    queue, now = q
    id = queue.enqueue("version")
    now[0] += 11
    with pytest.raises(QueueError, match="offline"):
        queue.result(id)
    assert queue.claim("mac") is None


def test_limits_and_allowlist(q):
    queue, _ = q
    with pytest.raises(QueueError):
        queue.enqueue("eval")
    for _ in range(16):
        queue.enqueue("media")
    with pytest.raises(QueueError, match="full"):
        queue.enqueue("media")
    job = queue.claim("mac")
    with pytest.raises(QueueError, match="large"):
        queue.complete(job["id"], "mac", job["lease"], True, {"data": "x" * 50000})


def test_receipt_survives_restart(tmp_path):
    path = str(tmp_path / "q.db")
    q = Queue(path, "mac")
    id = q.enqueue("version")
    j = q.claim("mac")
    q.complete(id, "mac", j["lease"], True, {})
    q.close()
    q = Queue(path, "mac")
    assert q.complete(id, "mac", j["lease"], True, {}) == "duplicate"
    q.close()


def test_unclaimed_and_cancelled_jobs_cannot_complete(q):
    queue, _ = q
    id = queue.enqueue("version")
    with pytest.raises(QueueError):
        queue.complete(id, "mac", "a" * 40, True, {})
    job = queue.claim("mac")
    queue.cancel(id)
    with pytest.raises(QueueError):
        queue.complete(id, "mac", job["lease"], True, {})
    assert queue.claim("mac") is None


def test_receipt_expiry_rejects_even_identical_completion(q):
    queue, now = q
    id = queue.enqueue("version")
    job = queue.claim("mac")
    queue.complete(id, "mac", job["lease"], True, {})
    now[0] += 11
    with pytest.raises(QueueError, match="expired"):
        queue.complete(id, "mac", job["lease"], True, {})
