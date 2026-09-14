from __future__ import annotations

import asyncio
import gzip

import httpx

from polymarket_scanner.weather_only_pws import (
    PWS_STATUS_RESPONSE_TOO_LARGE,
    PWS_STATUS_UNSUPPORTED_CONTENT_ENCODING,
    WeatherCompanyPWSClient,
)


class TrackingStream(httpx.AsyncByteStream):
    def __init__(self, chunks: list[bytes]) -> None:
        self.chunks = list(chunks)
        self.iterated = False
        self.yielded_bytes = 0
        self.closed = False

    async def __aiter__(self):
        self.iterated = True
        for chunk in self.chunks:
            self.yielded_bytes += len(chunk)
            yield chunk

    async def aclose(self) -> None:
        self.closed = True


def test_streamed_gzip_is_rejected_before_body_iteration_or_decompression():
    # The compressed payload represents a body much larger than the normal PWS cap.
    # The safety property is stronger than an eventual TOO_LARGE result: the stream
    # must never be iterated once the response headers disclose compression.
    encoded = gzip.compress(b'{"payload":"' + (b"A" * 1_000_000) + b'"}')
    stream = TrackingStream([encoded])

    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers.get("accept-encoding") == "identity"
        return httpx.Response(
            200,
            headers={"Content-Encoding": "gzip"},
            stream=stream,
            request=request,
        )

    async def scenario() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            client = WeatherCompanyPWSClient(api_key="secret", http=http)
            status, body = await client._json("/v3/location/near", {"format": "json"})
            assert status == PWS_STATUS_UNSUPPORTED_CONTENT_ENCODING
            assert body is None
            assert stream.iterated is False
            assert stream.yielded_bytes == 0

    asyncio.run(scenario())


def test_stacked_content_encoding_is_rejected_before_stream_is_touched():
    stream = TrackingStream([b"provider-controlled-compressed-bytes"])

    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers.get("accept-encoding") == "identity"
        return httpx.Response(
            200,
            headers={"Content-Encoding": "gzip, deflate"},
            stream=stream,
            request=request,
        )

    async def scenario() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            client = WeatherCompanyPWSClient(api_key="secret", http=http)
            status, body = await client._json("/v3/location/near", {"format": "json"})
            assert status == PWS_STATUS_UNSUPPORTED_CONTENT_ENCODING
            assert body is None
            assert stream.iterated is False
            assert stream.yielded_bytes == 0

    asyncio.run(scenario())


def test_compressed_rejection_does_not_poison_following_request():
    compressed_stream = TrackingStream([gzip.compress(b'{"ignored":true}')])
    calls = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        assert request.headers.get("accept-encoding") == "identity"
        if calls == 1:
            return httpx.Response(
                200,
                headers={"Content-Encoding": "gzip"},
                stream=compressed_stream,
                request=request,
            )
        return httpx.Response(200, content=b'{"ok":true}', request=request)

    async def scenario() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            client = WeatherCompanyPWSClient(api_key="secret", http=http)
            first_status, first_body = await client._json("/first", {})
            second_status, second_body = await client._json("/second", {})
            assert first_status == PWS_STATUS_UNSUPPORTED_CONTENT_ENCODING
            assert first_body is None
            assert compressed_stream.iterated is False
            assert second_status == "OK"
            assert second_body == {"ok": True}

    asyncio.run(scenario())


def test_identity_response_raw_byte_cap_still_applies_before_json_parse():
    stream = TrackingStream([b"x" * 80, b"y" * 80])

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, stream=stream, request=request)

    async def scenario() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            client = WeatherCompanyPWSClient(
                api_key="secret",
                http=http,
                max_response_bytes=100,
            )
            status, body = await client._json("/raw-limit", {})
            assert status == PWS_STATUS_RESPONSE_TOO_LARGE
            assert body is None
            assert stream.iterated is True
            assert stream.yielded_bytes == 160

    asyncio.run(scenario())


def test_declared_oversized_identity_body_is_rejected_without_iteration():
    stream = TrackingStream([b"never-read"])

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"Content-Length": "1000"},
            stream=stream,
            request=request,
        )

    async def scenario() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            client = WeatherCompanyPWSClient(
                api_key="secret",
                http=http,
                max_response_bytes=100,
            )
            status, body = await client._json("/declared-limit", {})
            assert status == PWS_STATUS_RESPONSE_TOO_LARGE
            assert body is None
            assert stream.iterated is False
            assert stream.yielded_bytes == 0

    asyncio.run(scenario())


def test_non_chunked_transfer_coding_is_rejected_before_iteration():
    stream = TrackingStream([b"never-read"])

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"Transfer-Encoding": "gzip, chunked"},
            stream=stream,
            request=request,
        )

    async def scenario() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            client = WeatherCompanyPWSClient(api_key="secret", http=http)
            status, body = await client._json("/transfer-coding", {})
            assert status == PWS_STATUS_UNSUPPORTED_CONTENT_ENCODING
            assert body is None
            assert stream.iterated is False
            assert stream.yielded_bytes == 0

    asyncio.run(scenario())
