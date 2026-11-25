"""The client's ``diagnosticBundle/`` methods.

An Edge's diagnostic bundles and packet captures: request one, read, update and
delete them.
"""

from __future__ import annotations

import datetime as dt
from typing import TYPE_CHECKING, Protocol

from pydantic import MISSING

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
    from collections.abc import Sequence

    from pyvelo.client._client_protocol import VcoClientBaseProtocol

_MIN_TS = dt.datetime.min.replace(tzinfo=dt.UTC)


class DiagnosticBundlesMixin(Protocol):
    """Operations for diagnosticBundle/ API endpoints."""

    @domain_tag(EDGE)
    async def insert_diagnostic_bundle(
        self: VcoClientBaseProtocol, request: InsertDiagnosticBundle
    ) -> InsertDiagnosticBundleResult:
        """Request a diagnostic bundle or a packet capture from an Edge.

        ``request.options`` says which: a diagnostic dump, or a capture of the
        packets on one interface. Returns the ID of the new bundle, whose
        status ``get_diagnostic_bundle`` reads.
        """
        return await self.portal(
            "diagnosticBundle/insertDiagnosticBundle",
            request,
            InsertDiagnosticBundleResult,
        )

    @domain_tag(EDGE)
    async def get_diagnostic_bundle(
        self: VcoClientBaseProtocol, request: GetDiagnosticBundle
    ) -> DiagnosticBundle:
        """Get one diagnostic bundle by its ID.

        The bundle has its status, its Edge and, when it is complete, its upload
        details and download URL.
        """
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
        """Delete a diagnostic bundle by its ID. Returns the count of rows deleted."""
        return await self.portal(
            "diagnosticBundle/deleteDiagnosticBundle",
            request,
            DeleteDiagnosticBundleResult,
        )

    @domain_tag(EDGE)
    async def update_diagnostic_bundle(
        self: VcoClientBaseProtocol, request: UpdateDiagnosticBundle
    ) -> UpdateDiagnosticBundleResult:
        """Set when a diagnostic bundle ages out, the one field this updates.

        Returns the count of rows updated.
        """
        return await self.portal(
            "diagnosticBundle/updateDiagnosticBundle",
            request,
            UpdateDiagnosticBundleResult,
        )
