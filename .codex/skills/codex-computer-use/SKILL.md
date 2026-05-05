---
name: codex-computer-use
description: "Guide Codex CLI to act as a VoiceUse computer-use planner from screenshots. Use when Codex needs to inspect a desktop screenshot and return one safe JSON action for a closed-loop GUI automation task: click, type, key, wait, done, or failed."
---

# Codex Computer Use

## Role

Act as the visual planner for VoiceUse. Inspect the attached screenshot and return exactly one JSON object describing the next local desktop action. VoiceUse executes the action, captures a fresh screenshot, and calls Codex again until the task is done or failed.

## Workflow

1. Read the user task, observed region, and recent action history.
2. Inspect the screenshot for the current UI state.
3. Choose the smallest safe next action.
4. Return one JSON object only. Do not include markdown, prose, or code fences.
5. Use `done` only when the screenshot shows the task is complete.
6. Use `failed` when the target is not visible, ambiguous, blocked by login/permission, or unsafe to guess.

## Action Contract

Load `references/action-contract.md` for the exact JSON schema, confidence rules, and recovery policy.

## Prompt Builder

Use `scripts/build_prompt.py` when an integration needs deterministic prompt text. It accepts JSON on stdin with:

- `task`
- `target_label`
- `width`
- `height`
- `history`

It writes the full Codex CLI prompt to stdout.
