#!/usr/bin/env python3
"""
Temu API - Ultimate Flask Backend using ScrapingBee
Fast, reliable, real Temu product data
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
# ScrapingBee Configuration
# ───────────────────────────────────────────────
SCRAPINGBEE_API_KEY = os.environ.get("SCRAPINGBEE_API_KEY", "")
SCRAPINGBEE_API_URL = "https://app.scrapingbee.com/api/v1"

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
# ScrapingBee Request Helper
# ───────────────────────────────────────────────
def scrapingbee_request(target_url, render_js=False, premium_proxy=False, timeout=30):
    """Make request through ScrapingBee API"""

    if not SCRAPINGBEE_API_KEY:
        return None, "SCRAPINGBEE_API_KEY not configured"

    params = {
        "api_key": SCRAPINGBEE_API_KEY,
        "url": target_url,
    }

    if render_js:
        params["render_js"] = "true"
    if premium_proxy:
        params["premium_proxy"] = "true"

    try:
        logger.info(f"ScrapingBee request: {target_url[:80]}...")

        response = requests.get(
            SCRAPINGBEE_API_URL,
            params=params,
            timeout=timeout
        )

        logger.info(f"ScrapingBee response: {response.status_code}")

        if response.status_code == 401:
            return None, "Invalid ScrapingBee API key"

        if response.status_code == 403:
            return None, "Access forbidden - check API key permissions"

        if response.status_code == 429:
            return None, "Rate limit reached on ScrapingBee"

        if response.status_code >= 500:
            return None, f"ScrapingBee server error: {response.status_code}"

        if response.status_code != 200:
            return None, f"ScrapingBee returned status {response.status_code}"

        return {"raw_html": response.text}, None

    except requests.exceptions.Timeout:
        return None, "ScrapingBee request timed out"
    except requests.exceptions.RequestException as e:
        return None, f"ScrapingBee request failed: {str(e)}"
    except Exception as e:
        return None, f"Unexpected error: {str(e)}"

# ───────────────────────────────────────────────
# Temu Search via ScrapingBee
# ───────────────────────────────────────────────
def scrape_temu_search(keyword, limit=20, offset=0):
    """Search Temu products using ScrapingBee"""

    encoded_keyword = quote(keyword)

    # Use Temu search page with JS rendering for full content
    url = f"https://www.temu.com/search_result.html?search_key={encoded_keyword}"

    data, error = scrapingbee_request(
        url,
        render_js=True,
        premium_proxy=True,
        timeout=30
    )

    if error:
        return None, error

    if data and "raw_html" in data:
        html = data["raw_html"]

        # Try multiple patterns to extract product data
        patterns = [
            r'window\\.__INITIAL_STATE__\\\s*=\\s*({.*?});',
            r'window\\._SSR_HYDRATED_DATA\\\s*=\\s*({.*?});',
            r'"goodsList":\\\s*(\\[.*?\\])',
            r'"searchResult":\\s*({.*?})',
            r'"goods_list":\\s*(\[.*?\])',
        ]

        for pattern in patterns:
            match = re.search(pattern, html, re.DOTALL)
            if match:
                try:
                    json_data = json.loads(match.group(1))
                    return json_data, None
                except:
                    continue

        # If no JSON found, return raw for debugging
        return {"raw_html_preview": html[:3000]}, None

    return data, None

# ───────────────────────────────────────────────
# Product Parser
# ───────────────────────────────────────────────
def parse_temu_products(raw_data):
    """Parse Temu response into standardized product format"""
    products = []

    if not raw_data or not isinstance(raw_data, dict):
        return products

    items = []

    # Try multiple response structures
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

            # Extract price
            price = item.get("price", item.get("sale_price", item.get("salePrice", item.get("min_on_sale_price", 0))))
            original = item.get("market_price", item.get("original_price", item.get("marketPrice", 0)))

            # Calculate discount
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

    # Scrape Temu via ScrapingBee
    raw_data, error = scrape_temu_search(keyword, limit, offset)
    if error:
        return error_response(error, 502)

    # Check if raw HTML (no products found)
    if raw_data and "raw_html_preview" in raw_data:
        return success_response({
            "keyword": keyword,
            "products": [],
            "total": 0,
            "returned": 0,
            "offset": offset,
            "limit": limit,
            "has_more": False,
            "debug": "Could not extract products from Temu HTML"
        })

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
    """Get product details from Temu"""
    if not product_id:
        return error_response("Product ID is required", 400)

    cached = cache.get("product", product_id)
    if cached:
        return success_response(cached["data"])

    # Scrape product page
    url = f"https://www.temu.com/item.html?goods_id={product_id}"

    data, error = scrapingbee_request(url, render_js=True, premium_proxy=True, timeout=30)
    if error:
        return error_response(error, 502)

    # Extract product data
    if "raw_html" in data:
        html = data["raw_html"]
        patterns = [
            r'window\\.__INITIAL_STATE__\\\s*=\\s*({.*?});',
            r'window\\._SSR_HYDRATED_DATA\\\s*=\\s*({.*?});',
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
    """Get trending products from Temu"""
    limit = min(int(request.args.get("limit", 20)), 50)

    cached = cache.get("trending", limit)
    if cached:
        return success_response(cached["data"])

    # Scrape Temu homepage
    url = "https://www.temu.com/"

    data, error = scrapingbee_request(url, render_js=True, premium_proxy=True, timeout=30)
    if error:
        return error_response(error, 502)

    products = []
    if "raw_html" in data:
        html = data["raw_html"]
        patterns = [
            r'window\\.__INITIAL_STATE__\\\s*=\\s*({.*?});',
            r'"goodsList":\\\s*(\\[.*?\\])',
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
    """Get deals from Temu"""
    limit = min(int(request.args.get("limit", 20)), 50)
    min_discount = int(request.args.get("min_discount", 50))

    cached = cache.get("deals", limit, min_discount)
    if cached:
        return success_response(cached["data"])

    # Scrape Temu deals page
    url = "https://www.temu.com/flash_sale.html"

    data, error = scrapingbee_request(url, render_js=True, premium_proxy=True, timeout=30)
    if error:
        return error_response(error, 502)

    products = []
    if "raw_html" in data:
        html = data["raw_html"]
        patterns = [
            r'window\\.__INITIAL_STATE__\\\s*=\\s*({.*?});',
            r'"goodsList":\\\s*(\\[.*?\\])',
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
        "service": "temu-api-scrapingbee",
        "version": "5.0.0",
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "cache": cache.stats(),
        "scrapingbee_configured": bool(SCRAPINGBEE_API_KEY)
    })


@app.route("/", methods=["GET"])
def home():
    """API Documentation"""
    return jsonify({
        "name": "Temu API - ScrapingBee Edition",
        "version": "5.0.0",
        "status": "online",
        "provider": "ScrapingBee",
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
    print("🚀 Temu API v5.0.0 - ScrapingBee")
    print(f"📍 Port: {port}")
    print(f"🔧 Debug: {debug}")
    print(f"🔑 ScrapingBee: {'Configured' if SCRAPINGBEE_API_KEY else 'NOT SET'}")
    print("=" * 60)

    app.run(host="0.0.0.0", port=port, debug=debug)
