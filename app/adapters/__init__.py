"""Adapter layer package for broker integration boundaries."""

from .flex_web_service import FlexWebServiceAdapter
from .interfaces import AdapterFetchResult, FlexAdapterPort

__all__ = ["AdapterFetchResult", "FlexAdapterPort", "FlexWebServiceAdapter"]
