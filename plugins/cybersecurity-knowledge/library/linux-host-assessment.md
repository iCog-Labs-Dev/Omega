# Linux host assessment

Knowledge class: reference guidance, not execution authority  
Reviewed: 2026-11-12

## Discovery

Identify the distribution and release, kernel, package origins, enabled services, listening sockets, user and group assignments, sudo rules, authentication paths, scheduled jobs, writable executable or configuration paths, and backup and recovery arrangements. Select release-specific controls only after confirming applicability.

Inspect firewall behavior, remote administrative access, mandatory access control such as AppArmor or SELinux, audit availability, update status, ownership, modes, ACLs, secrets handling, and service sandboxing. A container view cannot attest to all host controls.

## Change discipline

Propose small, reversible changes with a pre-change snapshot and working recovery access. Validate configuration before reload. Preserve an existing administrative session during remote-access changes. Do not perform recursive ownership or permission changes without understanding application dependencies.

## Verification evidence

Record before-and-after configuration hashes, file ownership and modes, service state, package and advisory identifiers, constrained connectivity results, mandatory-access-control status, functional checks, and rollback results. Root access, reboot, firewall changes, and authentication changes require explicit authorization.

## Sources

- [Linux Security Module Usage](https://docs.kernel.org/admin-guide/LSM/index.html)
- [Ubuntu Security Guide](https://ubuntu.com/security/certifications/docs/usg)
- [Ubuntu AppArmor documentation](https://documentation.ubuntu.com/server/how-to/security/apparmor/)
