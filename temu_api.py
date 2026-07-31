from flask import Flask, request, jsonify, Response
from flask_cors import CORS
import requests
import json

app = Flask(__name__)
CORS(app)

# 🔑 ضع مفتاح Parse.bot هنا:
PARSE_KEY = "pmx_cb4431400942096bf14e28e501eb7469"

PARSE_API = "https://api.parse.bot/scraper/19417d13-c955-4a31-bfb8-d40635cf048d"

HEADERS = {
    "X-API-Key": PARSE_KEY,
    "Content-Type": "application/json"
}


@app.route("/search")
def search():
    keyword = request.args.get("q", "")
    limit = min(int(request.args.get("limit", 10)), 20)
    
    if not keyword:
        return jsonify({"error": "اكتب كلمة للبحث"}), 400

    payload = {"query": keyword, "limit": limit, "locale": "en", "offset": 0}

    try:
        response = requests.post(
            f"{PARSE_API}/search_products",
            headers=HEADERS,
            json=payload,
            timeout=30
        )
        data = response.json()
        products = data.get("data", {}).get("products", [])
        
        cleaned = []
        for p in products:
            cleaned.append({
                "id": p.get("product_id"),
                "title": p.get("title"),
                "price": p.get("price"),
                "original_price": p.get("market_price"),
                "discount": p.get("discount_percent"),
                "rating": p.get("rating"),
                "review_count": p.get("review_count"),
                "sold_count": p.get("sold_count"),
                "thumbnail": p.get("thumbnail"),
                "product_url": p.get("product_url"),
                "shipping_days": p.get("shipping_days")
            })
        
        return jsonify({
            "status": "success",
            "keyword": keyword,
            "total": len(cleaned),
            "products": cleaned
        })

    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/product")
def product():
    product_id = request.args.get("id", "")
    if not product_id:
        return jsonify({"error": "id required"}), 400

    try:
        detail_resp = requests.post(
            f"{PARSE_API}/get_product_details",
            headers=HEADERS,
            json={"product_id": product_id},
            timeout=30
        )
        detail = detail_resp.json().get("data", {})
        
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
            "title": detail.get("title"),
            "price": detail.get("price"),
            "original_price": detail.get("market_price"),
            "discount": detail.get("discount_percent"),
            "rating": detail.get("rating"),
            "review_count": detail.get("review_count"),
            "sold_count": detail.get("sold_count"),
            "category": detail.get("category"),
            "product_url": detail.get("product_url"),
            "video_url": detail.get("video_url"),
            "images": images,
            "thumbnail": detail.get("thumbnail")
        }
        
        return jsonify({"status": "success", "product": result})

    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/")
def home():
    return jsonify({
        "message": "Temu API - Parse.bot Only",
        "endpoints": {
            "search": "/search?q=keyword&limit=10",
            "product": "/product?id=PRODUCT_ID"
        }
    })


if __name__ == "__main__":
    print("=" * 60)
    print("🚀 Temu API - Parse.bot Only")
    print("📍 http://localhost:5000")
    print("=" * 60)
    app.run(host="0.0.0.0", port=5000, debug=True)