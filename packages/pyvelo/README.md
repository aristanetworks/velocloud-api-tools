# pyvelo

Python SDK for the VeloCloud Orchestrator (VCO) API. Pydantic-typed
client, request/response schemas, enums, models, and resolver/validation
framework.

## Install

From the workspace root:

```bash
uv sync
```

As a git dependency:

```toml
[project]
dependencies = ["pyvelo"]

[tool.uv.sources]
pyvelo = { git = "https://github.com/aristanetworks/velocloud-api-tools.git", subdirectory = "packages/pyvelo" }
```

## Surface

| Module | What it provides |
|---|---|
| `pyvelo.client` | `VcoClient` (async JSON-RPC), `VcoClientProtocol`, `VcoAuth`, `get_vco_credentials` (reads `VCO_HOST` / `VCO_TOKEN`, optionally from a `.env` file with the `dotenv` extra), `VcoWebSocket` / `PendingDiagnostic`, `ExchangeHook`, request/response schemas, enums, operations, VCO data models |
| `pyvelo.core` | `VcoModel` base class (with `.merge_patch()`), field markers (`KeyField`, `Resolvable`, `NameOf`, `OverrideFlag`, `Alias`, `Normalizer`, `ConfigContext`, `ContextDefault`), defaulting helpers (`DefaultingModel`, `DefaultingScalar`), identity types (`EdgeId`, `SegmentId`, `ProfileId`, …) |
| `pyvelo.config.resolver` | `Resolver` Protocol, `BaseResolver`, `ResolverRegistry`, `ResolverContext`, `ResolutionError` |
| `pyvelo.config.resolvers.*` | Concrete resolvers: edge, segment, profile, address_group, application, service_group, license |
| `pyvelo.config.validation` | `Validator` Protocol, `ValidatorRegistry`, `@validator` decorator, `ConfigStateLike` Protocol, `ValidationError`, `ValidationResult` |
| `pyvelo.testing` | Test fixtures: `FakeEdge`, `FakeSegment`, `mock_vco_client_factory`, `make_jsonrpc_response`, `make_transport`; `ModuleStore`, a fake VCO that holds configuration module rows and serves their reads and writes (`store_of`, `as_stored`) |

## Writing a configuration module

`VcoClient.update_module(module, data)` writes a module's data as it was read,
with only the caller's changes applied, and returns the module as stored, of
the class of `module`. When nothing changed it sends nothing and returns
`module` itself. It is `plan_module` (`pyvelo.client.module_write`) followed by
`write_module`.

`VcoClient.write_module(module, plan)` sends a plan as an update or an insert
that asks for the module as stored (`returnData`), and reads the module back
when the VCO answers without it. It then compares what the VCO stored with what
the plan meant to change. When every planned change is stored it returns a
`ModuleWrite`: the module as stored, the planned changes `applied`, and the
`server_changes` (what else the VCO stores otherwise than sent, such as an ID
it issued). Otherwise it raises, from `pyvelo.client.exceptions`:

- `ConfigConflictError` when the module changed on the VCO since it was read;
- `WriteNotAppliedError` when the VCO stored the write otherwise than as sent.

Each carries `lost_edits` (the planned changes not stored, with the values as
read, meant and stored), `applied`, `server_changes`, the `plan` and the
`module` as stored, when it could be read. Where the VCO stores a value its own
way (a field marked `ServerOwned`, and the rewrites per module in
`MODULE_REWRITES`, `pyvelo.client.module_check`), what it stores is not a lost
edit; a value other than the one read is a server change.

`pyvelo.testing.ModuleStore` is an `httpx` handler that holds module rows as
JSON and answers module updates, inserts and `getConfiguration` as a VCO does.
Its knobs are the conditional version check, a VCO that ignores `returnData`,
writes that fail, hooks that change what is stored (`on_store`) and another
writer's change (`other_writer`). A client built over it
(`make_transport(store)`) runs its own request building, parsing and error
mapping in tests.

## Portal list and pagination methods

| Method | Use for | Source requests |
|---|---|---|
| `portal_list` | Non-paginated list endpoints | One. A complete paginated envelope is unwrapped; an incomplete one raises. |
| `portal_paginated` | Callers that control paging | One per yielded page. |
| `portal_paginated_list` | Paginated endpoints returning a list | Until the last page, or `request._max_pages` pages when it is non-zero. |

Both paginated methods record `_page_has_more` and `_page_next_link` on the
caller's request after every page; the caller's `next_page_link` is not
modified. After a capped `portal_paginated_list`, `_page_has_more=True` marks
the result incomplete and `_page_next_link` resumes from the next page. A page
reporting `more` without a cursor raises instead of repeating the request.

## Source request observations

Pass an optional synchronous `request_observer` callback to `VcoClient` to
receive a frozen `RequestObservation` for each completed transport call,
including errors and cancellation. Observations count HTTP dispatch attempts,
429 responses, limiter/retry waits, numeric Retry-After delays, source latency,
and the client's configured rate limit and retry maximum. Each pagination call
produces its own observation. The existing limiter and retry policy are unchanged.

Observations contain the endpoint path, RPC method and cursor presence, with no
host, query string, headers, credentials, cursor value or response payload. An
`http_response` outcome means HTTP completed; it does not establish RPC success,
source completeness or metric availability. The observer must be lightweight;
callback errors are logged without their contents and preserve the source result.

## Tests

```bash
uv run --package pyvelo pytest packages/pyvelo/tests
```
