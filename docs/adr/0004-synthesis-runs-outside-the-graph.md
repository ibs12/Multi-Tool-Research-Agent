# Synthesis runs outside the LangGraph graph

The research loop (`supervisor → dispatcher`) is a LangGraph `StateGraph` that ends at `END`. The final analyst brief is produced **outside** the graph: each entrypoint (`run.py`, `api/main.py`) calls `synthesis_node` / `stream_synthesis` on the final state after the loop. We keep synthesis outside rather than making it a terminal graph node.

## Why not a node

The SSE endpoint streams the report **token-by-token** (`report_chunk` events) via a direct Anthropic streaming call bridged with a thread + `queue.Queue`. Streaming tokens from *inside* a LangGraph node is possible but requires LangGraph's stream-writer / event machinery — trading a readable bridge for framework internals and weakening the live-typing demo, which is a genuine feature here. Making synthesis a node would give a cleaner "the graph is the whole agent" diagram but cost that.

## Consequences

- The graph **deliberately does not represent the terminal synthesis step**. `END` means "research complete, synthesis follows." A reader of `graph.py` will not find where the report is written — that is intentional, and documented at the boundary.
- To stop that phase boundary from being an implicit convention copy-pasted across entrypoints, the **batch** orchestration (`graph.invoke(state)` then `synthesis_node(result)`) is extracted into one named function that both the CLI and the batch API call.
- The two **streaming** sinks (CLI console vs SSE frames) stay separate. Their only shared code is the token loop; a shared sink abstraction would cost more legibility than the small duplication removes.
