#!/usr/bin/env python3
"""
Temu API - Flask Backend using Bright Data Web Unlocker
Scrapes Temu.com directly for real product data
"""

from flask import Flask, request, jsonify
from flask_cors import CORS
import requests
import json
import os
import re
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
# Bright Data Configuration
# ───────────────────────────────────────────────
BRIGHTDATA_USERNAME = os.environ.get("BRIGHTDATA_USERNAME", "abdesslam.gh17@gmail.com")
BRIGHTDATA_PASSWORD = os.environ.get("BRIGHTDATA_PASSWORD", "a0a42834-06a7-4dbf-8b8f-bfef8ddcb187")
BRIGHTDATA_HOST = os.environ.get("BRIGHTDATA_HOST", "brd.superproxy.io")
BRIGHTDATA_PORT = os.environ.get("BRIGHTDATA_PORT", "22225")

PROXY_URL = f"http://{BRIGHTDATA_USERNAME}:{BRIGHTDATA_PASSWORD}@{BRIGHTDATA_HOST}:{BRIGHTDATA_PORT}"
PROXIES = {
    "http": PROXY_URL,
    "https": PROXY_URL
}

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
# Temu Scraper using Bright Data
# ───────────────────────────────────────────────
def scrape_temu_search(keyword, limit=20, offset=0):
    """Scrape Temu search results using Bright Data proxy"""

    encoded_keyword = quote(keyword)
    url = f"https://www.temu.com/api/search?search_key={encoded_keyword}&page={offset//limit + 1}&size={limit}"

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": f"https://www.temu.com/search_result.html?search_key={encoded_keyword}",
    }

    try:
        logger.info(f"Scraping Temu: keyword='{keyword}', limit={limit}, offset={offset}")

        response = requests.get(
            url,
            headers=headers,
            proxies=PROXIES,
            timeout=60,
            verify=True
        )

        logger.info(f"Response status: {response.status_code}")

        if response.status_code != 200:
            logger.error(f"Temu returned status {response.status_code}")
            return None, f"Temu returned status {response.status_code}"

        # Try to parse JSON response
        try:
            data = response.json()
        except:
            # If not JSON, try to extract from HTML
            html = response.text
            # Look for JSON data in script tags
            match = re.search(r'window\.__INITIAL_STATE__\s*=\s*({.*?});', html, re.DOTALL)
            if match:
                data = json.loads(match.group(1))
            else:
                return None, "Could not parse response"

        return data, None

    except requests.exceptions.ProxyError as e:
        logger.error(f"Proxy error: {str(e)}")
        return None, "Proxy connection failed. Check Bright Data credentials."
    except requests.exceptions.Timeout:
        logger.error("Request timeout")
        return None, "Request timed out"
    except Exception as e:
        logger.error(f"Error: {str(e)}")
        return None, f"Error: {str(e)}"

# ───────────────────────────────────────────────
# Product Parser
# ───────────────────────────────────────────────
def parse_temu_products(raw_data):
    """Parse raw Temu response into standardized product format"""
    products = []

    if not raw_data:
        return products

    # Try different response structures
    items = []

    if isinstance(raw_data, dict):
        # Try common Temu response keys
        if "result" in raw_data and isinstance(raw_data["result"], dict):
            items = raw_data["result"].get("goods_list", []) or raw_data["result"].get("items", [])
        elif "goods_list" in raw_data:
            items = raw_data["goods_list"]
        elif "items" in raw_data:
            items = raw_data["items"]
        elif "data" in raw_data and isinstance(raw_data["data"], dict):
            items = raw_data["data"].get("goods_list", []) or raw_data["data"].get("items", [])
        elif "data" in raw_data and isinstance(raw_data["data"], list):
            items = raw_data["data"]
    elif isinstance(raw_data, list):
        items = raw_data

    for item in items:
        try:
            product = {
                "id": str(item.get("goods_id", item.get("id", ""))),
                "title": item.get("goods_name", item.get("title", "Untitled Product")),
                "price": str(item.get("price", item.get("sale_price", item.get("min_on_sale_price", "N/A")))),
                "original_price": str(item.get("market_price", item.get("original_price", ""))),
                "discount_percent": 0,
                "currency": item.get("currency", "USD"),
                "rating": round(float(item.get("avg_star", item.get("rating", 0)) or 0), 1),
                "review_count": int(item.get("comment_num", item.get("review_count", 0)) or 0),
                "sold_count": item.get("sales_tip", item.get("sold_count", "")),
                "thumbnail": item.get("thumb_url", item.get("image", item.get("img_url", ""))),
                "product_url": f"https://www.temu.com/item.html?goods_id={item.get('goods_id', item.get('id', ''))}",
                "shipping_days": item.get("shipping_days", ""),
                "category": item.get("cat_id", ""),
                "shop_name": item.get("mall_name", item.get("shop_name", "")),
                "shop_rating": item.get("mall_rating", 0),
                "free_shipping": item.get("is_free_shipping", False),
                "tags": item.get("tag_list", []) or []
            }

            # Calculate discount
            try:
                price = float(product["price"]) if product["price"] and product["price"] != "N/A" else 0
                original = float(product["original_price"]) if product["original_price"] else 0
                if original > 0 and price > 0:
                    product["discount_percent"] = round((1 - price / original) * 100)
            except:
                pass

            products.append(product)
        except Exception as e:
            logger.warning(f"Failed to parse product: {e}")
            continue

    return products

# ───────────────────────────────────────────────
# API Endpoints
# ───────────────────────────────────────────────

@app.route("/api/search", methods=["GET"])
def search_products():
    """Search products on Temu"""
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

    # Scrape Temu
    raw_data, error = scrape_temu_search(keyword, limit, offset)
    if error:
        return error_response(error, 502)

    products = parse_temu_products(raw_data)
    total = len(products)  # Temu doesn't always return total count

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
    """Get product details from Temu"""
    if not product_id:
        return error_response("Product ID is required", 400)

    cached = cache.get("product", product_id)
    if cached:
        return success_response(cached["data"])

    # Scrape product page
    url = f"https://www.temu.com/item.html?goods_id={product_id}"

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    }

    try:
        response = requests.get(url, headers=headers, proxies=PROXIES, timeout=60)
        html = response.text

        # Extract product data from HTML
        # Look for JSON data in script tags
        match = re.search(r'window\.__INITIAL_STATE__\s*=\s*({.*?});', html, re.DOTALL)
        if match:
            data = json.loads(match.group(1))
            # Parse product details...
            result = {
                "id": product_id,
                "title": data.get("goodsName", "Untitled"),
                "description": data.get("goodsDesc", ""),
                "price": str(data.get("salePrice", "N/A")),
                "original_price": str(data.get("marketPrice", "")),
                "currency": "USD",
                "rating": round(float(data.get("avgStar", 0) or 0), 1),
                "review_count": int(data.get("commentNum", 0) or 0),
                "sold_count": data.get("salesTip", ""),
                "thumbnail": data.get("thumbUrl", ""),
                "images": data.get("viewImageList", []),
                "shop": {
                    "name": data.get("mallName", ""),
                    "rating": data.get("mallRating", 0),
                },
                "shipping": {
                    "free": data.get("isFreeShipping", False),
                },
                "specifications": data.get("specs", {}),
                "reviews": [],
                "tags": data.get("tagList", [])
            }
        else:
            result = {"id": product_id, "error": "Could not extract product data"}

        cache.set({"data": result}, "product", product_id)
        return success_response(result)

    except Exception as e:
        return error_response(f"Failed to fetch product: {str(e)}", 502)


@app.route("/api/trending", methods=["GET"])
def get_trending():
    """Get trending products from Temu"""
    limit = min(int(request.args.get("limit", 20)), 50)

    cached = cache.get("trending", limit)
    if cached:
        return success_response(cached["data"])

    # Scrape Temu homepage/trending
    url = "https://www.temu.com/api/home/recommend"

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "application/json",
        "Referer": "https://www.temu.com/",
    }

    try:
        response = requests.get(url, headers=headers, proxies=PROXIES, timeout=60)
        data = response.json()
        products = parse_temu_products(data)

        result = {"products": products[:limit], "total": len(products[:limit])}
        cache.set({"data": result}, "trending", limit)
        return success_response(result)

    except Exception as e:
        return error_response(f"Failed to fetch trending: {str(e)}", 502)


@app.route("/api/deals", methods=["GET"])
def get_deals():
    """Get deals from Temu"""
    limit = min(int(request.args.get("limit", 20)), 50)
    min_discount = int(request.args.get("min_discount", 50))

    cached = cache.get("deals", limit, min_discount)
    if cached:
        return success_response(cached["data"])

    # Scrape Temu deals page
    url = f"https://www.temu.com/api/deals?limit={limit}"

    try:
        response = requests.get(url, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept": "application/json",
        }, proxies=PROXIES, timeout=60)

        data = response.json()
        products = parse_temu_products(data)

        # Filter by discount
        filtered = [p for p in products if p.get("discount_percent", 0) >= min_discount]

        result = {
            "products": filtered,
            "total": len(filtered),
            "min_discount": min_discount
        }
        cache.set({"data": result}, "deals", limit, min_discount)
        return success_response(result)

    except Exception as e:
        return error_response(f"Failed to fetch deals: {str(e)}", 502)


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
        "service": "temu-api-brightdata",
        "version": "3.0.0",
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "cache": cache.stats(),
        "proxy": "brightdata-web-unlocker"
    })


@app.route("/", methods=["GET"])
def home():
    """API Documentation"""
    return jsonify({
        "name": "Temu API - Bright Data Edition",
        "version": "3.0.0",
        "status": "online",
        "provider": "Bright Data Web Unlocker",
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
    print("🚀 Temu API v3.0.0 - Bright Data Web Unlocker")
    print(f"📍 Port: {port}")
    print(f"🔧 Debug: {debug}")
    print(f"🔗 Proxy: {BRIGHTDATA_HOST}:{BRIGHTDATA_PORT}")
    print("=" * 60)

    app.run(host="0.0.0.0", port=port, debug=debug)
