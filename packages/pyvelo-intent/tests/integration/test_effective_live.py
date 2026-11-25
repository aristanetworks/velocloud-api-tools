"""Live check of the effective view against the modules the Orchestrator composes.

For each edge of the lab, the test loads the edge and compares
``state.effective`` with what ``edge/getEdgeConfigurationModules`` returns for
the converted modules (``ConfigPipeline.compare_effective``). It only reads: it
is marked ``integration`` and not ``live_write``, and it calls ``load`` and
``compare_effective`` and nothing that writes.

A finding (``differs``, ``client_only`` or ``server_only``) fails the test with
the rendered comparison. It is either a bug in the view or a divergence to be
understood before an ``EFFECTIVE_IGNORES`` entry is added for it. The
``factory``, ``ignored`` and ``unconverted`` entries are information; the
rendering of every edge is printed (run with ``-s``) so that a run records them.
The rendering shows the lab's values: keep a run's output out of anything
committed.

Requires VCO_HOST and VCO_TOKEN (VCO_ENTERPRISE_ID if the token needs one), as
the other live tests do, and skips without them.

Run:
    uv run --env-file .env pytest -m integration \
        packages/pyvelo-intent/tests/integration/test_effective_live.py -s
"""

from __future__ import annotations

import pytest
from live_support import Lab, check
from pyvelo_intent import ConfigPipeline

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]


async def test_the_effective_view_matches_the_composed_modules(
    lab: Lab, pipeline: ConfigPipeline
) -> None:
    problems: list[str] = []
    compared = 0
    for layout in lab.layouts:
        label = f"edge #{layout.index}"
        state = await pipeline.load(layout.edge_id, enterprise_id=lab.enterprise_id)
        comparison = await pipeline.compare_effective(state)
        compared += 1
        print(f"\n{label}\n{comparison.render()}")
        if comparison.findings:
            problems.append(
                f"{label}: {len(comparison.findings)} finding(s)\n{comparison.render()}"
            )
    if not compared:
        pytest.skip("the Orchestrator has no edge with a profile")
    check(
        not problems,
        f"{len(problems)} of {compared} edge(s) differ from the composed modules:\n"
        + "\n".join(problems),
    )
