---
name: advance-narrative-scene
description: Open, advance, settle, or close an authoritative SagaSmith narrative scene. Use when resolving player intent, applying a profile mechanic, recording consequences, changing relationships or tracks, publishing audience-specific events, travelling, or transitioning to another scene or optional Conflict.
---

# Advance Narrative Scene

Read [the scene settlement contract](references/workflow.md) before acting.

Load the active scene, continuity, actor knowledge, profile capabilities, and expected revisions. Resolve only necessary mechanics, propose explicit events and state deltas with explicit audiences, commit them atomically, verify the resulting binding, then narrate from committed facts.

Never split one logical settlement into independent writes when the MCP supplies a settlement facade. Never infer listeners, comprehension, or proprietary rules. Block only when authority or a mechanical result would otherwise be undefined.

Reset context after scene, phase, branch, profile, actor-grant, restore, or Conflict changes.
