<<<<<<< HEAD:sf02.py
#スマホだと左右に分割しないように
=======
#障害物を置くと最大配置可能数を減らすように
>>>>>>> 8d13ecbd18472bff33f6001a4b1686c7ef3b108d:sf01.py
from flask import Flask, request, jsonify, render_template, Response
from flask_cors import CORS
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
import math
import os
import json
from whitenoise import WhiteNoise

# --- 定数定義 ---
# システム全体で使用する固定値を定義します。

MAX_HALL_DIMENSION_CM = 15000  # 会場の最大寸法（幅・奥行き）(cm)
MAX_CHAIR_DIMENSION_CM = 500   # 椅子の最大寸法（幅・奥行き）(cm)
MAX_CHAIR_COUNT = 50000        # 配置する椅子の最大数
AISLE_WIDTH_CM = 100           # 通路の幅 (cm)
MAX_SPACING_SEARCH_CM = 300    # 探索する椅子間隔の最大値 (cm)
SPACING_SEARCH_STEP_CM = 5     # 椅子間隔を探索する際の増分 (cm)
LARGE_DEFAULT_AISLE_INTERVAL = 10**9 # 通路間隔が指定されない場合の非常に大きなデフォルト値

# --- Flaskアプリケーションの初期化 ---
app = Flask(__name__)
# WhiteNoiseを使用して、静的ファイル（HTML, CSS, JSなど）を効率的に配信します。
app.wsgi_app = WhiteNoise(app.wsgi_app, root='static/')

# --- CORS (クロスオリジンリソース共有) の設定 ---
# 異なるドメインからのAPIリクエストを許可するための設定です。
allowed_origins = [
    "https://chair-layout.onrender.com",
    "http://127.0.0.1:5500",
    "http://localhost:5000",
    "null"
]
CORS(app, resources={r"/calculate": {"origins": allowed_origins}})

# --- APIレートリミットの設定 ---
# 同一IPアドレスからの過剰なリクエストを防ぎ、サーバーの負荷を軽減します。
limiter = Limiter(
    app=app,
    key_func=get_remote_address,
    default_limits=["100 per minute"],
    storage_uri=os.environ.get("REDIS_URL"), # 本番環境用にRedisなどの外部ストレージを指定
    strategy="fixed-window"
)

# --- 計算結果のキャッシュ（現在コメントアウト中）---
# 同じ計算を繰り返さないように、結果を一時的に保存する辞書。
calculation_cache = {}

def is_colliding(chair_x, chair_y, chair_width, chair_depth, obstacles):
    """
    指定された座標の椅子が、いずれかの障害物と衝突するかどうかを判定します。
    障害物の形状は四角形と円形に対応しています。

    Args:
        chair_x (float): 椅子のX座標
        chair_y (float): 椅子のY座標
        chair_width (int): 椅子の幅
        chair_depth (int): 椅子の奥行き
        obstacles (list): 障害物のリスト

    Returns:
        bool: 衝突する場合はTrue、しない場合はFalse
    """
    for obs in obstacles:
        collides = False
        # 障害物のタイプによって衝突判定ロジックを分岐
        if obs.get('type') == 'circle':
            # 円と四角形の衝突判定
            # 1. 円の中心に最も近い四角形（椅子）上の点(closest_x, closest_y)を見つける
            cx, cy, r = obs['x'], obs['y'], obs['radius']
            closest_x = max(chair_x, min(cx, chair_x + chair_width))
            closest_y = max(chair_y, min(cy, chair_y + chair_depth))
            
            # 2. その最近接点と円の中心との距離を計算
            dist_x = cx - closest_x
            dist_y = cy - closest_y
            distance_squared = (dist_x * dist_x) + (dist_y * dist_y)
            
            # 3. 距離が円の半径より小さければ衝突している
            if distance_squared < (r * r):
                collides = True
        else: # デフォルトは四角形として扱う
            # 四角形同士の衝突判定 (AABB: Axis-Aligned Bounding Box)
            if (chair_x < obs['x'] + obs['width'] and
                chair_x + chair_width > obs['x'] and
                chair_y < obs['y'] + obs['depth'] and
                chair_y + chair_depth > obs['y']):
                collides = True
        
        if collides:
            return True # 一つでも衝突があれば、即座にTrueを返す
    return False # どの障害物とも衝突しなかった

def parse_and_validate_input(data):
    """
    クライアントから受け取ったJSONデータを解析し、型変換や必須項目の検証（バリデーション）を行います。
    不正なデータや不足しているデータがあればエラーを送出します。

    Args:
        data (dict): リクエストから受け取ったJSONデータ

    Raises:
        ValueError: データに不備がある場合

    Returns:
        dict: 検証および型変換済みのパラメータ辞書
    """
    try:
        # 入力データを辞書として整理し、数値型に変換
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

        # 障害物データのバリデーション
        for obs in params["obstacles"]:
            obs_type = obs.get('type')
            if not obs_type or not all(k in obs for k in ['x', 'y']):
                raise ValueError("障害物の基本データ(type, x, y)が不足しています。")
            
            # 形状に応じて必要なキー（幅/奥行 or 半径）が存在するかチェック
            if obs_type == 'rectangle':
                if not all(k in obs for k in ['width', 'depth']):
                    raise ValueError("四角形障害物のデータ(width, depth)が不足しています。")
            elif obs_type == 'circle':
                if 'radius' not in obs:
                    raise ValueError("円形障害物のデータ(radius)が不足しています。")
            else:
                raise ValueError(f"未知の障害物タイプ: {obs_type}")

        return params
    except (KeyError, TypeError, ValueError) as e:
        # データに問題があった場合、エラー内容を付けてValueErrorを送出
        raise ValueError(str(e))

def find_optimal_layout(params):
    """
    最適な椅子配置（列数、行数、間隔）を見つけ出すためのメインロジックです。
    椅子の間隔を少しずつ広げながら、指定された数の椅子を配置できるかを試行します。

    Args:
        params (dict): 検証済みの入力パラメータ

    Returns:
        tuple: (best_layout, final_layout)
               best_layout: 最も多くの椅子を配置できるレイアウト情報
               final_layout: 要求された椅子数を満たすか、それが無理ならbest_layout
    """
    found = False # 要求された椅子数を配置できたかどうかのフラグ
    best_max_chairs = 0 # これまでに見つかった最大の配置可能数
    best_layout = {}    # best_max_chairsを達成したレイアウト
    final_layout = {}   # 最終的にクライアントに返すレイアウト

    # 椅子の横方向の間隔(spacing_x)と縦方向の間隔(spacing_y)を総当たりで探索
    for spacing_x in range(params["min_spacing_x"], MAX_SPACING_SEARCH_CM + 1, SPACING_SEARCH_STEP_CM):
        for spacing_y in range(params["min_spacing_y"], MAX_SPACING_SEARCH_CM + 1, SPACING_SEARCH_STEP_CM):
            # 椅子1脚が占めるスペースを計算
            space_x = params["chair_width"] + spacing_x
            space_y = params["chair_depth"] + spacing_y
            if space_x == 0 or space_y == 0: continue

            # ジグザグ配置の場合、実質的に必要な幅が増える
            additional_width = space_x / 2 if params["zigzag_layout"] else 0
            
            # 通路などを考慮した、椅子を配置できる有効なエリアの寸法を計算
            effective_hall_width = params["hall_width"]
            if params["add_side_aisles"]:
                effective_hall_width -= AISLE_WIDTH_CM * 2
            if effective_hall_width <= 0: continue
            
            effective_hall_depth = params["hall_depth"] - params["front_aisle_width"]
            if effective_hall_depth <= 0: continue

            # この間隔で最大何列・何行配置できるか計算
            max_cols, max_rows = _calculate_max_rows_cols(params, effective_hall_width, effective_hall_depth, space_x, space_y, additional_width)
            current_max = max_cols * max_rows

            # これまでの最大配置数を超えたら、ベストなレイアウトとして更新
            if current_max > best_max_chairs:
                best_max_chairs = current_max
                best_layout = {"cols": max_cols, "rows": max_rows, "spacing_x": spacing_x, "spacing_y": spacing_y, "max": current_max}

            # まだ条件を満たす解が見つかっていない、かつ、現在の配置数が要求数以上なら、
            # これを最終的な解として採用し、探索フラグを立てる
            if not found and current_max >= params["num_chairs"]:
                found = True
                final_layout = {"cols": max_cols, "rows": max_rows, "spacing_x": spacing_x, "spacing_y": spacing_y, "max": current_max, "found": True}
    
    # ループがすべて終わっても要求数を満たす解が見つからなかった場合、
    # 最も多く配置できたbest_layoutを最終結果とする
    if not final_layout:
        final_layout = best_layout
        final_layout["found"] = False

    return best_layout, final_layout

def _calculate_max_rows_cols(params, effective_hall_width, effective_hall_depth, space_x, space_y, additional_width):
    """
    【ヘルパー関数】与えられた条件下で配置可能な最大の列数と行数を計算します。
    通路のモード（every_n, fixed_number, none）に応じて計算方法が変わります。
    """
    max_cols, max_rows = 0, 0
    aisle_mode = params["aisle_mode"]

    # --- 通路モードに応じた計算分岐 ---
    if aisle_mode == 'every_n': # N脚おきに通路を設置するモード
        # 横方向（列）の計算
        aisle_every_x = params["aisle_every_x"]
        if aisle_every_x > 0:
            # 椅子N脚＋通路1本を1ブロックとして、会場に何ブロック入るか計算
            block_width = aisle_every_x * space_x + AISLE_WIDTH_CM
            num_blocks = math.floor(effective_hall_width / block_width) if block_width > 0 else 0
            remaining_width = effective_hall_width - num_blocks * block_width
            extra_cols = math.floor(remaining_width / space_x) if space_x > 0 else 0
            max_cols = num_blocks * aisle_every_x + extra_cols
        else: # 通路間隔が0なら通路なしと同じ
            max_cols = math.floor(effective_hall_width / space_x) if space_x > 0 else 0
        
        # 縦方向（行）の計算も同様
        aisle_every_y = params["aisle_every_y"]
        if aisle_every_y > 0:
            block_depth = aisle_every_y * space_y + AISLE_WIDTH_CM
            num_blocks = math.floor(effective_hall_depth / block_depth) if block_depth > 0 else 0
            remaining_depth = effective_hall_depth - num_blocks * block_depth
            extra_rows = math.floor(remaining_depth / space_y) if space_y > 0 else 0
            max_rows = num_blocks * aisle_every_y + extra_rows
        else:
            max_rows = math.floor(effective_hall_depth / space_y) if space_y > 0 else 0

    elif aisle_mode == 'fixed_number': # 指定本数の通路を均等に配置するモード
        num_aisles_x = params["num_aisles_x"]
        num_aisles_y = params["num_aisles_y"]
        # 全体の幅から通路の総幅を引いたエリアに椅子を配置
        chair_area_width = effective_hall_width - num_aisles_x * AISLE_WIDTH_CM
        chair_area_depth = effective_hall_depth - num_aisles_y * AISLE_WIDTH_CM
        if chair_area_width > 0 and chair_area_depth > 0:
            available_width = chair_area_width - additional_width
            max_cols = math.floor((available_width + (space_x - params["chair_width"])) / space_x) if space_x > 0 else 0
            max_rows = math.floor((chair_area_depth + (space_y - params["chair_depth"])) / space_y) if space_y > 0 else 0
        else:
            max_cols, max_rows = 0, 0

    else: # 通路なしモード
        available_width = effective_hall_width - additional_width
        max_cols = math.floor((available_width + (space_x - params["chair_width"])) / space_x) if space_x > 0 else 0
        max_rows = math.floor((effective_hall_depth + (space_y - params["chair_depth"])) / space_y) if space_y > 0 else 0
    
    return max_cols, max_rows

def _calculate_total_layout_size(params, layout_info, space_x, space_y, additional_width):
    """
    【ヘルパー関数】椅子と通路を含めたレイアウト全体の最終的な幅と奥行きを計算します。
    これはレイアウトを会場の中央に配置するために使われます。
    """
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
    else: # 通路なし
        total_layout_width = layout_cols * space_x - layout_info["spacing_x"] + additional_width
        total_layout_depth = layout_rows * space_y - layout_info["spacing_y"]
    
    return total_layout_width, total_layout_depth

def _get_chair_position(params, layout_info, offset_x, offset_y, space_x, space_y, row, col, zigzag_offset_x):
    """
    【ヘルパー関数】指定された行(row)と列(col)の椅子の左上の座標(x, y)を計算します。
    通路やジグザグ配置によるオフセットもここで考慮されます。
    """
    x, y = 0, 0
    aisle_mode = params["aisle_mode"]
    if aisle_mode == 'every_n':
        aisle_every_x = params["aisle_every_x"]
        aisle_every_y = params["aisle_every_y"]
        # この椅子より前にある通路の数を計算
        num_preceding_aisles_x = col // aisle_every_x if aisle_every_x > 0 else 0
        num_preceding_aisles_y = row // aisle_every_y if aisle_every_y > 0 else 0
        # 通路分のオフセットを追加
        aisle_offset_x = num_preceding_aisles_x * AISLE_WIDTH_CM
        aisle_offset_y = num_preceding_aisles_y * AISLE_WIDTH_CM
        x = offset_x + col * space_x + aisle_offset_x + zigzag_offset_x
        y = offset_y + row * space_y + aisle_offset_y
    elif aisle_mode == 'fixed_number':
        num_aisles_x = params["num_aisles_x"]
        num_aisles_y = params["num_aisles_y"]
        # 1つの椅子ブロックに何列・何行含まれるかを計算
        cols_per_block = layout_info["cols"] / (num_aisles_x + 1) if num_aisles_x > -1 else layout_info["cols"]
        rows_per_block = layout_info["rows"] / (num_aisles_y + 1) if num_aisles_y > -1 else layout_info["rows"]
        # この椅子より前にある通路の数を計算
        num_preceding_aisles_x = math.floor(col / cols_per_block) if cols_per_block > 0 else 0
        num_preceding_aisles_y = math.floor(row / rows_per_block) if rows_per_block > 0 else 0
        x = offset_x + col * space_x + num_preceding_aisles_x * AISLE_WIDTH_CM + zigzag_offset_x
        y = offset_y + row * space_y + num_preceding_aisles_y * AISLE_WIDTH_CM
    else: # 通路なし
        x = offset_x + col * space_x + zigzag_offset_x
        y = offset_y + row * space_y
    return x, y

def calculate_chair_coordinates(params, layout_info):
    """
    【アルゴリズム修正】
    最終レイアウトに基づき、まず配置可能な全ての椅子の座標を計算し、
    そこから障害物を考慮した「真の最大配置可能数」を求めます。
    """
    # --- ステップ1: グリッド上の全候補座標を生成 ---
    layout_cols, layout_rows = layout_info["cols"], layout_info["rows"]
    space_x = params["chair_width"] + layout_info["spacing_x"]
    space_y = params["chair_depth"] + layout_info["spacing_y"]
    additional_width = space_x / 2 if params["zigzag_layout"] else 0
    
    total_layout_width, total_layout_depth = _calculate_total_layout_size(params, layout_info, space_x, space_y, additional_width)
    offset_x = (params["hall_width"] - total_layout_width) / 2
    offset_y = params["front_aisle_width"]
    
    all_potential_coords = []
    for row in range(int(layout_rows)):
        zigzag_offset_x = space_x / 2 if params["zigzag_layout"] and row % 2 != 0 else 0
        for col in range(int(layout_cols)):
            x, y = _get_chair_position(params, layout_info, offset_x, offset_y, space_x, space_y, row, col, zigzag_offset_x)
            all_potential_coords.append((x, y))

    # --- ステップ2: 障害物との衝突判定 ---
    coords_after_collision_check = [
        coord for coord in all_potential_coords 
        if not is_colliding(coord[0], coord[1], params["chair_width"], params["chair_depth"], params["obstacles"])
    ]
    collision_skips = len(all_potential_coords) - len(coords_after_collision_check)

    # --- ステップ3: 障害物下の間隔調整 ---
    coords_to_remove = set()
    spacing_skips = 0
    if params['obstacles']:
        min_y_spacing = params['min_spacing_y']
        for obs in params['obstacles']:
            for coord in coords_after_collision_check:
                chair_x, chair_y = coord
                chair_w = params['chair_width']
                is_below = False
                is_aligned_horizontally = False
                gap = float('inf')

                if obs.get('type') == 'circle':
                    cx, cy, r = obs['x'], obs['y'], obs['radius']
                    is_below = chair_y >= (cy + r)
                    is_aligned_horizontally = (chair_x < cx + r) and (chair_x + chair_w > cx - r)
                    if is_below and is_aligned_horizontally:
                        gap = chair_y - (cy + r)
                else: # rectangle
                    obs_x, obs_y, obs_w, obs_d = obs['x'], obs['y'], obs['width'], obs['depth']
                    is_below = chair_y >= (obs_y + obs_d)
                    is_aligned_horizontally = (chair_x < obs_x + obs_w) and (chair_x + chair_w > obs_x)
                    if is_below and is_aligned_horizontally:
                        gap = chair_y - (obs_y + obs_d)

                if gap < min_y_spacing:
                    coords_to_remove.add(coord)
        
        final_placeable_coords = [coord for coord in coords_after_collision_check if coord not in coords_to_remove]
        spacing_skips = len(coords_to_remove)
    else:
        final_placeable_coords = coords_after_collision_check

    # --- ステップ4: 最終結果の集計 ---
    true_max_with_obstacles = len(final_placeable_coords)

    # ユーザーの要求数に応じて、表示する座標を切り出す
    num_chairs_to_draw = min(params["num_chairs"], true_max_with_obstacles)
    coords_for_display = final_placeable_coords[:num_chairs_to_draw]
    
    return {
        "coords_for_display": coords_for_display,
        "total_displayed": len(coords_for_display),
        "true_max": true_max_with_obstacles,
        "collision_skips": collision_skips,
        "spacing_skips": spacing_skips,
        "offset_x": offset_x,
        "offset_y": offset_y,
        "zigzag_offset": space_x / 2 if params["zigzag_layout"] else 0,
    }

def create_json_response(params, layout_info, coords_data):
    """
    【アルゴリズム修正】
    計算結果をまとめ、クライアントに返すためのJSONレスポンスオブジェクトを生成します。
    'max'には障害物を考慮した後の真の最大数を設定します。
    """
    return jsonify({
        "hall_width": params["hall_width"],
        "hall_depth": params["hall_depth"],
        "chair_width": params["chair_width"],
        "chair_depth": params["chair_depth"],
        "coords": coords_data.get("coords_for_display", []),
        "found": layout_info["found"],
        "cols": int(layout_info["cols"]),
        "rows": int(layout_info["rows"]),
        "spacing_x": layout_info["spacing_x"],
        "spacing_y": layout_info["spacing_y"],
        "total": coords_data.get("total_displayed", 0),
        # 'max' には、障害物考慮後の「真の最大配置可能数」を設定
        "max": coords_data.get("true_max", 0),
        "offset_x": int(coords_data.get("offset_x", 0)),
        "offset_y": int(coords_data.get("offset_y", 0)),
        "zigzag_offset": coords_data.get("zigzag_offset", 0),
        "obstacles": params.get("obstacles", []),
        # デバッグや詳細情報用に、個別のスキップ数もレスポンスに含める
        "collision_skips": coords_data.get("collision_skips", 0),
        "spacing_skips": coords_data.get("spacing_skips", 0)
    })

# --- Flask ルーティング ---
# URLと実行する関数を紐付けます。

@app.route("/")
def index():
    """
    ルートURL ("/") にアクセスがあった場合に、メインのHTMLページを返します。
    """
    return render_template("sf02.html")

@app.route("/calculate", methods=["POST"])
@limiter.limit("10 per minute") # エンドポイント個別のレートリミット
def calculate():
    """
    "/calculate" エンドポイントへのPOSTリクエストを処理します。
    これが椅子配置計算のメインAPIです。
    """
    try:
        # 1. リクエストからJSONデータを取得
        data = request.get_json()
        
        # --- キャッシュ機能（現在無効）---
        cache_key = json.dumps(data, sort_keys=True)
        # if cache_key in calculation_cache:
        #     return calculation_cache[cache_key]
        
        # 2. 入力データを検証
        params = parse_and_validate_input(data)
        
        # 3. 最適なレイアウト（格子）を探索
        best_layout, final_layout = find_optimal_layout(params)

        # レイアウトが見つからなかった場合は、空の結果を返す
        if not best_layout or not final_layout:
             return jsonify({"found": False, "max": 0, "coords": []})

        # 4. 椅子の全座標を計算し、真の最大数を算出
        coords_data = calculate_chair_coordinates(params, final_layout)
        
        # 5. JSONレスポンスを作成
        response = create_json_response(params, final_layout, coords_data)
        
        # --- キャッシュ保存（現在無効）---
        # calculation_cache[cache_key] = response
        
        # 6. レスポンスをクライアントに返す
        return response

    except ValueError as e:
        # 入力データに問題があった場合 (400 Bad Request)
        return jsonify({"error": f"入力内容が不正です: {e}"}), 400
    except Exception as e:
        # 予期しないサーバー内部のエラーが発生した場合 (500 Internal Server Error)
        print(f"An unexpected error occurred: {e}")
        return jsonify({"error": "サーバー内部で予期しないエラーが発生しました。"}), 500

# --- アプリケーションの実行 ---
if __name__ == "__main__":
    # 環境変数 FLASK_DEBUG が '1' に設定されている場合、デバッグモードで実行
    debug_mode = os.environ.get("FLASK_DEBUG") == '1'
    app.run(debug=debug_mode)