"""A profile state of the three modules that hold segments, and a pipeline over a store that holds them.

Test modules import this as they import each other
(``from profile_state import ...``). The segments and entries come from
``segment_helpers``.
"""

from typing import Any

from pydantic import MISSING
from pyvelo.client.schemas.configuration import EnterpriseConfigurationProfile
from pyvelo.config.resolver import ResolverContext
from pyvelo.testing import ModuleStore
from pyvelo_intent.config.pipeline import ConfigPipeline
from pyvelo_intent.config.state import ConfigState
from pyvelo_intent.testing import MockVcoClient
from refs_helpers import CONFIG
from segment_helpers import (
    GLOBAL,
    GUEST,
    VOICE,
    Segment,
    profile_rows,
    segment_registry,
)


async def profile_state(
    ds: list[dict[str, Any]],
    qos: list[dict[str, Any]],
    firewall: list[dict[str, Any]],
    *,
    segments: tuple[Segment, ...] = (GLOBAL, GUEST, VOICE),
    ds_refs: dict[str, Any] | MISSING | None = None,
    networks: list[dict[str, Any]] = [],  # noqa: B006 -- copied, never changed
) -> ConfigState:
    """A profile state of configuration ``CONFIG`` with the given segment lists, and a snapshot.

    The rows are those of ``profile_rows`` (*ds_refs* is its argument, given
    only when it is not None), and the device settings ``lan.networks`` are
    *networks*. The modules are at the profile level, as a load leaves them,
    so ``add_segment_entry`` takes them. The state's segment resolver is
    loaded with *segments*.
    """
    rows = (
        profile_rows(ds, qos, firewall, networks=networks)
        if ds_refs is None
        else profile_rows(ds, qos, firewall, ds_refs=ds_refs, networks=networks)
    )
    profile = EnterpriseConfigurationProfile.model_validate(
        {"name": "P", "id": CONFIG, "modules": rows}
    ).for_profile()
    state = ConfigState(
        edge_config=None,
        profile_config=profile,
        target_type="profile",
        resolver_context=ResolverContext(),
        resolvers=await segment_registry(*segments),
    )
    state.take_snapshot()
    return state


def module_store_pipeline(
    state: ConfigState, *, segments: tuple[Segment, ...] = (GLOBAL, GUEST, VOICE)
) -> tuple[ConfigPipeline, ModuleStore]:
    """A pipeline over a ``ModuleStore`` that holds the three modules of *state*'s profile and the objects of *segments*.

    A test that needs another writer adds a closure to ``store.on_store`` that
    calls ``store.other_writer(...)`` once.
    """
    client = MockVcoClient.for_state(state)
    store = client.store
    for segment_id, name, logical_id, object_id in segments:
        store.add_object(
            object_id,
            logical_id=logical_id,
            name=name,
            type_="REGULAR",
            object_="NETWORK_SEGMENT",
            data={"segmentId": segment_id},
        )
    return ConfigPipeline(client), store
