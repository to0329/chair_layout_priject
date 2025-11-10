#サーバ軽量化
#後ろのイスから壁の間隔を追加
#間隔のテキストサイズを大きく
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
    storage_uri=os.environ.get("REDIS_URL"), # 本番環境用にRedisなどの外部ストレージを指定
    strategy="fixed-window"
)

# --- キャッシュ設定 ---
calculation_cache = OrderedDict()
MAX_CACHE_ITEMS = 20

def is_colliding(chair_x, chair_y, chair_width, chair_depth, obstacles):
    """
    指定された座標の椅子が、いずれかの障害物と衝突するかどうかを判定します。
    障害物の形状は四角形と円形に対応しています。（sf05_03.pyのロジックに修正）
    """
    for obs in obstacles:
        collides = False
        if obs.get('type') == 'circle':
            # 円と四角形の衝突判定
            cx, cy, r = obs['x'], obs['y'], obs['radius']
            closest_x = max(chair_x, min(cx, chair_x + chair_width))
            closest_y = max(chair_y, min(cy, chair_y + chair_depth))
            
            dist_x = cx - closest_x
            dist_y = cy - closest_y
            distance_squared = (dist_x * dist_x) + (dist_y * dist_y)
            
            if distance_squared < (r * r):
                collides = True
        else: # デフォルトは四角形として扱う
            if (chair_x < obs['x'] + obs['width'] and
                chair_x + chair_width > obs['x'] and
                chair_y < obs['y'] + obs['depth'] and
                chair_y + chair_depth > obs['y']):
                collides = True
        
        if collides:
            return True
    return False

def parse_and_validate_input(data):
    """
    クライアントから受け取ったJSONデータを解析し、型変換や検証を行います。（sf05_03.pyのバリデーションロジックを追加）
    """
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
             raise ValueError(f"会場の幅または奥行きが最大許容値 ({MAX_HALL_DIMENSION_CM}cm, 約{MAX_HALL_DIMENSION_CM / 100}m) を超えています。")
        if params["num_chairs"] > MAX_CHAIR_COUNT:
             raise ValueError(f"イスの数が最大許容値 ({MAX_CHAIR_COUNT}脚) を超えています。")

        # 障害物データの詳細バリデーション
        for obs in params["obstacles"]:
            if 'x' not in obs or 'y' not in obs:
                 raise ValueError("障害物の基本データ(x, y)が不足しています。")

            obs['x'] = float(obs.get('x')) 
            obs['y'] = float(obs.get('y')) 
            
            obs_type = obs.get('type')
            if not obs_type:
                raise ValueError("障害物の基本データ(type)が不足しています。")
            
            if obs_type == 'rectangle':
                if not all(k in obs for k in ['width', 'depth']):
                    raise ValueError("四角形障害物のデータ(width, depth)が不足しています。")
                obs['width'] = int(obs.get('width'))
                obs['depth'] = int(obs.get('depth'))
            elif obs_type == 'circle':
                if 'radius' not in obs:
                    raise ValueError("円形障害物のデータ(radius)が不足しています。")
                obs['radius'] = int(obs.get('radius'))
            else:
                raise ValueError(f"未知の障害物タイプ: {obs_type}")
        
        return params
    except (KeyError, TypeError, ValueError) as e:
        raise ValueError(str(e))

def _calculate_max_rows_cols(params, effective_hall_width, effective_hall_depth, space_x, space_y, additional_width):
    """
    【ヘルパー関数】与えられた条件下で配置可能な最大の列数と行数を計算します。（sf05_03.pyから追加）
    """
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
        else:
            max_cols = math.floor(effective_hall_width / space_x) if space_x > 0 else 0
        
        aisle_every_y = params["aisle_every_y"]
        if aisle_every_y > 0:
            block_depth = aisle_every_y * space_y + AISLE_WIDTH_CM
            num_blocks = math.floor(effective_hall_depth / block_depth) if block_depth > 0 else 0
            remaining_depth = effective_hall_depth - num_blocks * block_depth
            extra_rows = math.floor(remaining_depth / space_y) if space_y > 0 else 0
            max_rows = num_blocks * aisle_every_y + extra_rows
        else:
            max_rows = math.floor(effective_hall_depth / space_y) if space_y > 0 else 0

    elif aisle_mode == 'fixed_number':
        num_aisles_x = params["num_aisles_x"]
        num_aisles_y = params["num_aisles_y"]
        chair_area_width = effective_hall_width - num_aisles_x * AISLE_WIDTH_CM
        chair_area_depth = effective_hall_depth - num_aisles_y * AISLE_WIDTH_CM
        if chair_area_width > 0 and chair_area_depth > 0:
            available_width = chair_area_width - additional_width
            max_cols = math.floor((available_width + (space_x - params["chair_width"])) / space_x) if space_x > 0 else 0
            max_rows = math.floor((chair_area_depth + (space_y - params["chair_depth"])) / space_y) if space_y > 0 else 0
        else:
            max_cols, max_rows = 0, 0

    else:
        available_width = effective_hall_width - additional_width
        max_cols = math.floor((available_width + (space_x - params["chair_width"])) / space_x) if space_x > 0 else 0
        max_rows = math.floor((effective_hall_depth + (space_y - params["chair_depth"])) / space_y) if space_y > 0 else 0
    
    return max_cols, max_rows

def find_optimal_layout(params):
    """
    最適な椅子配置（列数、行数、間隔）を見つけ出すためのメインロジックです。（sf05_03.pyのロジックに修正）
    """
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

            # sf05_03.pyのヘルパー関数を使用
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
        if "max" in final_layout: # best_layoutが見つかっていれば
             final_layout["found"] = False

    return best_layout, final_layout

def _calculate_total_layout_size(params, layout_info, space_x, space_y, additional_width):
    """
    【ヘルパー関数】レイアウト全体の最終的な幅と奥行きを計算します。（sf05_03.pyから追加）
    """
    layout_cols, layout_rows = layout_info["cols"], layout_info["rows"]
    aisle_mode = params["aisle_mode"]
    total_layout_width, total_layout_depth = 0, 0

    if aisle_mode == 'every_n':
        aisle_every_x = params["aisle_every_x"]
        aisle_every_y = params["aisle_every_y"]
        num_aisles_x = (layout_cols - 1) // aisle_every_x if aisle_every_x > 0 else 0
        num_aisles_y = (layout_rows - 1) // aisle_every_y if aisle_every_y > 0 else 0
        # 修正: 最終的な幅・奥行きは、椅子の幅/奥行き + (間隔 + 通路) の合計で計算すべき。
        # sf05_03.pyのロジックを踏襲。
        total_layout_width = layout_cols * params["chair_width"] + (layout_cols - 1) * layout_info["spacing_x"] + num_aisles_x * AISLE_WIDTH_CM + additional_width
        total_layout_depth = layout_rows * params["chair_depth"] + (layout_rows - 1) * layout_info["spacing_y"] + num_aisles_y * AISLE_WIDTH_CM
        
    elif aisle_mode == 'fixed_number':
        num_aisles_x = params["num_aisles_x"]
        num_aisles_y = params["num_aisles_y"]
        total_layout_width = layout_cols * params["chair_width"] + (layout_cols - 1) * layout_info["spacing_x"] + num_aisles_x * AISLE_WIDTH_CM + additional_width
        total_layout_depth = layout_rows * params["chair_depth"] + (layout_rows - 1) * layout_info["spacing_y"] + num_aisles_y * AISLE_WIDTH_CM
    else:
        total_layout_width = layout_cols * params["chair_width"] + (layout_cols - 1) * layout_info["spacing_x"] + additional_width
        total_layout_depth = layout_rows * params["chair_depth"] + (layout_rows - 1) * layout_info["spacing_y"]
    
    return total_layout_width, total_layout_depth

def _get_chair_position(params, layout_info, offset_x, offset_y, space_x, space_y, row, col, zigzag_offset_x):
    """
    【ヘルパー関数】指定された行と列の椅子の座標を計算します。（sf05_03.pyから追加）
    """
    x, y = 0, 0
    aisle_mode = params["aisle_mode"]
    
    # 実際の位置 = offset + (椅子のインデックス * (椅子の寸法 + 間隔)) + 通路のオフセット
    chair_dimension_x = params["chair_width"]
    chair_dimension_y = params["chair_depth"]
    spacing_x = layout_info["spacing_x"]
    spacing_y = layout_info["spacing_y"]

    if aisle_mode == 'every_n':
        aisle_every_x = params["aisle_every_x"]
        aisle_every_y = params["aisle_every_y"]
        
        num_preceding_aisles_x = col // aisle_every_x if aisle_every_x > 0 else 0
        num_preceding_aisles_y = row // aisle_every_y if aisle_every_y > 0 else 0
        
        # 実際の計算: (col * chair_w) + (col * spacing_x) + (aisle_count * aisle_w)
        x = offset_x + col * chair_dimension_x + col * spacing_x + num_preceding_aisles_x * AISLE_WIDTH_CM + zigzag_offset_x
        y = offset_y + row * chair_dimension_y + row * spacing_y + num_preceding_aisles_y * AISLE_WIDTH_CM
        
    elif aisle_mode == 'fixed_number':
        num_aisles_x = params["num_aisles_x"]
        num_aisles_y = params["num_aisles_y"]
        
        # 厳密には、各ブロックの列数/行数を計算する必要があるが、sf05_03.pyの単純化されたロジックを踏襲
        # block_cols = math.ceil(layout_info["cols"] / (num_aisles_x + 1)) 
        # num_preceding_aisles_x = math.floor(col / block_cols) 
        
        # sf05_03.pyのロジック: 単純に列・行ブロックで割って通路数を推定
        cols_per_block = layout_info["cols"] / (num_aisles_x + 1) if num_aisles_x > -1 else layout_info["cols"]
        rows_per_block = layout_info["rows"] / (num_aisles_y + 1) if num_aisles_y > -1 else layout_info["rows"]
        num_preceding_aisles_x = math.floor(col / cols_per_block) if cols_per_block > 0 else 0
        num_preceding_aisles_y = math.floor(row / rows_per_block) if rows_per_block > 0 else 0
        
        x = offset_x + col * chair_dimension_x + col * spacing_x + num_preceding_aisles_x * AISLE_WIDTH_CM + zigzag_offset_x
        y = offset_y + row * chair_dimension_y + row * spacing_y + num_preceding_aisles_y * AISLE_WIDTH_CM
        
    else:
        # 通路なし
        x = offset_x + col * chair_dimension_x + col * spacing_x + zigzag_offset_x
        y = offset_y + row * chair_dimension_y + row * spacing_y

    return x, y

def calculate_chair_coordinates(params, layout_info):
    """
    最終レイアウトに基づき、椅子の座標を計算し、障害物を考慮した結果を返します。（sf05_03.pyのロジックに修正）
    """
    layout_cols, layout_rows = layout_info["cols"], layout_info["rows"]
    space_x = params["chair_width"] + layout_info["spacing_x"]
    space_y = params["chair_depth"] + layout_info["spacing_y"]
    additional_width = space_x / 2 if params["zigzag_layout"] else 0
    
    total_layout_width, total_layout_depth = _calculate_total_layout_size(params, layout_info, space_x, space_y, additional_width)
    
    effective_hall_width = params["hall_width"]
    if params["add_side_aisles"]:
        effective_hall_width -= AISLE_WIDTH_CM * 2

    # 中央揃えのための offset_x の計算
    offset_x = (effective_hall_width - total_layout_width) / 2
    
    # 左右の通路を考慮した offset_x の調整
    if params["add_side_aisles"]:
        offset_x += AISLE_WIDTH_CM
    
    # 前方の通路を考慮した offset_y の計算
    offset_y = params["front_aisle_width"]
    
    all_potential_coords = []
    for row in range(int(layout_rows)):
        zigzag_offset_x = space_x / 2 if params["zigzag_layout"] and row % 2 != 0 else 0
        for col in range(int(layout_cols)):
            # sf05_03.pyのヘルパー関数を使用
            x, y = _get_chair_position(params, layout_info, offset_x, offset_y, space_x, space_y, row, col, zigzag_offset_x)
            all_potential_coords.append((x, y))

    # 1. 障害物との衝突チェック
    coords_after_collision_check = [
        coord for coord in all_potential_coords 
        if not is_colliding(coord[0], coord[1], params["chair_width"], params["chair_depth"], params["obstacles"])
    ]
    collision_skips = len(all_potential_coords) - len(coords_after_collision_check)

    # 2. 障害物の後方Y軸最小間隔チェック (sf05_03.pyから追加)
    coords_to_remove = set()
    spacing_skips = 0
    if params['obstacles']:
        min_y_spacing = params['min_spacing_y']
        chair_w = params['chair_width']
        for obs in params['obstacles']:
            for coord in coords_after_collision_check:
                chair_x, chair_y = coord
                is_below = False
                is_aligned_horizontally = False
                gap = float('inf')

                if obs.get('type') == 'circle':
                    cx, cy, r = obs['x'], obs['y'], obs['radius']
                    closest_y_coord = cy + r
                    is_below = chair_y >= closest_y_coord
                    # 円の水平投影内の椅子のみをチェック
                    is_aligned_horizontally = (chair_x < cx + r) and (chair_x + chair_w > cx - r)
                    if is_below and is_aligned_horizontally:
                        gap = chair_y - closest_y_coord
                else: # rectangle
                    obs_x, obs_y, obs_w, obs_d = obs['x'], obs['y'], obs['width'], obs['depth']
                    closest_y_coord = obs_y + obs_d
                    is_below = chair_y >= closest_y_coord
                    # 矩形の水平投影内の椅子のみをチェック
                    is_aligned_horizontally = (chair_x < obs_x + obs_w) and (chair_x + chair_w > obs_x)
                    if is_below and is_aligned_horizontally:
                        gap = chair_y - closest_y_coord

                if gap < min_y_spacing:
                    coords_to_remove.add(coord)
        
        final_placeable_coords = [coord for coord in coords_after_collision_check if coord not in coords_to_remove]
        spacing_skips = len(coords_to_remove)
    else:
        final_placeable_coords = coords_after_collision_check

    true_max_with_obstacles = len(final_placeable_coords)
    num_chairs_to_draw = min(params["num_chairs"], true_max_with_obstacles)
    # 座標の丸め込みは、描画時に行う (sf07_01.pyの元々のロジックを修正)
    coords_for_display = [[round(x, 1), round(y, 1)] for x, y in final_placeable_coords[:num_chairs_to_draw]]
    
    bottom_gap = 0
    if final_placeable_coords:
        # y座標の最大値（最も奥にある椅子の位置）を求める
        max_y = max(coord[1] for coord in final_placeable_coords[:num_chairs_to_draw])
        # 最後列の椅子の端 (max_y + chair_depth) から会場の奥行き (hall_depth) までの距離
        bottom_gap = params["hall_depth"] - (max_y + params["chair_depth"])
        bottom_gap = max(0, bottom_gap) # 負の値にならないように0未満は0とする

    return {
        "coords_for_display": coords_for_display,
        "total_displayed": len(coords_for_display),
        "true_max": true_max_with_obstacles,
        "collision_skips": collision_skips,
        "spacing_skips": spacing_skips,
        "offset_x": offset_x,
        "offset_y": offset_y,
        "zigzag_offset": space_x / 2 if params["zigzag_layout"] else 0,
        "bottom_gap": bottom_gap, # NEW: 後方の壁との距離を追加
    }

def create_json_response(params, layout_info, coords_data):
    """
    計算結果をまとめ、クライアントに返すためのJSONレスポンスオブジェクトを生成します。（sf05_03.pyのレスポンス形式に修正）
    """
    # sf05_03.pyに合わせて、offset, zigzag_offset, obstacles, skipsを追加
    response_data = {
        "hall_width": params["hall_width"],
        "hall_depth": params["hall_depth"],
        "chair_width": params["chair_width"],
        "chair_depth": params["chair_depth"],
        "coords": coords_data.get("coords_for_display", []),
        "found": layout_info.get("found", False),
        "cols": int(layout_info.get("cols", 0)),
        "rows": int(layout_info.get("rows", 0)),
        "spacing_x": layout_info.get("spacing_x", 0),
        "spacing_y": layout_info.get("spacing_y", 0),
        "total": coords_data.get("total_displayed", 0),
        "max": coords_data.get("true_max", 0),
        "offset_x": int(coords_data.get("offset_x", 0)), # sf05_03.pyと同様にintに丸める
        "offset_y": int(coords_data.get("offset_y", 0)), # sf05_03.pyと同様にintに丸める
        "zigzag_offset": coords_data.get("zigzag_offset", 0),
        "obstacles": params.get("obstacles", []),
        "collision_skips": coords_data.get("collision_skips", 0),
        "spacing_skips": coords_data.get("spacing_skips", 0),
        "bottom_gap": int(coords_data.get("bottom_gap", 0)), # NEW: レスポンスJSONに追加
    }
    return jsonify(response_data)


def calculate_layout_for_specific_grid(params, specific_cols, specific_rows):
    """
    指定された列数・行数が収まる最大のイス間隔を見つけ出し、レイアウトを返す。（sf05_03.pyから追加）
    """
    # 会場の有効な寸法を計算
    effective_hall_width = params["hall_width"]
    if params["add_side_aisles"]:
        effective_hall_width -= AISLE_WIDTH_CM * 2
    effective_hall_depth = params["hall_depth"] - params["front_aisle_width"]

    # 通路の本数と幅を計算
    num_aisles_x, num_aisles_y = 0, 0
    if params["aisle_mode"] == 'every_n':
        aisle_every_x = params.get("aisle_every_x", LARGE_DEFAULT_AISLE_INTERVAL)
        aisle_every_y = params.get("aisle_every_y", LARGE_DEFAULT_AISLE_INTERVAL)
        num_aisles_x = (specific_cols - 1) // aisle_every_x if aisle_every_x > 0 else 0
        num_aisles_y = (specific_rows - 1) // aisle_every_y if aisle_every_y > 0 else 0
    elif params["aisle_mode"] == 'fixed_number':
        num_aisles_x = params["num_aisles_x"]
        num_aisles_y = params["num_aisles_y"]
    
    total_aisle_width = num_aisles_x * AISLE_WIDTH_CM
    total_aisle_depth = num_aisles_y * AISLE_WIDTH_CM

    # --- ここからが新しい計算ロジック ---
    best_spacing_x = -1
    best_spacing_y = -1

    # 1. まず、指定された行が収まる最大の「縦」間隔を探す (広い方から試し、最初に見つかったものが最適解)
    for spacing_y in range(MAX_SPACING_SEARCH_CM, params["min_spacing_y"] - 1, -SPACING_SEARCH_STEP_CM):
        # 必要な全体の奥行きを計算: 椅子_depth * rows + 間隔 * (rows-1) + 通路_depth
        required_depth = (specific_rows * params["chair_depth"]) + ((specific_rows - 1) * spacing_y) + total_aisle_depth
        
        if required_depth <= effective_hall_depth:
            best_spacing_y = spacing_y
            break # 最適な縦間隔が見つかった

    # 2. 次に、指定された列が収まる最大の「横」間隔を探す
    for spacing_x in range(MAX_SPACING_SEARCH_CM, params["min_spacing_x"] - 1, -SPACING_SEARCH_STEP_CM):
        space_x = params["chair_width"] + spacing_x
        # 左右の壁との接触を避けるためのオフセット
        # 厳密には、ジグザグの場合は列数に関わらず + space_x/2 で計算する
        zigzag_offset = space_x / 2 if params["zigzag_layout"] else 0
        
        # 必要な全体の幅を計算: 椅子_width * cols + 間隔 * (cols-1) + 通路_width + ジグザグオフセット
        required_width = (specific_cols * params["chair_width"]) + ((specific_cols - 1) * spacing_x) + total_aisle_width + zigzag_offset
        
        if required_width <= effective_hall_width:
            best_spacing_x = spacing_x
            break # 最適な横間隔が見つかった

    # 3. 縦と横の両方で最適な間隔が見つかった場合、そのレイアウトを返す
    if best_spacing_x != -1 and best_spacing_y != -1:
        return {
            "cols": specific_cols,
            "rows": specific_rows,
            "spacing_x": best_spacing_x,
            "spacing_y": best_spacing_y,
            "max": specific_cols * specific_rows,
            "found": True
        }
    else:
        # 最低間隔でも収まらない場合は、配置不可として返す
        return { "found": False, "max": 0, "cols": specific_cols, "rows": specific_rows }

@app.route("/")
def index():
    return render_template("sf07_01.html")

@app.route("/calculate", methods=["POST"])
@limiter.limit("15 per minute")
def calculate():
    """
    sf05_03.py と同様に calculation_mode を処理するロジックに修正。
    """
    try:
        data = request.get_json()
        cache_key = json.dumps(data, sort_keys=True)
        if cache_key in calculation_cache:
            print("✅ Cache hit!")
            calculation_cache.move_to_end(cache_key)
            return calculation_cache[cache_key]
        
        params = parse_and_validate_input(data)
        calculation_mode = data.get("calculation_mode", "total")
        final_layout = {}
        original_request_failed = False # 代替案を提示したかどうかのフラグ

        if calculation_mode == "specific_grid":
            # specific_cols/rowsがない場合のフォールバック処理を追加
            specific_cols = int(data.get("specific_cols", 0))
            specific_rows = int(data.get("specific_rows", 0))

            if specific_cols <= 0 or specific_rows <= 0:
                 raise ValueError("specific_gridモードでは、有効なspecific_colsとspecific_rowsが必要です。")

            final_layout = calculate_layout_for_specific_grid(params, specific_cols, specific_rows)
            # もし指定された行・列で配置できなかったら...
            if not final_layout.get("found"):
                original_request_failed = True # フラグを立てる
                # ...代わりに、配置可能な最大のレイアウトを探す
                best_layout, _ = find_optimal_layout(params)
                final_layout = best_layout
        else: # "total" モード (sf07_01.pyの元々のロジック)
            best_layout, final_layout_by_num = find_optimal_layout(params)
            # 要求数を満たせなかった場合は、最大配置のレイアウトを使う
            if not final_layout_by_num.get("found"):
                final_layout = best_layout
            else:
                final_layout = final_layout_by_num
        
        # レイアウトが全く見つからなかった場合（best_layoutも空だった場合）のみ、処理を中断する。
        if not final_layout or "cols" not in final_layout:
             return jsonify({
                "found": False, "max": 0, "coords": [],
                "cols": 0, "rows": 0
            })

        # 椅子の全座標を計算し、真の最大数を算出
        coords_data = calculate_chair_coordinates(params, final_layout)
        
        # sf05_03.pyに合わせてレスポンスを生成
        response = create_json_response(params, final_layout, coords_data)
        response_data = response.get_json()
        response_data['original_request_failed'] = original_request_failed # sf05_03.pyのフラグを追加
        response.set_data(json.dumps(response_data))

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
    # sf05_03.pyの実行ロジックに合わせてポート10000やthreaded=Falseを削除
    app.run(host="0.0.0.0", debug=debug_mode)