#!/usr/bin/env python3
"""
Temu/SHEIN API - Flask Backend using SearchAPI.io
Returns REAL SHEIN products instantly
"""

from flask import Flask, request, jsonify
from flask_cors import CORS
import requests
import json
import os
import time
import logging
import hashlib
from datetime import datetime
from threading import Lock
from urllib.parse import quote

# ───────────────────────────────────────────────
# Logging
# ───────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)s | %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)

# ───────────────────────────────────────────────
# Flask App
# ───────────────────────────────────────────────
app = Flask(__name__)
CORS(app, resources={r"/api/*": {"origins": "*"}})

# ───────────────────────────────────────────────
# SearchAPI.io Configuration
# ───────────────────────────────────────────────
SEARCHAPI_KEY = os.environ.get("SEARCHAPI_KEY", "")
SEARCHAPI_BASE_URL = "https://www.searchapi.io/api/v1/search"

# ───────────────────────────────────────────────
# Cache System
# ───────────────────────────────────────────────
class Cache:
    def __init__(self, duration=300, max_size=1000):
        self._cache = {}
        self._lock = Lock()
        self.duration = duration
        self.max_size = max_size

    def _make_key(self, *args, **kwargs):
        key_data = json.dumps({"args": args, "kwargs": kwargs}, sort_keys=True)
        return hashlib.md5(key_data.encode()).hexdigest()

    def get(self, *args, **kwargs):
        key = self._make_key(*args, **kwargs)
        with self._lock:
            if key in self._cache:
                cached_time, data = self._cache[key]
                if time.time() - cached_time < self.duration:
                    return data
                else:
                    del self._cache[key]
        return None

    def set(self, data, *args, **kwargs):
        key = self._make_key(*args, **kwargs)
        with self._lock:
            if len(self._cache) >= self.max_size:
                oldest = min(self._cache, key=lambda k: self._cache[k][0])
                del self._cache[oldest]
            self._cache[key] = (time.time(), data)

    def stats(self):
        with self._lock:
            return {"size": len(self._cache), "max_size": self.max_size}

cache = Cache()

# ───────────────────────────────────────────────
# Response Helpers
# ───────────────────────────────────────────────
def error_response(message, status_code=500):
    return jsonify({
        "status": "error",
        "message": message,
        "timestamp": datetime.utcnow().isoformat() + "Z"
    }), status_code

def success_response(data, meta=None):
    response = {
        "status": "success",
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "data": data
    }
    if meta:
        response["meta"] = meta
    return jsonify(response)

# ───────────────────────────────────────────────
# SearchAPI.io Request Helper
# ───────────────────────────────────────────────
def searchapi_request(engine, params, timeout=45):
    """Make request to SearchAPI.io"""

    if not SEARCHAPI_KEY:
        return None, "SEARCHAPI_KEY not configured"

    request_params = {
        "engine": engine,
        "api_key": SEARCHAPI_KEY,
        **params
    }

    try:
        logger.info(f"SearchAPI request: engine={engine}, params={params}")

        response = requests.get(
            SEARCHAPI_BASE_URL,
            params=request_params,
            timeout=timeout
        )

        logger.info(f"SearchAPI response: {response.status_code}")

        if response.status_code == 401:
            return None, "Invalid SearchAPI key"

        if response.status_code == 429:
            return None, "Rate limit reached"

        if response.status_code >= 500:
            return None, f"SearchAPI server error: {response.status_code}"

        if response.status_code != 200:
            return None, f"Status {response.status_code}: {response.text[:200]}"

        try:
            return response.json(), None
        except:
            return None, f"Invalid JSON response: {response.text[:200]}"

    except requests.exceptions.Timeout:
        return None, "SearchAPI request timed out"
    except requests.exceptions.RequestException as e:
        return None, f"SearchAPI request failed: {str(e)}"
    except Exception as e:
        return None, f"Unexpected error: {str(e)}"

# ───────────────────────────────────────────────
# Product Parser for SHEIN
# ───────────────────────────────────────────────
def parse_shein_products(data):
    """Parse SearchAPI.io SHEIN response into standardized format"""
    products = []

    if not data or not isinstance(data, dict):
        return products

    # Get products from different possible keys
    items = data.get("products", []) or data.get("results", []) or data.get("items", [])

    for item in items:
        try:
            if not isinstance(item, dict):
                continue

            # Extract price info
            price = item.get("price", item.get("sale_price", 0))
            original_price = item.get("original_price", item.get("retail_price", 0))

            # Calculate discount
            discount = 0
            try:
                p = float(price) if price else 0
                o = float(original_price) if original_price else 0
                if o > 0 and p > 0 and o > p:
                    discount = round((1 - p / o) * 100)
            except:
                pass

            # Get images
            images = item.get("images", [])
            thumbnail = ""
            if images and isinstance(images, list) and len(images) > 0:
                thumbnail = images[0] if isinstance(images[0], str) else images[0].get("url", "")
            elif item.get("main_image"):
                thumbnail = item["main_image"]
            elif item.get("thumbnail"):
                thumbnail = item["thumbnail"]

            product = {
                "id": str(item.get("product_id", item.get("id", item.get("sku", "")))),
                "title": item.get("title", item.get("name", "Product")),
                "price": str(price) if price else "N/A",
                "original_price": str(original_price) if original_price else "",
                "discount_percent": discount,
                "currency": item.get("currency", "USD"),
                "rating": round(float(item.get("rating", item.get("avg_rating", 0)) or 0), 1),
                "review_count": int(item.get("review_count", item.get("reviews", 0)) or 0),
                "sold_count": str(item.get("sold_count", item.get("sales", ""))),
                "thumbnail": thumbnail,
                "product_url": item.get("url", item.get("product_url", "")),
                "category": item.get("category", ""),
                "shop_name": item.get("brand", item.get("seller", "SHEIN")),
                "shop_rating": item.get("brand_rating", 0),
                "free_shipping": item.get("free_shipping", False),
                "tags": item.get("tags", []) or []
            }

            products.append(product)
        except Exception as e:
            logger.warning(f"Parse error: {e}")
            continue

    return products

# ───────────────────────────────────────────────
# API Endpoints
# ───────────────────────────────────────────────

@app.route("/api/search", methods=["GET"])
def search_products():
    """Search products on SHEIN"""
    keyword = request.args.get("q", "").strip()
    limit = min(int(request.args.get("limit", 20)), 50)
    offset = max(int(request.args.get("offset", 0)), 0)
    sort = request.args.get("sort", "relevance")

    if not keyword:
        return error_response("Search keyword is required", 400)

    if len(keyword) < 2:
        return error_response("Keyword must be at least 2 characters", 400)

    # Check cache
    cached = cache.get("search", keyword, limit, offset, sort)
    if cached:
        return success_response(cached["data"], cached.get("meta"))

    # Search via SearchAPI.io
    params = {
        "q": keyword,
        "num": limit
    }

    data, error = searchapi_request("shein_search", params)
    if error:
        return error_response(error, 502)

    products = parse_shein_products(data)
    total = data.get("search_information", {}).get("total_results", len(products))

    result = {
        "keyword": keyword,
        "products": products,
        "total": total,
        "returned": len(products),
        "offset": offset,
        "limit": limit,
        "has_more": len(products) >= limit
    }

    meta = {
        "page": (offset // limit) + 1 if limit > 0 else 1,
        "total_pages": (total + limit - 1) // limit if limit > 0 else 1
    }

    cache.set({"data": result, "meta": meta}, "search", keyword, limit, offset, sort)
    return success_response(result, meta)


@app.route("/api/product/<product_id>", methods=["GET"])
def get_product(product_id):
    """Get product details from SHEIN"""
    if not product_id:
        return error_response("Product ID is required", 400)

    cached = cache.get("product", product_id)
    if cached:
        return success_response(cached["data"])

    # Search for product details
    params = {
        "q": product_id,
        "num": 1
    }

    data, error = searchapi_request("shein_search", params)
    if error:
        return error_response(error, 502)

    products = parse_shein_products(data)

    if products:
        result = products[0]
    else:
        result = {"id": product_id, "error": "Product not found"}

    cache.set({"data": result}, "product", product_id)
    return success_response(result)


@app.route("/api/trending", methods=["GET"])
def get_trending():
    """Get trending products from SHEIN"""
    limit = min(int(request.args.get("limit", 20)), 50)

    cached = cache.get("trending", limit)
    if cached:
        return success_response(cached["data"])

    # Search for trending items
    params = {
        "q": "trending",
        "num": limit
    }

    data, error = searchapi_request("shein_search", params)
    if error:
        return error_response(error, 502)

    products = parse_shein_products(data)

    result = {"products": products[:limit], "total": len(products[:limit])}
    cache.set({"data": result}, "trending", limit)
    return success_response(result)


@app.route("/api/deals", methods=["GET"])
def get_deals():
    """Get deals from SHEIN"""
    limit = min(int(request.args.get("limit", 20)), 50)
    min_discount = int(request.args.get("min_discount", 50))

    cached = cache.get("deals", limit, min_discount)
    if cached:
        return success_response(cached["data"])

    # Search for deals
    params = {
        "q": "sale discount",
        "num": limit
    }

    data, error = searchapi_request("shein_search", params)
    if error:
        return error_response(error, 502)

    products = parse_shein_products(data)

    # Filter by discount
    filtered = [p for p in products if p.get("discount_percent", 0) >= min_discount]

    result = {
        "products": filtered,
        "total": len(filtered),
        "min_discount": min_discount
    }
    cache.set({"data": result}, "deals", limit, min_discount)
    return success_response(result)


@app.route("/api/categories", methods=["GET"])
def get_categories():
    """Get product categories"""
    categories = [
        {"id": "100", "name": "Women's Clothing", "icon": "👗"},
        {"id": "200", "name": "Men's Clothing", "icon": "👔"},
        {"id": "300", "name": "Shoes", "icon": "👟"},
        {"id": "400", "name": "Electronics", "icon": "📱"},
        {"id": "500", "name": "Home & Kitchen", "icon": "🏠"},
        {"id": "600", "name": "Beauty & Health", "icon": "💄"},
        {"id": "700", "name": "Jewelry & Accessories", "icon": "💍"},
        {"id": "800", "name": "Sports & Outdoors", "icon": "⚽"},
        {"id": "900", "name": "Toys & Games", "icon": "🎮"},
        {"id": "1000", "name": "Automotive", "icon": "🚗"},
    ]
    return success_response(categories)


@app.route("/api/health", methods=["GET"])
def health():
    """Health check"""
    return jsonify({
        "status": "healthy",
        "service": "temu-api-searchapi",
        "version": "6.0.0",
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "cache": cache.stats(),
        "searchapi_configured": bool(SEARCHAPI_KEY)
    })


@app.route("/", methods=["GET"])
def home():
    """API Documentation"""
    return jsonify({
        "name": "Temu/SHEIN API - SearchAPI.io Edition",
        "version": "6.0.0",
        "status": "online",
        "provider": "SearchAPI.io",
        "endpoints": {
            "search": "/api/search?q=keyword&limit=20&offset=0",
            "product_details": "/api/product/<id>",
            "trending": "/api/trending?limit=20",
            "deals": "/api/deals?limit=20&min_discount=50",
            "categories": "/api/categories",
            "health": "/api/health"
        }
    })


# ───────────────────────────────────────────────
# Error Handlers
# ───────────────────────────────────────────────
@app.errorhandler(404)
def not_found(error):
    return error_response("Endpoint not found", 404)

@app.errorhandler(500)
def internal_error(error):
    logger.error(f"Internal error: {str(error)}")
    return error_response("Internal server error", 500)


# ───────────────────────────────────────────────
# Main
# ───────────────────────────────────────────────
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    debug = os.environ.get("FLASK_DEBUG", "false").lower() == "true"

    print("=" * 60)
    print("🚀 Temu/SHEIN API v6.0.0 - SearchAPI.io")
    print(f"📍 Port: {port}")
    print(f"🔧 Debug: {debug}")
    print(f"🔑 SearchAPI: {'Configured' if SEARCHAPI_KEY else 'NOT SET'}")
    print("=" * 60)

    app.run(host="0.0.0.0", port=port, debug=debug)
