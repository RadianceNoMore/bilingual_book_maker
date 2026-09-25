"""Translate through local OpenCode CLI using free models.

No API key or paid subscription required. Drives the local `opencode` CLI
(`opencode run --pure --format json --model opencode/mimo-v2.6-flash-free`)
to translate texts directly using OpenCode's free model tier.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from rich import print
from rich.markup import escape

from ..glossary import Glossary
from .base_translator import Base

DEFAULT_MODEL = "opencode/mimo-v2.6-flash-free"

BASE_INSTRUCTIONS = (
    "You are a professional book translator. Your task is to translate the "
    "input text into {language}. Return ONLY the {language} translation, with "
    "no preamble, notes, explanations, or quotes. Keep the paragraph structure "
    "and any inline markup exactly as given."
)


class OpenCodeTranslator(Base):
    """Translator backed by the local OpenCode CLI using free models."""

    SUPPORTS_STRUCTURED_OUTPUTS = False
    SUPPORTS_SESSION_CONTEXT = True
    SUPPORTS_GLOSSARY = True
    BATCH_SYS_MSG_PER_REQUEST = False

    PROMPT_SECTION_SLOTS = {
        "user": "native",
        "system": "appended",
        "style": "appended",
    }
    PROMPT_APPEND_TARGET = "the prompt instructions"

    def __init__(
        self,
        key: str = "",
        language: str = "zh-hans",
        model: str | None = None,
        prompt_template: str | None = None,
        prompt_sys_msg: str | None = None,
        style_note: str | None = None,
        glossary: Glossary | None = None,
        binary: str | None = None,
        **kwargs,
    ) -> None:
        super().__init__(key or "", language)
        self.binary = binary or shutil.which("opencode") or "opencode"
        self.model = self._normalize_model(model or DEFAULT_MODEL)
        self.model_list = [self.model]
        self.prompt_template = prompt_template
        self.prompt_sys_msg = prompt_sys_msg
        self.style_note = style_note
        self.pinned = glossary or Glossary()
        self.learned = Glossary()
        self.glossary = self.pinned
        self.session_id: str | None = None
        self.quiet = kwargs.get("quiet", False)

    @staticmethod
    def _normalize_model(name: str) -> str:
        name = name.strip()
        if not name:
            return DEFAULT_MODEL
        if "/" not in name:
            return f"opencode/{name}"
        return name

    def rotate_key(self) -> None:
        """No keys needed; OpenCode CLI manages its own authentication."""
        pass

    def set_model_list(self, model_list: list[str]) -> None:
        if not model_list:
            self.model = DEFAULT_MODEL
            self.model_list = [self.model]
            return
        cleaned = [self._normalize_model(m) for m in model_list if m and m.strip()]
        self.model = cleaned[0] if cleaned else DEFAULT_MODEL
        self.model_list = cleaned or [self.model]

    def preflight(self) -> bool:
        """Verify the OpenCode CLI is installed and accessible."""
        if not shutil.which(self.binary):
            print(
                f"[bold red]Error: OpenCode binary '{self.binary}' was not found in PATH.[/bold red]\n"
                f"Please ensure OpenCode is installed (e.g. curl -fsSL https://opencode.ai/install | bash)."
            )
            raise SystemExit(1)
        if not self.quiet:
            print(
                f"[green]OpenCode: CLI found, using free model: [bold]{escape(self.model)}[/bold][/green]"
            )
        return True

    def _build_prompt(self, text: str) -> str:
        """Construct the prompt sent to OpenCode."""
        parts = []

        # Base / system instruction
        base_inst = self.fill_optional(self.prompt_sys_msg or BASE_INSTRUCTIONS)
        if base_inst:
            parts.append(base_inst)

        # Style section if defined
        style = self.style_section()
        if style:
            parts.append(style)

        # Functional preambles (structure, markers)
        preamble = self._functional_preamble(text)
        if preamble:
            parts.append(preamble.strip())

        # Glossary pins
        if self.glossary:
            block = self.glossary.prompt_block(text)
            if block:
                parts.append(block.strip())

        # Target user text
        if self.prompt_template and "{text}" in self.prompt_template:
            user_content = self.prompt_template.format(
                text=text,
                language=self.language,
                crlf="\n",
            )
        else:
            user_content = f"Text to translate:\n{text}"

        parts.append(user_content)
        return "\n\n".join(parts)

    def _run_turn(self, prompt: str, session_id: str | None = None) -> tuple[str, str | None]:
        """Execute one turn via `opencode run` and stream JSON events."""
        cmd = [self.binary, "run", "--pure", "--format", "json", "--model", self.model]
        if session_id:
            cmd.extend(["--session", session_id])

        try:
            process = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            stdout, stderr = process.communicate(input=prompt)
        except Exception as ex:
            raise RuntimeError(f"Failed to execute opencode CLI: {ex}") from ex

        if process.returncode != 0 and not stdout.strip():
            raise RuntimeError(
                f"opencode CLI failed with exit code {process.returncode}: {stderr.strip()}"
            )

        result_texts = []
        new_session_id = session_id
        for line in stdout.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue

            event_session = event.get("sessionID")
            if event_session:
                new_session_id = event_session

            evt_type = event.get("type")
            if evt_type == "text":
                part = event.get("part", {})
                result_texts.append(part.get("text", ""))
            elif evt_type == "step_finish":
                part = event.get("part", {})
                tokens = part.get("tokens", {})
                inp = tokens.get("input", 0)
                out = tokens.get("output", 0)
                cache_read = tokens.get("cache", {}).get("read", 0)
                self.usage.note(prompt=inp, completion=out, cached=cache_read, model=self.model)

        output_text = "".join(result_texts).strip()
        if not output_text and process.returncode != 0:
            raise RuntimeError(f"OpenCode translation returned empty: {stderr.strip()}")

        return output_text, new_session_id

    def translate(self, text: str, needprint: bool = True) -> str:
        """Translate a single unit of text."""
        prompt = self._build_prompt(text)
        translated, new_session_id = self._run_turn(prompt, self.session_id)
        if new_session_id:
            self.session_id = new_session_id
        return translated

    def translate_list(self, text_list: list[str]) -> list[str]:
        """Translate a batch of paragraphs in one turn using delimiter."""
        return self._do_batch_translate(
            text_list,
            self.prompt_template,
            self.prompt_sys_msg,
            "{text}",
            self.translate,
        )

    def _chat_completion(self, prompt: str, model: str | None = None) -> str:
        """Send a single prompt without persisting session (used for classification)."""
        saved_model = self.model
        if model:
            self.model = self._normalize_model(model)
        try:
            result, _ = self._run_turn(prompt, session_id=None)
            return result
        finally:
            self.model = saved_model
