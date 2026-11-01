"""The wire protocol between a worker and the controller.

Every message is a single JSON object with a `type` field and a `data` payload. Both directions use
the same envelope. These are thin pydantic models so both ends validate what they receive and a
malformed message is rejected rather than acted on.

Direction is by convention, not enforced by the type:

worker -> controller
    hello        the worker introduces itself (NodeInfo)
    heartbeat    liveness ping, carries nothing
    task_log     one line of output from a running task
    task_update  a task changed state (running, then a terminal state) with its result

controller -> worker
    welcome      the controller accepts the worker and echoes the assigned node id
    task         run this task now
    cancel       stop this task if it is still running
    ping         liveness request; the worker answers with a heartbeat
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from .models import JobSpec, NodeInfo, TaskResult, TaskState

PROTOCOL_VERSION = 1


class Envelope(BaseModel):
    """The outer frame every message shares."""

    type: str
    data: dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def of(cls, msg: Message) -> Envelope:
        return cls(type=msg.MSG_TYPE, data=msg.model_dump(mode="json"))


class Message(BaseModel):
    """Base for a typed payload. `MSG_TYPE` is the string that appears in the envelope."""

    MSG_TYPE: Literal["_base"] = "_base"


# ----- worker -> controller -----


class Hello(Message):
    MSG_TYPE: Literal["hello"] = "hello"
    protocol: int = PROTOCOL_VERSION
    info: NodeInfo


class Heartbeat(Message):
    MSG_TYPE: Literal["heartbeat"] = "heartbeat"


class TaskLog(Message):
    MSG_TYPE: Literal["task_log"] = "task_log"
    task_id: str
    stream: Literal["stdout", "stderr"]
    line: str


class TaskUpdate(Message):
    MSG_TYPE: Literal["task_update"] = "task_update"
    task_id: str
    state: TaskState
    result: TaskResult | None = None


# ----- controller -> worker -----


class Welcome(Message):
    MSG_TYPE: Literal["welcome"] = "welcome"
    node_id: str
    protocol: int = PROTOCOL_VERSION


class TaskAssignment(Message):
    MSG_TYPE: Literal["task"] = "task"
    task_id: str
    job_id: str
    spec: JobSpec


class Cancel(Message):
    MSG_TYPE: Literal["cancel"] = "cancel"
    task_id: str


class Ping(Message):
    MSG_TYPE: Literal["ping"] = "ping"


# Registry so a received envelope can be turned back into the right typed model.
_MESSAGE_TYPES: dict[str, type[Message]] = {
    m.model_fields["MSG_TYPE"].default: m
    for m in (Hello, Heartbeat, TaskLog, TaskUpdate, Welcome, TaskAssignment, Cancel, Ping)
}


def decode(raw: str | bytes) -> Message:
    """Parse one raw frame into its typed message. Raises ValueError on an unknown or malformed type."""

    env = Envelope.model_validate_json(raw)
    cls = _MESSAGE_TYPES.get(env.type)
    if cls is None:
        raise ValueError(f"unknown message type: {env.type!r}")
    return cls.model_validate(env.data)


def encode(msg: Message) -> str:
    """Serialise a typed message to a single JSON frame."""

    return Envelope.of(msg).model_dump_json()
