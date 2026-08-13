---
name: style-go
description: "Code & delivery. Apply when writing or reviewing Go. Never silently ignore errors; errors.Is/As over string matching; ctx first param through I/O and concurrency; every goroutine has a managed lifecycle; native idioms over JS reflexes."
---

# Go style

- **Never silently ignore a returned `error`.** Intentional drops are `_ = w.Close() // why it's safe here`. No `_`, no comment → it's a bug.
- **Inspect errors with `errors.Is`/`errors.As`, never string-matching** — the message is for humans and can change. Wrap with `fmt.Errorf("...: %w", err)` to keep the chain.
- **Every function doing I/O or starting concurrency takes `ctx context.Context` first** and propagates it. No context in struct fields; no fresh `context.Background()` deep in the tree when a caller's ctx exists.
- **Every goroutine has a managed lifecycle** — bounded by ctx cancellation, `errgroup`, `WaitGroup`, or channel signaling. No fire-and-forget.

**Write Go, not JavaScript.** Litmus test: if the code would translate to JavaScript nearly line-for-line, rewrite with native constructs:

| JS reflex                            | Native idiom                                       |
| ------------------------------------ | -------------------------------------------------- |
| `map[string]any` for structured data | named structs                                      |
| `panic`/`recover` as try/catch       | error returns                                      |
| goroutines/channels as promises      | synchronous calls; `errgroup` for real concurrency |
