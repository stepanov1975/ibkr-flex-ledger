"""IBKR Flex Web Service adapter implementation for report retrieval."""

from __future__ import annotations

import random
import socket
import time
from dataclasses import dataclass
from typing import Callable, Final
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError
import xml.etree.ElementTree as element_tree

from app.domain import domain_build_stage_event

from .interfaces import AdapterFetchResult, FlexAdapterPort


@dataclass(frozen=True)
class _AdapterRetryStrategy:
    """Immutable retry strategy config and calculation helpers.

    Attributes:
        initial_wait_seconds: Delay floor before each poll attempt.
        retry_attempts: Number of polling attempts.
        backoff_base_seconds: Base delay for exponential backoff.
        max_backoff_seconds: Exponential delay cap before jitter.
        jitter_min_multiplier: Minimum jitter multiplier.
        jitter_max_multiplier: Maximum jitter multiplier.
        random_unit_interval_provider: Provider returning value in [0.0, 1.0].
    """

    initial_wait_seconds: float
    retry_attempts: int
    backoff_base_seconds: float
    max_backoff_seconds: float
    jitter_min_multiplier: float
    jitter_max_multiplier: float
    random_unit_interval_provider: Callable[[], float]

    def strategy_calculate_retry_wait_seconds(self, retry_index: int) -> float:
        """Calculate exponential retry wait with cap and jitter.

        Args:
            retry_index: Zero-based retry attempt index.

        Returns:
            float: Computed wait seconds for the poll attempt.

        Raises:
            ValueError: Raised when retry index is negative.
            RuntimeError: Raised when jitter provider returns out-of-range value.
        """

        if retry_index < 0:
            raise ValueError("retry_index must be >= 0")

        backoff_seconds = self.backoff_base_seconds * (2**retry_index)
        capped_backoff_seconds = min(backoff_seconds, self.max_backoff_seconds)
        jitter_multiplier = self.strategy_calculate_jitter_multiplier()
        jittered_backoff_seconds = capped_backoff_seconds * jitter_multiplier
        return max(float(self.initial_wait_seconds), float(jittered_backoff_seconds))

    def strategy_calculate_jitter_multiplier(self) -> float:
        """Return jitter multiplier using configured min/max bounds.

        Returns:
            float: Jitter multiplier value.

        Raises:
            RuntimeError: Raised when jitter source returns value outside [0.0, 1.0].
        """

        random_ratio = float(self.random_unit_interval_provider())
        if random_ratio < 0.0 or random_ratio > 1.0:
            raise RuntimeError("random_unit_interval_provider must return a value in [0.0, 1.0]")

        jitter_span = self.jitter_max_multiplier - self.jitter_min_multiplier
        return self.jitter_min_multiplier + (random_ratio * jitter_span)


class FlexWebServiceAdapter(FlexAdapterPort):
    """Adapter implementation for IBKR Flex `SendRequest` and `GetStatement` flow."""

    _USER_AGENT: Final[str] = "ibkr-flex-ledger/1.0 (Python/urllib.request)"
    _KNOWN_FLEX_ERROR_MESSAGES: Final[dict[str, str]] = {
        "1003": "Statement is not available.",
        "1004": "Statement is incomplete at this time. Please try again shortly.",
        "1005": "Settlement data is not ready at this time. Please try again shortly.",
        "1006": "FIFO P/L data is not ready at this time. Please try again shortly.",
        "1007": "MTM P/L data is not ready at this time. Please try again shortly.",
        "1008": "MTM and FIFO P/L data is not ready at this time. Please try again shortly.",
        "1009": "The server is under heavy load. Statement could not be generated at this time. Please try again shortly.",
        "1010": "Legacy Flex Queries are no longer supported. Please convert over to Activity Flex.",
        "1011": "Service account is inactive.",
        "1012": "Token has expired.",
        "1013": "IP restriction.",
        "1014": "Query is invalid.",
        "1015": "Token is invalid.",
        "1016": "Account in invalid.",
        "1017": "Reference code is invalid.",
        "1018": "Too many requests have been made from this token. Please try again shortly.",
        "1019": "Statement generation in progress. Please try again shortly.",
        "1020": "Invalid request or unable to validate request.",
        "1021": "Statement could not be retrieved at this time. Please try again shortly.",
    }
    _SERVER_BUSY_ERROR_CODE: Final[str] = "1009"
    _NOT_READY_ERROR_CODE: Final[str] = "1019"
    _THROTTLED_ERROR_CODE: Final[str] = "1018"
    _RETRYABLE_POLL_ERROR_CODES: Final[frozenset[str]] = frozenset(
        {_SERVER_BUSY_ERROR_CODE, _NOT_READY_ERROR_CODE, _THROTTLED_ERROR_CODE}
    )

    def __init__(
        self,
        token: str,
        base_url: str = "https://ndcdyn.interactivebrokers.com/AccountManagement/FlexWebService",
        api_version: str = "3",
        initial_wait_seconds: float = 5.0,
        retry_attempts: int = 7,
        retry_backoff_base_seconds: float = 10.0,
        retry_max_backoff_seconds: float = 60.0,
        jitter_min_multiplier: float = 0.5,
        jitter_max_multiplier: float = 1.5,
        random_unit_interval_provider: Callable[[], float] | None = None,
        request_timeout_seconds: float = 30.0,
    ):
        """Initialize Flex Web Service adapter.

        Args:
            token: IBKR Flex token.
            base_url: Base endpoint URL for Flex Web Service.
            api_version: Flex API version value.
            initial_wait_seconds: Delay before first poll attempt.
            retry_attempts: Number of download polling attempts.
            retry_backoff_base_seconds: Base retry delay used by exponential backoff.
            retry_max_backoff_seconds: Maximum retry delay cap before applying jitter.
            jitter_min_multiplier: Minimum jitter multiplier for computed retry delay.
            jitter_max_multiplier: Maximum jitter multiplier for computed retry delay.
            random_unit_interval_provider: Optional provider returning random values in [0.0, 1.0].
            request_timeout_seconds: HTTP request timeout in seconds.

        Returns:
            None: Initializer does not return a value.

        Raises:
            ValueError: Raised when required config values are invalid.
        """

        normalized_token = token.strip()
        normalized_base_url = base_url.strip()
        normalized_api_version = api_version.strip()

        if not normalized_token:
            raise ValueError("token must not be blank")
        if not normalized_base_url:
            raise ValueError("base_url must not be blank")
        if not normalized_api_version:
            raise ValueError("api_version must not be blank")
        if retry_attempts < 1:
            raise ValueError("retry_attempts must be >= 1")
        if initial_wait_seconds < 0:
            raise ValueError("initial_wait_seconds must be >= 0")
        if retry_backoff_base_seconds < 0:
            raise ValueError("retry_backoff_base_seconds must be >= 0")
        if retry_max_backoff_seconds <= 0:
            raise ValueError("retry_max_backoff_seconds must be > 0")
        if jitter_min_multiplier <= 0:
            raise ValueError("jitter_min_multiplier must be > 0")
        if jitter_max_multiplier <= 0:
            raise ValueError("jitter_max_multiplier must be > 0")
        if jitter_max_multiplier < jitter_min_multiplier:
            raise ValueError("jitter_max_multiplier must be >= jitter_min_multiplier")
        if request_timeout_seconds <= 0:
            raise ValueError("request_timeout_seconds must be > 0")

        self._token = normalized_token
        self._base_url = normalized_base_url.rstrip("/")
        self._api_version = normalized_api_version
        self._retry_strategy = _AdapterRetryStrategy(
            initial_wait_seconds=initial_wait_seconds,
            retry_attempts=retry_attempts,
            backoff_base_seconds=retry_backoff_base_seconds,
            max_backoff_seconds=retry_max_backoff_seconds,
            jitter_min_multiplier=jitter_min_multiplier,
            jitter_max_multiplier=jitter_max_multiplier,
            random_unit_interval_provider=random_unit_interval_provider or random.random,
        )
        self._request_timeout_seconds = request_timeout_seconds

    def adapter_source_name(self) -> str:
        """Return stable adapter source label.

        Returns:
            str: Source identifier.

        Raises:
            RuntimeError: This implementation does not raise runtime errors.
        """

        return "ibkr_flex_web_service"

    def adapter_fetch_report(self, query_id: str) -> AdapterFetchResult:
        """Fetch report payload bytes through request then poll/download flow.

        Args:
            query_id: Upstream Flex query id.

        Returns:
            AdapterFetchResult: Upstream reference code and immutable payload bytes.

        Raises:
            ConnectionError: Raised for network/HTTP transport failures.
            TimeoutError: Raised when report generation did not become ready in time.
            ValueError: Raised when upstream rejects request or response contract is invalid.
            RuntimeError: Raised on unexpected parse/runtime failures.
        """

        normalized_query_id = query_id.strip()
        if not normalized_query_id:
            raise ValueError("query_id must not be blank")

        stage_timeline: list[dict[str, object]] = []

        request_url = f"{self._base_url}/SendRequest"
        request_parameters = {"t": self._token, "q": normalized_query_id, "v": self._api_version}
        self._adapter_record_stage_event(stage_timeline=stage_timeline, stage="request", status="started")
        request_payload = self._adapter_http_get(url=request_url, query_parameters=request_parameters)
        response_root = self._adapter_parse_xml(payload=request_payload, context_label="send_request")

        status_value = (response_root.findtext("Status") or "").strip()
        if status_value.lower() != "success":
            error_code, error_message = self._adapter_extract_response_error(
                response_root,
                fallback_message="request rejected by upstream",
            )
            raise ValueError(f"Flex request rejected: code={error_code}, message={error_message}")

        reference_code = (response_root.findtext("ReferenceCode") or "").strip()
        statement_url = (response_root.findtext("Url") or "").strip()
        if not reference_code:
            raise ValueError("Flex request response missing ReferenceCode")
        if not statement_url:
            statement_url = f"{self._base_url}/GetStatement"
        self._adapter_record_stage_event(
            stage_timeline=stage_timeline,
            stage="request",
            status="completed",
            details={"run_reference": reference_code},
        )

        self._adapter_record_stage_event(stage_timeline=stage_timeline, stage="poll", status="started")
        report_payload = self._adapter_poll_statement(
            statement_url=statement_url,
            reference_code=reference_code,
            stage_timeline=stage_timeline,
        )
        self._adapter_record_stage_event(stage_timeline=stage_timeline, stage="poll", status="completed")
        return AdapterFetchResult(
            run_reference=reference_code,
            payload_bytes=bytes(report_payload),
            stage_timeline=stage_timeline,
        )

    def _adapter_poll_statement(
        self,
        statement_url: str,
        reference_code: str,
        stage_timeline: list[dict[str, object]],
    ) -> bytes:
        """Poll statement endpoint until report payload is available.

        Args:
            statement_url: Statement retrieval endpoint.
            reference_code: Upstream request reference code.

        Returns:
            bytes: Downloaded report payload.

        Raises:
            ConnectionError: Raised for transport failures.
            TimeoutError: Raised when report was not ready after all retries.
            RuntimeError: Raised for unexpected upstream response states.
        """

        query_parameters = {"q": reference_code, "t": self._token, "v": self._api_version}
        pending_retry_delay_seconds = 0

        for retry_index in range(self._retry_strategy.retry_attempts):
            wait_seconds = max(
                self._adapter_calculate_retry_wait_seconds(retry_index=retry_index),
                pending_retry_delay_seconds,
            )
            pending_retry_delay_seconds = 0
            if wait_seconds > 0:
                time.sleep(wait_seconds)

            poll_payload = self._adapter_http_get(url=statement_url, query_parameters=query_parameters)
            poll_root = self._adapter_try_parse_xml(payload=poll_payload)
            if poll_root is None:
                if not poll_payload:
                    continue
                self._adapter_record_stage_event(
                    stage_timeline=stage_timeline,
                    stage="download",
                    status="completed",
                        details={"poll_attempt": retry_index + 1, "payload_format": "non_xml"},
                )
                return poll_payload

            if self._adapter_poll_payload_is_statement_xml(poll_root):
                self._adapter_record_stage_event(
                    stage_timeline=stage_timeline,
                    stage="download",
                    status="completed",
                    details={"poll_attempt": retry_index + 1},
                )
                return poll_payload

            error_code, error_message = self._adapter_extract_response_error(poll_root)
            if error_code in self._RETRYABLE_POLL_ERROR_CODES:
                pending_retry_delay_seconds = self._adapter_retry_delay_seconds_for_error(error_code)
                self._adapter_record_stage_event(
                    stage_timeline=stage_timeline,
                    stage="download",
                    status="retrying",
                    details={
                        "poll_attempt": retry_index + 1,
                        "error_code": error_code,
                        "error_message": error_message,
                        "retry_after_seconds": pending_retry_delay_seconds,
                    },
                )
                continue

            raise RuntimeError(f"Flex statement polling failed: code={error_code or 'UNKNOWN'}, message={error_message}")

        raise TimeoutError("Flex statement polling timed out after all retries")

    def _adapter_http_get(self, url: str, query_parameters: dict[str, str]) -> bytes:
        """Execute one HTTP GET and return response payload bytes.

        Args:
            url: Endpoint URL.
            query_parameters: Query string parameters.

        Returns:
            bytes: HTTP response payload.

        Raises:
            ConnectionError: Raised for network and non-success HTTP status.
            RuntimeError: Raised for unexpected client errors.
        """

        full_url = f"{url}?{urlencode(query_parameters)}"
        try:
            request = Request(full_url, method="GET", headers={"User-Agent": self._USER_AGENT})
            with urlopen(request, timeout=self._request_timeout_seconds) as response:
                status_code = int(response.getcode() or 200)
                payload = response.read()
        except TimeoutError as error:
            raise TimeoutError("Flex transport request timed out") from error
        except HTTPError as error:
            raise ConnectionError(f"Flex upstream returned HTTP {error.code}") from error
        except URLError as error:
            if isinstance(error.reason, (TimeoutError, socket.timeout)):
                raise TimeoutError("Flex transport request timed out") from error
            raise ConnectionError("Flex transport request failed") from error

        if status_code >= 400:
            raise ConnectionError(f"Flex upstream returned HTTP {status_code}")

        return bytes(payload)

    def _adapter_poll_payload_is_statement_xml(self, poll_root: element_tree.Element) -> bool:
        """Return whether poll response root contains a Flex statement payload.

        Args:
            poll_root: Parsed poll XML root node.

        Returns:
            bool: True when poll payload is a statement document.

        Raises:
            RuntimeError: This helper does not raise runtime errors.
        """

        if poll_root.tag == "FlexQueryResponse":
            return poll_root.find("FlexStatements") is not None
        return poll_root.tag == "FlexStatements"

    def _adapter_extract_response_error(
        self,
        response_root: element_tree.Element,
        fallback_message: str = "unexpected upstream response",
    ) -> tuple[str, str]:
        """Extract normalized error code and message from Flex response XML.

        Args:
            response_root: Parsed response XML root node.

        Returns:
            tuple[str, str]: Normalized error code and error message.

        Raises:
            RuntimeError: This helper does not raise runtime errors.
        """

        error_code = (response_root.findtext("ErrorCode") or "UNKNOWN").strip()
        error_message = (response_root.findtext("ErrorMessage") or "").strip()
        if not error_message:
            error_message = self._KNOWN_FLEX_ERROR_MESSAGES.get(error_code, fallback_message)
        return error_code, error_message

    def _adapter_retry_delay_seconds_for_error(self, error_code: str) -> int:
        """Return retry delay override for known retryable Flex poll errors.

        Args:
            error_code: Upstream Flex error code.

        Returns:
            int: Retry delay in seconds.

        Raises:
            RuntimeError: This helper does not raise runtime errors.
        """

        if error_code == self._THROTTLED_ERROR_CODE:
            return 10
        if error_code == self._SERVER_BUSY_ERROR_CODE:
            return 5
        return 5

    def _adapter_calculate_retry_wait_seconds(self, retry_index: int) -> float:
        """Calculate exponential retry wait with cap and jitter.

        Args:
            retry_index: Zero-based retry attempt index.

        Returns:
            float: Computed wait seconds for the poll attempt.

        Raises:
            ValueError: Raised when retry index is negative.
            RuntimeError: Raised when jitter provider returns out-of-range value.
        """

        if retry_index < 0:
            raise ValueError("retry_index must be >= 0")

        return self._retry_strategy.strategy_calculate_retry_wait_seconds(retry_index=retry_index)

    def _adapter_calculate_jitter_multiplier(self) -> float:
        """Return jitter multiplier using configured min/max bounds.

        Returns:
            float: Jitter multiplier value.

        Raises:
            RuntimeError: Raised when jitter source returns value outside [0.0, 1.0].
        """

        return self._retry_strategy.strategy_calculate_jitter_multiplier()

    def _adapter_parse_xml(self, payload: bytes, context_label: str) -> element_tree.Element:
        """Parse payload as XML and raise deterministic parsing errors.

        Args:
            payload: Candidate XML payload.
            context_label: Context label for error messages.

        Returns:
            xml.etree.ElementTree.Element: Parsed root node.

        Raises:
            RuntimeError: Raised when payload is not valid XML.
        """

        try:
            return element_tree.fromstring(payload)
        except element_tree.ParseError as error:
            raise RuntimeError(f"Flex XML parse failed for context={context_label}") from error

    def _adapter_try_parse_xml(self, payload: bytes) -> element_tree.Element | None:
        """Best-effort XML parse helper for polling responses.

        Args:
            payload: Candidate response payload.

        Returns:
            xml.etree.ElementTree.Element | None: Parsed root element when XML, otherwise None.

        Raises:
            RuntimeError: This helper does not raise runtime errors.
        """

        try:
            return element_tree.fromstring(payload)
        except element_tree.ParseError:
            return None

    def _adapter_record_stage_event(
        self,
        stage_timeline: list[dict[str, object]],
        stage: str,
        status: str,
        details: dict[str, object] | None = None,
    ) -> None:
        """Append one structured stage event to the adapter timeline.

        Args:
            stage_timeline: Mutable diagnostics timeline list.
            stage: Stage name.
            status: Stage status marker.
            details: Optional structured payload details.

        Returns:
            None: Appends to timeline as side effect.

        Raises:
            RuntimeError: This helper does not raise runtime errors.
        """
        stage_timeline.append(domain_build_stage_event(stage=stage, status=status, details=details))
