# Multi-Tool Research Agent

Ubiquitous language for the agent's tool-orchestration model. Hard constraints between tools are enforced in code; soft preferences live in the supervisor prompt.

## Language

**Prerequisite**:
An ordering dependency between tools — tool A has a prerequisite on tool B when A consumes data B produced, so B must finish in an earlier iteration. A hard constraint, enforced structurally in code rather than left to the model to honor.
_Avoid_: dependency, guard.

**Guard**:
A precondition on a single tool's own inputs — whether that tool can validly run right now (e.g. a required ticker symbol is present). Answers "is this input valid?", never "what must have run first".
_Avoid_: prerequisite, validation, check.
