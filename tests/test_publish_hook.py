import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from server.publish_hook import main


def test_main_posts_to_start_endpoint():
    calls = []

    def fake_opener(req, timeout=None):
        calls.append((req.full_url, req.method))
        class _Resp:
            def __enter__(self): return self
            def __exit__(self, *a): return False
        return _Resp()

    main("start", "instance0", "http://127.0.0.1:8080", opener=fake_opener)

    assert calls == [("http://127.0.0.1:8080/internal/instances/instance0/publish/start", "POST")]


def test_main_posts_to_stop_endpoint():
    calls = []

    def fake_opener(req, timeout=None):
        calls.append((req.full_url, req.method))
        class _Resp:
            def __enter__(self): return self
            def __exit__(self, *a): return False
        return _Resp()

    main("stop", "instance2", "http://127.0.0.1:8080", opener=fake_opener)

    assert calls == [("http://127.0.0.1:8080/internal/instances/instance2/publish/stop", "POST")]


def test_main_swallows_request_errors():
    def failing_opener(req, timeout=None):
        raise OSError("connection refused")

    # Must not raise -- mediamtx doesn't care about this script's exit code
    # for runOnUnDemand, and a raised exception would just show up as noise
    # in mediamtx's own log with no one able to act on it.
    main("stop", "instance0", "http://127.0.0.1:8080", opener=failing_opener)
