from flask import Flask, request, jsonify, render_template, Response
from flask_cors import CORS
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
import math
import os
import json
from whitenoise import WhiteNoise

# 定数定義
MAX_HALL_DIMENSION_CM = 15000
MAX_CHAIR_DIMENSION_CM = 500
MAX_CHAIR_COUNT = 50000
AISLE_WIDTH_CM = 100
MAX_SPACING_SEARCH_CM = 100
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

limiter = Limiter(
    app=app,
    key_func=get_remote_address,
    default_limits=["100 per minute"],
    storage_uri=os.environ.get("REDIS_URL"),
    strategy="fixed-window"
)

calculation_cache = {}

def is_colliding(chair_x, chair_y, chair_width, chair_depth, obstacles):
    """イス1脚が、いずれかの障害物と重なっているか判定する"""
    for obs in obstacles:
        if (chair_x < obs['x'] + obs['width'] and
            chair_x + chair_width > obs['x'] and
            chair_y < obs['y'] + obs['depth'] and
            chair_y + chair_depth > obs['y']):
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

        for obs in params["obstacles"]:
            if not all(k in obs for k in ['x', 'y', 'width', 'depth']):
                raise ValueError("障害物のデータ形式が正しくありません。")
            for k, v in obs.items():
                if not isinstance(v, (int, float)) or v < 0:
                    raise ValueError(f"障害物の値 {k}:{v} が不正です。")
        return params
    except (KeyError, TypeError, ValueError) as e:
        raise ValueError(str(e))

def find_optimal_layout(params):
    found = False
    best_max_chairs = 0
    best_layout = {}
    final_layout = {}
    for spacing_x in range(params["min_spacing_x"], MAX_SPACING_SEARCH_CM + 1, SPACING_SEARCH_STEP_CM):
        for spacing_y in range(params["min_spacing_y"], MAX_SPACING_SEARCH_CM + 1, SPACING_SEARCH_STEP_CM):
            space_x = params["chair_width"] + spacing_x
            space_y = params["chair_depth"] + spacing_y
            if space_x == 0 or space_y == 0: continue
            additional_width = space_x / 2 if params["zigzag_layout"] else 0
            effective_hall_width = params["hall_width"]
            if params["add_side_aisles"]:
                effective_hall_width -= AISLE_WIDTH_CM * 2
            if effective_hall_width <= 0: continue
            effective_hall_depth = params["hall_depth"] - params["front_aisle_width"]
            if effective_hall_depth <= 0: continue
            max_cols, max_rows = _calculate_max_rows_cols(params, effective_hall_width, effective_hall_depth, space_x, space_y, additional_width)
            current_max = max_cols * max_rows
            if current_max > best_max_chairs:
                best_max_chairs = current_max
                best_layout = {"cols": max_cols, "rows": max_rows, "spacing_x": spacing_x, "spacing_y": spacing_y, "max": current_max}
            if not found and current_max >= params["num_chairs"]:
                found = True
                final_layout = {"cols": max_cols, "rows": max_rows, "spacing_x": spacing_x, "spacing_y": spacing_y, "max": current_max, "found": True}
    if not final_layout:
        final_layout = best_layout
        final_layout["found"] = False
    return best_layout, final_layout

def _calculate_max_rows_cols(params, effective_hall_width, effective_hall_depth, space_x, space_y, additional_width):
    max_cols, max_rows = 0, 0
    aisle_mode = params["aisle_mode"]
    if aisle_mode == 'every_n':
        aisle_every_x = params["aisle_every_x"]
        if aisle_every_x > 0:
            block_width = aisle_every_x * space_x + AISLE_WIDTH_CM
            num_blocks = math.floor(effective_hall_width / block_width) if block_width > 0 else 0
            remaining_width = effective_hall_width - num_blocks * block_width
            extra_cols = math.floor(remaining_width / space_x) if space_x > 0 else 0
            max_cols = num_blocks * aisle_every_x + extra_cols
        else: max_cols = math.floor(effective_hall_width / space_x) if space_x > 0 else 0
        aisle_every_y = params["aisle_every_y"]
        if aisle_every_y > 0:
            block_depth = aisle_every_y * space_y + AISLE_WIDTH_CM
            num_blocks = math.floor(effective_hall_depth / block_depth) if block_depth > 0 else 0
            remaining_depth = effective_hall_depth - num_blocks * block_depth
            extra_rows = math.floor(remaining_depth / space_y) if space_y > 0 else 0
            max_rows = num_blocks * aisle_every_y + extra_rows
        else: max_rows = math.floor(effective_hall_depth / space_y) if space_y > 0 else 0
    elif aisle_mode == 'fixed_number':
        num_aisles_x = params["num_aisles_x"]
        num_aisles_y = params["num_aisles_y"]
        chair_area_width = effective_hall_width - num_aisles_x * AISLE_WIDTH_CM
        chair_area_depth = effective_hall_depth - num_aisles_y * AISLE_WIDTH_CM
        if chair_area_width > 0 and chair_area_depth > 0:
            available_width = chair_area_width - additional_width
            max_cols = math.floor((available_width + (space_x - params["chair_width"])) / space_x) if space_x > 0 else 0
            max_rows = math.floor((chair_area_depth + (space_y - params["chair_depth"])) / space_y) if space_y > 0 else 0
        else: max_cols, max_rows = 0, 0
    else:
        available_width = effective_hall_width - additional_width
        max_cols = math.floor((available_width + (space_x - params["chair_width"])) / space_x) if space_x > 0 else 0
        max_rows = math.floor((effective_hall_depth + (space_y - params["chair_depth"])) / space_y) if space_y > 0 else 0
    return max_cols, max_rows

def _calculate_total_layout_size(params, layout_info, space_x, space_y, additional_width):
    layout_cols, layout_rows = layout_info["cols"], layout_info["rows"]
    aisle_mode = params["aisle_mode"]
    total_layout_width, total_layout_depth = 0, 0
    if aisle_mode == 'every_n':
        aisle_every_x = params["aisle_every_x"]
        aisle_every_y = params["aisle_every_y"]
        num_aisles_x = (layout_cols - 1) // aisle_every_x if aisle_every_x > 0 else 0
        num_aisles_y = (layout_rows - 1) // aisle_every_y if aisle_every_y > 0 else 0
        total_layout_width = layout_cols * space_x - layout_info["spacing_x"] + num_aisles_x * AISLE_WIDTH_CM + additional_width
        total_layout_depth = layout_rows * space_y - layout_info["spacing_y"] + num_aisles_y * AISLE_WIDTH_CM
    elif aisle_mode == 'fixed_number':
        num_aisles_x = params["num_aisles_x"]
        num_aisles_y = params["num_aisles_y"]
        total_layout_width = layout_cols * space_x - layout_info["spacing_x"] + num_aisles_x * AISLE_WIDTH_CM + additional_width
        total_layout_depth = layout_rows * space_y - layout_info["spacing_y"] + num_aisles_y * AISLE_WIDTH_CM
    else:
        total_layout_width = layout_cols * space_x - layout_info["spacing_x"] + additional_width
        total_layout_depth = layout_rows * space_y - layout_info["spacing_y"]
    return total_layout_width, total_layout_depth

def _get_chair_position(params, layout_info, offset_x, offset_y, space_x, space_y, row, col, zigzag_offset_x):
    x, y = 0, 0
    aisle_mode = params["aisle_mode"]
    if aisle_mode == 'every_n':
        aisle_every_x = params["aisle_every_x"]
        aisle_every_y = params["aisle_every_y"]
        num_preceding_aisles_x = col // aisle_every_x if aisle_every_x > 0 else 0
        num_preceding_