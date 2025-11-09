from flask import Flask, request, jsonify, render_template, Response
from flask_cors import CORS
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from collections import OrderedDict
import math
import os
import json
from whitenoise import WhiteNoise
import gc

# --- 定数定義 ---
MAX_HALL_DIMENSION_CM = 15000
MAX_CHAIR_DIMENSION_CM = 500
MAX_CHAIR_COUNT = 50000
AISLE_WIDTH_CM = 100
MAX_SPACING_SEARCH_CM = 300
SPACING_SEARCH_STEP_CM = 5
LARGE_DEFAULT_AISLE_INTERVAL = 10**9

app = Flask(__name__)
app.wsgi_app = WhiteNoise(app.wsgi_app, root='static/')
allowed_origins = [
    "https://chair-layout.onrender.com",
    "http://127.0.0.1:5500",
    "http://localhost:5000",
    "null"
]
CORS(app, resources={r"/calculate": {"origins": allowed_origins}})

# --- Limiter設定 ---
limiter = Limiter(
    key_func=get_remote_address,
    app=app,
    default_limits=["15 per minute"],
    storage_uri=None,
)

# --- キャッシュ設定 ---
calculation_cache = OrderedDict()
MAX_CACHE_ITEMS = 20

def is_colliding(chair_x, chair_y, chair_width, chair_depth, obstacles):
    for obs in obstacles:
        collides = False
        if obs.get('type') == 'circle':
            cx, cy, r = obs['x'], obs['y'], obs['radius']
            closest_x = max(chair_x, min(cx, chair_x + chair_width))
            closest_y = max(chair_y, min(cy, chair_y + chair_depth))
            if (cx - closest_x)**2 + (cy - closest_y)**2 < r**2:
                collides = True
        else:
            if (chair_x < obs['x'] + obs['width'] and chair_x + chair_width > obs['x'] and
                chair_y < obs['y'] + obs['depth'] and chair_y + chair_depth > obs['y']):
                collides = True
        if collides:
            return True
    return False

def parse_and_validate_input(data):
    try:
        params = {
            "hall_width": float(data["hall_width"]),
            "hall_depth": float(data["hall_depth"]),
            "chair_width": int(data["chair_width"]),
            "chair_depth": int(data["chair_depth"]),
            "num_chairs": int(data["num_chairs"]),
            "aisle_mode": data.get("aisle_mode", "none"),
            "add_side_aisles": data.get("add_side_aisles", False),
            "zigzag_layout": data.get("zigzag_layout", False),
            "aisle_every_x": data.get("aisle_every_x", LARGE_DEFAULT_AISLE_INTERVAL),
            "aisle_every_y": data.get("aisle_every_y", LARGE_DEFAULT_AISLE_INTERVAL),
            "num_aisles_x": data.get("num_aisles_x", 0),
            "num_aisles_y": data.get("num_aisles_y", 0),
            "front_aisle_width": int(data.get("front_aisle_width", 100)),
            "min_spacing_x": int(data.get("min_spacing_x", 20)),
            "min_spacing_y": int(data.get("min_spacing_y", 100)),
            "obstacles": data.get("obstacles", [])
        }
        if params["hall_width"] > MAX_HALL_DIMENSION_CM or params["hall_depth"] > MAX_HALL_DIMENSION_CM:
            raise ValueError(f"会場サイズが最大値 ({MAX_HALL_DIMENSION_CM}cm) を超えています。")
        if params["num_chairs"] > MAX_CHAIR_COUNT:
            raise ValueError(f"椅子数が最大値 ({MAX_CHAIR_COUNT}脚) を超えています。")
        return params
    except (KeyError, TypeError, ValueError) as e:
        raise ValueError(str(e))

def find_optimal_layout(params):
    best_layout, final_layout = {}, {}
    best_max_chairs, found = 0, False
    for spacing_x in range(params["min_spacing_x"], MAX_SPACING_SEARCH_CM + 1, SPACING_SEARCH_STEP_CM):
        for spacing_y in range(params["min_spacing_y"], MAX_SPACING_SEARCH_CM + 1, SPACING_SEARCH_STEP_CM):
            space_x = params["chair_width"] + spacing_x
            space_y = params["chair_depth"] + spacing_y
            if space_x == 0 or space_y == 0:
                continue
            additional_width = space_x / 2 if params["zigzag_layout"] else 0
            effective_w = params["hall_width"] - (AISLE_WIDTH_CM * 2 if params["add_side_aisles"] else 0)
            effective_d = params["hall_depth"] - params["front_aisle_width"]
            if effective_w <= 0 or effective_d <= 0:
                continue
            cols = int(effective_w // (space_x))
            rows = int(effective_d // (space_y))
            current_max = cols * rows
            if current_max > best_max_chairs:
                best_max_chairs = current_max
                best_layout = {"cols": cols, "rows": rows, "spacing_x": spacing_x, "spacing_y": spacing_y, "max": current_max}
            if not found and current_max >= params["num_chairs"]:
                found = True
                final_layout = {**best_layout, "found": True}
    if not final_layout:
        final_layout = {**best_layout, "found": False}
    return best_layout, final_layout

def calculate_chair_coordinates(params, layout_info):
    cols, rows = layout_info["cols"], layout_info["rows"]
    space_x = params["chair_width"] + layout_info["spacing_x"]
    space_y = params["chair_depth"] + layout_info["spacing_y"]
    coords = []
    for r in range(rows):
        zigzag_offset = (space_x / 2) if params["zigzag_layout"] and r % 2 else 0
        for c in range(cols):
            x = c * space_x + zigzag_offset
            y = r * space_y + params["front_aisle_width"]
            coords.append((x, y))
    coords_trimmed = coords[:10000]
    coords_compressed = [[round(x, 1), round(y, 1)] for x, y in coords_trimmed]
    return {"coords_for_display": coords_compressed, "total_displayed": len(coords_compressed), "true_max": len(coords)}

def create_json_response(params, layout_info, coords_data):
    return jsonify({
        "hall_width": params["hall_width"],
        "hall_depth": params["hall_depth"],
        "chair_width": params["chair_width"],
        "chair_depth": params["chair_depth"],
        "coords": coords_data["coords_for_display"],
        "found": layout_info.get("found", False),
        "cols": layout_info.get("cols", 0),
        "rows": layout_info.get("rows", 0),
        "spacing_x": layout_info.get("spacing_x", 0),
        "spacing_y": layout_info.get("spacing_y", 0),
        "total": coords_data.get("total_displayed", 0),
        "max": coords_data.get("true_max", 0),
    })

@app.route("/")
def index():
    return render_template("sf05_03.html")

@app.route("/calculate", methods=["POST"])
@limiter.limit("15 per minute")
def calculate():
    try:
        data = request.get_json()
        cache_key = json.dumps(data, sort_keys=True)
        if cache_key in calculation_cache:
            print("✅ Cache hit!")
            calculation_cache.move_to_end(cache_key)
            return calculation_cache[cache_key]
        params = parse_and_validate_input(data)
        best_layout, final_layout = find_optimal_layout(params)
        if not final_layout or "cols" not in final_layout:
            return jsonify({"found": False, "max": 0, "coords": [], "cols": 0, "rows": 0})
        coords_data = calculate_chair_coordinates(params, final_layout)
        response = create_json_response(params, final_layout, coords_data)
        calculation_cache[cache_key] = response
        if len(calculation_cache) > MAX_CACHE_ITEMS:
            calculation_cache.popitem(last=False)
        gc.collect()
        return response
    except ValueError as e:
        return jsonify({"error": f"入力内容が不正です: {e}"}), 400
    except Exception as e:
        print(f"An unexpected error occurred: {e}")
        return jsonify({"error": "サーバー内部で予期しないエラーが発生しました。"}), 500

if __name__ == "__main__":
    debug_mode = os.environ.get("FLASK_DEBUG") == '1'
    app.run(host="0.0.0.0", port=10000, threaded=False, debug=debug_mode)
