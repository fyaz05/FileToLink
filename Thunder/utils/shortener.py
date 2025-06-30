# Thunder/utils/shortener.py

import cloudscraper
from abc import ABC, abstractmethod
from base64 import b64encode
from random import random, choice
from urllib.parse import quote
from Thunder.vars import Var
from Thunder.utils.logger import logger

class ShortenerPlugin(ABC):
    @classmethod
    @abstractmethod
    def matches(cls, domain: str) -> bool:
        pass
    
    @abstractmethod
    async def shorten(self, url: str, api_key: str) -> str:
        pass

class LinkvertisePlugin(ShortenerPlugin):
    @classmethod
    def matches(cls, domain: str) -> bool:
        return "linkvertise" in domain
    
    async def shorten(self, url: str, api_key: str) -> str:
        encoded_url = quote(b64encode(url.encode("utf-8")))
        return choice([
            f"https://link-to.net/{api_key}/{random() * 1000}/dynamic?r={encoded_url}",
            f"https://up-to-down.net/{api_key}/{random() * 1000}/dynamic?r={encoded_url}",
            f"https://direct-link.net/{api_key}/{random() * 1000}/dynamic?r={encoded_url}",
            f"https://file-link.net/{api_key}/{random() * 1000}/dynamic?r={encoded_url}",
        ])

class BitlyPlugin(ShortenerPlugin):
    @classmethod
    def matches(cls, domain: str) -> bool:
        return "bitly.com" in domain
    
    async def shorten(self, url: str, api_key: str) -> str:
        response = self.session.post(
            "https://api-ssl.bit.ly/v4/shorten",
            json={"long_url": url},
            headers={"Authorization": f"Bearer {api_key}"}
        )
        if response.status_code == 200:
            return response.json()["link"]
        return url

class OuoIoPlugin(ShortenerPlugin):
    @classmethod
    def matches(cls, domain: str) -> bool:
        return "ouo.io" in domain
    
    async def shorten(self, url: str, api_key: str) -> str:
        response = self.session.get(f"http://ouo.io/api/{api_key}?s={url}")
        if response.status_code == 200 and response.text:
            return response.text
        return url

class CuttLyPlugin(ShortenerPlugin):
    @classmethod
    def matches(cls, domain: str) -> bool:
        return "cutt.ly" in domain
    
    async def shorten(self, url: str, api_key: str) -> str:
        response = self.session.get(f"http://cutt.ly/api/api.php?key={api_key}&short={url}")
        if response.status_code == 200:
            return response.json()["url"]["shortLink"]
        return url

class GenericShortenerPlugin(ShortenerPlugin):
    @classmethod
    def matches(cls, domain: str) -> bool:
        return True
    
    async def shorten(self, url: str, api_key: str) -> str:
        response = self.session.get(f"https://{self.domain}/api?api={api_key}&url={quote(url)}")
        if response.status_code == 200:
            return response.json().get("shortenedUrl", url)
        return url

class ShortenerSystem:
    def __init__(self):
        self.session = None
        self.plugin = None
        self.ready = False
    
    def _get_plugin_class(self, domain: str):
        for plugin_class in ShortenerPlugin.__subclasses__():
            if plugin_class.matches(domain):
                return plugin_class

        return GenericShortenerPlugin
    
    async def initialize(self) -> bool:
        if self.ready:
            return True
        
        if not (getattr(Var, "SHORTEN_ENABLED", False) or 
                getattr(Var, "SHORTEN_MEDIA_LINKS", False)):
            return False
        
        site = getattr(Var, "URL_SHORTENER_SITE", "")
        api_key = getattr(Var, "URL_SHORTENER_API_KEY", "")
        
        if not (site and api_key):
            return False
        
        try:
            self.session = cloudscraper.create_scraper(
                browser={
                    'browser': 'chrome',
                    'platform': 'windows',
                    'desktop': True,
                    'mobile': False
                },
                delay=1
            )
            
            plugin_class = self._get_plugin_class(site)
            self.plugin = plugin_class()
            self.plugin.session = self.session
            self.plugin.domain = site
            self.ready = True
            return True
        except Exception as e:
            logger.error(f"Failed to initialize ShortenerSystem: {e}", exc_info=True)
            return False
    
    async def short_url(self, url: str) -> str:
        if not self.ready:
            return url
        
        try:
            return await self.plugin.shorten(url, Var.URL_SHORTENER_API_KEY)
        except Exception as e:
            logger.error(f"Error shortening URL {url}: {e}", exc_info=True)
            return url

_system = ShortenerSystem()

async def shorten(url: str) -> str:
    if not _system.ready:
        await _system.initialize()
    return await _system.short_url(url)
