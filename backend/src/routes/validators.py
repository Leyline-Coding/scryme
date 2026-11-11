"""Shared helpers for CSV importers."""

from __future__ import annotations

from typing import Annotated
from pydantic import AfterValidator
from src.importers.util import _LANGUAGES
from src.collection_edit import _CONDITION_CODES




def validate_condition(v: str) -> str:
    # Allow completely empty strings
    if v == "":
        return v
    
    # Check if the value is in the allowed list
    if v not in [x[1] for x in _CONDITION_CODES]:
        raise ValueError(f"Invalid condition entry.")
        
    return v

ValidCondition = Annotated[str, AfterValidator(validate_condition)]


def validate_language(v: str) -> str:
    # Allow completely empty strings
    if v == "":
        return v
    
    # Check if the value is in the allowed list
    if v not in _LANGUAGES.values():
        raise ValueError(f"Invalid language code.")
        
    return v

ValidLanguage = Annotated[str, AfterValidator(validate_language)]


def validate_finish(v: str) -> str:
    # Allow completely empty strings
    if v == "":
        return v
    
    # Check if the value is in the allowed list
    if v not in ['normal', 'foil', 'etched']:
        raise ValueError(f"Invalid finish.")
        
    return v

ValidFinish = Annotated[str, AfterValidator(validate_finish)]