# Narrative MCP Package Boundary

Follow the root `AGENTS.md` and workspace Convergence Doctrine. This package owns authoritative
campaign phase, per-request authorization, deterministic tool catalog, revisions,
idempotency, random streams, atomic narrative settlement, profile/Pack runtime
binding, and isolated NPC conversation journals.

It must not infer prose meaning, audiences, NPC choices, geometry, or rules.
Level 1 mechanics are deliberately bounded and declarative. A mechanism that
needs executable code or a multi-step state machine belongs in an independent
system package.

MCP 2026-07-28 is the primary public protocol. The Host selects a small model
subset from the stable catalog; legacy initialized/session behavior is an
explicit migration adapter. Do not add CLI state mutation, text simulations,
compatibility aliases, or silent fallback paths.
