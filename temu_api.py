from flask import Flask, request, jsonify
from flask_cors import CORS
import requests
import json
import os
import re
import time

app = Flask(__name__)
CORS(app)

# 🔑 ScraperAPI Key
SCRAPER_API_KEY = "32dad6f22bf378d2bee20e862ff6f4b9"

def search_temu(keyword, limit=10):
    """Search Temu products using ScraperAPI"""

    # Temu search URL
    temu_url = f"https://www.temu.com/search_result.html?search_key={keyword}&search_type=normal"

    # ScraperAPI URL
    scraper_url = "https://api.scraperapi.com"

    params = {
        "api_key": SCRAPER_API_KEY,
        "url": temu_url,
        "render": "true",
        "country_code": "us"
    }

    try:
        response = requests.get(scraper_url, params=params, timeout=60)

        if response.status_code != 200:
            return {
                "status": "error",
                "message": f"ScraperAPI returned status {response.status_code}"
            }

        html = response.text

        # Extract product data from HTML using regex
        products = []

        # Pattern 1: Product data in JSON format
        json_pattern = r'"goodsId":"(\d+)".*?"title":"([^"]+)".*?"price":"([^"]+)".*?"marketPrice":"([^"]*)".*?"thumbUrl":"([^"]+)"'
        matches = re.findall(json_pattern, html, re.DOTALL)

        for match in matches[:limit]:
            goods_id, title, price, market_price, thumb = match

            # Calculate discount
            discount = 0
            if market_price and float(market_price) > 0:
                discount = int((1 - float(price) / float(market_price)) * 100)

            products.append({
                "id": goods_id,
                "title": title.replace("\\u0026", "&").replace("\\n", " "),
                "price": f"${price}",
                "original_price": f"${market_price}" if market_price else None,
                "discount": f"-{discount}%" if discount > 0 else None,
                "rating": 4.5,
                "review_count": 0,
                "sold_count": 0,
                "thumbnail": thumb.replace("\\u0026", "&"),
                "product_url": f"https://www.temu.com/goods.html?goods_id={goods_id}",
                "shipping_days": "7-15"
            })

        # Pattern 2: Alternative product pattern
        if not products:
            alt_pattern = r'data-goods-id="(\d+)".*?alt="([^"]+)".*?data-price="([^"]+)"'
            alt_matches = re.findall(alt_pattern, html, re.DOTALL)

            for match in alt_matches[:limit]:
                goods_id, title, price = match
                products.append({
                    "id": goods_id,
                    "title": title,
                    "price": f"${price}",
                    "original_price": None,
                    "discount": None,
                    "rating": 4.5,
                    "review_count": 0,
                    "sold_count": 0,
                    "thumbnail": "",
                    "product_url": f"https://www.temu.com/goods.html?goods_id={goods_id}",
                    "shipping_days": "7-15"
                })

        return {
            "status": "success",
            "keyword": keyword,
            "total": len(products),
            "products": products
        }

    except requests.exceptions.Timeout:
        return {
            "status": "error",
            "message": "Request timed out. Please try again."
        }
    except requests.exceptions.RequestException as e:
        return {
            "status": "error",
            "message": f"Network error: {str(e)}"
        }
    except Exception as e:
        return {
            "status": "error",
            "message": f"Server error: {str(e)}"
        }

# Cache to reduce API calls
cache = {}
CACHE_DURATION = 300  # 5 minutes

@app.route("/search")
def search():
    keyword = request.args.get("q", "").strip()
    limit = min(int(request.args.get("limit", 10)), 20)

    if not keyword:
        return jsonify({"error": "Please enter a search keyword"}), 400

    # Check cache
    cache_key = f"{keyword}_{limit}"
    if cache_key in cache:
        cached_time, cached_data = cache[cache_key]
        if time.time() - cached_time < CACHE_DURATION:
            return jsonify(cached_data)

    # Search Temu
    result = search_temu(keyword, limit)

    # Cache successful results
    if result.get("status") == "success":
        cache[cache_key] = (time.time(), result)

    return jsonify(result)

@app.route("/product")
def product():
    product_id = request.args.get("id", "").strip()

    if not product_id:
        return jsonify({"error": "Product ID is required"}), 400

    return jsonify({
        "status": "success",
        "product": {
            "id": product_id,
            "title": "Product details coming soon",
            "price": "N/A",
            "product_url": f"https://www.temu.com/goods.html?goods_id={product_id}"
        }
    })

@app.route("/")
def home():
    return jsonify({
        "message": "Temu API - Working with ScraperAPI",
        "status": "online",
        "endpoints": {
            "search": "/search?q=keyword&limit=10",
            "product": "/product?id=PRODUCT_ID"
        }
    })

@app.route("/health")
def health():
    return jsonify({
        "status": "healthy",
        "service": "temu-api"
    })

# Use PORT from environment variable (Render provides this)
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    print("=" * 60)
    print("🚀 Temu API - ScraperAPI Version")
    print(f"📍 Port: {port}")
    print("=" * 60)
    app.run(host="0.0.0.0", port=port, debug=False)
