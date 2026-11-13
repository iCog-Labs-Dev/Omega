# Container isolation and hardening

Knowledge class: reference guidance, not execution authority  
Reviewed: 2026-11-12

## Assessment

Inspect the effective user, privileged flag, Linux capabilities, security options, namespaces, mounts, devices, published ports, network mode, resource limits, writable paths, and access to the container runtime socket. Namespace isolation and resource limits address different risks; both must be evaluated.

Treat access to the Docker daemon or its socket as host-sensitive because it can permit creation of containers with powerful mounts or privileges. Container authorization never implies authorization over the host, daemon, kernel, neighboring workloads, or external systems.

## Hardening

Prefer a non-root runtime identity, a read-only root filesystem, no-new-privileges, all capabilities dropped unless explicitly required, a default seccomp profile, private namespaces, bounded CPU and memory, and narrowly scoped writable tmpfs locations mounted with `nosuid`, `noexec`, and `nodev` where compatible. Do not attach the Docker socket, host devices, host namespaces, or broad host mounts to an assessment container.

Use a reviewed image and declarative runtime configuration. Avoid undocumented live changes as the production hardening mechanism. Preserve a reset snapshot and a tested recovery path before remediation.

## Verification evidence

Record the image digest, container identity, effective runtime configuration, UID, capability masks, security options, mount flags, routes, resource limits, and allowed and denied operation results. Independently verify both the security changes and required application behavior after recreation.

## Sources

- [Docker Engine security](https://docs.docker.com/engine/security/)
- [Docker seccomp security profiles](https://docs.docker.com/engine/security/seccomp/)
- [Docker resource constraints](https://docs.docker.com/engine/containers/resource_constraints/)
