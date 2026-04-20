"""Benchmark tasks — one module per realistic agent workflow."""

from __future__ import annotations

from benchmarks.tasks.task_01_search_orders import run as task_01
from benchmarks.tasks.task_02_aggregate_orders import run as task_02
from benchmarks.tasks.task_03_field_select import run as task_03
from benchmarks.tasks.task_04_recovery_401 import run as task_04
from benchmarks.tasks.task_05_text_search import run as task_05
from benchmarks.tasks.task_06_cross_api_normalize import run as task_06
from benchmarks.tasks.task_07_estimate_fetch import run as task_07
from benchmarks.tasks.task_08_max_tokens import run as task_08

ALL_TASKS = [task_01, task_02, task_03, task_04, task_05, task_06, task_07, task_08]

__all__ = ["ALL_TASKS"]
