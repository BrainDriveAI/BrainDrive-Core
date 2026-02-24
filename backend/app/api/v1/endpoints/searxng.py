from fastapi import APIRouter, HTTPException, Query, Depends
from typing import Optional, List, Dict, Any
import httpx
import asyncio
import ipaddress
from urllib.parse import urlparse
from app.core.auth_deps import require_user
from app.core.auth_context import AuthContext
from app.core.config import settings
from app.models.user import User
import structlog
from bs4 import BeautifulSoup
import re

router = APIRouter()
logger = structlog.get_logger()

# SearXNG configuration
SEARXNG_BASE_URL = settings.SEARXNG_BASE_URL
REQUEST_TIMEOUT = 10.0

# SSRF-blocked hostnames
_BLOCKED_HOSTNAMES = {"metadata.google.internal", "metadata.goog"}


def _is_safe_url(url: str) -> bool:
    """Reject URLs targeting private/internal network ranges."""
    try:
        parsed = urlparse(url)
        hostname = parsed.hostname
        if not hostname:
            return False
        # Block known cloud metadata endpoints
        if hostname.lower() in _BLOCKED_HOSTNAMES:
            return False
        if hostname.lower() in ("localhost",):
            return False
        # Resolve and check IP ranges
        try:
            addr = ipaddress.ip_address(hostname)
            if addr.is_private or addr.is_loopback or addr.is_link_local or addr.is_reserved:
                return False
        except ValueError:
            # hostname is a DNS name, not an IP -- allow (it will be resolved by httpx)
            pass
        return True
    except Exception:
        return False

@router.get("/web")
async def search_web(
    q: str = Query(..., description="Search query"),
    category: str = Query("general", description="Search category"),
    language: str = Query("en", description="Search language"),
    time_range: Optional[str] = Query(None, description="Time range filter"),
    safesearch: int = Query(1, description="Safe search level (0-2)"),
    engines: Optional[str] = Query(None, description="Comma-separated list of engines"),
    auth: AuthContext = Depends(require_user)
) -> Dict[str, Any]:
    """
    Proxy web search requests to SearXNG server.
    This endpoint provides CORS-safe access to SearXNG from the frontend.
    """
    
    if not q or not q.strip():
        raise HTTPException(status_code=400, detail="Search query is required")
    
    # Build search parameters
    params = {
        "q": q.strip(),
        "format": "json",
        "category": category,
        "language": language,
        "safesearch": safesearch
    }
    
    # Add optional parameters
    if time_range:
        params["time_range"] = time_range
    if engines:
        params["engines"] = engines
    
    try:
        logger.info(f"🔍 Proxying search request to SearXNG", query=q, user_id=auth.user_id)
        
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
            response = await client.get(
                f"{SEARXNG_BASE_URL}/search",
                params=params,
                headers={
                    "Accept": "application/json",
                    "User-Agent": "BrainDrive/1.0"
                }
            )
            
            if response.status_code != 200:
                logger.error(f"SearXNG request failed", 
                           status_code=response.status_code, 
                           response_text=response.text[:500])
                raise HTTPException(
                    status_code=502, 
                    detail=f"Search service unavailable: {response.status_code}"
                )
            
            # Parse and return the JSON response
            search_results = response.json()
            
            logger.info(f"✅ Search completed successfully", 
                       query=q, 
                       results_count=search_results.get("number_of_results", 0),
                       user_id=auth.user_id)
            
            return search_results
            
    except httpx.TimeoutException:
        logger.error(f"SearXNG request timeout", query=q, user_id=auth.user_id)
        raise HTTPException(
            status_code=504, 
            detail="Search service timeout - please try again"
        )
    except httpx.ConnectError:
        logger.error(f"Cannot connect to SearXNG", query=q, user_id=auth.user_id)
        raise HTTPException(
            status_code=502, 
            detail="Search service is not available. Please ensure SearXNG is running."
        )
    except Exception as e:
        logger.error(f"Unexpected error during search", 
                    query=q, 
                    error=str(e), 
                    user_id=auth.user_id)
        raise HTTPException(
            status_code=500, 
            detail="Internal server error during search"
        )

@router.post("/scrape")
async def scrape_urls(
    urls: List[str],
    max_content_length: int = Query(5000, description="Maximum content length per URL"),
    auth: AuthContext = Depends(require_user)
) -> Dict[str, Any]:
    """
    Scrape content from multiple URLs and return cleaned text.
    This endpoint fetches and extracts readable content from web pages.
    """
    
    if not urls or len(urls) == 0:
        raise HTTPException(status_code=400, detail="At least one URL is required")
    
    if len(urls) > 5:  # Limit to 5 URLs to prevent abuse
        raise HTTPException(status_code=400, detail="Maximum 5 URLs allowed per request")
    
    scraped_results = []
    
    async def scrape_single_url(url: str) -> Dict[str, Any]:
        """Scrape a single URL and return cleaned content"""
        if not _is_safe_url(url):
            return {
                "url": url,
                "success": False,
                "error": "URL targets a blocked network range",
                "content": ""
            }
        try:
            logger.info(f"🕷️ Scraping URL", url=url, user_id=auth.user_id)
            
            async with httpx.AsyncClient(
                timeout=10.0,
                headers={
                    "User-Agent": "Mozilla/5.0 (compatible; BrainDrive/1.0; +https://braindrive.ai/bot)",
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                    "Accept-Language": "en-US,en;q=0.5",
                    "Accept-Encoding": "gzip, deflate",
                    "Connection": "keep-alive",
                }
            ) as client:
                response = await client.get(url)
                
                if response.status_code != 200:
                    return {
                        "url": url,
                        "success": False,
                        "error": f"HTTP {response.status_code}",
                        "content": ""
                    }
                
                # Check content type
                content_type = response.headers.get("content-type", "").lower()
                if not any(ct in content_type for ct in ["text/html", "application/xhtml", "text/plain"]):
                    return {
                        "url": url,
                        "success": False,
                        "error": f"Unsupported content type: {content_type}",
                        "content": ""
                    }
                
                # Parse HTML and extract text
                soup = BeautifulSoup(response.text, 'html.parser')
                
                # Remove script and style elements
                for script in soup(["script", "style", "nav", "footer", "header", "aside"]):
                    script.decompose()
                
                # Get text content
                text = soup.get_text()
                
                # Clean up text
                lines = (line.strip() for line in text.splitlines())
                chunks = (phrase.strip() for line in lines for phrase in line.split("  "))
                text = ' '.join(chunk for chunk in chunks if chunk)
                
                # Remove excessive whitespace
                text = re.sub(r'\s+', ' ', text).strip()
                
                # Limit content length
                if len(text) > max_content_length:
                    text = text[:max_content_length] + "..."
                
                logger.info(f"✅ Successfully scraped URL", 
                           url=url, 
                           content_length=len(text),
                           user_id=auth.user_id)
                
                return {
                    "url": url,
                    "success": True,
                    "content": text,
                    "content_length": len(text)
                }
                
        except httpx.TimeoutException:
            return {
                "url": url,
                "success": False,
                "error": "Request timeout",
                "content": ""
            }
        except httpx.ConnectError:
            return {
                "url": url,
                "success": False,
                "error": "Connection failed",
                "content": ""
            }
        except Exception as e:
            logger.error(f"Error scraping URL", url=url, error=str(e), user_id=auth.user_id)
            return {
                "url": url,
                "success": False,
                "error": str(e),
                "content": ""
            }
    
    # Scrape all URLs concurrently
    try:
        scraped_results = await asyncio.gather(
            *[scrape_single_url(url) for url in urls],
            return_exceptions=True
        )
        
        # Handle any exceptions that occurred
        final_results = []
        for i, result in enumerate(scraped_results):
            if isinstance(result, Exception):
                final_results.append({
                    "url": urls[i],
                    "success": False,
                    "error": str(result),
                    "content": ""
                })
            else:
                final_results.append(result)
        
        successful_scrapes = sum(1 for r in final_results if r["success"])
        total_content_length = sum(len(r["content"]) for r in final_results if r["success"])
        
        logger.info(f"🕷️ Scraping completed", 
                   total_urls=len(urls),
                   successful=successful_scrapes,
                   total_content_length=total_content_length,
                   user_id=auth.user_id)
        
        return {
            "results": final_results,
            "summary": {
                "total_urls": len(urls),
                "successful_scrapes": successful_scrapes,
                "total_content_length": total_content_length
            }
        }
        
    except Exception as e:
        logger.error(f"Error in bulk scraping", error=str(e), user_id=auth.user_id)
        raise HTTPException(status_code=500, detail="Error during web scraping")

@router.get("/health")
async def search_health() -> Dict[str, Any]:
    """
    Check if the SearXNG service is accessible.
    This endpoint can be used by the frontend to test connectivity.
    """
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get(f"{SEARXNG_BASE_URL}/")
            
            if response.status_code == 200:
                return {
                    "status": "healthy",
                    "searxng_url": SEARXNG_BASE_URL,
                    "accessible": True
                }
            else:
                return {
                    "status": "unhealthy",
                    "searxng_url": SEARXNG_BASE_URL,
                    "accessible": False,
                    "error": f"HTTP {response.status_code}"
                }
                
    except httpx.ConnectError:
        return {
            "status": "unhealthy",
            "searxng_url": SEARXNG_BASE_URL,
            "accessible": False,
            "error": "Connection failed"
        }
    except httpx.TimeoutException:
        return {
            "status": "unhealthy",
            "searxng_url": SEARXNG_BASE_URL,
            "accessible": False,
            "error": "Timeout"
        }
    except Exception as e:
        return {
            "status": "unhealthy",
            "searxng_url": SEARXNG_BASE_URL,
            "accessible": False,
            "error": str(e)
        }