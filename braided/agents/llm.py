"""LLM completion with two backends:

- `anthropic` SDK when ANTHROPIC_API_KEY is set;
- headless `claude -p` (the user's Claude Code login) otherwise.

Model selectable via BRAIDED_MODEL (SDK id or CLI alias); default sonnet.
"""

from __future__ import annotations

import os
import subprocess
import tempfile

DEFAULT_SDK_MODEL = "claude-sonnet-5"
DEFAULT_CLI_MODEL = "sonnet"

CLI_TIMEOUT = 600


class LLMError(RuntimeError):
    pass


def _complete_sdk(prompt: str, system: str, model: str | None, max_tokens: int) -> str:
    import anthropic

    client = anthropic.Anthropic()
    msg = client.messages.create(
        model=model or DEFAULT_SDK_MODEL,
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": prompt}],
    )
    return "".join(block.text for block in msg.content if block.type == "text")


def _complete_cli(prompt: str, system: str, model: str | None, max_tokens: int) -> str:
    full = f"<system-instructions>\n{system}\n</system-instructions>\n\n{prompt}" if system else prompt
    # Neutral cwd: don't let the CLI pick up any project's CLAUDE.md/context.
    proc = subprocess.run(
        ["claude", "-p", "--model", model or DEFAULT_CLI_MODEL, "--output-format", "text"],
        input=full,
        capture_output=True,
        text=True,
        timeout=CLI_TIMEOUT,
        cwd=tempfile.gettempdir(),
    )
    if proc.returncode != 0:
        raise LLMError(f"claude CLI failed (exit {proc.returncode}): {proc.stderr.strip()[:500]}")
    return proc.stdout.strip()


def complete(prompt: str, system: str = "", max_tokens: int = 8000) -> str:
    import time

    model = os.environ.get("BRAIDED_MODEL")
    backoffs = [0, 15, 60]  # seconds before each try; rides out rate-limit blips
    last_err = None
    for delay in backoffs:
        if delay:
            time.sleep(delay)
        try:
            if os.environ.get("ANTHROPIC_API_KEY"):
                out = _complete_sdk(prompt, system, model, max_tokens)
            else:
                out = _complete_cli(prompt, system, model, max_tokens)
            if out.strip():
                return out
            last_err = LLMError("empty completion")
        except (LLMError, subprocess.TimeoutExpired) as e:
            last_err = e
    raise LLMError(f"completion failed after {len(backoffs)} tries: {last_err}")
