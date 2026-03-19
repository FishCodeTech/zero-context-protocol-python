# Releasing The Python SDK

## Local Validation

```bash
python -m pip install -e ".[dev,openai,mcp]"
python -m ruff check src tests examples tools
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest -q
python -m build
python -m twine check dist/*
```

## GitHub Release Flow

The repository includes a trusted-publisher release workflow at
`.github/workflows/release.yml`.

To use it for the `zero-context-protocol-sdk` distribution:

1. configure this GitHub repository as a trusted publisher in PyPI
2. push a tag like `v0.2.0`
3. let the workflow build and publish the package

## Manual Publishing

If you prefer local publishing:

```bash
python -m build
python -m twine upload dist/*
```

That path requires a configured PyPI token or `.pypirc`.
