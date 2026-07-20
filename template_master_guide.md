# <PROJECT_NAME> Development Guide for AI Agents

**Version:** 1.0
**Last Updated:** YYYY-MM-DD
**Purpose:** Master guide for AI agents working on <PROJECT_NAME> — routes to specific guides as needed

---

## 🎯 Overview

This guide helps you navigate <PROJECT_NAME>'s documentation efficiently. **This file should be attached to every session** where an AI agent is helping develop this project. From here, fetch specific guides as needed to save tokens.

<PROJECT_SUMMARY — 2-3 sentences describing what the project does>

---

## ⚡ First Action: Read the Repo

Before doing anything else, read this repository to get the latest instructions,
flow, and best coding practices. The `.guides/` and `docs/` directories contain
the current standards — they may have been updated since this file was last
modified. The codebase itself is the source of truth for patterns and conventions.

> **This entire guide is a starting point.** The `.guides/` and `docs/` directories
> contain the actual, up-to-date standards. Always check them before assuming
> this file's contents are current.

---

## 📋 Project Structure

```
<PROJECT_NAME>/
├── <PROJECT>_GUIDE.md       # ← YOU ARE HERE (master orchestrator)
│
├── .guides/                  # Your decision-making guides (cascade system)
│   ├── architecture/         # High-level architecture decisions and patterns
│   ├── backend/              # Backend-specific patterns (if applicable)
│   ├── frontend/             # Frontend-specific patterns (if applicable)
│   ├── deployment/           # Build, deploy, infrastructure patterns
│   ├── security/             # Security, data handling, encryption
│   └── testing/              # Testing patterns and setup
│
├── docs/                     # Technical reference documentation
│   ├── technical/            # System architecture docs (ADR-style)
│   ├── api/                  # API specifications and contracts
│   ├── components/           # Component documentation
│   ├── troubleshooting/      # Common issues and solutions
│   └── examples/             # Step-by-step examples and walkthroughs
│
├── src/                      # Source code (adjust per project structure)
│   ├── ...
│   └── tests/
│
└── [other project files]
```

---

## 🧭 When to Use Which Guide

### Quick Decision Tree

```
User/TRON gives a task
    ↓
Read THIS file (<PROJECT>_GUIDE.md)
    ↓
Read the repo to check for updates to guides & docs
    ↓
Is this a development task (not just a question)?
    YES → Determine task type:
    ├─ Architecture decision? → .guides/architecture/
    ├─ Backend work? → .guides/backend/
    ├─ Frontend work? → .guides/frontend/
    ├─ Deployment work? → .guides/deployment/
    ├─ Security concern? → .guides/security/
    └─ Testing setup? → .guides/testing/
```

### Guide Reference Table

Populate this table as guides are created:

| Working On | Fetch This Guide | What You'll Learn |
|------------|------------------|-------------------|
| 🏗️ Architecture | `.guides/architecture/OVERVIEW.md` | System design, component relationships, data flow |
| 🔧 Backend | `.guides/backend/BE_GUIDE.md` | Backend patterns, API design, data layer |
| 🎨 Frontend | `.guides/frontend/FE_GUIDE.md` | UI patterns, component structure, state management |
| 🐋 Deployment | `.guides/deployment/DEPLOYMENT_GUIDE.md` | Build, deploy, CI/CD, infrastructure |
| 🔒 Security | `.guides/security/SECURITY_GUIDE.md` | Encryption, secrets, data handling |
| 🧪 Testing | `.guides/testing/TESTING_GUIDE.md` | Test patterns, setup, running tests |

---

## 🏗️ Tech Stack

### Core
- **Language:** <language, e.g., Python 3.11>
- **Framework:** <framework, e.g., FastAPI>
- **Database:** <database, e.g., PostgreSQL 16 + pgvector>

### Frontend (if applicable)
- **Framework:** <framework, e.g., React 18 + TypeScript>
- **Build Tool:** <tool, e.g., Vite>
- **UI Library:** <library, e.g., Chakra UI v3>

### Backend (if applicable)
- **Runtime:** <runtime, e.g., Python 3.11 / Node.js 22>
- **Framework:** <framework, e.g., FastAPI / Express>
- **Key Libraries:** <list important dependencies>

### Infrastructure
- **Hosting:** <platform, e.g., Docker / k8s>
- **CI/CD:** <tool, e.g., GitHub Actions>
- **Monitoring:** <tool, e.g., Prometheus + Grafana>

---

## 🚀 Development Workflow for AI Agents

Follow this workflow for ALL development tasks:

### 1. Understand the Request

**Read the prompt carefully:**
- What is the specific task?
- What files/systems are involved?
- What patterns should be followed?
- What constraints exist?

### 2. Read the Repo for Latest Standards

Check `.guides/` and `docs/` for any updates since this file was written.
The codebase may have evolved — always work from the latest patterns.

### 3. Determine Task Type

**Ask yourself:**
- Is this architecture work? → `.guides/architecture/`
- Is this backend? → `.guides/backend/`
- Is this frontend? → `.guides/frontend/`
- Is this deployment/CI? → `.guides/deployment/`
- Is this security-related? → `.guides/security/`
- Is this testing? → `.guides/testing/`

### 4. Load the Appropriate Guide

Based on the task type, load the corresponding guide from `.guides/`.
The guide will tell you the specific patterns, conventions, and rules to follow.

### 5. Read Technical Docs (As Needed)

Guides will reference technical documentation in `docs/`. Fetch them when needed:
- System architecture → `docs/technical/`
- API specs → `docs/api/`
- Component docs → `docs/components/`
- Examples → `docs/examples/`

### 6. Examine Existing Code

Before proposing a solution, look at existing patterns in the codebase:
- How are similar features implemented?
- What naming conventions are used?
- What patterns are repeated?

### 7. Security Review (CRITICAL)

Before writing any code, evaluate whether the change introduces security risks:

- [ ] Does this change expose the application to hackers? (XSS, SQL injection, path traversal, etc.)
- [ ] Does this change introduce new attack surface? (new endpoints, new file handling, new user input)
- [ ] Does this change handle sensitive data? (PII, credentials, tokens)
- [ ] Does this change modify authentication or authorization logic?
- [ ] Does this change introduce new dependencies with known vulnerabilities?

**If YES to any of the above:**
- Design a proper security solution before proceeding
- Flag the finding to Christopher and get express approval before implementation
- Do NOT proceed with an insecure implementation and "fix it later"

### 8. Implement

Follow the patterns you found. Adhere to the guides. Do NOT introduce new
patterns unless existing ones are demonstrably deficient — and if you find
deficiencies, suggest upgrades rather than silently diverging.

### 9. Write Tests

- **Unit tests are required** for all new functionality
- Follow the testing patterns in `.guides/testing/`
- Cover: happy path, error cases, edge cases
- Run the existing test suite to verify nothing broke
- Aim for meaningful coverage, not 100% line coverage for its own sake

### 10. Commit

Follow the project's commit message format (defined in `.guides/` or project conventions):

Standard format (adapt as needed):
```
<type>(<scope>): <short summary>

<optional body with details>
```

| Type | When |
|------|------|
| `feat` | New feature |
| `fix` | Bug fix |
| `refactor` | Code change with no behavior change |
| `test` | Adding or fixing tests |
| `docs` | Documentation only |
| `chore` | Build, CI, tooling |

### 11. Verify

- [ ] Does it compile/build?
- [ ] Do tests pass?
- [ ] Is the diff clean? (no debug code, no unrelated changes)
- [ ] Does it follow existing patterns?
- [ ] Was the security review completed?
- [ ] Is documentation updated?

---

## 🔐 Key Conventions

<Add project-specific conventions here as they're discovered. Examples:>

- **Error handling:** Use result types / exceptions / error codes consistently
- **Logging:** Structured logging with correlation IDs
- **Naming:** snake_case for Python, camelCase for TypeScript
- **Tests:** pytest with fixtures, describe/it blocks
- **Database:** Migrations via Alembic, all queries use parameterized statements
- **API:** RESTful, versioned, JSON request/response

---

## ⚠️ Common Pitfalls

<Add project-specific pitfalls here as they're discovered. Examples:>

- **Forgetting to @-reference the GUIDE file:** Every agent prompt must include
  `@<PROJECT>_GUIDE.md` or the agent loses project context
- **Skipping security review:** Always run through the security checklist before
  implementing — fixing a vulnerability after it's in production is 10x harder
- **No tests for new code:** Unit tests are not optional. If existing tests don't
  cover the new path, add tests.

---

*This guide should be updated as the project evolves. Keep it current.*
