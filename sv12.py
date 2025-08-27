#ファビコンの設定
#サーバへのアクセス回数を1分間に20回に制限
from flask import Flask, request, jsonify, render_template, send_from_directory, Response
from flask_cors import CORS #PythonとHTML間の通信
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
import matplotlib
matplotlib.use('Agg') #AggはWebサーバなどのGUIのない環境でMatplotlibが使える
import matplotlib.pyplot as plt #グラフ生成の命令を簡単に行う方法を指定
import io #図の一時的に保管するメモリを提供
import base64 #画像データを文字にエンコードする
import math #様々な数学計算が使えるライブラリ
import matplotlib.ticker as mticker #目盛りをカスタマイズするためのライブラリ
import os
from whitenoise import WhiteNoise

# ▼▼▼ 定数定義 ▼▼▼

# --- バリデーション（入力値の制限） ---
MAX_HALL_DIMENSION_CM = 35000 #会場の最大サイズ (350m)
MAX_CHAIR_DIMENSION_CM = 500  #イスの最大サイズ (5m)
MAX_CHAIR_COUNT = 100000      #イスの最大数（10万脚）

# --- レイアウト計算 ---
MIN_SPACING_X_CM = 20  #イスの最小横間隔 (20cm)
MIN_SPACING_Y_CM = 100 #イスの最小縦間隔 (100cm) 兼 最前列の通路幅
AISLE_WIDTH_CM = 100   #通路の幅 (100cm)
WALL_GAP_CM = 5 #壁との隙間

# --- 探索アルゴリズム ---
#【変更箇所】古い探索用の定数は不要になったため削除
LARGE_DEFAULT_AISLE_INTERVAL = 10**9  #aisle_every_x/y が未入力の場合に使用する、非常に大きな値


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
    default_limits=["20 per minute"],
    storage_uri=os.environ.get("REDIS_URL"), # 保存先をRenderのRedisに指定
    strategy="fixed-window" # Flask-Limiter推奨の設定
)

# ▼▼▼ 関数群 ▼▼▼

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
        }

        # 値の範囲チェック
        if not (0 < params["hall_width"] <= MAX_HALL_DIMENSION_CM and 0 < params["hall_depth"] <= MAX_HALL_DIMENSION_CM):
            raise ValueError(f"会場のサイズは0より大きく、{MAX_HALL_DIMENSION_CM / 100}m以下にしてください。")
        if not (0 < params["chair_width"] <= MAX_CHAIR_DIMENSION_CM and 0 < params["chair_depth"] <= MAX_CHAIR_DIMENSION_CM):
            raise ValueError(f"イスのサイズは0より大きく、{MAX_CHAIR_DIMENSION_CM}cm以下にしてください。")
        if not (0 < params["num_chairs"] <= MAX_CHAIR_COUNT):
            raise ValueError(f"イスの数は0より大きく、{MAX_CHAIR_COUNT:,}脚以下にしてください。")
        
        return params
    except (KeyError, TypeError, ValueError) as e:
        # エラー内容をそのまま呼び出し元に投げる
        raise ValueError(str(e))


#2.列数・行数ベースで最適なイスの配置を探す関数
def find_optimal_layout(params):
    
    # --- 1. 初期化 ---
    best_max_chairs = 0
    best_layout = {}
    final_layout = {}
    found_solution = False

    # --- 2. 事前準備 (イスを配置できる有効なエリアのサイズを計算) ---
    effective_hall_width = params["hall_width"] - (WALL_GAP_CM * 2)
    if params["add_side_aisles"]:
        effective_hall_width -= AISLE_WIDTH_CM * 2
    
    effective_hall_depth = params["hall_depth"] - MIN_SPACING_Y_CM - WALL_GAP_CM

    if effective_hall_width <= 0 or effective_hall_depth <= 0:
        return {}, {}

    # --- 3. ループ上限の設定 (枝刈り) ---
    min_space_per_chair_x = params["chair_width"] + MIN_SPACING_X_CM
    min_space_per_chair_y = params["chair_depth"] + MIN_SPACING_Y_CM
    
    if min_space_per_chair_x > 0:
        max_c = math.ceil(effective_hall_width / min_space_per_chair_x)
    else:
        max_c = 0
        
    if min_space_per_chair_y > 0:
        max_r = math.ceil(effective_hall_depth / min_space_per_chair_y)
    else:
        max_r = 0

    # --- 4. メインループ (全ての列数cと行数rの組み合わせを試す) ---
    for c in range(1, max_c + 1):
        for r in range(1, max_r + 1):

            # --- 5. 必要な間隔の逆算 ---
            total_chair_width = c * params["chair_width"]
            additional_width = 0
            
            # ▼▼▼【修正点】通路幅の計算を「c > 1」の場合のみ実行するように変更 ▼▼▼
            if c > 1:
                if params["aisle_mode"] == 'every_n':
                    if params["aisle_every_x"] > 0:
                        num_aisles_x = (c - 1) // params["aisle_every_x"]
                        additional_width += num_aisles_x * AISLE_WIDTH_CM
                elif params["aisle_mode"] == 'fixed_number':
                    additional_width += params["num_aisles_x"] * AISLE_WIDTH_CM
            # ▲▲▲ 修正ここまで ▲▲▲

            if c > 1:
                if params["zigzag_layout"]:
                    denominator = c - 0.5
                    if denominator > 0:
                         required_spacing_x = (effective_hall_width - additional_width - (c + 0.5) * params["chair_width"]) / denominator
                    else:
                        required_spacing_x = -1
                else:
                    required_spacing_x = (effective_hall_width - total_chair_width - additional_width) / (c - 1)
            else:
                required_spacing_x = float('inf') if effective_hall_width >= total_chair_width + additional_width else -1

            total_chair_depth = r * params["chair_depth"]
            additional_depth = 0
            
            # ▼▼▼【修正点】通路幅の計算を「r > 1」の場合のみ実行するように変更 ▼▼▼
            if r > 1:
                if params["aisle_mode"] == 'every_n':
                    if params["aisle_every_y"] > 0:
                        num_aisles_y = (r - 1) // params["aisle_every_y"]
                        additional_depth += num_aisles_y * AISLE_WIDTH_CM
                elif params["aisle_mode"] == 'fixed_number':
                    additional_depth += params["num_aisles_y"] * AISLE_WIDTH_CM
            # ▲▲▲ 修正ここまで ▲▲▲

            if r > 1:
                required_spacing_y = (effective_hall_depth - total_chair_depth - additional_depth) / (r - 1)
            else:
                required_spacing_y = float('inf') if effective_hall_depth >= total_chair_depth + additional_depth else -1

            # --- 6. 判定と最適解の更新 ---
            if required_spacing_x >= MIN_SPACING_X_CM and required_spacing_y >= MIN_SPACING_Y_CM:
                current_chairs = c * r
                if current_chairs > best_max_chairs:
                    best_max_chairs = current_chairs
                    best_layout = {
                        "cols": c, "rows": r,
                        "spacing_x": required_spacing_x, "spacing_y": required_spacing_y,
                        "max": current_chairs
                    }
                if not found_solution and current_chairs >= params["num_chairs"]:
                    found_solution = True
                    final_layout = {
                        "cols": c, "rows": r,
                        "spacing_x": required_spacing_x, "spacing_y": required_spacing_y,
                        "max": current_chairs, "found": True
                    }

    # --- 7. 最終結果の決定 ---
    # 希望数を満たす解が見つからなかった場合、最大配置数レイアウトを採用する
    if not final_layout:
        if not best_layout:
            return {}, {} # 1脚も配置できなかった場合
        
        final_layout = best_layout
        final_layout["found"] = False

    # 間隔を小数点以下第一位に丸める
    if "spacing_x" in final_layout:
        final_layout["spacing_x"] = round(final_layout["spacing_x"], 1)
    if "spacing_y" in best_layout:
         best_layout["spacing_y"] = round(best_layout["spacing_y"], 1)

    return best_layout, final_layout
# ▲▲▲【変更箇所】ここまで ▲▲▲


<<<<<<<< HEAD:sv13.py
#2-1最大列数・行数を計算
def _calculate_max_rows_cols(params, effective_hall_width, effective_hall_depth, space_x, space_y, additional_width):
    max_cols, max_rows = 0, 0
    aisle_mode = params["aisle_mode"]
    
    if aisle_mode == 'every_n':
        aisle_every_x = params["aisle_every_x"]
        if aisle_every_x > 0:
            # 「イス(aisle_every_x)脚 + 通路1本」を1ブロックとして計算
            block_width = aisle_every_x * space_x + AISLE_WIDTH_CM
            num_blocks = math.floor(effective_hall_width / block_width) if block_width > 0 else 0
            
            # ブロックを置いた後の残りスペースを計算
            remaining_width = effective_hall_width - num_blocks * block_width
            
            # 残りスペースに置けるイスの数を計算
            extra_cols = math.floor(remaining_width / space_x) if space_x > 0 else 0
            
            # 合計の列数 = ブロック内のイス数 + 残りのイス数
            max_cols = num_blocks * aisle_every_x + extra_cols
        else: # 縦通路がない場合
             max_cols = math.floor(effective_hall_width / space_x) if space_x > 0 else 0

        # 同じロジックを行（奥行き）にも適用
        aisle_every_y = params["aisle_every_y"]
        if aisle_every_y > 0:
            block_depth = aisle_every_y * space_y + AISLE_WIDTH_CM
            num_blocks = math.floor(effective_hall_depth / block_depth) if block_depth > 0 else 0
            remaining_depth = effective_hall_depth - num_blocks * block_depth
            extra_rows = math.floor(remaining_depth / space_y) if space_y > 0 else 0
            max_rows = num_blocks * aisle_every_y + extra_rows
        else: # 横通路がない場合
            max_rows = math.floor(effective_hall_depth / space_y) if space_y > 0 else 0

    elif aisle_mode == 'fixed_number':
        # (この部分は元から高速なため、修正不要)
        num_aisles_x = params["num_aisles_x"]
        num_aisles_y = params["num_aisles_y"]
        chair_area_width = effective_hall_width - num_aisles_x * AISLE_WIDTH_CM
        chair_area_depth = effective_hall_depth - num_aisles_y * AISLE_WIDTH_CM
        if chair_area_width > 0 and chair_area_depth > 0:
            available_width = chair_area_width - additional_width
            max_cols = math.floor(available_width / space_x) if available_width > 0 else 0
            max_rows = math.floor(chair_area_depth / space_y)

    else: # aisle_mode == 'none'
        # (この部分も元から高速なため、修正不要)
        available_width = effective_hall_width - additional_width
        max_cols = math.floor(available_width / space_x) if space_x > 0 else 0
        max_rows = math.floor(effective_hall_depth / space_y) if space_y > 0 else 0
        
    return max_cols, max_rows

#3-0.イスの座標を計算し、リストを作成
========
#【変更不要】イスの座標を計算し、リストを作成
>>>>>>>> 2992db9b02eb8879324594ac6c3b46005c0cbe45:sv12.py
def calculate_chair_coordinates(params, layout_info):
    coords = [] #座標の入れ物
    layout_cols, layout_rows = layout_info["cols"], layout_info["rows"]
    layout_spacing_x, layout_spacing_y = layout_info["spacing_x"], layout_info["spacing_y"]

    space_x = params["chair_width"] + layout_spacing_x
    space_y = params["chair_depth"] + layout_spacing_y

    additional_width = space_x / 2 if params["zigzag_layout"] and layout_rows > 1 else 0
    total_layout_width, total_layout_depth = _calculate_total_layout_size(params, layout_info, space_x, space_y, additional_width)

    #座標の開始位置の計算
    if params["add_side_aisles"]:
        chair_area_width = params["hall_width"] - AISLE_WIDTH_CM * 2 #イスが置けるスペース
        offset_x = AISLE_WIDTH_CM + (chair_area_width - total_layout_width) / 2 #x座標の開始位置
    else:
        offset_x = (params["hall_width"] - total_layout_width) / 2
    offset_y = MIN_SPACING_Y_CM #y座標の開始位置

    total_chairs_to_draw = min(params["num_chairs"], layout_info["max"]) if layout_info["found"] else layout_info["max"]
    
    count = 0
    for row in range(int(layout_rows)):
        zigzag_offset_x = space_x / 2 if params["zigzag_layout"] and row % 2 != 0 else 0
        for col in range(int(layout_cols)):
            if count >= total_chairs_to_draw: break
            x, y = _get_chair_position(params, layout_info, offset_x, offset_y, space_x, space_y, row, col, zigzag_offset_x)
            coords.append((x, y))
            count += 1
        if count >= total_chairs_to_draw: break

    zigzag_offset_value = space_x / 2 if params["zigzag_layout"] else 0

    return {
        "coords": coords, "offset_x": offset_x, "offset_y": offset_y, 
        "total": total_chairs_to_draw, "zigzag_offset": zigzag_offset_value
    }

#【変更不要】イス群全体の総幅と総奥行きを計算
def _calculate_total_layout_size(params, layout_info, space_x, space_y, additional_width):
    layout_cols, layout_rows = layout_info["cols"], layout_info["rows"]
    aisle_mode = params["aisle_mode"]
    total_layout_width, total_layout_depth = 0, 0

    if aisle_mode == 'every_n':
        aisle_every_x = params["aisle_every_x"]
        aisle_every_y = params["aisle_every_y"]
        num_aisles_x = (layout_cols - 1) // aisle_every_x if aisle_every_x > 0 else 0
        num_aisles_y = (layout_rows - 1) // aisle_every_y if aisle_every_y > 0 else 0
        # ジグザグの場合、単純な掛け算では総幅がずれるため再計算
        if params["zigzag_layout"] and layout_rows > 1:
            total_layout_width = (layout_cols - 0.5) * space_x + 0.5 * params["chair_width"] + num_aisles_x * AISLE_WIDTH_CM
        else:
            total_layout_width = layout_cols * space_x - layout_info["spacing_x"] + num_aisles_x * AISLE_WIDTH_CM
        total_layout_depth = layout_rows * space_y - layout_info["spacing_y"] + num_aisles_y * AISLE_WIDTH_CM
    elif aisle_mode == 'fixed_number':
        num_aisles_x = params["num_aisles_x"]
        num_aisles_y = params["num_aisles_y"]
        if params["zigzag_layout"] and layout_rows > 1:
            total_layout_width = (layout_cols - 0.5) * space_x + 0.5 * params["chair_width"] + num_aisles_x * AISLE_WIDTH_CM
        else:
            total_layout_width = layout_cols * space_x - layout_info["spacing_x"] + num_aisles_x * AISLE_WIDTH_CM
        total_layout_depth = layout_rows * space_y - layout_info["spacing_y"] + num_aisles_y * AISLE_WIDTH_CM
    else: # aisle_mode == 'none'
        if params["zigzag_layout"] and layout_rows > 1:
            total_layout_width = (layout_cols - 0.5) * space_x + 0.5 * params["chair_width"]
        else:
            total_layout_width = layout_cols * space_x - layout_info["spacing_x"]

        total_layout_depth = layout_rows * space_y - layout_info["spacing_y"]

    return total_layout_width, total_layout_depth


#【変更不要】イス1脚ごとの座標を計算
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


#【変更不要】座標を元にMatplotlibでレイアウト画像を生成
def generate_layout_image(params, coords_data):
    #▼▼画像生成▼▼
    fig, ax = plt.subplots()
    ax.set_aspect('equal')
    ax.set_xlim(0, params["hall_width"])
    ax.set_ylim(0, params["hall_depth"])
    ax.set_title("Chair Layout")
    ax.set_xlabel("width (m)") #単位をmに変更
    ax.set_ylabel("depth (m)") #単位をmに変更

    formatter = mticker.FuncFormatter(lambda x, pos: f'{x/100:.1f}')
    ax.xaxis.set_major_formatter(formatter)
    ax.yaxis.set_major_formatter(formatter)

    ax.add_patch(plt.Rectangle((0, 0), params["hall_width"], params["hall_depth"], fill=False, edgecolor='black'))

    for (x, y) in coords_data["coords"]:
        ax.add_patch(plt.Rectangle((x, y), params["chair_width"], params["chair_depth"], facecolor='skyblue', edgecolor='gray'))

    plt.gca().invert_yaxis() #y軸の反転
    plt.tight_layout() #レイアウトの自動調整

    buf = io.BytesIO()
    plt.savefig(buf, format="png")
    plt.close(fig)
    buf.seek(0)
    return base64.b64encode(buf.read()).decode("utf-8")


#【変更不要】JSONレスポンスを組み立てる
def create_json_response(params, layout_info, coords_data, image_base64):
    return jsonify({
        "found": layout_info["found"],
        "cols": int(layout_info["cols"]),
        "rows": int(layout_info["rows"]),
        "spacing_x": layout_info["spacing_x"],
        "spacing_y": layout_info["spacing_y"],
        "total": coords_data["total"],
        "max": int(layout_info["max"]),
        "image": image_base64,
        "offset_x": int(coords_data["offset_x"]),
        "offset_y": int(coords_data["offset_y"]),
        "zigzag_offset": coords_data["zigzag_offset"]
    })


# ▼▼▼!! メイン関数 !!▼▼▼
@app.route("/")
def index():
<<<<<<<< HEAD:sv13.py
    return render_template("sv13.html")
# robots.txtを提供するルート
========
    return render_template("sv12.html")

>>>>>>>> 2992db9b02eb8879324594ac6c3b46005c0cbe45:sv12.py
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
        #1.データを受け取り、問題があるか確認
        params = parse_and_validate_input(request.json)

        #2.最適なレイアウトを探す
        best_layout, final_layout = find_optimal_layout(params)

        #イスが1脚も置けない場合は、ここで処理を終了
        if not final_layout:
            return jsonify({"found": False, "max": 0, "image": None})

        #3.イスの座標を計算して、リストを作成
        coords_data = calculate_chair_coordinates(params, final_layout)

        #4.座標を元にMatplotlibでレイアウト画像を生成
        image_base64 = generate_layout_image(params, coords_data)

        #5.JSONレスポンスを組み立てる
        return create_json_response(params, final_layout, coords_data, image_base64)

    except ValueError as e:
        # バリデーションエラーなど、予期されるエラーの処理
        return jsonify({"error": f"入力内容が不正です: {e}"}), 400
    except Exception as e:
        # 予期しないサーバー内部のエラーの処理
        print(f"An unexpected error occurred: {e}") # デバッグ用
        return jsonify({"error": "サーバー内部で予期しないエラーが発生しました。"}), 500


if __name__ == "__main__":
    #FLASK_DEBUGという環境変数が '1' の時だけデバッグモードを有効にする
    debug_mode = os.environ.get("FLASK_DEBUG") == '1'
    app.run(debug=debug_mode) #デバッグモードを環境変数で制御