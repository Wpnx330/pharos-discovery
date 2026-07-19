# Pharos Discovery

> The universal agent discovery framework for MCP — search, evaluate, approve, and connect to MCP services from any AI agent.

Pharos Discovery is a framework that any AI agent can embed to discover MCP services at runtime. Instead of each agent provider building their own walled-garden discovery (Claude Connectors, MS Copilot, etc.), Pharos Discovery provides a single open standard that works across all agents.

## Why?

The MCP ecosystem is fragmenting. Every major agent provider is building their own discovery channel. Businesses that want to be found by agents have to publish to 10+ different directories. Pharos Discovery solves this with one universal, open framework.

## How It Works

1. **Agent receives a user request** that requires a capability it doesn't have
2. **Agent searches the Pharos registry** using natural language or structured queries
3. **Agent presents matching MCP servers** to the user with rich metadata (capabilities, auth requirements, pricing, reviews)
4. **User approves** the connection — privacy-first, no silent connections
5. **Agent installs/enables the MCP server** and begins using it
6. **Agent reports results** back to the user with tool usage transparency

## Features (Planned)

- **Provider-agnostic** — Works with Claude, GPT, DeepSeek, Gemini, xAI, Zap, any agent
- **User-approval-gated** — No agent connects to anything without explicit user consent
- **Rich metadata** — Capabilities, auth requirements, pricing, publisher verification
- **Runtime discovery** — Find and enable capabilities at runtime, no pre-configuration
- **Transport-agnostic** — Works with both stdio and HTTP/SSE MCP transports
- **Embeddable** — Thin client library for Python, TypeScript, and more

## Status

🚧 **Early development** — Specification and architecture in progress.

## License

MIT
