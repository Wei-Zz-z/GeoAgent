from __future__ import annotations

from types import SimpleNamespace

import pytest

from geoagent.core.node import Flow, Node


class AddOne(Node):
    async def exec(self, ctx, payload):
        ctx.calls.append(self.name)
        return "next", payload + 1


class EndNode(Node):
    async def exec(self, ctx, payload):
        ctx.calls.append(self.name)
        return "default", payload


@pytest.mark.asyncio
async def test_flow_walks_edges_in_order():
    a = AddOne(name="a")
    b = AddOne(name="b")
    end = EndNode(name="end")
    a - "next" >> b
    b - "next" >> end

    ctx = SimpleNamespace(calls=[])
    last_action, payload = await Flow(a).run(ctx, payload=0)
    assert last_action == "default"
    assert payload == 2
    assert ctx.calls == ["a", "b", "end"]


class LoopNode(Node):
    async def exec(self, ctx, payload):
        return "again", payload


@pytest.mark.asyncio
async def test_flow_stops_unbounded_cycles():
    loop = LoopNode(name="loop")
    loop - "again" >> loop
    ctx = SimpleNamespace()
    with pytest.raises(RuntimeError, match="max_steps"):
        await Flow(loop, max_steps=5).run(ctx, payload=None)


@pytest.mark.asyncio
async def test_node_retry_then_success():
    class Flaky(Node):
        def __init__(self):
            super().__init__(name="flaky", max_retries=3, wait=0)
            self.attempts = 0

        async def exec(self, ctx, payload):
            self.attempts += 1
            if self.attempts < 2:
                raise ValueError("boom")
            return "default", "ok"

    flaky = Flaky()
    ctx = SimpleNamespace()
    _, payload = await Flow(flaky).run(ctx, payload=None)
    assert payload == "ok"
    assert flaky.attempts == 2
