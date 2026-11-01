"""Unit tests for the core model, the selector, and the protocol codec."""

from __future__ import annotations

from corral.core.models import (
    Executor,
    JobSpec,
    Node,
    NodeInfo,
    selector_matches,
)
from corral.core.protocol import Hello, TaskLog, decode, encode


def _node(node_id: str, os: str = "linux", arch: str = "x86_64", executors=None, labels=None) -> Node:
    return Node(
        info=NodeInfo(
            node_id=node_id,
            os=os,
            arch=arch,
            executors=executors or [Executor.LOCAL, Executor.DOCKER],
            labels=labels or [],
        )
    )


def test_empty_selector_matches_any_node_with_the_executor():
    spec = JobSpec(command=["true"], executor=Executor.LOCAL)
    assert selector_matches(spec, _node("n1"))


def test_executor_must_be_supported_by_node():
    spec = JobSpec(command=["true"], executor=Executor.DOCKER)
    node = _node("n1", executors=[Executor.LOCAL])
    assert not selector_matches(spec, node)


def test_os_and_arch_constraints():
    spec = JobSpec(command=["true"], executor=Executor.LOCAL, select_os="windows")
    assert not selector_matches(spec, _node("n1", os="linux"))
    assert selector_matches(spec, _node("n2", os="windows"))


def test_labels_must_all_be_present():
    spec = JobSpec(command=["true"], executor=Executor.LOCAL, select_labels=["cve", "fast"])
    assert not selector_matches(spec, _node("n1", labels=["cve"]))
    assert selector_matches(spec, _node("n2", labels=["cve", "fast", "extra"]))


def test_select_specific_nodes():
    spec = JobSpec(command=["true"], executor=Executor.LOCAL, select_nodes=["keep"])
    assert selector_matches(spec, _node("keep"))
    assert not selector_matches(spec, _node("other"))


def test_protocol_roundtrip_hello():
    msg = Hello(info=NodeInfo(node_id="n1", os="linux", executors=[Executor.LOCAL]))
    back = decode(encode(msg))
    assert isinstance(back, Hello)
    assert back.info.node_id == "n1"
    assert back.info.executors == [Executor.LOCAL]


def test_protocol_roundtrip_tasklog():
    msg = TaskLog(task_id="t1", stream="stderr", line="boom")
    back = decode(encode(msg))
    assert isinstance(back, TaskLog)
    assert back.stream == "stderr"
    assert back.line == "boom"


def test_decode_rejects_unknown_type():
    import pytest

    with pytest.raises(ValueError):
        decode('{"type": "nope", "data": {}}')
