import unittest
from types import SimpleNamespace

import httpx

from reelay.publishers.youtube import YouTubePublisher


class YouTubeAuthTests(unittest.IsolatedAsyncioTestCase):
    async def test_invalid_grant_explains_reauthorization_without_leaking_credentials(self):
        settings = SimpleNamespace(
            youtube_client_id="test-client-id",
            youtube_client_secret="test-secret",
            youtube_refresh_token="test-refresh",
            youtube_privacy_status="public",
        )
        publisher = YouTubePublisher(settings)
        transport = httpx.MockTransport(
            lambda request: httpx.Response(
                400,
                json={
                    "error": "invalid_grant",
                    "error_description": "Token has been expired or revoked. test-secret",
                },
            )
        )
        async with httpx.AsyncClient(transport=transport) as client:
            with self.assertRaises(RuntimeError) as caught:
                await publisher._access_token(client)
        message = str(caught.exception)
        self.assertIn("invalid_grant", message)
        self.assertIn("OAuth", message)
        self.assertIn("https://console.cloud.google.com/auth/audience", message)
        self.assertNotIn(settings.youtube_client_secret, message)
        self.assertNotIn(settings.youtube_refresh_token, message)

    async def test_valid_refresh_returns_access_token(self):
        settings = SimpleNamespace(
            youtube_client_id="test-client-id",
            youtube_client_secret="test-secret",
            youtube_refresh_token="test-refresh",
            youtube_privacy_status="public",
        )
        transport = httpx.MockTransport(
            lambda request: httpx.Response(200, json={"access_token": "new-access"})
        )
        async with httpx.AsyncClient(transport=transport) as client:
            self.assertEqual(await YouTubePublisher(settings)._access_token(client), "new-access")
