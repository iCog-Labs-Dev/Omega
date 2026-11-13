# Authorization and agent boundaries

Knowledge class: reference guidance, not execution authority  
Reviewed: 2026-11-12

## Core controls

Apply least privilege and deny by default. Distinguish authentication, authorization, delegation, and approval. A valid identity or a delegated task does not imply permission to access every resource or perform every operation.

Bind each subagent run to an explicit role, target scope, allowed tool set, execution budget, expected output, and parent workflow. The research role remains read-only. A coding or remediation role may write only the declared artifacts. A reviewer independently checks claims using read-only tools. Only one role should hold write authority for a target at a time.

## Enforcement

Enforce restrictions at the tool boundary rather than relying only on prompt instructions. Validate the agent, action, resource, scope, and remaining budget on every tool call. Missing, expired, revoked, or malformed policy inputs must deny the operation. Do not broaden a worker's permissions when it requests help; return the bounded problem to the parent for a narrower reassignment.

## Coordination

The parent may use a blocked result to delegate a missing capability once, provided the new task remains within the original scope. Prevent delegation loops, duplicate writers, unbounded retries, and conversion of timeout or cancellation into completion. Preserve session, run, policy, and result identifiers in the audit trace.

## Verification

Test allowed and denied operations, expired grants, scope mismatches, budget exhaustion, and concurrent write attempts. Confirm the effective decision at the enforcement point, not only the planned policy or model response.

## Sources

- [NIST SP 800-207, Zero Trust Architecture](https://csrc.nist.gov/pubs/sp/800/207/final)
- [OWASP Authorization Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/Authorization_Cheat_Sheet.html)
