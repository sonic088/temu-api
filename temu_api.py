from flask import Flask, request, jsonify
from flask_cors import CORS
import requests
import json
import os

app = Flask(__name__)
CORS(app)

# 🔑 Parse.bot API Key
PARSE_KEY = "pmx_cb4431400942096bf14e28e501eb7469"
PARSE_API = "https://api.parse.bot/scraper/19417d13-c955-4a31-bfb8-d40635cf048d"

HEADERS = {
    "X-API-Key": PARSE_KEY,
    "Content-Type": "application/json"
}

@app.route("/search")
def search():
    keyword = request.args.get("q", "").strip()
    limit = min(int(request.args.get("limit", 10)), 20)

    if not keyword:
        return jsonify({"error": "Please enter a search keyword"}), 400

    payload = {
        "query": keyword,
        "limit": limit,
        "locale": "en",
        "offset": 0
    }

    try:
        response = requests.post(
            f"{PARSE_API}/search_products",
            headers=HEADERS,
            json=payload,
            timeout=30
        )

        if response.status_code != 200:
            return jsonify({
                "status": "error",
                "message": f"Parse API returned status {response.status_code}"
            }), 500

        data = response.json()
        products = data.get("data", {}).get("products", [])

        if not products:
            return jsonify({
                "status": "success",
                "keyword": keyword,
                "total": 0,
                "products": [],
                "message": "No products found for this keyword"
            })

        cleaned = []
        for p in products:
            cleaned.append({
                "id": p.get("product_id"),
                "title": p.get("title", "No title"),
                "price": p.get("price", "N/A"),
                "original_price": p.get("market_price"),
                "discount": p.get("discount_percent", 0),
                "rating": p.get("rating", 0),
                "review_count": p.get("review_count", 0),
                "sold_count": p.get("sold_count", 0),
                "thumbnail": p.get("thumbnail", ""),
                "product_url": p.get("product_url", ""),
                "shipping_days": p.get("shipping_days", "")
            })

        return jsonify({
            "status": "success",
            "keyword": keyword,
            "total": len(cleaned),
            "products": cleaned
        })

    except requests.exceptions.Timeout:
        return jsonify({
            "status": "error",
            "message": "Request timed out. Please try again."
        }), 504
    except requests.exceptions.RequestException as e:
        return jsonify({
            "status": "error",
            "message": f"Network error: {str(e)}"
        }), 502
    except Exception as e:
        return jsonify({
            "status": "error",
            "message": f"Server error: {str(e)}"
        }), 500

@app.route("/product")
def product():
    product_id = request.args.get("id", "").strip()

    if not product_id:
        return jsonify({"error": "Product ID is required"}), 400

    try:
        # Get product details
        detail_resp = requests.post(
            f"{PARSE_API}/get_product_details",
            headers=HEADERS,
            json={"product_id": product_id},
            timeout=30
        )

        detail = detail_resp.json().get("data", {})

        # Get product images
        img_resp = requests.post(
            f"{PARSE_API}/get_product_images",
            headers=HEADERS,
            json={"product_ids": json.dumps([str(product_id)])},
            timeout=30
        )

        img_data = img_resp.json()

        images = []
        for item in img_data.get("data", {}).get("results", []):
            if item.get("status") == "success":
                urls = item.get("image_urls", "")
                images = [u.strip() for u in urls.split(",") if u.strip()]

        result = {
            "id": product_id,
            "title": detail.get("title", "No title"),
            "price": detail.get("price", "N/A"),
            "original_price": detail.get("market_price"),
            "discount": detail.get("discount_percent", 0),
            "rating": detail.get("rating", 0),
            "review_count": detail.get("review_count", 0),
            "sold_count": detail.get("sold_count", 0),
            "category": detail.get("category", ""),
            "product_url": detail.get("product_url", ""),
            "video_url": detail.get("video_url", ""),
            "images": images,
            "thumbnail": detail.get("thumbnail", "")
        }

        return jsonify({
            "status": "success",
            "product": result
        })

    except requests.exceptions.Timeout:
        return jsonify({
            "status": "error",
            "message": "Request timed out. Please try again."
        }), 504
    except requests.exceptions.RequestException as e:
        return jsonify({
            "status": "error",
            "message": f"Network error: {str(e)}"
        }), 502
    except Exception as e:
        return jsonify({
            "status": "error",
            "message": f"Server error: {str(e)}"
        }), 500

@app.route("/")
def home():
    return jsonify({
        "message": "Temu API - Working",
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
    print("🚀 Temu API - Production Ready")
    print(f"📍 Port: {port}")
    print("=" * 60)
    app.run(host="0.0.0.0", port=port, debug=False)
