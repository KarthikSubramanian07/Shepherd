"""
Tests for non-overlapping local browser window tiling — each parallel session
claims a distinct grid slot so windows never stack on top of each other.

No browser needed: only the slot allocator + geometry math are exercised.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import services.browserbase_session as BS


def _overlap(a, b) -> bool:
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    return ax < bx + bw and bx < ax + aw and ay < by + bh and by < ay + ah


def test_slots_alloc_unique_and_reused():
    BS._used_slots.clear()
    a, b, c = BS._alloc_slot(), BS._alloc_slot(), BS._alloc_slot()
    assert {a, b, c} == {0, 1, 2}
    BS._free_slot(b)
    assert BS._alloc_slot() == 1   # lowest freed slot reused
    BS._used_slots.clear()


def test_free_none_is_safe():
    BS._free_slot(None)  # must not raise


def test_tiles_never_overlap_and_fit_screen(monkeypatch):
    for count in (1, 2, 3, 4, 6, 9):
        monkeypatch.setattr(BS, "_TILE_COUNT", count)
        rects = [BS._tile_geometry(s) for s in range(count)]
        # All within the screen.
        for (x, y, w, h) in rects:
            assert x >= 0 and y >= 0
            assert x + w <= BS.SCREEN_WIDTH
            assert y + h <= BS.SCREEN_HEIGHT
            assert w > 0 and h > 0
        # Pairwise non-overlapping.
        for i in range(len(rects)):
            for j in range(i + 1, len(rects)):
                assert not _overlap(rects[i], rects[j]), \
                    f"slots {i},{j} overlap at count={count}: {rects[i]} {rects[j]}"


def test_overflow_slot_wraps_onto_grid(monkeypatch):
    # A slot beyond the configured count must still land on a real cell, not
    # spill off-screen.
    monkeypatch.setattr(BS, "_TILE_COUNT", 3)
    x, y, w, h = BS._tile_geometry(99)
    assert 0 <= x and x + w <= BS.SCREEN_WIDTH
    assert 0 <= y and y + h <= BS.SCREEN_HEIGHT


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))
