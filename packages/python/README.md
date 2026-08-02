# Pharos Discovery SDK — Python

Discovery SDK for MCP servers — search, approve, connect.

## Installation

```bash
cd packages/python

# Create a virtual environment (required on Ubuntu 24+ / Debian 12+ / PEP 668)
python3 -m venv .venv
source .venv/bin/activate

# Install in editable mode
pip install -e .
```

## Quick start

```python
from pharos_discovery import ApprovalEngine, RiskLevel

# Review an install plan before running it
engine = ApprovalEngine()
review = engine.review(plan)
print(review.risk)   # RiskLevel.LOW | MEDIUM | HIGH
```

## Optional: embeddings

```bash
pip install -e ".[embeddings]"
```

See the root [README](../../README.md) for full project details.
