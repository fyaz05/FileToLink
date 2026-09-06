# Thunder/utils/speedtest.py

import asyncio
from typing import Optional, Tuple, Dict, Any

import speedtest
from Thunder.utils.logger import logger


async def run_speedtest() -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    try:
        return await asyncio.to_thread(_perform_speedtest)
    except Exception as e:
        logger.error(f"Speedtest failed: {e}", exc_info=True)
        return None, None


def _perform_speedtest() -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    try:
        st = speedtest.Speedtest(timeout=15, secure=True)
        st.get_best_server()
        st.download()
        st.upload(pre_allocate=False)
        
        results = st.results.dict()
        download_mbps = st.results.download / 1_000_000
        upload_mbps = st.results.upload / 1_000_000
        
        results['download_mbps'] = download_mbps
        results['upload_mbps'] = upload_mbps
        results['download_bps'] = st.results.download / 8
        results['upload_bps'] = st.results.upload / 8
        
        logger.debug(f"Download: {download_mbps:.2f} Mbps | Upload: {upload_mbps:.2f} Mbps")
        
        try:
            return results, st.results.share()
        except Exception:
            return results, None
            
    except Exception as e:
        logger.error(f"Speedtest failed: {e}")
        return None, None
