#障害物の追加
from flask import Flask, request, jsonify, render_template, send_from_directory, Response
from flask_cors import CORS #PythonとHTML間の通信
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
import matplotlib
matplotlib.use('Agg') #AggはWebサーバなどのGUIのない環境でMatplotlibが使える
# ▼▼▼ 変更 ▼▼▼
# import matplotlib.pyplot as plt #グラフ生成の命令を簡単に行う方法を指定
# ▲▲▲ 変更 ▲▲▲
import io #図の一時的に保管するメモリを提供
import base64 #画像データを文字にエンコードする
import math #様々な数学計算が使えるライブラリ
import matplotlib.ticker as mticker #目盛りをカスタマイズするためのライブラリ
import os
from whitenoise import WhiteNoise
# ▼▼▼ 追加 ▼▼▼
import json # キャッシュのキーを生成するために追加
# ▲▲▲ 追加 ▲▲▲

# ▼▼▼ 定数定義 ▼▼▼

# --- バリデーション（入力値の制限） ---
MAX_HALL_DIMENSION_CM = 15000 #会場の最大サイズ (150m)
MAX_CHAIR_DIMENSION_CM = 500  #イスの最大サイズ (5m)
MAX_CHAIR_COUNT = 50000      #イスの最大数（5万脚）

# --- レイアウト計算 ---
# ▼▼▼ 変更: イスの最小間隔と最前列通路幅はHTMLから指定するため、ここの定数定義を削除 ▼▼▼
# MIN_SPACING_X_CM = 20  #イスの最小横間隔 (20cm)
# MIN_SPACING_Y_CM = 100 #イスの最小縦間隔 (100cm) 兼 最前列の通路幅
AISLE_WIDTH_CM = 100   #通路の幅 (100cm)
# ▲▲▲ 変更 ▲▲▲

# --- 探索アルゴリズム ---
MAX_SPACING_SEARCH_CM = 100    #間隔を探す際の最大値 (cm)
SPACING_SEARCH_STEP_CM = 5     #間隔を探す際の刻み幅 (cm)
LARGE_DEFAULT_AISLE_INTERVAL = 10**9  #aisle_every_x/y が未入力の場合に使用する、非常に大きな値
MAX_COLS_ROWS_TO_CHECK = 1500 #ループの計算量が増えすぎないようにするための安全装置



app = Flask(__name__) #Flaskアプリを生成
app.wsgi_app = WhiteNoise(app.wsgi_app, root='static/')
#接続を許可するウェブサイト（オリジン）のリストを定義する
allowed_origins = [
    #本番サイトのURL
    "https://chair-layout.onrender.com",

    #ローカルでの開発・テスト用のオリジン
    "http://127.0.0.1:5500",
    "http://localhost:5000",
    "null"#ローカルのHTMLファイルを直接ブラウザで開くことを許可する
]

#CORSの設定を適用する
CORS(app, resources={r"/calculate": {"origins": allowed_origins}})

#レートリミットの設定
limiter = Limiter(
    app=app,
    key_func=get_remote_address,
    default_limits=["100 per minute"],
    # ▼▼▼ 以下の2行を追加 ▼▼▼
    storage_uri=os.environ.get("REDIS_URL"), # 保存先をRenderのRedisに指定
    strategy="fixed-window" # Flask-Limiter推奨の設定
)

# ▼▼▼ 追加 ▼▼▼
# 計算結果を一時保存するキャッシュ
calculation_cache = {}
# ▲▲▲ 追加 ▲▲▲

# ▼▼▼ 関数群 ▼▼▼

# 障害物との衝突を判定する関数
def is_colliding(chair_x, chair_y, chair_width, chair_depth, obstacle_params):
    if not obstacle_params or not obstacle_params.get("enable_obstacle"):
        return False

    obs_x = obstacle_params["obstacle_x"]
    obs_y = obstacle_params["obstacle_y"]
    obs_width = obstacle_params["obstacle_width"]
    obs_depth = obstacle_params["obstacle_depth"]

    if (chair_x < obs_x + obs_width and
        chair_x + chair_width > obs_x and
        chair_y < obs_y + obs_depth and
        chair_y + chair_depth > obs_y):
        return True
    return False

#1.データを受け取り、問題があるか確認
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
            "enable_obstacle": data.get("enable_obstacle", False),
            "obstacle_x": float(data.get("obstacle_x", 0)),
            "obstacle_y": float(data.get("obstacle_y", 0)),
            "obstacle_width": float(data.get("obstacle_width", 0)),
            "obstacle_depth": float(data.get("obstacle_depth", 0)),
        }

        # 値の範囲チェック (省略)
        # ...

        return params
    except (KeyError, TypeError, ValueError) as e:
        raise ValueError(str(e))


#2-0.最適なレイアウトを探す
def find_optimal_layout(params):
    found = False #ユーザの希望を満たせたか
    best_max_chairs = 0
    best_layout = {}
    final_layout = {}

    #最大脚数を求めるループ
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

            max_cols, max_rows = _calculate_max_rows_cols(
                params, effective_hall_width, effective_hall_depth, space_x, space_y, additional_width
            )

            current_max = max_cols * max_rows

            if current_max > best_max_chairs:
                best_max_chairs = current_max
                best_layout = {
                    "cols": max_cols, "rows": max_rows,
                    "spacing_x": spacing_x, "spacing_y": spacing_y, "max": current_max
                }

            if not found and current_max >= params["num_chairs"]:
                found = True
                final_layout = {
                    "cols": max_cols, "rows": max_rows,
                    "spacing_x": spacing_x, "spacing_y": spacing_y, "max": current_max, "found": True
                }

    if not final_layout:
        final_layout = best_layout
        final_layout["found"] = False

    return best_layout, final_layout


#2-1最大列数・行数を計算
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
            
    else: # aisle_mode == 'none'
        available_width = effective_hall_width - additional_width
        max_cols = math.floor((available_width + (space_x - params["chair_width"])) / space_x) if space_x > 0 else 0
        max_rows = math.floor((effective_hall_depth + (space_y - params["chair_depth"])) / space_y) if space_y > 0 else 0
        
    return max_cols, max_rows

#3-0.イスの座標を計算し、リストを作成
def calculate_chair_coordinates(params, layout_info):
    coords = []
    layout_cols, layout_rows = layout_info["cols"], layout_info["rows"]
    layout_spacing_x, layout_spacing_y = layout_info["spacing_x"], layout_info["spacing_y"]

    space_x = params["chair_width"] + layout_spacing_x
    space_y = params["chair_depth"] + layout_spacing_y

    additional_width = space_x / 2 if params["zigzag_layout"] else 0
    total_layout_width, total_layout_depth = _calculate_total_layout_size(params, layout_info, space_x, space_y, additional_width)

    if params["add_side_aisles"]:
        chair_area_width = params["hall_width"] - AISLE_WIDTH_CM * 2
        offset_x = AISLE_WIDTH_CM + (chair_area_width - total_layout_width) / 2
    else:
        offset_x = (params["hall_width"] - total_layout_width) / 2
    offset_y = params["front_aisle_width"]

    total_chairs_to_draw = min(params["num_chairs"], layout_info["max"]) if layout_info["found"] else layout_info["max"]
    
    count = 0
    # まず、障害物との衝突だけを考慮してイスを配置
    initial_coords = []
    for row in range(int(layout_rows)):
        if count >= total_chairs_to_draw: break
        zigzag_offset_x = space_x / 2 if params["zigzag_layout"] and row % 2 != 0 else 0
        for col in range(int(layout_cols)):
            if count >= total_chairs_to_draw: break
            x, y = _get_chair_position(params, layout_info, offset_x, offset_y, space_x, space_y, row, col, zigzag_offset_x)
            if not is_colliding(x, y, params["chair_width"], params["chair_depth"], params):
                initial_coords.append((x, y))
                count += 1

    # ▼▼▼ 変更箇所 ▼▼▼
    # ここから、障害物の下側で近すぎるイスを削除する処理を追加
    if not params['enable_obstacle']:
        # 障害物が無効なら、追加のフィルタリングは不要
        final_coords = initial_coords
    else:
        final_coords = []
        obs_x = params['obstacle_x']
        obs_y = params['obstacle_y']
        obs_w = params['obstacle_width']
        obs_d = params['obstacle_depth']
        min_y_spacing = params['min_spacing_y']

        for coord in initial_coords:
            chair_x, chair_y = coord
            chair_w = params['chair_width']
            chair_d = params['chair_depth']

            # イスが障害物の下側にあり、かつ水平方向で重なっているかチェック
            is_below = chair_y >= (obs_y + obs_d)
            is_aligned_horizontally = (chair_x < obs_x + obs_w) and (chair_x + chair_w > obs_x)

            if is_below and is_aligned_horizontally:
                # 垂直方向の隙間（距離）を計算
                gap = chair_y - (obs_y + obs_d)
                if gap < min_y_spacing:
                    # 距離が最低間隔より小さい場合、このイスは追加しない（=削除）
                    continue
            
            # 条件に当てはまらないイスは最終リストに追加
            final_coords.append(coord)
    # ▲▲▲ 変更ここまで ▲▲▲

    zigzag_offset_value = space_x / 2 if params["zigzag_layout"] else 0

    return {
        "coords": final_coords, 
        "offset_x": offset_x, 
        "offset_y": offset_y,
        "total": len(final_coords), # 実際に配置したイスの数
        "zigzag_offset": zigzag_offset_value
    }


#3-1.イス群全体の総幅と総奥行きを計算
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
    else: # aisle_mode == 'none'
        total_layout_width = layout_cols * space_x - layout_info["spacing_x"] + additional_width
        total_layout_depth = layout_rows * space_y - layout_info["spacing_y"]

    return total_layout_width, total_layout_depth


#3-2.イス1脚ごとの座標を計算
def _get_chair_position(params, layout_info, offset_x, offset_y, space_x, space_y, row, col, zigzag_offset_x):
    x, y = 0, 0
    aisle_mode = params["aisle_mode"]

    if aisle_mode == 'every_n':
        aisle_every_x = params["aisle_every_x"]
        aisle_every_y = params["aisle_every_y"]
        num_preceding_aisles_x = col // aisle_every_x if aisle_every_x > 0 else 0
        num_preceding_aisles_y = row // aisle_every_y if aisle_every_y > 0 else 0
        aisle_offset_x = num_preceding_aisles_x * AISLE_WIDTH_CM
        aisle_offset_y = num_preceding_aisles_y * AISLE_WIDTH_CM
        x = offset_x + col * space_x + aisle_offset_x + zigzag_offset_x
        y = offset_y + row * space_y + aisle_offset_y
    elif aisle_mode == 'fixed_number':
        num_aisles_x = params["num_aisles_x"]
        num_aisles_y = params["num_aisles_y"]
        cols_per_block = layout_info["cols"] / (num_aisles_x + 1) if num_aisles_x > -1 else layout_info["cols"]
        rows_per_block = layout_info["rows"] / (num_aisles_y + 1) if num_aisles_y > -1 else layout_info["rows"]
        num_preceding_aisles_x = math.floor(col / cols_per_block) if cols_per_block > 0 else 0
        num_preceding_aisles_y = math.floor(row / rows_per_block) if rows_per_block > 0 else 0
        x = offset_x + col * space_x + num_preceding_aisles_x * AISLE_WIDTH_CM + zigzag_offset_x
        y = offset_y + row * space_y + num_preceding_aisles_y * AISLE_WIDTH_CM
    else: # aisle_mode == 'none'
        x = offset_x + col * space_x + zigzag_offset_x
        y = offset_y + row * space_y
    return x, y


#5.JSONレスポンスを組み立てる
def create_json_response(params, layout_info, coords_data):
    response_data = {
        "hall_width": params["hall_width"],
        "hall_depth": params["hall_depth"],
        "chair_width": params["chair_width"],
        "chair_depth": params["chair_depth"],
        "coords": coords_data["coords"],
        "found": layout_info["found"],
        "cols": int(layout_info["cols"]),
        "rows": int(layout_info["rows"]),
        "spacing_x": layout_info["spacing_x"],
        "spacing_y": layout_info["spacing_y"],
        "total": coords_data["total"],
        "max": int(layout_info["max"]),
        "offset_x": int(coords_data["offset_x"]),
        "offset_y": int(coords_data["offset_y"]),
        "zigzag_offset": coords_data["zigzag_offset"],
        "enable_obstacle": params["enable_obstacle"],
    }
    if params["enable_obstacle"]:
        response_data.update({
            "obstacle_x": params["obstacle_x"],
            "obstacle_y": params["obstacle_y"],
            "obstacle_width": params["obstacle_width"],
            "obstacle_depth": params["obstacle_depth"],
        })
    return jsonify(response_data)


# ▼▼▼!! メイン関数 !!▼▼▼
@app.route("/")
def index():
    return render_template("sv20.html")

@app.route('/robots.txt')
def robots_txt():
    content = "User-agent: *\nAllow: /"
    return Response(content, mimetype='text/plain')

@app.route('/sitemap.xml')
def sitemap():
    url = "https://chair-layout.onrender.com"
    content = f"""<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url>
    <loc>{url}/</loc>
    <lastmod>2025-08-23</lastmod> <changefreq>monthly</changefreq>
    <priority>1.0</priority>
  </url>
</urlset>
"""
    return Response(content, mimetype='application/xml')

@app.route("/calculate", methods=["POST"])
def calculate():
    try:
        data = request.get_json()
        cache_key = json.dumps(data, sort_keys=True)
        if cache_key in calculation_cache:
            return calculation_cache[cache_key]
        
        params = parse_and_validate_input(data)
        best_layout, final_layout = find_optimal_layout(params)

        if not best_layout:
            return jsonify({"found": False, "max": 0, "coords": []})

        coords_data = calculate_chair_coordinates(params, final_layout)
        response = create_json_response(params, final_layout, coords_data)
        
        calculation_cache[cache_key] = response
        
        return response

    except ValueError as e:
        return jsonify({"error": f"入力内容が不正です: {e}"}), 400
    except Exception as e:
        print(f"An unexpected error occurred: {e}")
        return jsonify({"error": "サーバー内部で予期しないエラーが発生しました。"}), 500


if __name__ == "__main__":
    debug_mode = os.environ.get("FLASK_DEBUG") == '1'
    app.run(debug=debug_mode)