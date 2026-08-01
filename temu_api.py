#!/usr/bin/env python3
"""
Temu API - Flask Backend for MCP Integration
Compatible with Parse.bot scraper API
Supports: search, product details, images, reviews, recommendations
"""

from flask import Flask, request, jsonify, Response
from flask_cors import CORS
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
import requests
import json
import os
import time
import logging
import hashlib
from datetime import datetime, timedelta
from functools import wraps
from threading import Lock

# ───────────────────────────────────────────────
# Logging Configuration
# ───────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)s | %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)

# ───────────────────────────────────────────────
# Flask App Initialization
# ───────────────────────────────────────────────
app = Flask(__name__)
CORS(app, resources={
    r"/api/*": {
        "origins": "*",
        "methods": ["GET", "POST", "OPTIONS"],
        "allow_headers": ["Content-Type", "Authorization", "X-API-Key"]
    }
})

# Rate Limiting
limiter = Limiter(
    app=app,
    key_func=get_remote_address,
    default_limits=["200 per day", "50 per hour"],
    storage_uri="memory://"
)

# ───────────────────────────────────────────────
# Configuration
# ───────────────────────────────────────────────
PARSE_KEY = os.environ.get("PARSE_KEY", "pmx_f046e920ca7dca177c6153fe8250c830")
PARSE_API = os.environ.get("PARSE_API", "https://api.parse.bot/scraper/19417d13-c955-4a31-bfb8-d40635cf048d")

HEADERS = {
    "X-API-Key": PARSE_KEY,
    "Content-Type": "application/json",
    "Accept": "application/json"
}

# Cache Configuration
CACHE_DURATION = int(os.environ.get("CACHE_DURATION", 300))  # 5 minutes
MAX_CACHE_SIZE = int(os.environ.get("MAX_CACHE_SIZE", 1000))

# ───────────────────────────────────────────────
# Thread-Safe Cache System
# ───────────────────────────────────────────────
class Cache:
    def __init__(self, duration=CACHE_DURATION, max_size=MAX_CACHE_SIZE):
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
                    logger.info(f"Cache HIT for key: {key[:8]}...")
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
            logger.info(f"Cache SET for key: {key[:8]}...")

    def clear(self):
        with self._lock:
            self._cache.clear()

    def stats(self):
        with self._lock:
            return {
                "size": len(self._cache),
                "max_size": self.max_size,
                "duration": self.duration
            }

cache = Cache()

# ───────────────────────────────────────────────
# Error Response Helper
# ───────────────────────────────────────────────
def error_response(message, status_code=500, details=None):
    response = {
        "status": "error",
        "message": message,
        "timestamp": datetime.utcnow().isoformat() + "Z"
    }
    if details:
        response["details"] = details
    return jsonify(response), status_code

def success_response(data, meta=None):
    response = {
        "status": "success",
        "timestamp": datetime.utcnow().
isoformat() + "Z",
        "data": data
    }
    if meta:
        response["meta"] = meta
    return jsonify(response)

# ───────────────────────────────────────────────
# API Request Helper
# ───────────────────────────────────────────────
def parse_api_request(endpoint, payload, timeout=30):
    """Send request to Parse.bot API with full error handling"""
    url = f"{PARSE_API}/{endpoint}"
    try:
        logger.info(f"API Request: {endpoint} | Payload: {json.dumps(payload)[:200]}")
        response = requests.post(url, headers=HEADERS, json=payload, timeout=timeout)

        if response.status_code == 429:
            logger.warning(f"Rate limit hit on {endpoint}")
            return None, "Rate limit reached. Please wait and try again.", 429

        if response.status_code == 401:
            logger.error(f"Authentication failed on {endpoint}")
            return None, "API authentication failed. Check your API key.", 401

        if response.status_code == 403:
            return None, "Access forbidden. Check API permissions.", 403

        if response.status_code >= 500:
            logger.error(f"Parse API server error: {response.status_code}")
            return None, f"Parse API server error: {response.status_code}", 502

        if response.status_code != 200:
            return None, f"Parse API returned status {response.status_code}", response.status_code

        data = response.json()
        logger.info(f"API Response: {endpoint} | Status: OK")
        return data, None, 200

    except requests.exceptions.Timeout:
        logger.error(f"Timeout on {endpoint}")
        return None, "Request timed out. The API is taking too long to respond.", 504
    except requests.exceptions.ConnectionError:
        logger.error(f"Connection error on {endpoint}")
        return None, "Cannot connect to Parse API. Check your internet connection.", 502
    except requests.exceptions.RequestException as e:
        logger.error(f"Request error on {endpoint}: {str(e)}")
        return None, f"Network error: {str(e)}", 502
    except json.JSONDecodeError:
        logger.error(f"Invalid JSON response from {endpoint}")
        return None, "Invalid response format from API.", 500

# ───────────────────────────────────────────────
# Product Data Cleaner
# ───────────────────────────────────────────────
def clean_product(p):
    """Standardize product data from Parse API"""
    price = p.get("price")
    original_price = p.get("market_price")

    # Calculate discount if not provided
    discount = p.get("discount_percent", 0)
    if not discount and price and original_price:
        try:
            discount = round((1 - float(price) / float(original_price)) * 100)
        except (ValueError, ZeroDivisionError):
            discount = 0

    return {
        "id": p.get("product_id") or p.get("id"),
        "title": p.get("title", "Untitled Product"),
        "price": price if price else "N/A",
        "original_price": original_price,
        "discount_percent": discount,
        "currency": p.get("currency", "USD"),
        "rating": round(float(p.get("rating", 0) or 0), 1),
        "review_count": int(p.get("review_count", 0) or 0),
        "sold_count": p.get("sold_count", 0),
        "thumbnail": p.get("thumbnail", ""),
        "product_url": p.get("product_url", ""),
        "shipping_days": p.get("shipping_days", ""),
        "category": p.get("category", ""),
        "shop_name": p.get("shop_name", ""),
        "shop_rating": p.get("shop_rating", 0),
        "free_shipping": p.get("free_shipping", False),
        "tags": p.get("tags", [])
    }

# ───────────────────────────────────────────────
# API Endpoints
# ───────────────────────────────────────────────

@app.route("/api/search", methods=["GET"])
@limiter.limit("30 per minute")
def search_products():
    """
    Search products by keyword
    Query params: q (required), limit (default 20, max 50), offset (default 0), sort (default relevance)
    """
    keyword = request.args.get("q", "").strip()
    limit = min(int(request.args.get("limit", 20)), 50)
    offset = max(int(request.args.get("offset", 0)), 0)
    sort = request.args.get("sort", "relevance")  # relevance, price_asc, price_desc, rating, sold
    locale = request.args.get("locale", "en")

    if not keyword:
        return error_response("Search keyword is required", 400)

    if len(keyword) < 2:
        return error_response("Keyword must be at least 2 characters", 400)

    # Check cache
    cached = cache.get("search", keyword, limit, offset, sort, locale)
    if cached:
        return success_response(cached["data"], cached.get("meta"))

    payload = {
        "query": keyword,
        "limit": limit,
        "offset": offset,
        "locale": locale,
        "sort": sort
    }

    data, error, status = parse_api_request("search_products", payload)
    if error:
        return error_response(error, status)

    products_raw = data.get("data", {}).get("products", [])
    total = data.get("data", {}).get("total", len(products_raw))

    cleaned = [clean_product(p) for p in products_raw]

    result = {
        "keyword": keyword,
        "products": cleaned,
        "total": total,
        "returned": len(cleaned),
        "offset": offset,
        "limit": limit,
        "has_more": (offset + len(cleaned)) < total
    }

    meta = {
        "page": (offset // limit) + 1 if limit > 0 else 1,
        "total_pages": (total + limit - 1) // limit if limit > 0 else 1
    }

    cache.set({"data": result, "meta": meta}, "search", keyword, limit, offset, sort, locale)
    return success_response(result, meta)


@app.route("/api/product/<product_id>", methods=["GET"])
@limiter.limit("60 per minute")
def get_product(product_id):
    """
    Get full product details including images
    """
    if not product_id or not product_id.strip():
        return error_response("Product ID is required", 400)

    # Check cache
    cached = cache.get("product", product_id)
    if cached:
        return success_response(cached["data"])

    # Fetch product details
    detail_data, error, status = parse_api_request("get_product_details", {
        "product_id": product_id
    })
    if error:
        return error_response(error, status)

    detail = detail_data.get("data", {})

    # Fetch product images
    img_data, img_error, _ = parse_api_request("get_product_images", {
        "product_ids": json.dumps([str(product_id)])
    })

    images = []
    if not img_error and img_data:
        for item in img_data.get("data", {}).get("results", []):
            if item.get("status") == "success":
                urls = item.get("image_urls", "")
                images = [u.strip() for u in urls.split(",") if u.strip()]

    # Fetch reviews if available
    reviews_data, _, _ = parse_api_request("get_product_reviews", {
        "product_id": product_id,
        "limit": 5
    })
    reviews = []
    if reviews_data:
        reviews = reviews_data.get("data", {}).get("reviews", [])

    result = {
        "id": product_id,
        "title": detail.get("title", "Untitled"),
        "description": detail.get("description", ""),
        "price": detail.get("price", "N/A"),
        "original_price": detail.get("market_price"),
        "discount_percent": detail.get("discount_percent", 0),
        "currency": detail.get("currency", "USD"),
        "rating": round(float(detail.get("rating", 0) or 0), 1),
        "review_count": int(detail.get("review_count", 0) or 0),
        "sold_count": detail.get("sold_count", 0),
        "category": detail.get("category", ""),
        "subcategory": detail.get("subcategory", ""),
        "product_url": detail.get("product_url", ""),
        "video_url": detail.get("video_url", ""),
        "thumbnail": detail.get("thumbnail", ""),
        "images": images,
        "shop": {
            "name": detail.get("shop_name", ""),
            "rating": detail.get("shop_rating", 0),
            "url": detail.get("shop_url", "")
        },
        "shipping": {
            "days": detail.get("shipping_days", ""),
            "free": detail.get("free_shipping", False),
            "cost": detail.
get("shipping_cost", 0)
        },
        "specifications": detail.get("specifications", {}),
        "reviews": reviews,
        "tags": detail.get("tags", [])
    }

    cache.set({"data": result}, "product", product_id)
    return success_response(result)


@app.route("/api/product/<product_id>/images", methods=["GET"])
@limiter.limit("60 per minute")
def get_product_images(product_id):
    """Get product images only"""
    if not product_id:
        return error_response("Product ID is required", 400)

    cached = cache.get("images", product_id)
    if cached:
        return success_response(cached["data"])

    img_data, error, status = parse_api_request("get_product_images", {
        "product_ids": json.dumps([str(product_id)])
    })

    if error:
        return error_response(error, status)

    images = []
    for item in img_data.get("data", {}).get("results", []):
        if item.get("status") == "success":
            urls = item.get("image_urls", "")
            images = [u.strip() for u in urls.split(",") if u.strip()]

    result = {"product_id": product_id, "images": images, "count": len(images)}
    cache.set({"data": result}, "images", product_id)
    return success_response(result)


@app.route("/api/product/<product_id>/reviews", methods=["GET"])
@limiter.limit("30 per minute")
def get_product_reviews(product_id):
    """Get product reviews with pagination"""
    if not product_id:
        return error_response("Product ID is required", 400)

    limit = min(int(request.args.get("limit", 10)), 50)
    offset = max(int(request.args.get("offset", 0)), 0)
    sort = request.args.get("sort", "newest")  # newest, highest, lowest

    payload = {
        "product_id": product_id,
        "limit": limit,
        "offset": offset,
        "sort": sort
    }

    data, error, status = parse_api_request("get_product_reviews", payload)
    if error:
        return error_response(error, status)

    reviews = data.get("data", {}).get("reviews", [])
    total = data.get("data", {}).get("total", len(reviews))

    result = {
        "product_id": product_id,
        "reviews": reviews,
        "total": total,
        "returned": len(reviews),
        "offset": offset,
        "limit": limit,
        "has_more": (offset + len(reviews)) < total
    }

    return success_response(result)


@app.route("/api/product/<product_id>/similar", methods=["GET"])
@limiter.limit("30 per minute")
def get_similar_products(product_id):
    """Get similar/recommended products"""
    if not product_id:
        return error_response("Product ID is required", 400)

    limit = min(int(request.args.get("limit", 10)), 20)

    payload = {
        "product_id": product_id,
        "limit": limit
    }

    data, error, status = parse_api_request("get_similar_products", payload)
    if error:
        return error_response(error, status)

    products_raw = data.get("data", {}).get("products", [])
    cleaned = [clean_product(p) for p in products_raw]

    result = {
        "product_id": product_id,
        "products": cleaned,
        "total": len(cleaned)
    }

    return success_response(result)


@app.route("/api/categories", methods=["GET"])
@limiter.limit("20 per minute")
def get_categories():
    """Get available product categories"""
    cached = cache.get("categories")
    if cached:
        return success_response(cached["data"])

    data, error, status = parse_api_request("get_categories", {})
    if error:
        return error_response(error, status)

    categories = data.get("data", {}).get("categories", [])
    cache.set({"data": categories}, "categories")
    return success_response(categories)


@app.route("/api/category/<category_id>/products", methods=["GET"])
@limiter.limit("30 per minute")
def get_category_products(category_id):
    """Get products by category"""
    limit = min(int(request.args.get("limit", 20)), 50)
    offset = max(int(request.args.get("offset", 0)), 0)
    sort = request.args.get("sort", "relevance")

    payload = {
        "category_id": category_id,
        "limit": limit,
"offset": offset,
        "sort": sort
    }

    data, error, status = parse_api_request("get_category_products", payload)
    if error:
        return error_response(error, status)

    products_raw = data.get("data", {}).get("products", [])
    total = data.get("data", {}).get("total", len(products_raw))
    cleaned = [clean_product(p) for p in products_raw]

    result = {
        "category_id": category_id,
        "products": cleaned,
        "total": total,
        "returned": len(cleaned),
        "offset": offset,
        "limit": limit,
        "has_more": (offset + len(cleaned)) < total
    }

    return success_response(result)


@app.route("/api/trending", methods=["GET"])
@limiter.limit("20 per minute")
def get_trending():
    """Get trending products"""
    limit = min(int(request.args.get("limit", 20)), 50)

    cached = cache.get("trending", limit)
    if cached:
        return success_response(cached["data"])

    payload = {"limit": limit}
    data, error, status = parse_api_request("get_trending_products", payload)
    if error:
        return error_response(error, status)

    products_raw = data.get("data", {}).get("products", [])
    cleaned = [clean_product(p) for p in products_raw]

    result = {"products": cleaned, "total": len(cleaned)}
    cache.set({"data": result}, "trending", limit)
    return success_response(result)


@app.route("/api/deals", methods=["GET"])
@limiter.limit("20 per minute")
def get_deals():
    """Get products with best discounts"""
    limit = min(int(request.args.get("limit", 20)), 50)
    min_discount = int(request.args.get("min_discount", 50))  # Minimum discount %

    cached = cache.get("deals", limit, min_discount)
    if cached:
        return success_response(cached["data"])

    payload = {
        "limit": limit,
        "min_discount": min_discount
    }

    data, error, status = parse_api_request("get_deals", payload)
    if error:
        return error_response(error, status)

    products_raw = data.get("data", {}).get("products", [])
    cleaned = [clean_product(p) for p in products_raw]

    result = {
        "products": cleaned,
        "total": len(cleaned),
        "min_discount": min_discount
    }
    cache.set({"data": result}, "deals", limit, min_discount)
    return success_response(result)


# ───────────────────────────────────────────────
# System Endpoints
# ───────────────────────────────────────────────

@app.route("/", methods=["GET"])
def home():
    """API Home / Documentation"""
    return jsonify({
        "name": "Temu API",
        "version": "2.0.0",
        "status": "online",
        "provider": "Parse.bot",
        "documentation": "/api/docs",
        "endpoints": {
            "search": "/api/search?q=keyword&limit=20&offset=0",
            "product_details": "/api/product/<id>",
            "product_images": "/api/product/<id>/images",
            "product_reviews": "/api/product/<id>/reviews?limit=10&offset=0",
            "similar_products": "/api/product/<id>/similar?limit=10",
            "categories": "/api/categories",
            "category_products": "/api/category/<id>/products?limit=20",
            "trending": "/api/trending?limit=20",
            "deals": "/api/deals?limit=20&min_discount=50",
            "health": "/api/health",
            "cache_stats": "/api/cache/stats",
            "clear_cache": "/api/cache/clear"
        }
    })


@app.route("/api/docs", methods=["GET"])
def docs():
    """API Documentation"""
    return jsonify({
        "title": "Temu API Documentation",
        "base_url": "/api",
        "authentication": "None required (API key managed server-side)",
        "rate_limits": "30-60 requests/minute per endpoint",
        "endpoints": [
            {
                "path": "/search",
                "method": "GET",
                "description": "Search products by keyword",
                "params": {
                    "q": "Search keyword (required, min 2 chars)",
                    "limit": "Results per page (default 20, max 50)",
"offset": "Pagination offset (default 0)",
                    "sort": "Sort order: relevance, price_asc, price_desc, rating, sold",
                    "locale": "Language code (default: en)"
                }
            },
            {
                "path": "/product/<id>",
                "method": "GET",
                "description": "Get full product details with images and reviews"
            },
            {
                "path": "/product/<id>/images",
                "method": "GET",
                "description": "Get product images only"
            },
            {
                "path": "/product/<id>/reviews",
                "method": "GET",
                "description": "Get product reviews",
                "params": {
                    "limit": "Reviews per page (default 10, max 50)",
                    "offset": "Pagination offset",
                    "sort": "newest, highest, lowest"
                }
            },
            {
                "path": "/product/<id>/similar",
                "method": "GET",
                "description": "Get similar/recommended products"
            },
            {
                "path": "/categories",
                "method": "GET",
                "description": "Get all product categories"
            },
            {
                "path": "/category/<id>/products",
                "method": "GET",
                "description": "Get products in a category"
            },
            {
                "path": "/trending",
                "method": "GET",
                "description": "Get trending products"
            },
            {
                "path": "/deals",
                "method": "GET",
                "description": "Get products with best discounts",
                "params": {
                    "min_discount": "Minimum discount percentage (default 50)"
                }
            }
        ]
    })


@app.route("/api/health", methods=["GET"])
def health():
    """Health Check"""
    return jsonify({
        "status": "healthy",
        "service": "temu-api",
        "version": "2.0.0",
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "cache": cache.stats(),
        "parse_api": PARSE_API
    })


@app.route("/api/cache/stats", methods=["GET"])
def cache_stats():
    """Get cache statistics"""
    return jsonify({
        "status": "success",
        "cache": cache.stats()
    })


@app.route("/api/cache/clear", methods=["POST"])
def clear_cache():
    """Clear all cached data"""
    cache.clear()
    return jsonify({"status": "success", "message": "Cache cleared successfully"})


# ───────────────────────────────────────────────
# Error Handlers
# ───────────────────────────────────────────────

@app.errorhandler(404)
def not_found(error):
    return error_response("Endpoint not found. Visit / for API documentation.", 404)

@app.errorhandler(405)
def method_not_allowed(error):
    return error_response("Method not allowed for this endpoint", 405)

@app.errorhandler(429)
def rate_limit_exceeded(error):
    return error_response("Rate limit exceeded. Please slow down your requests.", 429)

@app.errorhandler(500)
def internal_error(error):
    logger.error(f"Internal server error: {str(error)}")
    return error_response("Internal server error occurred", 500)


# ───────────────────────────────────────────────
# Main Entry Point
# ───────────────────────────────────────────────
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    debug = os.environ.get("FLASK_DEBUG", "false").lower() == "true"

    print("=" * 60)
    print("🚀 Temu API v2.0.0 - MCP Ready")
    print(f"📍 Port: {port}")
    print(f"🔧 Debug: {debug}")
    print(f"🔗 Parse API: {PARSE_API}")
    print("=" * 60)

    app.run(host="0.0.0.0", port=port, debug=debug)
