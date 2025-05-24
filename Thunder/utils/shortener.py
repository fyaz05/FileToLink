'''URL shortening utility.'''
import asyncio
from typing import Dict, Optional
from base64 import b64encode
from random import random, choice
from urllib.parse import quote, urlparse
from warnings import filterwarnings
import cloudscraper

from Thunder.vars import Var
from Thunder.utils.logger import logger

# Global shortener dictionary, scraper session, lock, and status flag
shortener_dict: Dict[str, str] = {}
_scraper_session: Optional[cloudscraper.CloudScraper] = None
_init_lock = asyncio.Lock()
_shortener_system_ready = False

# Suppress insecure HTTPS warnings
try:
    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
except (ImportError, AttributeError):
    filterwarnings('ignore', message='Unverified HTTPS request')

def _init_shorteners() -> bool:
    """Initializes shortener config and the global scraper session."""
    global shortener_dict, _scraper_session, _shortener_system_ready
    
    _shortener_system_ready = False # Assume failure until success
    shortener_dict = {}
    _scraper_session = None
    
    # Check if shortening is enabled via Var
    is_enabled = getattr(Var, "SHORTEN_ENABLED", False) or getattr(Var, "SHORTEN_MEDIA_LINKS", False)
    if not is_enabled:
        logger.info("ⓘ URL shortening is disabled")
        return False
    
    # Get URL shortener configuration
    site = getattr(Var, "URL_SHORTENER_SITE", "")
    api_key = getattr(Var, "URL_SHORTENER_API_KEY", "")
    
    if site and api_key:
        shortener_dict[site] = api_key
        try:
            _scraper_session = cloudscraper.create_scraper(
                browser={
                    'browser': 'chrome',
                    'platform': 'android',
                    'desktop': False
                }
            )
            logger.info(f"✓ Shortener initialized: {site} (session created)")
            _shortener_system_ready = True
            return True
        except Exception as e:
            logger.error(f"Failed to create scraper session: {e}")
            # Reset to ensure clean state on session creation failure
            shortener_dict = {}
            _scraper_session = None
            _shortener_system_ready = False
            return False
    
    logger.warning("ⓘ URL shortener not configured or API key missing")
    _shortener_system_ready = False # Explicitly ensure it's false
    return False

def _validate_url(url: str) -> bool:
    """Checks if a string is a well-formed URL."""
    try:
        result = urlparse(url)
        return all([result.scheme, result.netloc])
    except Exception:
        return False

async def short_url(longurl: str, attempt: int = 0) -> str:
    """Shortens a URL using a configured service, with retries and fallback."""
    global _shortener_system_ready, _scraper_session # Ensure globals are accessible

    if not _shortener_system_ready:  # First check (no lock)
        async with _init_lock:
            if not _shortener_system_ready:  # Double check (with lock)
                _init_shorteners()  # Attempt initialization

    if not _shortener_system_ready:
        # Initialization failed or not enabled, _init_shorteners logs details
        return longurl
        
    if _scraper_session is None:
        logger.error(
            "Critical state: _shortener_system_ready is True, but _scraper_session is None. "
            "Attempting a locked re-initialization."
        )
        async with _init_lock:
            _init_shorteners()
        
        if not _shortener_system_ready or _scraper_session is None:
            logger.error(
                "Critical: Scraper session remains uninitialized after re-attempt. "
                "URL shortening will be skipped for this request."
            )
            return longurl

    if attempt >= 3:
        logger.warning(f"Max attempts reached for {longurl}. Returning original.")
        return longurl
    
    if not _validate_url(longurl):
        logger.warning(f"Invalid URL format: {longurl}. Returning original.")
        return longurl
    
    try:
        session = _scraper_session 
        
        # Ensure shortener_dict is not empty before trying to access its items
        if not shortener_dict:
            logger.error("Shortener dictionary is empty. Cannot proceed with shortening.")
            return longurl # Or raise an exception if this state is unexpected
            
        _shortener, _shortener_api = choice(list(shortener_dict.items()))
        
        # --- Shorte.st --- 
        if "shorte.st" in _shortener:
            headers = {"public-api-token": _shortener_api}
            data = {"urlToShorten": quote(longurl)}
            response = session.put(
                "https://api.shorte.st/v1/data/url",
                headers=headers,
                data=data
            )
            
            if response.status_code != 200:
                raise Exception(f"HTTP error {response.status_code}")
                
            return response.json()["shortenedUrl"]
            
        # --- Linkvertise ---        
        elif "linkvertise" in _shortener:
            url = quote(b64encode(longurl.encode("utf-8")))
            linkvertise = [
                f"https://link-to.net/{_shortener_api}/{random() * 1000}/dynamic?r={url}",
                f"https://up-to-down.net/{_shortener_api}/{random() * 1000}/dynamic?r={url}",
                f"https://direct-link.net/{_shortener_api}/{random() * 1000}/dynamic?r={url}",
                f"https://file-link.net/{_shortener_api}/{random() * 1000}/dynamic?r={url}",
            ]
            return choice(linkvertise)
            
        # --- Bitly.com ---    
        elif "bitly.com" in _shortener:
            headers = {"Authorization": f"Bearer {_shortener_api}"}
            response = session.post(
                "https://api-ssl.bit.ly/v4/shorten",
                json={"long_url": longurl},
                headers=headers
            )
            
            if response.status_code != 200:
                raise Exception(f"HTTP error {response.status_code}")
                
            return response.json()["link"]
            
        # --- Ouo.io ---    
        elif "ouo.io" in _shortener:
            response = session.get(
                f"http://ouo.io/api/{_shortener_api}?s={longurl}",
                verify=False
            )
            
            if not response.text or response.status_code != 200:
                raise Exception(f"HTTP error {response.status_code}")
                
            return response.text
            
        # --- Cutt.ly ---    
        elif "cutt.ly" in _shortener:
            response = session.get(
                f"http://cutt.ly/api/api.php?key={_shortener_api}&short={longurl}"
            )
            
            if response.status_code != 200:
                raise Exception(f"HTTP error {response.status_code}")
                
            return response.json()["url"]["shortLink"]
            
        # --- Generic & Fallback ---    
        else:
            # Generic shortener handling
            response = session.get(
                f"https://{_shortener}/api?api={_shortener_api}&url={quote(longurl)}"
            )
            
            if response.status_code != 200:
                raise Exception(f"Generic API error {response.status_code}")
                
            result = response.json()
            shorted = result.get("shortenedUrl")
            
            # Fallback to shrtco.de if generic failed or no result
            if not shorted:
                try:
                    shrtco_response = session.get(
                        f"https://api.shrtco.de/v2/shorten?url={quote(longurl)}"
                    )
                    
                    if shrtco_response.status_code != 200:
                        logger.warning(f"shrtco.de fallback failed with status {shrtco_response.status_code}")
                        return longurl # Return original if fallback also fails badly
                        
                    shrtco_result = shrtco_response.json()
                    if not shrtco_result.get("ok") or "result" not in shrtco_result or "full_short_link" not in shrtco_result["result"]:
                        logger.warning(f"shrtco.de fallback response malformed: {shrtco_result}")
                        return longurl
                        
                    shrtco_link = shrtco_result["result"]["full_short_link"]
                    
                    # Try the configured generic shortener again with the shrtco.de link
                    response = session.get(
                        f"https://{_shortener}/api?api={_shortener_api}&url={shrtco_link}"
                    )
                    
                    if response.status_code != 200:
                        logger.warning(f"Generic shortener retry with shrtco.de link failed: status {response.status_code}")
                        return longurl # Return original if retry fails
                        
                    result = response.json()
                    shorted = result.get("shortenedUrl")
                except Exception as fallback_e:
                    logger.error(f"shrtco.de fallback exception: {fallback_e}")
                    return longurl # Return original on fallback exception
            
            return shorted if shorted else longurl
            
    except Exception as e:
        shortener_name = _shortener if '_shortener' in locals() else 'unknown'
        logger.error(f"URL shortener error ({shortener_name} for {longurl}): {e}, attempt {attempt + 1}")
        
        await asyncio.sleep(1) # Wait before retrying
        return await short_url(longurl, attempt + 1)

async def shorten(url: str) -> str:
    """Async public wrapper for short_url."""
    return await short_url(url)