"""task_queue.py — TaskQueue（threading.Queue 包装）测试。"""
from app.rag.task_queue import TaskQueue


def test_initial_state():
    q = TaskQueue()
    assert q.qsize() == 0
    assert q.empty() is True
    assert q.get_completed_count() == 0
    assert q.get_total_count() == 0
    assert q.is_finished() is False


def test_put_get_roundtrip():
    q = TaskQueue()
    q.put("item-1")
    q.put({"a": 1})
    assert q.qsize() == 2
    assert q.empty() is False
    assert q.get() == "item-1"
    assert q.get() == {"a": 1}
    assert q.qsize() == 0
    assert q.empty() is True


def test_get_with_timeout_raises_when_empty():
    import queue

    q = TaskQueue()
    try:
        q.get(timeout=0.05)
        raise AssertionError("expected Empty")
    except queue.Empty:
        pass


def test_set_and_get_total_count():
    q = TaskQueue()
    q.set_total_count(5)
    assert q.get_total_count() == 5


def test_task_done_tracks_completed_count():
    q = TaskQueue()
    q.put(1)
    q.put(2)
    q.put(3)
    assert q.qsize() == 3
    q.get()
    q.task_done()
    assert q.get_completed_count() == 1
    q.get()
    q.task_done()
    assert q.get_completed_count() == 2
    q.get()
    q.task_done()
    assert q.get_completed_count() == 3
    assert q.qsize() == 0


def test_is_finished_requires_finished_flag_and_all_done():
    q = TaskQueue()
    q.set_total_count(2)
    q.put(1)
    q.put(2)

    q.get()
    q.task_done()
    # 未调用 set_finished
    assert q.is_finished() is False

    q.set_finished()
    # 已完成 1/2，即使 finished 标志置位也未完成
    assert q.is_finished() is False

    q.get()
    q.task_done()
    assert q.is_finished() is True


def test_is_finished_with_zero_total():
    q = TaskQueue()
    q.set_total_count(0)
    q.set_finished()
    assert q.is_finished() is True


def test_join_returns_after_all_tasks_done():
    q = TaskQueue()
    q.put("x")
    q.get()
    q.task_done()
    q.join()  # 所有任务已 task_done，join 立即返回


def test_full_when_maxsize_reached():
    q = TaskQueue(maxsize=2)
    assert q.full() is False
    q.put(1)
    q.put(2)
    assert q.full() is True


def test_progress_calculation():
    q = TaskQueue()
    q.set_total_count(4)
    assert q.is_finished() is False
    for i in range(4):
        q.put(i)
    for _ in range(4):
        q.get()
        q.task_done()
    q.set_finished()
    assert q.get_completed_count() == 4
    assert q.get_total_count() == 4
    assert q.is_finished() is True