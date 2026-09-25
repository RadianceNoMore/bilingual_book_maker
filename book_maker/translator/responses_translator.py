"""OpenAI Responses API route (`--api_format responses`).

The same translator as `ChatGPTAPI`, but speaking `POST /responses`
(`client.responses.create` / `client.responses.parse`) instead of
`POST /chat/completions`. Needed for endpoints that serve a model on the
Responses shape only — e.g. OpenCode Zen's
`muse-spark-1.3-contributor-free`, whose Zen row is
`https://opencode.ai/zen/v1/responses` while the chat-compatible free
models live under `.../zen/v1/chat/completions`.

With `--api_base https://opencode.ai/zen/v1` the SDK posts responses to
`.../v1/responses`, so one base URL serves both routes; the format picks
the path. Everything above the transport — batching, session context,
glossary, the structured ladder, plan classification — is inherited
unchanged from `ChatGPTAPI`.
"""

import json
from os import environ
from types import SimpleNamespace

from openai import AsyncOpenAI, BadRequestError
from pydantic import ValidationError
from rich import print

from ..redaction import redact
from ..session_context import SEED_MAX_TOKENS
from ..structured import RungRejected
from .base_translator import (
    AsyncTranslationUnsupported,
    TranslationContext,
    TranslationResult,
)
from .capabilities import (
    PROBE_EXPECTED,
    PROBE_FATAL_ERRORS,
    PROBE_KEY,
    PROBE_PROMPT,
    PROBE_TRANSIENT_ERRORS,
    REASONING_ALLOWANCE_TOKENS,
    RUNG_REFUSAL_ERRORS,
    STRUCTURED_PROBE_SCHEMA,
    ModelUnavailable,
    ProbeDeferred,
    StructuredOutputUnsupported,
    StructuredRefusal,
    classify_bad_request,
    describe_listing,
    fetch_endpoint_models,
    names_missing_model,
)
from .chatgptapi_translator import (
    REQUEST_LIMITS,
    SCHEMA_BATCH_DEGREES,
    ChatGPTAPI,
    batch_field_name,
    single_field_name,
    single_translation_model,
)

# Route probe: one tiny responses request per model, answer unread. No text
# format, temperature or token cap — each is refused by some model
# somewhere, and a refusal of the question is not an answer about the model.
ROUTE_PROBE_INPUT = [{"role": "user", "content": "Reply with the single word: PONG."}]

# Responses API length cap. `max_tokens` / `max_completion_tokens` are chat
# spellings the endpoint does not know; `max_output_tokens` sizes the reply
# including any reasoning before it, so the compact turn gets the same
# reasoning allowance the chat route adds under its spelling.
RESPONSE_CAP_FIELD = "max_output_tokens"


def _output_text(response):
    """The model's reply text out of a Responses API response."""
    text = getattr(response, "output_text", None)
    if isinstance(text, str) and text:
        return text
    # Fallback for gateways that return the shape without the convenience
    # property: walk message outputs for `output_text` content.
    parts = []
    for item in getattr(response, "output", None) or []:
        if getattr(item, "type", None) != "message":
            continue
        for block in getattr(item, "content", None) or []:
            if getattr(block, "type", None) == "output_text":
                parts.append(getattr(block, "text", "") or "")
    return "".join(parts)


def _adapt_usage(response, model):
    """A chat-shaped usage record out of a Responses API response.

    The meter (`ChatGPTAPI._note_usage`) reads `prompt_tokens`,
    `completion_tokens` and `prompt_tokens_details.cached_tokens`; the
    Responses shape calls them `input_tokens` / `output_tokens` with the
    cached count under `input_tokens_details.cached_tokens`.
    """
    usage = getattr(response, "usage", None)
    if usage is None:
        return SimpleNamespace(
            prompt_tokens=0,
            completion_tokens=0,
            prompt_tokens_details=SimpleNamespace(cached_tokens=0),
        )
    details = getattr(usage, "input_tokens_details", None)
    return SimpleNamespace(
        prompt_tokens=getattr(usage, "input_tokens", 0) or 0,
        completion_tokens=getattr(usage, "output_tokens", 0) or 0,
        prompt_tokens_details=SimpleNamespace(
            cached_tokens=getattr(details, "cached_tokens", 0) or 0
        ),
    )


def _chat_like_completion(response):
    """Wrap a Responses reply as what the chat path returns.

    Lets the inherited batch/session/meter code keep reading
    `completion.choices[0].message.content` and `completion.usage`.
    """
    text = _output_text(response) or ""
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content=text, parsed=None, refusal=None)
            )
        ],
        usage=_adapt_usage(response, None),
    )


def _responses_text_format(response_format):
    """`chat response_format` -> Responses `text` param, or None for plain."""
    if not response_format:
        return None
    kind = response_format.get("type")
    if kind == "json_object":
        return {"format": {"type": "json_object"}}
    if kind == "json_schema":
        envelope = response_format.get("json_schema", {})
        return {
            "format": {
                "type": "json_schema",
                "name": envelope.get("name", "response"),
                "strict": envelope.get("strict", True),
                "schema": envelope.get("schema", {}),
            }
        }
    return None


def grade_responses_probe_text(text):
    """Grade a probe reply: 'strict', 'shape', 'json', 'unsupported'.

    Same contract as `capabilities.grade_probe_response`, read off the
    Responses output text instead of a chat message: the prompt asks for
    plain text, so anything JSON-shaped that comes back is evidence of
    *some* structuring.
    """
    if not text:
        return "unsupported"
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return "unsupported"
    if not isinstance(parsed, dict) or set(parsed) != {PROBE_KEY}:
        return "json"
    if not isinstance(parsed[PROBE_KEY], str):
        return "json"
    return "strict" if parsed[PROBE_KEY] == PROBE_EXPECTED else "shape"


def probe_responses_structured_output(client, model, extra_body=None):
    """Ask `model` over `/responses` whether it applies a strict JSON Schema.

    Mirrors `capabilities.probe_structured_output`: accepting the request
    proves nothing, so the echoed value is graded, not the status code. No
    temperature and no token cap — the probe must test exactly one
    capability.
    """
    try:
        response = client.responses.create(
            model=model,
            input=[{"role": "user", "content": PROBE_PROMPT}],
            text={
                "format": {
                    "type": "json_schema",
                    "name": STRUCTURED_PROBE_SCHEMA["name"],
                    "strict": True,
                    "schema": STRUCTURED_PROBE_SCHEMA["schema"],
                }
            },
            extra_body=extra_body or None,
        )
    except PROBE_FATAL_ERRORS:
        raise
    except PROBE_TRANSIENT_ERRORS as e:
        raise ProbeDeferred(str(e)) from e
    except Exception as e:
        return f"request rejected: {redact(e)}"
    return grade_responses_probe_text(_output_text(response))


def probe_responses_model_route(client, model, extra_body=None):
    """Ask the `/responses` endpoint to serve `model` once, as cheaply as
    a request can be. Raises `ModelUnavailable` when the endpoint says
    there is no such model; any other failure is re-raised untouched."""
    try:
        client.responses.create(
            model=model,
            input=ROUTE_PROBE_INPUT,
            extra_body=extra_body or None,
        )
    except Exception as e:
        if names_missing_model(e, model):
            raise ModelUnavailable(
                f"This endpoint does not serve the model {model!r} "
                f"({redact(e)})."
            ) from e
        raise
    return None


def verify_responses_model_routes(client, model_list, extra_body=None):
    """Which of `model_list` the `/responses` endpoint serves, in order.

    Same contract as `capabilities.verify_model_routes`.
    """
    model_list = list(model_list)
    available, unavailable = [], []
    for model_name in model_list:
        try:
            probe_responses_model_route(client, model_name, extra_body=extra_body)
        except ModelUnavailable as e:
            print(f"[red]{redact(e)}[/red]")
            unavailable.append(model_name)
            continue
        except Exception as e:
            print(
                f"[yellow]ℹ could not confirm {model_name!r} ({redact(e)}); "
                f"that is no answer about the model, so the run keeps it"
                f"[/yellow]"
            )
        available.append(model_name)

    try:
        api_models = fetch_endpoint_models(client) if unavailable else None
    except Exception:
        api_models = None
    if unavailable and api_models:
        print(f"[yellow]This endpoint lists: {describe_listing(api_models)}[/yellow]")

    if not available:
        print(
            f"[red]Error: this endpoint served none of the models "
            f"{model_list}.[/red]"
        )
        print(
            "[yellow]Check the model id, the API base, and your key's model "
            "permissions.[/yellow]"
        )
        return {
            "success": False,
            "available_models": [],
            "unavailable_models": model_list,
            "api_models": api_models,
        }

    if unavailable:
        print(
            f"[yellow]Warning: {unavailable} not served by this endpoint, "
            f"using {available}[/yellow]"
        )
    return {
        "success": True,
        "available_models": available,
        "unavailable_models": unavailable,
        "api_models": api_models,
    }


# Route's own prompt variables, collected by the CLI's dry-run notice
# alongside the chat and gemini ones.
PROMPT_ENV_MAP = {
    "user": "BBM_RESPONSES_USER_MSG_TEMPLATE",
    "system": "BBM_RESPONSES_SYS_MSG",
}


class ResponsesTranslator(ChatGPTAPI):
    """`ChatGPTAPI` over the Responses API (`POST /responses`).

    Subclass, not a copy: prompts, batching, session history, glossary and
    the structured ladder are inherited. Only the transport — the half-dozen
    places that call `client.chat.completions` — is re-implemented here.
    """

    def __init__(self, *args, prompt_template=None, prompt_sys_msg=None, **kwargs):
        """Same arguments as `ChatGPTAPI`; the route's own prompt variables win.

        `BBM_RESPONSES_USER_MSG_TEMPLATE` / `BBM_RESPONSES_SYS_MSG` outrank
        the shared `BBM_CHATGPTAPI_*` ones the parent reads, so one process
        can carry a different prompt per route. Explicit arguments outrank
        both, as on the chat route.
        """
        super().__init__(
            *args,
            prompt_template=prompt_template,
            prompt_sys_msg=prompt_sys_msg,
            **kwargs,
        )
        if prompt_template is None:
            override = environ.get(PROMPT_ENV_MAP["user"])
            if override:
                self.prompt_template = override
        if prompt_sys_msg is None:
            override = environ.get(PROMPT_ENV_MAP["system"])
            if override:
                self.prompt_sys_msg = override

    def _responses_create(self, model, messages, response_format=None,
                          extra_body=None, sampling=None, cap=None):
        """One Responses request. Errors propagate untouched.

        `self._request` (temperature retry) and the rung ladder
        (`RungRejected`) each classify them; converting here would hide a
        temperature refusal from the retry that owns it.
        """
        kwargs = {"model": model, "input": messages}
        text_format = _responses_text_format(response_format)
        if text_format is not None:
            kwargs["text"] = text_format
        sampling = dict(sampling or {})
        if "temperature" in sampling:
            kwargs["temperature"] = sampling["temperature"]
        if cap:
            kwargs[cap[0]] = cap[1]
        if extra_body:
            kwargs["extra_body"] = extra_body
        return self.openai_client.responses.create(**kwargs)

    def _completion_text(self, model, content, **kwargs):
        """One single-turn request, with shape refusals marked as such."""
        kwargs.setdefault("extra_body", self.extra_body or None)
        response_format = kwargs.pop("response_format", None)
        sampling = {
            k: v for k, v in kwargs.items()
            if k in ("temperature",)
        }
        # The chat path sends sampling through `_request`, which retries
        # once without temperature; here the caller already did that, so
        # whatever arrives is sent as-is.
        try:
            response = self._responses_create(
                model,
                [{"role": "user", "content": content}],
                response_format=response_format,
                extra_body=kwargs.get("extra_body"),
                sampling=sampling,
            )
        except RUNG_REFUSAL_ERRORS as e:
            self.warn_if_extras_refused(e)
            raise RungRejected(e) from e
        self._note_usage(_adapt_usage(response, model), model)
        return _output_text(response) or ""

    def _note_usage(self, completion_or_usage, model=None):
        """The meter, accepting either a chat completion or a Responses reply.

        The inherited `_note_usage(completion)` reads
        `completion.usage.prompt_tokens`; probe/rung paths here hand over
        the adapted usage record directly, so both shapes are accepted.
        """
        usage = getattr(completion_or_usage, "usage", completion_or_usage)
        try:
            if usage is None:
                return
            details = getattr(usage, "prompt_tokens_details", None)
            if details is None:
                details = getattr(usage, "input_tokens_details", None)
            prompt = getattr(usage, "prompt_tokens", None)
            if prompt is None:
                prompt = getattr(usage, "input_tokens", 0)
            completion = getattr(usage, "completion_tokens", None)
            if completion is None:
                completion = getattr(usage, "output_tokens", 0)
            self.usage.note(
                prompt or 0,
                completion or 0,
                getattr(details, "cached_tokens", 0) or 0,
                model=model or self.model,
            )
        except Exception:
            return

    def _probe(self, model):
        return probe_responses_structured_output(
            self.openai_client, model, extra_body=self.extra_body or None
        )

    def _ensure_models_routable(self):
        state = self._route_state
        if state is None:
            if not self._model_names:
                return
            state = self._route_state = {
                "pending": list(self._model_names),
                "failure": None,
            }
        with self._api_lock:
            if state["failure"] is not None:
                raise state["failure"]
            pending = state["pending"]
            if pending is None:
                return
            state["pending"] = None
            result = verify_responses_model_routes(
                self.openai_client, pending, extra_body=self.extra_body or None
            )
            if not result["success"]:
                listed = result["api_models"]
                state["failure"] = ModelUnavailable(
                    f"This endpoint served none of the models {pending}."
                    + (f" It lists {describe_listing(listed)}." if listed else "")
                    + " Check the model id, the API base, and your key's "
                    "model permissions."
                )
                raise state["failure"]
            available = result["available_models"]
            if available == pending:
                return
            from itertools import cycle

            self._model_names = available
            self.model_list = cycle(available)
            if self.model not in available:
                self.model = available[0]

    def _classify_turn(self, messages, model):
        """One turn of a classifier session over `/responses`."""
        response = self._request(
            lambda sampling: self._responses_create(
                model,
                messages,
                extra_body=self.extra_body if self.extra_body else None,
                sampling=sampling,
            ),
            model=model,
        )
        if hasattr(response, "output_text"):
            self._note_usage(response, model)
            return _output_text(response) or ""
        # Chat-like wrapper (kept for symmetry with `_completion_text`).
        self._note_usage(response, model)
        return response.choices[0].message.content or ""

    def create_chat_completion(self, text):
        """Plain (delimiter-mode) completion over `/responses`."""
        messages = self.create_messages(text, self.create_context_messages())
        return self._request(
            lambda sampling: _chat_like_completion(
                self._responses_create(
                    self.model,
                    messages,
                    extra_body=self.extra_body if self.extra_body else None,
                    sampling=sampling,
                )
            )
        )

    def _structured_single_translation(self, text):
        """Translate one paragraph via Responses structured outputs."""
        messages = self.create_messages(text, self.create_context_messages())
        pydantic_model = single_translation_model(
            self.language, field_language=self.language_field_tag
        )
        field = single_field_name(self.field_language)
        try:
            parsed = self._request(
                lambda sampling: self.openai_client.responses.parse(
                    model=self.model,
                    input=messages,
                    text_format=pydantic_model,
                    extra_body=self.extra_body if self.extra_body else None,
                    **sampling,
                )
            )
        except BadRequestError as e:
            if classify_bad_request(e) != "schema":
                raise
            raise StructuredOutputUnsupported(str(e)) from e
        except (ValidationError, json.JSONDecodeError) as e:
            raise StructuredOutputUnsupported(str(e)) from e
        self._note_usage(parsed, self.model)
        refusal = getattr(parsed, "refusal", None)
        if refusal:
            raise StructuredRefusal(refusal)
        obj = getattr(parsed, "output_parsed", None)
        if obj is None:
            raise StructuredOutputUnsupported("no parsed content in response")
        self._note_structured_success()
        return getattr(obj, field)

    def _execute_structured_batch_translate(self, text_list, plist_len):
        """One structured batch request over `/responses.parse`."""
        from .base_translator import BatchMismatch
        from .chatgptapi_translator import batch_translation_model as _btm

        self.rotate_key()
        self.rotate_model()
        degree = self._structured_enabled()
        if not degree:
            raise StructuredOutputUnsupported(
                f"'{self.model}' has no structured-output support"
            )
        if degree not in SCHEMA_BATCH_DEGREES:
            cap = self.substrict_batch_cap
            if plist_len > cap:
                raise BatchMismatch(
                    f"batch of {plist_len} exceeds the json-degree cap of "
                    f"{cap} units for '{self.model}'"
                )
        messages = self._create_structured_batch_messages(text_list, degree=degree)
        if degree not in SCHEMA_BATCH_DEGREES:
            return self._execute_json_object_batch(messages)
        try:
            parsed = self._request(
                lambda sampling: self.openai_client.responses.parse(
                    model=self.model,
                    input=messages,
                    text_format=_btm(
                        self.language,
                        plist_len,
                        self.source_language,
                        self.language_field_tag,
                    ),
                    extra_body=self.extra_body if self.extra_body else None,
                    **sampling,
                )
            )
        except BadRequestError as e:
            if classify_bad_request(e) != "schema":
                raise
            raise StructuredOutputUnsupported(str(e)) from e
        except (ValidationError, json.JSONDecodeError) as e:
            raise StructuredOutputUnsupported(str(e)) from e
        self._note_usage(parsed, self.model)
        if getattr(parsed, "refusal", None):
            raise StructuredRefusal(parsed.refusal)
        obj = getattr(parsed, "output_parsed", None)
        if obj is None:
            raise StructuredOutputUnsupported("no parsed content in response")
        items = getattr(obj, batch_field_name(self.field_language))
        raw_reply = _output_text(parsed)
        if not raw_reply:
            raw_reply = json.dumps(
                {batch_field_name(self.field_language): [str(i) for i in items]},
                ensure_ascii=False,
            )
        return items, messages[-1]["content"], raw_reply

    def _execute_json_object_batch(self, messages):
        """One id-echo batch at the json_object degree over `/responses`."""
        try:
            response = self._request(
                lambda sampling: self._responses_create(
                    self.model,
                    messages,
                    response_format={"type": "json_object"},
                    extra_body=self.extra_body if self.extra_body else None,
                    sampling=sampling,
                )
            )
        except BadRequestError as e:
            if classify_bad_request(e) != "schema":
                raise
            raise StructuredOutputUnsupported(str(e)) from e
        if hasattr(response, "output_text"):
            self._note_usage(response, self.model)
            raw_reply = _output_text(response) or ""
        else:
            self._note_usage(response, self.model)
            raw_reply = response.choices[0].message.content or ""
        return (
            self._parse_json_object_batch(raw_reply),
            messages[-1]["content"],
            raw_reply,
        )

    def _compact_request(self, messages):
        """The compact turn over `/responses`, capped with `max_output_tokens`.

        The chat spellings (`max_tokens`, `max_completion_tokens`) are not
        known here; a single attempt carries `max_output_tokens` sized for
        the seed plus the reasoning allowance, and a refusal of the cap
        falls back to no cap (the client truncates the seed regardless).
        """
        cap = SEED_MAX_TOKENS + REASONING_ALLOWANCE_TOKENS

        def create(sampling):
            return _chat_like_completion(
                self._responses_create(
                    self.model,
                    messages,
                    extra_body=self.extra_body if self.extra_body else None,
                    sampling=sampling,
                    cap=(RESPONSE_CAP_FIELD, cap),
                )
            )

        try:
            return self._request(create)
        except BadRequestError as e:
            text = str(e).lower()
            if RESPONSE_CAP_FIELD not in text and "max_output_tokens" not in text:
                raise
            print(
                f"[yellow]ℹ '{self.model}' rejected {RESPONSE_CAP_FIELD} on the "
                f"handoff turn; asking without a length cap (the seed is "
                f"still truncated on this side)[/yellow]"
            )
            return self._request(
                lambda sampling: _chat_like_completion(
                    self._responses_create(
                        self.model,
                        messages,
                        extra_body=self.extra_body if self.extra_body else None,
                        sampling=sampling,
                    )
                )
            )

    def _create_async_client(self, key):
        return AsyncOpenAI(
            api_key=key,
            base_url=self.api_base,
            default_headers=self.extra_headers or None,
            **REQUEST_LIMITS,
        )

    async def translate_async(
        self, text: str, *, context: TranslationContext | None = None
    ) -> TranslationResult:
        """Window-mode async translation over `/responses`."""
        from openai import BadRequestError as _BadRequest

        from .capabilities import classify_bad_request as _classify

        if self.session is not None:
            raise AsyncTranslationUnsupported(
                "session context is not supported on the async path; "
                "use --use_context (window mode) there"
            )
        with self._api_lock:
            key = next(self.keys)
            if self.model_list:
                model = (
                    next(self.model_list)
                    if hasattr(self.model_list, "__next__")
                    else self.model_list[0]
                )
            else:
                model = self.model
        current_context = context or TranslationContext()
        messages = self.create_messages(
            text, self.create_context_messages(current_context)
        )
        client = self._get_async_client(key)

        async def create(sampling):
            kwargs = {"model": model, "input": messages}
            if sampling.get("temperature") is not None:
                kwargs["temperature"] = sampling["temperature"]
            if self.extra_body:
                kwargs["extra_body"] = self.extra_body
            return await client.responses.create(**kwargs)

        try:
            response = await create(self._sampling_kwargs(model))
        except _BadRequest as e:
            if _classify(e) != "temperature":
                raise
            self._note_temperature_rejected(model)
            response = await create({})
        self._note_usage(response, model)
        translated = _output_text(response) or ""
        if self.context_flag:
            current_context = current_context.append(
                text, translated, self.context_paragraph_limit
            )
        return TranslationResult(translated, current_context)
