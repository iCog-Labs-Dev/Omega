# Incident response and recovery

Knowledge class: reference guidance, not execution authority  
Reviewed: 2026-11-12

## Preparation and triage

Define roles, responsibilities, authority, communication paths, evidence handling, recovery access, and common playbooks before an incident. Group related observations, distinguish event from confirmed incident, and keep severity, confidence, and urgency separate.

## Bounded response

Containment, eradication, recovery, isolation, credential revocation, shutdown, and destructive actions require the authority defined for the affected assets. Escalation may notify a controller immediately but does not create new execution permission. Stop when scope drifts, recovery access is lost, or an action causes unexpected effects.

## Recovery and verification

Preserve evidence before disruptive changes when safe and authorized. Verify the repaired condition, service availability, persistence of the fix, and outstanding risk before closure. Record partial effects and rollback results. Convert lessons into evidence-linked follow-up work rather than silently changing policy or agent permissions.

## Reporting

Maintain an incident timeline with affected assets, observations and hypotheses, authorization decisions, actions, evidence references, recovery checks, remaining risk, owner, and review date. Missing information must remain visible as blocked or inconclusive.

## Sources

- [NIST SP 800-61 Rev. 3, Incident Response Recommendations and Considerations](https://csrc.nist.gov/pubs/sp/800/61/r3/final)
