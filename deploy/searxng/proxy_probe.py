import asyncio

from searx import settings
from searx.network.network import Network


async def main() -> None:
    network = Network(proxies=settings["outgoing"]["proxies"])
    try:
        response = await network.get_client()
        public_response = await response.get("https://example.com/", timeout=10)
        public_response.raise_for_status()
    finally:
        await network.aclose()


asyncio.run(main())
