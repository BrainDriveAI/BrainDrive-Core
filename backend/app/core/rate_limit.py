"""
In-process rate limiter for BrainDrive.

Implements a simple sliding window rate limiter using deque for low-friction
abuse protection without requiring external infrastructure (Redis).

Future: Can be swapped to Redis-backed implementation without changing callers.
"""
import time
from collections import defaultdict, deque
from fastapi import HTTPException, status
from typing import Dict, Deque
import threading


class RateLimiter:
    """
    Simple in-memory rate limiter using sliding window algorithm.
    
    Thread-safe for use in async FastAPI applications.
    """
    
    def __init__(self):
        """Initialize the rate limiter with empty buckets."""
        self.buckets: Dict[str, Deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()
    
    def check(self, key: str, limit: int, window_seconds: int) -> None:
        """
        Check if a request should be allowed based on rate limits.
        
        Args:
            key: Identifier for rate limiting (user_id or IP address)
            limit: Maximum number of requests allowed in the window
            window_seconds: Time window in seconds
            
        Raises:
            HTTPException: 429 Too Many Requests if limit exceeded
        """
        now = time.time()
        
        with self._lock:
            bucket = self.buckets[key]
            
            # Remove expired entries (outside the time window)
            while bucket and bucket[0] <= now - window_seconds:
                bucket.popleft()
            
            # Check if limit exceeded
            if len(bucket) >= limit:
                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    detail="Rate limit exceeded. Please try again later."
                )
            
            # Add current timestamp
            bucket.append(now)
    
    def cleanup_old_buckets(self, max_age_seconds: int = 3600) -> int:
        """
        Clean up buckets that haven't been accessed in a while.
        
        This prevents unbounded memory growth from tracking many unique keys.
        Should be called periodically (e.g., via background task).
        
        Args:
            max_age_seconds: Remove buckets with no entries newer than this
            
        Returns:
            Number of buckets cleaned up
        """
        now = time.time()
        cleaned = 0
        
        with self._lock:
            keys_to_remove = []
            
            for key, bucket in self.buckets.items():
                if not bucket or (bucket[-1] <= now - max_age_seconds):
                    keys_to_remove.append(key)
            
            for key in keys_to_remove:
                del self.buckets[key]
                cleaned += 1
        
        return cleaned
    
    def get_remaining(self, key: str, limit: int, window_seconds: int) -> int:
        """
        Get the number of requests remaining for a key.
        
        Useful for adding rate limit headers (X-RateLimit-Remaining, etc.).
        
        Args:
            key: Identifier for rate limiting
            limit: Maximum requests allowed
            window_seconds: Time window in seconds
            
        Returns:
            Number of requests remaining in current window
        """
        now = time.time()
        
        with self._lock:
            bucket = self.buckets[key]
            
            # Remove expired entries
            while bucket and bucket[0] <= now - window_seconds:
                bucket.popleft()
            
            return max(0, limit - len(bucket))


# Global rate limiter instance
rate_limiter = RateLimiter()

