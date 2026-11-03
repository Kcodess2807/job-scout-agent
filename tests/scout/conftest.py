"""His tests, as they ran in his repo (his conftest, plus the paths): his src is vendored under src/scout."""
import asyncio
import datetime as dt
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1] / "src" / "scout"))   # his imports are bare names, as hosted
sys.path.insert(0, str(HERE))

TODAY = dt.date(2026, 11, 1)


async def no_sleep(_seconds):
    await asyncio.sleep(0)


@pytest.fixture
def today():
    return TODAY


def run(coro):
    return asyncio.run(coro)
