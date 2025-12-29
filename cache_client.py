# This file defines a simple, generic client for a key-value cache.
# For this implementation, it uses a basic in-memory dictionary,
# but it could be easily adapted to use a proper caching backend like Redis.

import json

class CacheClient:
    """
    A simple client for a key-value cache.
    """
    def __init__(self):
        self._cache = {}
        print("Initialized in-memory cache client.")

    def get(self, key):
        """
        Retrieves a value from the cache for the given key.
        Returns None if the key is not found.
        """
        value = self._cache.get(key)
        if value:
            print(f"CACHE HIT for key: {key}")
            return json.loads(value)
        print(f"CACHE MISS for key: {key}")
        return None

    def set(self, key, value, ttl=3600):
        """
        Stores a value in the cache with the given key and an optional TTL (in seconds).
        The TTL is ignored in this in-memory implementation.
        """
        print(f"CACHE SET for key: {key} with TTL: {ttl}")
        self._cache[key] = json.dumps(value)
