# Evidence integrity and reproducibility

Knowledge class: reference guidance, not execution authority  
Reviewed: 2026-11-12

## Collection

Define the question and required evidence before collection. Record the collection's effect on the target, especially for volatile state. Collect only within the approved scope and minimize sensitive or unrelated data.

Preserve original artifacts with a cryptographic hash, source target, timestamp and time-zone context, collector identity, tool and parser versions, authorization reference, redaction status, and custody or access history. A hash detects change relative to captured bytes; it does not independently authenticate the collector or establish that an observation is true.

## Analysis

Analyze controlled copies. Separate direct observations from interpretations and hypotheses. Retain commands, parameters, versions, and deterministic verification steps needed to reproduce the result. Reference sensitive evidence from agent memory instead of duplicating credentials, private dumps, or full transcripts.

## Reporting

Connect every finding to evidence and every remediation claim to an independent check. Report alternative explanations, confidence, collection limits, unresolved risk, and required functionality results. A successful process exit is not sufficient evidence that the security objective was achieved.

## Sources

- [NIST SP 800-86, Guide to Integrating Forensic Techniques into Incident Response](https://csrc.nist.gov/pubs/sp/800/86/final)
