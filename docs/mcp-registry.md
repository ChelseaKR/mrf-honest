# MCP registry listing

Last verified: 2026-10-17. Recheck when a release is cut, when the package is
uploaded to an index, and before any `mcp-publisher publish`. The registry's
schema and rules move; the sources are listed at the end.

## There is no `server.json`, on purpose

`mrf-honest mcp --site <dir>` serves the published dataset over MCP on stdio,
read-only and offline. The registry lists a server by pointing at a package it can
install: npm, PyPI, NuGet, a container image, or an MCPB bundle on GitHub
Releases. This project has none of those.

- It is not on PyPI (`https://pypi.org/pypi/mrf-honest/json` returned 404 on
  2026-10-17).
- `v0.1.0` is a signed tag with a GitHub Release, which
  [ADR 0008](adr/0008-release-versioning-applies.md) says is not an index listing.
- The dataset is not part of the package. `<dir>` is a site built by
  `mrf-honest site`.

A `server.json` today would either name a package that does not exist, which a
client would try to install and fail, or name none and give the registry a pointer
to this repository with nothing to install. Neither helps a reader, so the file is
not committed. The README says the same in its MCP section.

## What a client can do today

```bash
uvx --from "git+https://github.com/ChelseaKR/mrf-honest@v0.1.0" mrf-honest mcp --site <dir>
```

The server's name is `io.github.chelseakr/mrf-honest` and its version is read from
the installed distribution's metadata, so it follows `pyproject.toml`.

## When the package is on PyPI

1. Add this line to `README.md`, which becomes the PyPI description. The registry
   looks for it to confirm the package belongs to this server name:

   ```markdown
   <!-- mcp-name: io.github.chelseakr/mrf-honest -->
   ```

2. Add `server.json` at the repository root. `version` is the version actually on
   PyPI and has to be updated with each release. Fields come from the 2025-12-11
   schema; the description limit is 100 characters:

   ```json
   {
     "$schema": "https://static.modelcontextprotocol.io/schemas/2025-12-11/server.schema.json",
     "name": "io.github.chelseakr/mrf-honest",
     "title": "MRF Honest",
     "description": "Read-only, offline access to published hospital price-transparency grades and statistics.",
     "version": "0.1.0",
     "websiteUrl": "https://github.com/ChelseaKR/mrf-honest",
     "repository": {
       "url": "https://github.com/ChelseaKR/mrf-honest",
       "source": "github"
     },
     "packages": [
       {
         "registryType": "pypi",
         "identifier": "mrf-honest",
         "version": "0.1.0",
         "runtimeHint": "uvx",
         "transport": { "type": "stdio" },
         "packageArguments": [
           { "type": "positional", "value": "mcp" },
           {
             "type": "named",
             "name": "--site",
             "description": "Directory written by `mrf-honest site`, containing api/",
             "format": "filepath",
             "default": "site"
           }
         ]
       }
     ]
   }
   ```

   `mcp` goes in `packageArguments`, not `runtimeArguments`: the latter are flags
   for the runner (`uvx`), the former are arguments to the program.

3. Check it against the schema:

   ```bash
   curl -sSfo /tmp/server.schema.json \
     https://static.modelcontextprotocol.io/schemas/2025-12-11/server.schema.json
   uv run --with jsonschema python -c '
   import json, sys
   from jsonschema import Draft7Validator
   schema = json.load(open("/tmp/server.schema.json"))
   doc = json.load(open("server.json"))
   errors = list(Draft7Validator(schema).iter_errors(doc))
   for e in errors:
       print(list(e.path), e.message)
   sys.exit(1 if errors else 0)'
   ```

4. Install `mcp-publisher` from the registry's releases, run
   `mcp-publisher login github` as the `ChelseaKR` account, then
   `mcp-publisher publish` from the repository root. The name is in the
   `io.github.<login>/` namespace.

The server name is lower case while the GitHub login is `ChelseaKR`. Whether the
registry compares the two case-insensitively was not verified. If publishing is
refused on the namespace, the name has to change to match, in `server.json` and in
`SERVER_NAME` in `src/mrf_honest/mcp.py`.

Do not add the `packages` entry before the upload: the registry checks the package
at publish time, and a client that reads the entry will try to install it.

The example above was validated against the 2025-12-11 schema on 2026-10-17.

## Sources

- `https://static.modelcontextprotocol.io/schemas/2025-12-11/server.schema.json`
- `docs/reference/server-json/` and `docs/modelcontextprotocol-io/package-types.mdx`
  in `github.com/modelcontextprotocol/registry`, read 2026-10-17
