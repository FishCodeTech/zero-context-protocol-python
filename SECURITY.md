# Security Policy

## Supported Scope

Security reports are welcome for:

- the Python SDK and runtime in `src/zcp`
- transport and auth surfaces
- benchmark tooling when it can affect user environments

## Reporting A Vulnerability

Please do not open public issues for unpatched vulnerabilities.

When reporting:

- describe the affected component and version or commit
- include reproduction steps
- include impact assessment when possible

Maintainers should acknowledge reports promptly and coordinate a fix before
public disclosure.

## Secret Handling

This repository must not contain live provider API keys, OAuth client secrets,
or production tokens. Examples and benchmark tooling are expected to read
credentials from environment variables.
