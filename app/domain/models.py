"""Typed domain models shared across runtime layers.

This module provides simple data contracts for cross-layer communication during
the MVP foundation stage.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class AppMetadata:
    """Static application metadata for runtime identification.

    Attributes:
        application_name: Human-readable app name.
        environment_name: Runtime environment label.
    """

    application_name: str
    environment_name: str


@dataclass(frozen=True)
class HealthStatus:
    """Health response contract used by health-check surfaces.

    Attributes:
        status: Overall status text for service health.
        detail: Additional message suitable for operational diagnostics.
    """

    status: str
    detail: str
