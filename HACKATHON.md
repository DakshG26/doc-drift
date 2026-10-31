# HACKATHON.md

## Team Name
Daksh

## Team Members
- Daksh Gupta (2601010104)

## Problem Statement
Problem 05 — Automated Documentation Drift Detection

## AI Models Used
- **gemma4:e4b** (Local open-weight model via Ollama)
- **qwen** (Local open-weight model via Ollama)
- **deepseek** (Local open-weight model via Ollama)
- **sentence-transformers/all-MiniLM-L6-v2** (Local embedding model)

## Reason for Model Selection
- **gemma4:e4b via Ollama:** Utilized for deep semantic reasoning and contextual verification to determine whether code changes invalidate specific documentation sentences.
- **qwen via Ollama:** Deployed locally for structured parsing, extracting code parameters, configuration flags, and technical claims from pull request diffs.
- **deepseek via Ollama:** Used locally for pattern matching, logical consistency checks, and cross-referencing code behavior against markdown documentation prose.
- **all-MiniLM-L6-v2:** A lightweight local embedding model used for fast semantic vector matching between code changes and documentation paragraphs to find relevant context without cloud dependencies.
