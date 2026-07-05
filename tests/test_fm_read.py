"""fm-read boots headless against the synthetic home and maps the fleet."""
import pytest

import fm_read


@pytest.mark.asyncio
async def test_reading_room_boots_and_maps_the_fleet(fake_home):
    app = fm_read.ReadingRoom(fake_home, None)
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        tree = app.query_one("Tree")
        labels = [str(n.label) for n in tree.root.children]
        assert any("FLEET" in x for x in labels)
        assert any("IN FLIGHT" in x for x in labels)
        await pilot.press("q")
