from __future__ import annotations

import datetime as _dt
from collections.abc import Sequence
from typing import TYPE_CHECKING, Protocol

from pydantic_core import MISSING

from pyvelo.client.operations._tagging import EDGE, domain_tag
from pyvelo.client.schemas.diagnostic_bundles import (
    DeleteDiagnosticBundle,
    DeleteDiagnosticBundleResult,
    DiagnosticBundle,
    GetDiagnosticBundle,
    GetDiagnosticBundles,
    InsertDiagnosticBundle,
    InsertDiagnosticBundleResult,
    UpdateDiagnosticBundle,
    UpdateDiagnosticBundleResult,
)

if TYPE_CHECKING:
    from pyvelo.client._client_protocol import VcoClientBaseProtocol

_MIN_TS = _dt.datetime.min.replace(tzinfo=_dt.UTC)


class DiagnosticBundlesMixin(Protocol):
    """Operations for diagnosticBundle/ API endpoints."""

    @domain_tag(EDGE)
    async def insert_diagnostic_bundle(
        self: VcoClientBaseProtocol, request: InsertDiagnosticBundle
    ) -> InsertDiagnosticBundleResult:
        return await self.portal(
            "diagnosticBundle/insertDiagnosticBundle",
            request,
            InsertDiagnosticBundleResult,
        )

    @domain_tag(EDGE)
    async def get_diagnostic_bundle(
        self: VcoClientBaseProtocol, request: GetDiagnosticBundle
    ) -> DiagnosticBundle:
        return await self.portal(
            "diagnosticBundle/getDiagnosticBundle",
            request,
            DiagnosticBundle,
        )

    @domain_tag(EDGE)
    async def get_diagnostic_bundles(
        self: VcoClientBaseProtocol, request: GetDiagnosticBundles
    ) -> Sequence[DiagnosticBundle]:
        """List diagnostic bundles for an enterprise, optionally filtered by edge_id.

        VCO always returns the full unsorted list (bundles are stored id-ASC =
        oldest-first). This wrapper applies ``request.sort`` and ``request.count``
        client-side after the response arrives — neither param is sent to VCO,
        since the endpoint does not declare filterSpec support for them and would
        reject them with ``sorting not supported for api``.

        Each returned DiagnosticBundle includes request_id (UUID), status,
        created timestamp, edge identifiers, and (when status is COMPLETE)
        uploadDetails.hash — the SHA shown on the VCO UI. Use this to
        resolve a user-supplied bundle hash to its request_id: list bundles
        for the enterprise, then match uploadDetails.hash to find the
        corresponding bundle entry.
        """
        bundles = await self.portal_list(
            "diagnosticBundle/getDiagnosticBundles",
            request,
            DiagnosticBundle,
        )
        ordered = sorted(
            bundles,
            key=lambda b: b.created if b.created is not MISSING else _MIN_TS,
            reverse=request.sort == "desc",
        )
        return ordered[: request.count]

    @domain_tag(EDGE)
    async def delete_diagnostic_bundle(
        self: VcoClientBaseProtocol, request: DeleteDiagnosticBundle
    ) -> DeleteDiagnosticBundleResult:
        return await self.portal(
            "diagnosticBundle/deleteDiagnosticBundle",
            request,
            DeleteDiagnosticBundleResult,
        )

    @domain_tag(EDGE)
    async def update_diagnostic_bundle(
        self: VcoClientBaseProtocol, request: UpdateDiagnosticBundle
    ) -> UpdateDiagnosticBundleResult:
        return await self.portal(
            "diagnosticBundle/updateDiagnosticBundle",
            request,
            UpdateDiagnosticBundleResult,
        )
