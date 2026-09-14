# Optimize for legibility over production-hardening

This repo is a job-search / reference artifact meant to be walked through live with a reviewer, not a system serving real traffic. We therefore optimize for **correctness** on the intended cases, **legibility** (an unfamiliar reader grasps the architecture in one pass), and **clean extensibility** (adding 5–10 more tools or a second agent must not force restructuring the graph). We explicitly do **not** optimize for horizontal scale, multi-tenancy, or latency under load — that work would add concurrency/tenancy complexity that directly harms the one-pass legibility this project exists to demonstrate.

## Consequences

- Scaling is treated as a **talking-points / documentation** concern, not a code concern. "How would this handle 10k users?" is answered in prose, never by pre-emptive abstraction in the diff. Constructs like retry queues, backpressure, connection pooling beyond the default, and tenant isolation stay out of the code unless a demoed case actually needs them.
- When reviewing any later change, the test is "does this make the codebase easier to read and extend?" — not "is this how a production system would do it?" The two answers differ often, and the first one wins here.
- Existing signals that encode this stance deliberately: Railway free-tier deploy, `Semaphore(3)` on streaming, and reviewer-facing "interview talking point" comments. These are features of the artifact, not tech debt.
