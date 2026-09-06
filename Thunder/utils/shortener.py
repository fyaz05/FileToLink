# Thunder/utils/shortener.py

"""URL shortener (plan H5b + M5).

* HTTP layer is asyncio-native ``aiohttp`` (cloudscraper removed; the
  requests/urllib3 transitive tree is gone).  ``curl_cffi`` remains an
  optional escape hatch for Cloudflare-protected providers -- declared as
  the ``shortener-cf`` extra, never a hard dependency.
* M5 hardening: LRU cache + per-URL singleflight, API key moved to an
  ``Authorization: Bearer`` header (never the query string), https-only
  endpoints, redirects never followed, and the returned short URL's host
  must match the configured site's host (anti redirect-to-attacker).
* The plugin registry and the offline Linkvertise builder are preserved.
"""

import asyncio
from abc import ABC, abstractmethod
from base64 import b64encode
from collections import OrderedDict
from random import choice, random
from urllib.parse import quote, urlparse

import aiohttp

from Thunder.utils.logger import logger
from Thunder.vars import Var

SHORTEN_TIMEOUT_SECONDS = 10
CACHE_MAX_ITEMS = 10_000


class ShortenerError(Exception):
    pass


class ShortenerPlugin(ABC):
    @classmethod
    @abstractmethod
    def matches(cls, domain: str) -> bool:
        pass

    @abstractmethod
    async def shorten(
        self, session: aiohttp.ClientSession, url: str, api_key: str, domain: str
    ) -> str:
        pass

    @staticmethod
    def _validate_short_url(short_url: str, domain: str) -> bool:
        """The response host must match the configured site (M5)."""
        try:
            result_host = urlparse(short_url).hostname or ""
            site_host = urlparse(f"https://{domain}").hostname or ""
            return result_host == site_host
        except ValueError:
            return False


class LinkvertisePlugin(ShortenerPlugin):
    """Offline constructor: no HTTP call involved, host check not needed."""

    @classmethod
    def matches(cls, domain: str) -> bool:
        return "linkvertise" in domain

    async def shorten(
        self, session: aiohttp.ClientSession, url: str, api_key: str, domain: str
    ) -> str:
        encoded_url = quote(b64encode(url.encode("utf-8")))
        return choice(
            [
                f"https://link-to.net/{api_key}/{random() * 1000}/dynamic?r={encoded_url}",
                f"https://up-to-down.net/{api_key}/{random() * 1000}/dynamic?r={encoded_url}",
                f"https://direct-link.net/{api_key}/{random() * 1000}/dynamic?r={encoded_url}",
                f"https://file-link.net/{api_key}/{random() * 1000}/dynamic?r={encoded_url}",
            ]
        )


class BitlyPlugin(ShortenerPlugin):
    @classmethod
    def matches(cls, domain: str) -> bool:
        return "bitly.com" in domain or "bit.ly" in domain

    async def shorten(
        self, session: aiohttp.ClientSession, url: str, api_key: str, domain: str
    ) -> str:
        async with session.post(
            "https://api-ssl.bit.ly/v4/shorten",
            json={"long_url": url},
            headers={"Authorization": f"Bearer {api_key}"},
            allow_redirects=False,  # M5: a 30x can never pass for a short URL
        ) as resp:
            if resp.status == 200:
                data = await resp.json()
                return data.get("link", url)
        return url


class OuoIoPlugin(ShortenerPlugin):
    @classmethod
    def matches(cls, domain: str) -> bool:
        return "ouo.io" in domain

    async def shorten(
        self, session: aiohttp.ClientSession, url: str, api_key: str, domain: str
    ) -> str:
        async with session.get(
            f"https://ouo.io/api/{api_key}", params={"s": url}, allow_redirects=False
        ) as resp:
            if resp.status == 200:
                text = (await resp.text()).strip()
                if text and self._validate_short_url(text, domain):
                    return text
        return url


class CuttLyPlugin(ShortenerPlugin):
    @classmethod
    def matches(cls, domain: str) -> bool:
        return "cutt.ly" in domain

    async def shorten(
        self, session: aiohttp.ClientSession, url: str, api_key: str, domain: str
    ) -> str:
        async with session.get(
            "https://cutt.ly/api/api.php",
            params={"key": api_key, "short": url},
            allow_redirects=False,
        ) as resp:
            if resp.status == 200:
                data = await resp.json()
                short = (data.get("url") or {}).get("shortLink")
                if short and self._validate_short_url(short, domain):
                    return short
        return url


class GenericShortenerPlugin(ShortenerPlugin):
    @classmethod
    def matches(cls, domain: str) -> bool:
        return True

    async def shorten(
        self, session: aiohttp.ClientSession, url: str, api_key: str, domain: str
    ) -> str:
        async with session.get(
            f"https://{domain}/api",
            params={"api": api_key, "url": url},
            headers={"Authorization": f"Bearer {api_key}"} if api_key else {},
            allow_redirects=False,
        ) as resp:
            if resp.status == 200:
                data = await resp.json()
                short = data.get("shortenedUrl", url)
                if short != url and not self._validate_short_url(short, domain):
                    logger.warning(f"Shortener returned foreign host {short!r}; rejecting.")
                    return url
                return short
        return url


class ShortenerSystem:
    def __init__(self):
        self.session: aiohttp.ClientSession | None = None
        self.plugin: ShortenerPlugin | None = None
        self.domain: str = ""
        self.ready = False
        self._cache: OrderedDict[str, str] = OrderedDict()
        self._inflight: dict[str, asyncio.Future] = {}

    def _get_plugin_class(self, domain: str):
        for plugin_class in ShortenerPlugin.__subclasses__():
            if plugin_class is not GenericShortenerPlugin and plugin_class.matches(domain):
                return plugin_class
        return GenericShortenerPlugin

    async def initialize(self) -> bool:
        if self.ready:
            return True

        if not (
            getattr(Var, "SHORTEN_ENABLED", False) or getattr(Var, "SHORTEN_MEDIA_LINKS", False)
        ):
            return False

        site = getattr(Var, "URL_SHORTENER_SITE", "")
        api_key = getattr(Var, "URL_SHORTENER_API_KEY", "")

        if not (site and api_key):
            return False

        try:
            timeout = aiohttp.ClientTimeout(total=SHORTEN_TIMEOUT_SECONDS)
            self.session = aiohttp.ClientSession(
                timeout=timeout,
                headers={"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) FileToLink/shortener"},
            )
            # NOTE: redirects are disabled per-request (aiohttp does not accept
            # ``allow_redirects`` on the session constructor -- passing it there
            # raises TypeError at runtime and silently disabled the shortener).
            self.domain = site
            plugin_class = self._get_plugin_class(site)
            self.plugin = plugin_class()
            self.ready = True
            logger.info(f"Shortener ready (plugin={type(plugin_class).__name__}, site={site})")
            return True
        except Exception as e:
            logger.error(f"Failed to initialize ShortenerSystem: {e}", exc_info=True)
            return False

    async def _shorten_uncached(self, url: str) -> str:
        if self.session is None or self.plugin is None:
            return url
        try:
            short = await self.plugin.shorten(
                self.session, url, Var.URL_SHORTENER_API_KEY, self.domain
            )
            if short and short != url:
                self._cache[url] = short
                self._cache.move_to_end(url)
                while len(self._cache) > CACHE_MAX_ITEMS:
                    self._cache.popitem(last=False)
            return short or url
        except Exception as e:
            logger.error(f"Error shortening URL {url}: {e}", exc_info=True)
            return url

    async def short_url(self, url: str) -> str:
        if not self.ready:
            return url

        cached = self._cache.get(url)
        if cached is not None:
            self._cache.move_to_end(url)
            return cached

        future = self._inflight.get(url)
        if future is not None:
            return await asyncio.shield(future)

        loop = asyncio.get_running_loop()
        future = loop.create_future()
        self._inflight[url] = future
        try:
            result = await self._shorten_uncached(url)
            if not future.done():
                future.set_result(result)
            return result
        except Exception as e:
            if not future.done():
                future.set_exception(e)
            raise
        finally:
            self._inflight.pop(url, None)

    async def close(self) -> None:
        if self.session and not self.session.closed:
            await self.session.close()


_system = ShortenerSystem()


async def shorten(url: str) -> str:
    if not _system.ready:
        await _system.initialize()
    return await _system.short_url(url)
