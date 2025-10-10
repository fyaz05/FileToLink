# Thunder/utils/speedtest.py

import asyncio
from typing import Optional, Tuple, Dict, Any

from speedtest import Speedtest, ConfigRetrievalError
from Thunder.utils.logger import logger


async def run_speedtest() -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    
    try:
        def _run_speedtest():
            try:
                st = Speedtest()
            except ConfigRetrievalError:
                logger.error("Can't connect to speedtest server at the moment")
                return None
            
            st.get_best_server()
            st.download()
            st.upload()
            st.results.share()
            return st.results
        
        results = await asyncio.to_thread(_run_speedtest)
        
        if results is None:
            return None, None
        
        image_url = results.share()
        
        result_dict = results.dict()
        
        return result_dict, image_url
        
    except Exception as e:
        logger.error(f"Speedtest failed: {e}", exc_info=True)
        return None, None
