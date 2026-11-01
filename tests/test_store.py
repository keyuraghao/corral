"""Persistence tests: a controller with a store can be torn down and rebuilt and still know its jobs."""

from __future__ import annotations

from corral.core.controller import Controller
from corral.core.models import Executor, Job, JobSpec, JobState, Task, TaskState
from corral.core.store import JobStore


def test_store_roundtrips_jobs_and_tasks(tmp_path):
    store = JobStore(tmp_path / "corral.db")
    spec = JobSpec(command=["echo", "hi"], executor=Executor.LOCAL, name="persisted")
    job = Job(spec=spec, state=JobState.DONE)
    task = Task(job_id=job.job_id, node_id="n1", spec=spec, state=TaskState.SUCCEEDED)
    job.task_ids.append(task.task_id)
    store.save_job(job)
    store.save_task(task)
    store.close()

    reopened = JobStore(tmp_path / "corral.db")
    jobs = reopened.load_jobs()
    tasks = reopened.load_tasks()
    assert len(jobs) == 1
    assert jobs[0].job_id == job.job_id
    assert jobs[0].spec.name == "persisted"
    assert len(tasks) == 1
    assert tasks[0].state == TaskState.SUCCEEDED


def test_controller_replays_history_from_store(tmp_path):
    store = JobStore(tmp_path / "c.db")
    spec = JobSpec(command=["echo", "hi"], executor=Executor.LOCAL, name="old")
    job = Job(spec=spec, state=JobState.DONE)
    store.save_job(job)
    store.close()

    # A fresh controller pointed at the same store should already know the job.
    controller = Controller(store=JobStore(tmp_path / "c.db"))
    assert job.job_id in controller.jobs
    assert controller.jobs[job.job_id].spec.name == "old"
