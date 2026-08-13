---
name: principle-interface-design
description: "Apply when designing REST/gRPC/CLI or internal interfaces. AIP (google.aip.dev) is the source of truth: its principles generalize everywhere; its concrete rules are for resource-oriented network APIs. Fetch and read a specific AIP before citing it."
disable-model-invocation: true
---

# Interface & API design

- AIP (https://google.aip.dev) is the source of truth. Two layers:
  - Its **principles** — consistency, standard verbs, resource naming, pagination thinking — generalize to REST, gRPC, CLI, and internal interfaces alike.
  - Its **concrete rules** — HTTP mapping, field masks, LROs, `:customVerb` — are for resource-oriented network APIs (gRPC/protobuf, Google-style REST). Don't force RPC/streaming/GraphQL/CLI into them.
- When citing or applying a **specific AIP rule** to a resource-oriented API, fetch and read it first — don't work from memory of AIP numbers. General interface design needs only the principles layer.
