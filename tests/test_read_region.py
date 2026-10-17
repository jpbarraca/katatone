"""read_region reports per-block progress (regression: the callback was
wrapped in a lambda that rejected read_block's two arguments, so every
SYSTEM read with a progress callback failed)."""
from __future__ import annotations

from katatone.address.map import load_map
from katatone.amp.read import read_region


class _FakeTransport:
    """Answers every read with zero bytes of the requested length."""

    def rq1_nib(self, addr, length):
        return bytes(length)

    def rq1(self, addr, length, single_transaction=False):
        return bytes(length)


def test_read_region_with_progress() -> None:
    seen = []
    ps = read_region(_FakeTransport(), "SYSTEM",
                     progress=lambda d, n: seen.append((d, n)))
    total = len(load_map()["SYSTEM"].blocks)
    assert seen == [(i + 1, total) for i in range(total)]
    assert len(ps.blocks()) == total
