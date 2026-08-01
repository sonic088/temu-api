#!/usr/bin/env python3
"""
Temu API - Flask Backend using Bright Data Web Unlocker API
Uses Bright Data's API endpoint directly - NO proxy needed
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
# Bright Data Web Unlocker API Configuration
# ───────────────────────────────────────────────
BRIGHTDATA_API_TOKEN = os.environ.get("BRIGHTDATA_API_TOKEN", "")
BRIGHTDATA_ZONE = os.environ.get("BRIGHTDATA_ZONE", "web_unlocker2")

# Bright Data Web Unlocker API endpoint
BRIGHTDATA_API_URL = "https://api.brightdata.com/request"

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
# Bright Data Web Unlocker API Request
# ───────────────────────────────────────────────
def brightdata_request(target_url, method="GET", headers=None, timeout=60):
    """Make request through Bright Data Web Unlocker API"""

    if not BRIGHTDATA_API_TOKEN:
        return None, "BRIGHTDATA_API_TOKEN not configured"

    payload = {
        "zone": BRIGHTDATA_ZONE,
        "url": target_url,
        "method": method,
        "format": "raw"
    }

    if headers:
        payload["headers"] = headers

    try:
        logger.info(f"Bright Data request: {target_url[:100]}...")

        response = requests.post(
            BRIGHTDATA_API_URL,
            headers={
                "Authorization": f"Bearer {BRIGHTDATA_API_TOKEN}",
                "Content-Type": "application/json"
            },
            json=payload,
            timeout=timeout
        )

        logger.info(f"Bright Data response status: {response.status_code}")

        if response.status_code == 401:
            return None, "Invalid API token"

        if response.status_code == 403:
            return None, "Access forbidden - check zone permissions"

        if response.status_code == 429:
            return None, "Rate limit reached"

        if response.status_code >= 500:
            return None, f"Server error: {response.status_code}"

        if response.status_code != 200:
            return None, f"Status {response.status_code}"

        # Try JSON first
        try:
            return response.json(), None
        except:
            return {"raw_html": response.text}, None

    except requests.exceptions.Timeout:
        return None, "Request timed out"
    except requests.exceptions.RequestException as e:
        return None, f"Request failed: {str(e)}"
    except Exception as e:
        return None, f"Error: {str(e)}"

# ───────────────────────────────────────────────
# Temu Search via Bright Data
# ───────────────────────────────────────────────
def scrape_temu_search(keyword, limit=20, offset=0):
    """Search Temu products using Bright Data"""

    encoded_keyword = quote(keyword)

    urls = [
        f"https://www.temu.com/api/search?search_key={encoded_keyword}&page={offset//limit + 1}&size={limit}",
        f"https://www.temu.com/search_result.html?search_key={encoded_keyword}",
    ]

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "application/json, text/html, */*",
        "Accept-Language": "en-US,en;q=0.9",
    }

    for url in urls:
        data, error = brightdata_request(url, headers=headers)
        if error:
            logger.warning(f"URL failed: {url[:80]}... | Error: {error}")
            continue

        if data:
            if isinstance(data, dict) and ("goods_list" in data or "items" in data or "result" in data):
                return data, None

            if "raw_html" in data:
                html = data["raw_html"]
                patterns = [
                    r'window\.__INITIAL_STATE__\s*=\s*({.*?});',
                    r'window\._SSR_HYDRATED_DATA\s*=\s*({.*?});',
                    r'"goodsList":\s*(\[.*?\])',
                ]
                for pattern in patterns:
                    match = re.search(pattern, html, re.DOTALL)
                    if match:
                        try:
                            json_data = json.loads(match.group(1))
                            return json_data, None
                        except:
                            continue

    return None, "Could not fetch products from Temu"

# ───────────────────────────────────────────────
# Product Parser
# ───────────────────────────────────────────────
def parse_temu_products(raw_data):
    """Parse Temu response into standardized format"""
    products = []

    if not raw_data:
        return products

    items = []

    if isinstance(raw_data, dict):
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
        elif "goodsList" in raw_data:
            items = raw_data["goodsList"]
        elif "searchResult" in raw_data:
            items = raw_data["searchResult"].get("goodsList", [])
    elif isinstance(raw_data, list):
        items = raw_data

    for item in items:
        try:
            if not isinstance(item, dict):
                continue

            product = {
                "id": str(item.get("goods_id", item.get("id", item.get("goodsId", "")))),
                "title": item.get("goods_name", item.get("title", item.get("goodsName", "Untitled Product"))),
                "price": str(item.get("price", item.get("sale_price", item.get("min_on_sale_price", item.get("salePrice", "N/A"))))),
                "original_price": str(item.get("market_price", item.get("original_price", item.get("marketPrice", "")))),
                "discount_percent": 0,
                "currency": item.get("currency", "USD"),
                "rating": round(float(item.get("avg_star", item.get("rating", item.get("avgStar", 0))) or 0), 1),
                "review_count": int(item.get("comment_num", item.get("review_count", item.get("commentNum", 0))) or 0),
                "sold_count": str(item.get("sales_tip", item.get("sold_count", item.get("salesTip", "")))),
                "thumbnail": item.get("thumb_url", item.get("image", item.get("img_url", item.get("thumbUrl", "")))),
                "product_url": f"https://www.temu.com/item.html?goods_id={item.get('goods_id', item.get('id', item.get('goodsId', '')))}",
                "shipping_days": item.get("shipping_days", ""),
                "category": item.get("cat_id", item.get("category", "")),
                "shop_name": item.get("mall_name", item.get("shop_name", item.get("mallName", ""))),
                "shop_rating": item.get("mall_rating", item.get("shop_rating", 0)),
                "free_shipping": item.get("is_free_shipping", item.get("freeShipping", False)),
                "tags": item.get("tag_list", item.get("tags", [])) or []
            }

            try:
                price = float(product["price"]) if product["price"] and product["price"] != "N/A" else 0
                original = float(product["original_price"]) if product["original_price"] else 0
                if original > 0 and price > 0 and original > price:
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

    cached = cache.get("search", keyword, limit, offset, sort)
    if cached:
        return success_response(cached["data"], cached.get("meta"))

    raw_data, error = scrape_temu_search(keyword, limit, offset)
    if error:
        return error_response(error, 502)

    products = parse_temu_products(raw_data)
    total = len(products)

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
    """Get product details"""
    if not product_id:
        return error_response("Product ID is required", 400)

    cached = cache.get("product", product_id)
    if cached:
        return success_response(cached["data"])

    url = f"https://www.temu.com/item.html?goods_id={product_id}"

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }

    data, error = brightdata_request(url, headers=headers)
    if error:
        return error_response(error, 502)

    if "raw_html" in data:
        html = data["raw_html"]
        patterns = [
            r'window\.__INITIAL_STATE__\s*=\s*({.*?});',
            r'window\._SSR_HYDRATED_DATA\s*=\s*({.*?});',
        ]
        product_data = None
        for pattern in patterns:
            match = re.search(pattern, html, re.DOTALL)
            if match:
                try:
                    product_data = json.loads(match.group(1))
                    break
                except:
                    continue

        if product_data:
            result = {
                "id": product_id,
                "title": product_data.get("goodsName", product_data.get("title", "Untitled")),
                "description": product_data.get("goodsDesc", product_data.get("description", "")),
                "price": str(product_data.get("salePrice", product_data.get("price", "N/A"))),
                "original_price": str(product_data.get("marketPrice", product_data.get("original_price", ""))),
                "currency": "USD",
                "rating": round(float(product_data.get("avgStar", product_data.get("rating", 0)) or 0), 1),
                "review_count": int(product_data.get("commentNum", product_data.get("review_count", 0)) or 0),
                "sold_count": str(product_data.get("salesTip", product_data.get("sold_count", ""))),
                "thumbnail": product_data.get("thumbUrl", product_data.get("thumbnail", "")),
                "images": product_data.get("viewImageList", product_data.get("images", [])),
                "shop": {
                    "name": product_data.get("mallName", product_data.get("shop_name", "")),
                    "rating": product_data.get("mallRating", product_data.get("shop_rating", 0)),
                },
                "shipping": {
                    "free": product_data.get("isFreeShipping", product_data.get("free_shipping", False)),
                },
                "specifications": product_data.get("specs", product_data.get("specifications", {})),
                "reviews": [],
                "tags": product_data.get("tagList", product_data.get("tags", []))
            }
        else:
            result = {"id": product_id, "error": "Could not extract product data"}
    else:
        result = {"id": product_id, "data": data}

    cache.set({"data": result}, "product", product_id)
    return success_response(result)


@app.route("/api/trending", methods=["GET"])
def get_trending():
    """Get trending products"""
    limit = min(int(request.args.get("limit", 20)), 50)

    cached = cache.get("trending", limit)
    if cached:
        return success_response(cached["data"])

    url = "https://www.temu.com/"

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "text/html,application/json",
    }

    data, error = brightdata_request(url, headers=headers)
    if error:
        return error_response(error, 502)

    products = []
    if "raw_html" in data:
        html = data["raw_html"]
        patterns = [
            r'window\.__INITIAL_STATE__\s*=\s*({.*?});',
            r'"goodsList":\s*(\[.*?\])',
        ]
        for pattern in patterns:
            match = re.search(pattern, html, re.DOTALL)
            if match:
                try:
                    json_data = json.loads(match.group(1))
                    products = parse_temu_products(json_data)
                    break
                except:
                    continue

    result = {"products": products[:limit], "total": len(products[:limit])}
    cache.set({"data": result}, "trending", limit)
    return success_response(result)


@app.route("/api/deals", methods=["GET"])
def get_deals():
    """Get deals"""
    limit = min(int(request.args.get("limit", 20)), 50)
    min_discount = int(request.args.get("min_discount", 50))

    cached = cache.get("deals", limit, min_discount)
    if cached:
        return success_response(cached["data"])

    url = "https://www.temu.com/flash_sale.html"

    data, error = brightdata_request(url)
    if error:
        return error_response(error, 502)

    products = []
    if "raw_html" in data:
        html = data["raw_html"]
        patterns = [
            r'window\.__INITIAL_STATE__\s*=\s*({.*?});',
            r'"goodsList":\s*(\[.*?\])',
        ]
        for pattern in patterns:
            match = re.search(pattern, html, re.DOTALL)
            if match:
                try:
                    json_data = json.loads(match.group(1))
                    products = parse_temu_products(json_data)
                    break
                except:
                    continue

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
    """Get categories"""
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
        "version": "3.2.0",
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "cache": cache.stats(),
        "brightdata_zone": BRIGHTDATA_ZONE,
        "api_token_configured": bool(BRIGHTDATA_API_TOKEN)
    })


@app.route("/", methods=["GET"])
def home():
    """API Documentation"""
    return jsonify({
        "name": "Temu API - Bright Data Web Unlocker",
        "version": "3.2.0",
        "status": "online",
        "provider": "Bright Data",
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
    print("🚀 Temu API v3.2.0 - Bright Data Web Unlocker")
    print(f"📍 Port: {port}")
    print(f"🔧 Debug: {debug}")
    print(f"🔗 Zone: {BRIGHTDATA_ZONE}")
    print(f"🔑 API Token: {'Configured' if BRIGHTDATA_API_TOKEN else 'NOT SET'}")
    print("=" * 60)

    app.run(host="0.0.0.0", port=port, debug=debug)
