# Bun + TypeScript Migration Proposal

## Why migrate?

The current architecture works, but several parts are tightly coupled to Python and Claude Code Action.

The repository is actually IO-heavy rather than compute-heavy:

- RSS/API fetching
- JSON processing
- archive rendering
- Discord webhook delivery
- state persistence

These workloads map very naturally to Bun + TypeScript.

---

## Recommended Target Architecture

```text
src/
 ├── collect.ts
 ├── curate.ts
 ├── publish.ts
 ├── lib/
 │    ├── rss.ts
 │    ├── normalize.ts
 │    ├── discord.ts
 │    ├── github.ts
 │    └── state.ts
 └── types/
```

---

## Migration Strategy

### Phase 1 — Runtime migration only

Keep current behavior.

Replace:

- collect_data.py
- daily_report.py
- send_discord.py

with:

- collect.ts
- curate.ts
- publish.ts

This minimizes risk.

---

### Phase 2 — LLM abstraction

Current architecture is heavily coupled to:

```yaml
anthropics/claude-code-action@v1
```

Introduce provider abstraction:

```text
src/llm/
 ├── claude.ts
 ├── codex.ts
 ├── openai.ts
 └── local.ts
```

Benefits:

- easier provider switching
- Codex experimentation
- local LLM support
- future API support

---

## Bun Advantages

### Faster CI startup

Bun avoids Python dependency installation overhead.

### Cleaner fetch API

```ts
const res = await fetch(url)
const data = await res.json()
```

### Built-in filesystem helpers

```ts
await Bun.write(path, content)
```

### Better alignment with modern AI tooling

Most modern AI SDKs prioritize:

- TypeScript
- Node runtime
- streaming APIs
- tool calling

---

## Suggested GitHub Actions Update

Current:

```yaml
pip install requests feedparser
python3 scripts/collect_data.py
```

Suggested:

```yaml
- uses: oven-sh/setup-bun@v2

- run: bun install
- run: bun run collect
- run: bun run curate
- run: bun run publish
```

---

## Important Observation

The largest architectural dependency is NOT Python.

It is:

```yaml
anthropics/claude-code-action@v1
```

Therefore:

- language migration = easy
- provider decoupling = harder

Migration should prioritize:

1. runtime simplification
2. provider abstraction
3. Codex experimentation

in that order.

---

## Recommended Next Step

Do NOT fully rewrite everything immediately.

Start with:

1. Bun setup
2. Type definitions
3. collect.ts migration
4. publish.ts migration
5. finally curate.ts

This reduces regression risk substantially.
