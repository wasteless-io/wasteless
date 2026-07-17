# Security policy

Wasteless reads cloud inventory and can optionally perform AWS write actions.
Security reports should therefore be handled privately and with enough detail
to reproduce the issue safely.

## Reporting a vulnerability

Do not open a public GitHub issue for a suspected vulnerability.

Email **wasteless.io.entreprise@gmail.com** with the subject
`[SECURITY] Wasteless vulnerability report` and include:

- the affected release or commit;
- the component and deployment model involved;
- reproduction steps or a minimal proof of concept;
- the expected and observed security impact;
- any temporary mitigation you identified.

Do not include live AWS credentials, `.env` files, customer data or other
secrets. Use synthetic identifiers and redact account details.

The maintainers will acknowledge the report as soon as practical, reproduce
and assess it, coordinate a fix and disclosure with the reporter, then publish
an advisory when users need to take action. No fixed response SLA is currently
offered.

## Supported versions

Security fixes target the latest published release and the current `main`
branch. Older releases may not receive backports. Confirm the affected version
in your report.

## Deployment responsibilities

Wasteless is self-hosted. Operators remain responsible for:

- placing the UI behind authentication and TLS when it is network-accessible;
- keeping PostgreSQL private and restricting host access;
- protecting `.env`, AWS source credentials and AI-provider keys;
- using the read-only and remediation roles described in
  [docs/AWS_SETUP.md](docs/AWS_SETUP.md);
- reviewing dry-run, grace-period, whitelist and action policy before enabling
  writes;
- updating Wasteless, Python packages, Docker and the host operating system;
- reviewing CloudTrail and Wasteless action history after sensitive changes.

In the recommended role-based setup, detection can run with only the read-only
role and a matching Steampipe connection. This is the recommended starting
point for evaluating the product.

## Public security questions

General hardening questions that contain no sensitive information can be
opened as a [GitHub issue](https://github.com/wasteless-io/wasteless/issues).
Use private reporting whenever exploitability or confidential details are
involved.
