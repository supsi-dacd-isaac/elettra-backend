import pytest
from httpx import ASGITransport, AsyncClient

import main


@pytest.mark.asyncio
async def test_version_is_public_lightweight_and_never_cached():
    async with AsyncClient(transport=ASGITransport(app=main.app), base_url="http://local.test") as client:
        response = await client.get("/api/v1/version")
    assert response.status_code == 200
    assert response.json() == {"version": main.settings.app_version}
    assert response.headers["cache-control"] == "no-store"
