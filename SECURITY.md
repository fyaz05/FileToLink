# Security Policy

## Reporting a vulnerability

Please open a private security advisory via GitHub's **Report a vulnerability**
button on the Security tab, or contact the owner directly. Do not open a
public issue for security reports.

## Scope notes

- `/shell` is disabled by default; enable only with `ENABLE_SHELL=True` on
  trusted deployments (owner-only regardless).
- Session files are chmod 0600 after startup; keep them out of backups.
- `/log` uploads are regex-redacted (bot tokens, Mongo URIs) before upload.
