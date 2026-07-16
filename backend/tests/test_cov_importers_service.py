"""Coverage for src/importers/service.py error branches."""

import uuid

import pytest
from src.importers.base import UnknownFormatError
from src.importers.merge import MergeStrategy
from src.importers.service import confirm_upload, stage_mapped_upload


@pytest.mark.asyncio
async def test_stage_mapped_upload_no_rows_raises(session):
    # A mapping without a usable name column yields no rows -> UnknownFormatError.
    with pytest.raises(UnknownFormatError):
        await stage_mapped_upload(session, "A,B\n1,2\n", {"quantity": "A"})


@pytest.mark.asyncio
async def test_confirm_upload_bad_token_string_raises(session):
    # A non-UUID token -> uuid.UUID() ValueError -> staging None -> UnknownFormatError.
    with pytest.raises(UnknownFormatError):
        await confirm_upload(session, "not-a-uuid", MergeStrategy.INCREMENT)


@pytest.mark.asyncio
async def test_confirm_upload_missing_token_raises(session):
    # A well-formed but nonexistent token -> session.get returns None -> UnknownFormatError.
    with pytest.raises(UnknownFormatError):
        await confirm_upload(session, str(uuid.uuid4()), MergeStrategy.INCREMENT)
