# 27 - Definition of Done

A feature/agent is production-ready only when:

- input/output schemas are versioned,
- capability is exposed as a registered tool in a toolset (or covered by a test),
- tool/data permissions are explicit,
- unit and contract tests exist,
- observability fields are emitted,
- failure and timeout behavior is defined,
- replay test cases exist,
- security review is complete,
- claim/evidence requirements are documented,
- rollback/disable switch exists.

A mission is complete only when:

- mission run steps reach terminal states,
- required claims pass provenance policy,
- the EvidenceValidator gate passes or unresolved limitations are stated,
- final output references evidence/artifacts,
- audit trail is persisted.
