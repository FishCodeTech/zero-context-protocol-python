# Contributing

## Development Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev,openai,mcp]"
```

For benchmark reproduction that depends on provider-backed calls, copy
[`.env.example`](.env.example)
to a local `.env` or export the variables directly.

## Before Opening A Pull Request

Run the local validation subset:

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest -q
python3 -m ruff check src tests examples tools
```

If you touched the docs repository, also run the docs build there:

```bash
cd ../zero-context-protocol/docs/web
npm ci
npm run build
```

## Contribution Scope

Good pull requests usually fall into one of these categories:

- protocol/runtime correctness
- MCP compatibility
- transport/auth improvements
- benchmark reproducibility
- docs and migration guidance

Avoid bundling unrelated refactors with functional changes.

## Commit And PR Guidance

- keep changes narrow and reviewable
- add or update tests for behavior changes
- describe compatibility impact clearly
- note benchmark methodology changes when touching benchmark code

## Security

Never commit provider secrets, benchmark API keys, or private deployment
details. Use environment variables and local `.env` files that stay ignored.
