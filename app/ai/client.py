from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any

import httpx

logger = logging.getLogger(__name__)


class OpenAIClientError(RuntimeError):
    """Base error for Responses API failures safe to expose to service code."""


class OpenAIConfigurationError(OpenAIClientError):
    pass


class OpenAIAPIError(OpenAIClientError):
    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        request_id: str | None = None,
        response_id: str | None = None,
        response_status: str | None = None,
        incomplete_reason: str | None = None,
        model: str | None = None,
        output_types: tuple[str, ...] = (),
        content_types: tuple[str, ...] = (),
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.request_id = request_id
        self.response_id = response_id
        self.response_status = response_status
        self.incomplete_reason = incomplete_reason
        self.model = model
        self.output_types = output_types
        self.content_types = content_types

    def diagnostic_context(self) -> dict[str, Any]:
        """Non-prompt diagnostics safe to put into structured logs/error context."""

        return {
            "openai_status_code": self.status_code,
            "openai_request_id": self.request_id,
            "openai_response_id": self.response_id,
            "openai_response_status": self.response_status,
            "openai_incomplete_reason": self.incomplete_reason,
            "openai_model": self.model,
            "openai_output_types": list(self.output_types),
            "openai_content_types": list(self.content_types),
        }


class OpenAIIncompleteResponseError(OpenAIAPIError):
    """The Responses API returned status=incomplete instead of a final answer."""


class OpenAIContentBlockedError(OpenAIAPIError):
    """The API explicitly blocked content without returning a textual refusal."""


@dataclass(frozen=True, slots=True)
class OpenAIUsageData:
    input_tokens: int
    cached_input_tokens: int
    output_tokens: int
    reasoning_tokens: int


@dataclass(frozen=True, slots=True)
class OpenAIResponseResult:
    response_id: str
    request_id: str | None
    text: str
    usage: OpenAIUsageData
    status: str


class OpenAIResponsesClient:
    def __init__(
        self,
        *,
        api_key: str | None,
        base_url: str = "https://api.openai.com/v1",
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self.api_key = api_key.strip() if api_key else None
        self.base_url = base_url.rstrip("/")
        self._http_client = http_client
        self._owns_http_client = http_client is None

    async def close(self) -> None:
        if self._http_client is not None and self._owns_http_client:
            await self._http_client.aclose()
            self._http_client = None

    async def create_response(
        self,
        *,
        model: str,
        input_messages: list[dict[str, str]],
        instructions: str,
        max_output_tokens: int,
        timeout_seconds: float,
        reasoning_effort: str | None = None,
        temperature: float | None = None,
    ) -> OpenAIResponseResult:
        if not self.api_key:
            raise OpenAIConfigurationError("OPENAI_API_KEY is not configured")

        payload: dict[str, Any] = {
            "model": model,
            "instructions": instructions,
            "input": input_messages,
            "max_output_tokens": max_output_tokens,
            # The application persists its own conversation state and summaries.
            "store": False,
        }
        if reasoning_effort:
            payload["reasoning"] = {"effort": reasoning_effort}
        if temperature is not None:
            payload["temperature"] = temperature

        client = self._get_http_client()
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        last_error: Exception | None = None
        # Retry only failures that are known not to represent a completed model response:
        # connection establishment failures and explicit 429 rejection.
        for attempt in range(2):
            try:
                response = await client.post(
                    f"{self.base_url}/responses",
                    headers=headers,
                    json=payload,
                    timeout=timeout_seconds,
                )
            except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
                last_error = exc
                if attempt == 0:
                    await asyncio.sleep(0.5)
                    continue
                raise OpenAIAPIError("Unable to connect to OpenAI", model=model) from exc
            except httpx.TimeoutException as exc:
                # A read/write timeout can happen after the server started processing the request.
                # Do not automatically retry it and risk double-billing.
                raise OpenAIAPIError("OpenAI request timed out", model=model) from exc
            except httpx.HTTPError as exc:
                raise OpenAIAPIError("OpenAI transport error", model=model) from exc

            request_id = response.headers.get("x-request-id")
            if response.status_code == 429 and attempt == 0:
                await asyncio.sleep(self._retry_after_seconds(response))
                continue
            if response.status_code >= 400:
                raise OpenAIAPIError(
                    self._error_message(response),
                    status_code=response.status_code,
                    request_id=request_id,
                    model=model,
                )

            try:
                data = response.json()
            except ValueError as exc:
                raise OpenAIAPIError(
                    "OpenAI returned invalid JSON",
                    status_code=response.status_code,
                    request_id=request_id,
                    model=model,
                ) from exc

            response_id = str(data.get("id") or "")
            response_status = str(data.get("status") or "completed")
            incomplete_reason = self._incomplete_reason(data)
            output_types, content_types = self._response_shape(data)
            diagnostics = {
                "openai_status_code": response.status_code,
                "openai_request_id": request_id,
                "openai_response_id": response_id or None,
                "openai_response_status": response_status,
                "openai_incomplete_reason": incomplete_reason,
                "openai_model": str(data.get("model") or model),
                "openai_output_types": list(output_types),
                "openai_content_types": list(content_types),
            }

            # `incomplete` is a valid HTTP 200 API response, but not a complete assistant answer.
            # Do not treat it as a successful chat turn or charge the user-facing credit.
            if response_status == "incomplete":
                logger.warning("OpenAI response incomplete", extra=diagnostics)
                reason_suffix = f": {incomplete_reason}" if incomplete_reason else ""
                raise OpenAIIncompleteResponseError(
                    f"OpenAI response incomplete{reason_suffix}",
                    status_code=response.status_code,
                    request_id=request_id,
                    response_id=response_id or None,
                    response_status=response_status,
                    incomplete_reason=incomplete_reason,
                    model=str(data.get("model") or model),
                    output_types=output_types,
                    content_types=content_types,
                )

            if response_status in {"failed", "cancelled"}:
                message = self._response_failure_message(data, response_status)
                logger.error("OpenAI response failed", extra=diagnostics)
                raise OpenAIAPIError(
                    message,
                    status_code=response.status_code,
                    request_id=request_id,
                    response_id=response_id or None,
                    response_status=response_status,
                    incomplete_reason=incomplete_reason,
                    model=str(data.get("model") or model),
                    output_types=output_types,
                    content_types=content_types,
                )

            text = self._extract_output_text(data)
            if not text:
                if self._moderation_flagged(data):
                    logger.info("OpenAI response blocked by moderation", extra=diagnostics)
                    raise OpenAIContentBlockedError(
                        "OpenAI blocked the response content",
                        status_code=response.status_code,
                        request_id=request_id,
                        response_id=response_id or None,
                        response_status=response_status,
                        incomplete_reason=incomplete_reason,
                        model=str(data.get("model") or model),
                        output_types=output_types,
                        content_types=content_types,
                    )

                # This is the genuinely abnormal case. Keep enough shape metadata in both the
                # exception and the JSON log to diagnose it without logging user prompts/content.
                logger.error("OpenAI returned no usable text output", extra=diagnostics)
                shape = (
                    f"status={response_status}, model={data.get('model') or model}, "
                    f"output_types={','.join(output_types) or 'none'}, "
                    f"content_types={','.join(content_types) or 'none'}"
                )
                raise OpenAIAPIError(
                    f"OpenAI returned no text output ({shape})",
                    status_code=response.status_code,
                    request_id=request_id,
                    response_id=response_id or None,
                    response_status=response_status,
                    incomplete_reason=incomplete_reason,
                    model=str(data.get("model") or model),
                    output_types=output_types,
                    content_types=content_types,
                )

            usage = self._extract_usage(data)
            return OpenAIResponseResult(
                response_id=response_id,
                request_id=request_id,
                text=text,
                usage=usage,
                status=response_status,
            )

        raise OpenAIAPIError("OpenAI request failed", model=model) from last_error

    def _get_http_client(self) -> httpx.AsyncClient:
        if self._http_client is None:
            self._http_client = httpx.AsyncClient(
                limits=httpx.Limits(max_connections=50, max_keepalive_connections=20),
            )
        return self._http_client

    @staticmethod
    def _extract_output_text(data: dict[str, Any]) -> str:
        parts: list[str] = []

        # Some Responses API representations expose a convenience `output_text` field.
        top_level_text = data.get("output_text")
        if isinstance(top_level_text, str) and top_level_text.strip():
            parts.append(top_level_text.strip())

        for item in data.get("output") or []:
            if not isinstance(item, dict):
                continue

            # Normal Responses API shape: output item -> message -> content parts.
            if item.get("type") == "message":
                for content in item.get("content") or []:
                    if not isinstance(content, dict):
                        continue
                    if content.get("type") == "output_text" and isinstance(content.get("text"), str):
                        parts.append(content["text"])
                    elif content.get("type") == "refusal" and isinstance(content.get("refusal"), str):
                        # Refusals are valid model responses, not transport/API failures.
                        parts.append(content["refusal"])

            # Be defensive about alternate/SDK-normalized shapes.
            elif item.get("type") == "output_text" and isinstance(item.get("text"), str):
                parts.append(item["text"])
            elif item.get("type") == "refusal" and isinstance(item.get("refusal"), str):
                parts.append(item["refusal"])

        # Avoid duplicating the same text when both the convenience field and nested output exist.
        unique_parts: list[str] = []
        seen: set[str] = set()
        for part in parts:
            cleaned = part.strip()
            if cleaned and cleaned not in seen:
                unique_parts.append(cleaned)
                seen.add(cleaned)
        return "\n".join(unique_parts).strip()

    @staticmethod
    def _extract_usage(data: dict[str, Any]) -> OpenAIUsageData:
        usage = data.get("usage")
        if not isinstance(usage, dict):
            raise OpenAIAPIError("OpenAI returned no usage accounting")
        input_details = usage.get("input_tokens_details") or {}
        output_details = usage.get("output_tokens_details") or {}
        return OpenAIUsageData(
            input_tokens=int(usage.get("input_tokens") or 0),
            cached_input_tokens=int(input_details.get("cached_tokens") or 0),
            output_tokens=int(usage.get("output_tokens") or 0),
            reasoning_tokens=int(output_details.get("reasoning_tokens") or 0),
        )

    @staticmethod
    def _incomplete_reason(data: dict[str, Any]) -> str | None:
        details = data.get("incomplete_details")
        if isinstance(details, dict):
            reason = details.get("reason")
            if isinstance(reason, str) and reason:
                return reason
        return None

    @staticmethod
    def _response_shape(data: dict[str, Any]) -> tuple[tuple[str, ...], tuple[str, ...]]:
        output_types: list[str] = []
        content_types: list[str] = []
        for item in data.get("output") or []:
            if not isinstance(item, dict):
                continue
            item_type = item.get("type")
            if isinstance(item_type, str):
                output_types.append(item_type)
            for content in item.get("content") or []:
                if not isinstance(content, dict):
                    continue
                content_type = content.get("type")
                if isinstance(content_type, str):
                    content_types.append(content_type)
        return tuple(dict.fromkeys(output_types)), tuple(dict.fromkeys(content_types))

    @staticmethod
    def _moderation_flagged(data: dict[str, Any]) -> bool:
        moderation = data.get("moderation")
        if not isinstance(moderation, dict):
            return False
        for side in ("input", "output"):
            result = moderation.get(side)
            if isinstance(result, dict) and result.get("flagged") is True:
                return True
        return False

    @staticmethod
    def _response_failure_message(data: dict[str, Any], status: str) -> str:
        error = data.get("error")
        if isinstance(error, dict):
            message = error.get("message")
            if isinstance(message, str) and message:
                return f"OpenAI response {status}: {message[:1600]}"
        return f"OpenAI response {status}"

    @staticmethod
    def _error_message(response: httpx.Response) -> str:
        try:
            data = response.json()
            error = data.get("error") if isinstance(data, dict) else None
            if isinstance(error, dict) and isinstance(error.get("message"), str):
                return error["message"][:2000]
        except ValueError:
            pass
        return f"OpenAI API returned HTTP {response.status_code}"

    @staticmethod
    def _retry_after_seconds(response: httpx.Response) -> float:
        raw = response.headers.get("retry-after")
        if raw:
            try:
                return max(0.25, min(float(raw), 5.0))
            except ValueError:
                pass
        return 1.0
