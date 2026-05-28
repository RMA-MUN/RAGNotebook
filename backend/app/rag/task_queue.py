import queue
import threading
from typing import Any


class TaskQueue:
    """
    线程安全的任务队列管理器

    用于协调多线程切片和单线程写入之间的数据传递
    """

    def __init__(self, maxsize: int = 100):
        """
        初始化任务队列

        :param maxsize: 队列最大容量，超过时put会阻塞
        """
        self._queue = queue.Queue(maxsize=maxsize)
        self._completed_count = 0
        self._total_count = 0
        self._lock = threading.Lock()
        self._finished = False

    def set_total_count(self, count: int):
        """
        设置总任务数

        :param count: 总任务数量
        """
        with self._lock:
            self._total_count = count

    def put(self, item: Any, block: bool = True, timeout: float | None = None):
        """
        向队列中放入任务结果

        :param item: 任务结果数据
        :param block: 是否阻塞等待
        :param timeout: 超时时间
        """
        self._queue.put(item, block=block, timeout=timeout)

    def get(self, block: bool = True, timeout: float | None = None) -> Any:
        """
        从队列中获取任务结果

        :param block: 是否阻塞等待
        :param timeout: 超时时间
        :return: 任务结果数据
        """
        return self._queue.get(block=block, timeout=timeout)

    def task_done(self):
        """标记一个任务已处理完成"""
        with self._lock:
            self._completed_count += 1
        self._queue.task_done()

    def get_completed_count(self) -> int:
        """获取已完成任务数"""
        with self._lock:
            return self._completed_count

    def get_total_count(self) -> int:
        """获取总任务数"""
        with self._lock:
            return self._total_count

    def is_finished(self) -> bool:
        """
        判断是否所有任务都已完成

        :return: 是否完成
        """
        with self._lock:
            return self._finished and self._completed_count >= self._total_count

    def set_finished(self):
        """标记切片阶段已完成"""
        with self._lock:
            self._finished = True

    def join(self):
        """阻塞直到所有任务都被处理完成"""
        self._queue.join()

    def qsize(self) -> int:
        """获取队列当前大小"""
        return self._queue.qsize()

    def empty(self) -> bool:
        """判断队列是否为空"""
        return self._queue.empty()

    def full(self) -> bool:
        """判断队列是否已满"""
        return self._queue.full()
