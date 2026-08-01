#!/usr/bin/env python3
"""
Temu API - Async Flask Backend using Bright Data
Returns cached data immediately, scrapes in background
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
import threading
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
BRIGHTDATA_API_TOKEN = os.environ.get("BRIGHTDATA_API_TOKEN", "")
BRIGHTDATA_ZONE = os.environ.get("BRIGHTDATA_ZONE", "web_unlocker2")
BRIGHTDATA_API_URL = "https://api.brightdata.com/request"

# ───────────────────────────────────────────────
# Cache System
# ───────────────────────────────────────────────
class Cache:
    def __init__(self, duration=600, max_size=500):
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
# Background Scraping
# ───────────────────────────────────────────────
def brightdata_request_async(target_url, method="GET", headers=None, timeout=120):
    """Make request through Bright Data API - background thread"""

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
        logger.info(f"BD async request: {target_url[:80]}...")

        response = requests.post(
            BRIGHTDATA_API_URL,
            headers={
                "Authorization": f"Bearer {BRIGHTDATA_API_TOKEN}",
                "Content-Type": "application/json"
            },
            json=payload,
            timeout=timeout
        )

        logger.info(f"BD async response: {response.status_code}")

        if response.status_code != 200:
            return None, f"Status {response.status_code}"

        try:
            return response.json(), None
        except:
            return {"raw_html": response.text}, None

    except Exception as e:
        return None, f"Error: {str(e)}"

# ───────────────────────────────────────────────
# Product Parser
# ───────────────────────────────────────────────
def parse_temu_products(raw_data):
    """Parse Temu response"""
    products = []

    if not raw_data or not isinstance(raw_data, dict):
        return products

    items = []

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

    for item in items:
        try:
            if not isinstance(item, dict):
                continue

            price = item.get("price", item.get("sale_price", item.get("salePrice", item.get("min_on_sale_price", 0))))
            original = item.get("market_price", item.get("original_price", item.get("marketPrice", 0)))

            discount = 0
            try:
                p = float(price) if price else 0
                o = float(original) if original else 0
                if o > 0 and p > 0 and o > p:
                    discount = round((1 - p / o) * 100)
            except:
                pass

            product = {
                "id": str(item.get("goods_id", item.get("id", item.get("goodsId", "")))),
                "title": item.get("goods_name", item.get("title", item.get("goodsName", "Product"))),
                "price": str(price) if price else "N/A",
                "original_price": str(original) if original else "",
                "discount_percent": discount,
                "currency": item.get("currency", "USD"),
                "rating": round(float(item.get("avg_star", item.get("rating", item.get("avgStar", 0))) or 0), 1),
                "review_count": int(item.get("comment_num", item.get("review_count", item.get("commentNum", 0))) or 0),
                "sold_count": str(item.get("sales_tip", item.get("sold_count", item.get("salesTip", "")))),
                "thumbnail": item.get("thumb_url", item.get("image", item.get("img_url", item.get("thumbUrl", "")))),
                "product_url": f"https://www.temu.com/item.html?goods_id={item.get('goods_id', item.get('id', item.get('goodsId', '')))}",
                "category": item.get("cat_id", item.get("category", "")),
                "shop_name": item.get("mall_name", item.get("shop_name", item.get("mallName", ""))),
                "shop_rating": item.get("mall_rating", item.get("shop_rating", 0)),
                "free_shipping": item.get("is_free_shipping", item.get("freeShipping", False)),
                "tags": item.get("tag_list", item.get("tags", [])) or []
            }

            products.append(product)
        except Exception as e:
            logger.warning(f"Parse error: {e}")
            continue

    return products

def scrape_and_cache(keyword, limit, offset):
    """Background scraping function"""
    try:
        encoded_keyword = quote(keyword)
        url = f"https://www.temu.com/search_result.html?search_key={encoded_keyword}"

        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept": "text/html,application/json",
            "Accept-Language": "en-US,en;q=0.9",
        }

        data, error = brightdata_request_async(url, headers=headers, timeout=120)
        if error:
            logger.error(f"Background scrape error: {error}")
            return

        if data and "raw_html" in data:
            html = data["raw_html"]
            patterns = [
                r'window\.__INITIAL_STATE__\s*=\s*({.*?});',
                r'window\._SSR_HYDRATED_DATA\s*=\s*({.*?});',
                r'"goodsList":\s*(\[.*?\])',
                r'"searchResult":\s*({.*?})',
            ]
            for pattern in patterns:
                match = re.search(pattern, html, re.DOTALL)
                if match:
                    try:
                        json_data = json.loads(match.group(1))
                        products = parse_temu_products(json_data)

                        result = {
                            "keyword": keyword,
                            "products": products,
                            "total": len(products),
                            "returned": len(products),
                            "offset": offset,
                            "limit": limit,
                            "has_more": len(products) >= limit
                        }

                        meta = {
                            "page": (offset // limit) + 1 if limit > 0 else 1,
                            "total_pages": (len(products) + limit - 1) // limit if limit > 0 else 1
                        }

                        cache.set({"data": result, "meta": meta}, "search", keyword, limit, offset)
                        logger.info(f"Cached {len(products)} products for '{keyword}'")
                        break
                    except Exception as e:
                        logger.warning(f"JSON parse error: {e}")
                        continue
    except Exception as e:
        logger.error(f"Background thread error: {e}")

# ───────────────────────────────────────────────
# API Endpoints
# ───────────────────────────────────────────────

@app.route("/api/search", methods=["GET"])
def search_products():
    """Search products - returns cached data immediately, scrapes in background"""
    keyword = request.args.get("q", "").strip()
    limit = min(int(request.args.get("limit", 20)), 50)
    offset = max(int(request.args.get("offset", 0)), 0)

    if not keyword or len(keyword) < 2:
        return error_response("Keyword required (min 2 chars)", 400)

    # Check cache first
    cached = cache.get("search", keyword, limit, offset)
    if cached:
        # Start background scraping to refresh cache
        thread = threading.Thread(
            target=scrape_and_cache,
            args=(keyword, limit, offset),
            daemon=True
        )
        thread.start()

        return success_response(cached["data"], cached.get("meta"))

    # No cache - try quick scrape with short timeout
    encoded_keyword = quote(keyword)
    url = f"https://www.temu.com/search_result.html?search_key={encoded_keyword}"

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "text/html,application/json",
    }

    try:
        # Quick request with 20 second timeout
        data, error = brightdata_request_async(url, headers=headers, timeout=20)

        if data and "raw_html" in data:
            html = data["raw_html"]
            products = []

            for pattern in [r'window\.__INITIAL_STATE__\s*=\s*({.*?});', r'"goodsList":\s*(\[.*?\])']:
                match = re.search(pattern, html, re.DOTALL)
                if match:
                    try:
                        json_data = json.loads(match.group(1))
                        products = parse_temu_products(json_data)
                        break
                    except:
                        continue

            result = {
                "keyword": keyword,
                "products": products,
                "total": len(products),
                "returned": len(products),
                "offset": offset,
                "limit": limit,
                "has_more": len(products) >= limit
            }

            meta = {
                "page": (offset // limit) + 1 if limit > 0 else 1,
                "total_pages": (len(products) + limit - 1) // limit if limit > 0 else 1
            }

            cache.set({"data": result, "meta": meta}, "search", keyword, limit, offset)
            return success_response(result, meta)

        # If quick scrape failed, start background and return empty
        thread = threading.Thread(
            target=scrape_and_cache,
            args=(keyword, limit, offset),
            daemon=True
        )
        thread.start()

        return success_response({
            "keyword": keyword,
            "products": [],
            "total": 0,
            "returned": 0,
            "offset": offset,
            "limit": limit,
            "has_more": False,
            "message": "Scraping in progress, please try again in 30 seconds"
        })

    except Exception as e:
        # Start background scraping
        thread = threading.Thread(
            target=scrape_and_cache,
            args=(keyword, limit, offset),
            daemon=True
        )
        thread.start()

        return success_response({
            "keyword": keyword,
            "products": [],
            "total": 0,
            "returned": 0,
            "offset": offset,
            "limit": limit,
            "has_more": False,
            "message": "Scraping in progress, please try again in 30 seconds"
        })


@app.route("/api/product/<product_id>", methods=["GET"])
def get_product(product_id):
    """Get product details"""
    if not product_id:
        return error_response("Product ID required", 400)

    cached = cache.get("product", product_id)
    if cached:
        return success_response(cached["data"])

    return success_response({
        "id": product_id,
        "message": "Product details scraping in progress"
    })


@app.route("/api/trending", methods=["GET"])
def get_trending():
    """Get trending"""
    limit = min(int(request.args.get("limit", 20)), 50)

    cached = cache.get("trending", limit)
    if cached:
        return success_response(cached["data"])

    return success_response({
        "products": [],
        "total": 0,
        "message": "Trending data scraping in progress"
    })


@app.route("/api/deals", methods=["GET"])
def get_deals():
    """Get deals"""
    limit = min(int(request.args.get("limit", 20)), 50)
    min_discount = int(request.args.get("min_discount", 50))

    cached = cache.get("deals", limit, min_discount)
    if cached:
        return success_response(cached["data"])

    return success_response({
        "products": [],
        "total": 0,
        "min_discount": min_discount,
        "message": "Deals scraping in progress"
    })


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
        "service": "temu-api-async",
        "version": "4.0.0",
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "cache": cache.stats(),
        "brightdata_zone": BRIGHTDATA_ZONE,
        "api_token_configured": bool(BRIGHTDATA_API_TOKEN)
    })


@app.route("/", methods=["GET"])
def home():
    """API Documentation"""
    return jsonify({
        "name": "Temu API - Async Bright Data",
        "version": "4.0.0",
        "status": "online",
        "endpoints": {
            "search": "/api/search?q=keyword&limit=20",
            "product": "/api/product/<id>",
            "trending": "/api/trending?limit=20",
            "deals": "/api/deals?limit=20",
            "categories": "/api/categories",
            "health": "/api/health"
        }
    })


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
    print("🚀 Temu API v4.0.0 - Async Bright Data")
    print(f"📍 Port: {port}")
    print(f"🔗 Zone: {BRIGHTDATA_ZONE}")
    print(f"🔑 Token: {'Configured' if BRIGHTDATA_API_TOKEN else 'NOT SET'}")
    print("=" * 60)

    app.run(host="0.0.0.0", port=port, debug=debug)
