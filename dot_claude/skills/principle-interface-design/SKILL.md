---
name: principle-interface-design
description: "Architecture. Apply when designing or reviewing a resource-oriented network API (gRPC/protobuf or Google-style REST): resource naming, standard verbs, custom and stateless methods, pagination, field masks, long-running operations. AIP (google.aip.dev) is the authority — fetch the specific AIP before citing it. Not for CLI or internal interface shape."
---

# Interface & API design

This leaf is a pointer to an external authority, not a rule of its own.

**AIP (https://google.aip.dev) is the source of truth for resource-oriented network APIs** — gRPC/protobuf and Google-style REST. It covers resource naming, standard verbs, pagination, field masks, long-running operations, and `:customVerb` HTTP mapping.

**Fetch the specific AIP before citing it.** Don't work from memory of AIP numbers: the numbering is dense and the rules get revised. Read the page, then cite it.

**A method that doesn't fit CRUD is still in scope.** AIP-136 covers custom methods, including stateless ones attached to no resource at all — and forbids reusing the standard verbs (Get/List/Create/Update/Delete) in a custom method's name. "Not resource-shaped" is not the same as "outside AIP".

**Genuinely outside:** streaming protocols, GraphQL, and CLI surfaces have their own conventions. Don't reach here for those.

Internal interface *shape* is not this leaf. Interface compression and layer count live in **principle-minimize-reader-load**; keeping transport, storage, and framework types from leaking across the edge lives in **principle-boundary-discipline**; changing an existing contract lives in **principle-migrate-callers-then-delete-legacy-apis**.
