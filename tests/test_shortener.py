"""URL shortener: plugin selection and the fall-back-to-original contract.

The shortener ships disabled and depends on an external service, so the
session is stubbed. The behaviour that matters is that a failure of any kind
returns the original URL rather than breaking the link.
"""
import pytest

from Thunder.utils import shortener
from Thunder.utils.shortener import (
    BitlyPlugin,
    CuttLyPlugin,
    GenericShortenerPlugin,
    LinkvertisePlugin,
    OuoIoPlugin,
    ShortenerPlugin,
    ShortenerSystem,
)
from Thunder.vars import Var

ORIGINAL = "https://example.com/f/abcdef/name.bin"


class FakeResponse:
    def __init__(self, status_code=200, text="", payload=None):
        self.status_code = status_code
        self.text = text
        self._payload = payload or {}

    def json(self):
        return self._payload


class FakeSession:
    """Records calls and returns a prepared response (or raises)."""

    def __init__(self, response=None, raises=None):
        self.response = response or FakeResponse()
        self.raises = raises
        self.calls = []

    def _call(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        if self.raises:
            raise self.raises
        return self.response

    def get(self, *args, **kwargs):
        return self._call(*args, **kwargs)

    def post(self, *args, **kwargs):
        return self._call(*args, **kwargs)


class TestPluginSelection:
    @pytest.mark.parametrize("domain,expected", [
        ("https://linkvertise.com/x", LinkvertisePlugin),
        ("https://bitly.com/", BitlyPlugin),
        ("https://ouo.io/", OuoIoPlugin),
        ("https://cutt.ly/", CuttLyPlugin),
        ("https://something-else.example/", GenericShortenerPlugin),
    ])
    def test_domain_picks_the_matching_plugin(self, domain, expected):
        system = ShortenerSystem()
        assert system._get_plugin_class(domain) is expected

    def test_every_plugin_subclasses_the_base(self):
        for plugin in ShortenerPlugin.__subclasses__():
            assert issubclass(plugin, ShortenerPlugin)


class TestInitialize:
    async def test_disabled_system_stays_unready(self, monkeypatch, caplog):
        # Credentials AND a known plugin are supplied, so if the flag check
        # were removed the system would initialise; only the flag can stop it.
        monkeypatch.setattr(Var, "SHORTEN_ENABLED", False, raising=False)
        monkeypatch.setattr(Var, "SHORTEN_MEDIA_LINKS", False, raising=False)
        monkeypatch.setattr(Var, "URL_SHORTENER_SITE", "cutt.ly", raising=False)
        monkeypatch.setattr(Var, "URL_SHORTENER_API_KEY", "key", raising=False)
        system = ShortenerSystem()
        assert await system.initialize() is False
        assert system.ready is False

    async def test_disabled_flag_wins_over_complete_configuration(self, monkeypatch):
        """With the flag set, initialisation must stop before touching the
        network even though every other setting is valid."""
        monkeypatch.setattr(Var, "SHORTEN_ENABLED", False, raising=False)
        monkeypatch.setattr(Var, "SHORTEN_MEDIA_LINKS", False, raising=False)
        monkeypatch.setattr(Var, "URL_SHORTENER_SITE", "cutt.ly", raising=False)
        monkeypatch.setattr(Var, "URL_SHORTENER_API_KEY", "key", raising=False)

        called = []

        def spy(**kwargs):
            called.append(kwargs)
            return FakeSession()

        monkeypatch.setattr(shortener, "cloudscraper",
                            type("M", (), {"create_scraper": staticmethod(spy)}))
        system = ShortenerSystem()
        assert await system.initialize() is False
        assert called == [], "no scraper may be created while disabled"

    async def test_missing_site_or_key_stays_unready(self, monkeypatch):
        monkeypatch.setattr(Var, "SHORTEN_ENABLED", True, raising=False)
        monkeypatch.setattr(Var, "SHORTEN_MEDIA_LINKS", False, raising=False)
        monkeypatch.setattr(Var, "URL_SHORTENER_SITE", "", raising=False)
        monkeypatch.setattr(Var, "URL_SHORTENER_API_KEY", "", raising=False)
        system = ShortenerSystem()
        assert await system.initialize() is False

    async def test_enabled_with_credentials_becomes_ready(self, monkeypatch):
        monkeypatch.setattr(Var, "SHORTEN_ENABLED", True, raising=False)
        monkeypatch.setattr(Var, "URL_SHORTENER_SITE", "https://cutt.ly/", raising=False)
        monkeypatch.setattr(Var, "URL_SHORTENER_API_KEY", "key", raising=False)

        # create_scraper is synchronous and is called via asyncio.to_thread.
        def fake_create_scraper(**kwargs):
            return FakeSession()

        monkeypatch.setattr(shortener, "cloudscraper",
                            type("M", (), {"create_scraper": staticmethod(fake_create_scraper)}))
        system = ShortenerSystem()
        assert await system.initialize() is True
        assert system.ready is True
        assert isinstance(system.plugin, CuttLyPlugin)

    async def test_scraper_failure_leaves_system_unready(self, monkeypatch):
        monkeypatch.setattr(Var, "SHORTEN_ENABLED", True, raising=False)
        monkeypatch.setattr(Var, "URL_SHORTENER_SITE", "https://cutt.ly/", raising=False)
        monkeypatch.setattr(Var, "URL_SHORTENER_API_KEY", "key", raising=False)

        def boom(**kwargs):
            raise RuntimeError("scraper unavailable")

        monkeypatch.setattr(shortener, "cloudscraper",
                            type("M", (), {"create_scraper": staticmethod(boom)}))
        system = ShortenerSystem()
        assert await system.initialize() is False
        assert system.ready is False


class TestShortUrl:
    async def test_unready_system_returns_the_original(self):
        system = ShortenerSystem()
        assert await system.short_url(ORIGINAL) == ORIGINAL

    async def test_plugin_error_returns_the_original(self):
        system = ShortenerSystem()
        system.ready = True
        system.plugin = CuttLyPlugin()
        system.plugin.session = FakeSession(raises=RuntimeError("network down"))
        system.plugin.domain = "https://cutt.ly/"
        assert await system.short_url(ORIGINAL) == ORIGINAL

    async def test_successful_shortening_returns_the_short_link(self):
        system = ShortenerSystem()
        system.ready = True
        system.plugin = CuttLyPlugin()
        system.plugin.session = FakeSession(
            FakeResponse(payload={"url": {"shortLink": "https://cutt.ly/abc"}}))
        system.plugin.domain = "https://cutt.ly/"
        assert await system.short_url(ORIGINAL) == "https://cutt.ly/abc"


class TestPlugins:
    async def test_cuttly_falls_back_on_http_error(self):
        plugin = CuttLyPlugin()
        plugin.session = FakeSession(FakeResponse(status_code=500))
        assert await plugin.shorten(ORIGINAL, "k") == ORIGINAL

    async def test_ouo_falls_back_on_empty_body(self):
        plugin = OuoIoPlugin()
        plugin.session = FakeSession(FakeResponse(status_code=200, text=""))
        assert await plugin.shorten(ORIGINAL, "k") == ORIGINAL

    async def test_ouo_returns_body_text(self):
        plugin = OuoIoPlugin()
        plugin.session = FakeSession(FakeResponse(status_code=200, text="https://ouo.io/x"))
        assert await plugin.shorten(ORIGINAL, "k") == "https://ouo.io/x"

    async def test_bitly_falls_back_on_http_error(self):
        plugin = BitlyPlugin()
        plugin.session = FakeSession(FakeResponse(status_code=401))
        assert await plugin.shorten(ORIGINAL, "k") == ORIGINAL

    async def test_bitly_returns_link(self):
        plugin = BitlyPlugin()
        plugin.session = FakeSession(FakeResponse(payload={"link": "https://bit.ly/x"}))
        assert await plugin.shorten(ORIGINAL, "k") == "https://bit.ly/x"

    async def test_generic_uses_configured_domain(self):
        # config_sample.env documents a bare domain ("example.com").
        plugin = GenericShortenerPlugin()
        plugin.domain = "sh.example"
        session = FakeSession(
            FakeResponse(payload={"shortenedUrl": "https://sh.example/y"}))
        plugin.session = session
        assert await plugin.shorten(ORIGINAL, "k") == "https://sh.example/y"
        requested_url = session.calls[0][0][0]
        assert requested_url.startswith("https://sh.example/api")
        assert "api=k" in requested_url

    async def test_generic_accepts_a_scheme_prefixed_domain(self):
        """A configured value with a scheme must not produce https://https://."""
        plugin = GenericShortenerPlugin()
        plugin.domain = "https://sh.example"
        session = FakeSession(FakeResponse(payload={}))
        plugin.session = session
        await plugin.shorten(ORIGINAL, "k")
        requested_url = session.calls[0][0][0]
        assert requested_url.startswith("https://sh.example/api")
        assert "https://https://" not in requested_url

    async def test_generic_falls_back_when_key_absent(self):
        plugin = GenericShortenerPlugin()
        plugin.domain = "https://sh.example"
        plugin.session = FakeSession(FakeResponse(payload={}))
        assert await plugin.shorten(ORIGINAL, "k") == ORIGINAL

    async def test_linkvertise_returns_one_of_its_domains(self):
        plugin = LinkvertisePlugin()
        result = await plugin.shorten(ORIGINAL, "key")
        assert result.startswith("https://")
        assert any(domain in result for domain in (
            "link-to.net", "up-to-down.net", "direct-link.net", "file-link.net"))
        assert "key" in result
