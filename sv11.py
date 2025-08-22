#ファビコンの設定
from flask import Flask, request, jsonify, render_template, send_from_directory
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
    key_func=get_remote_address, # IPアドレスを基準にユーザーを識別
    default_limits=["20 per minute"] # アプリ全体でのデフォルト制限 (1分間に20回まで)
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


#2-0.最適なレイアウトを探す
def find_optimal_layout(params):
    found = False #ユーザの希望を満たせたか
    best_max_chairs = 0
    best_layout = {}
    final_layout = {}

    #最大脚数を求めるループ
    for spacing_x in range(MIN_SPACING_X_CM, MAX_SPACING_SEARCH_CM + 1, SPACING_SEARCH_STEP_CM):
        for spacing_y in range(MIN_SPACING_Y_CM, MAX_SPACING_SEARCH_CM + 1, SPACING_SEARCH_STEP_CM):
            #1人分のスペース＝イスの大きさ＋イスの間隔
            space_x = params["chair_width"] + spacing_x
            space_y = params["chair_depth"] + spacing_y
            if space_x == 0 or space_y == 0: continue

            #「ジグザグ」にチェックが付いている場合
            additional_width = space_x / 2 if params["zigzag_layout"] else 0

            #「両端に通路」にチェックが付いている場合
            effective_hall_width = params["hall_width"]
            if params["add_side_aisles"]:
                effective_hall_width -= AISLE_WIDTH_CM * 2 #会場の幅を通路2つ分小さく
            if effective_hall_width <= 0: continue #通路を設置して会場がマイナスになる場合、ループの次の回へ

            #最前列の通路を確保
            effective_hall_depth = params["hall_depth"] - MIN_SPACING_Y_CM
            if effective_hall_depth <= 0: continue

            max_cols, max_rows = _calculate_max_rows_cols(
                params, effective_hall_width, effective_hall_depth, space_x, space_y, additional_width
            )

            #最大脚数
            current_max = max_cols * max_rows

            if current_max > best_max_chairs:#過去最高を記録した場合
                #記録更新！
                best_max_chairs = current_max
                best_layout = {
                    "cols": max_cols, "rows": max_rows,
                    "spacing_x": spacing_x, "spacing_y": spacing_y, "max": current_max
                }

            if not found and current_max >= params["num_chairs"]: #希望を満たしていない＆指定されたイスより多いイスが配置出来た場合
                #最終版のデータとして保存
                found = True
                final_layout = {
                    "cols": max_cols, "rows": max_rows,
                    "spacing_x": spacing_x, "spacing_y": spacing_y, "max": current_max, "found": True
                }

    # もし希望数が見つからなかった場合、best_layoutをfinal_layoutとして扱う
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
        aisle_every_y = params["aisle_every_y"]
        max_cols = 0
        for c in range(1, MAX_COLS_ROWS_TO_CHECK):
            num_aisles = (c - 1) // aisle_every_x if aisle_every_x > 0 else 0
            tmp_total_w = c * space_x - (space_x - params["chair_width"]) + num_aisles * AISLE_WIDTH_CM + additional_width
            if tmp_total_w > effective_hall_width: break
            max_cols = c
        for r in range(1, MAX_COLS_ROWS_TO_CHECK):
            num_aisles = (r - 1) // aisle_every_y if aisle_every_y > 0 else 0
            total_d = r * space_y - (space_y - params["chair_depth"]) + num_aisles * AISLE_WIDTH_CM
            if total_d > effective_hall_depth: break
            max_rows = r
            
        # every_nモードでも余りスペースをチェック
        # 現在の列数・行数で実際に使用している幅と奥行きを再計算
        num_aisles_x = (max_cols - 1) // aisle_every_x if aisle_every_x > 0 else 0
        current_width = max_cols * space_x - (space_x - params["chair_width"]) + num_aisles_x * AISLE_WIDTH_CM + additional_width
        rem_w = effective_hall_width - current_width
        
        num_aisles_y = (max_rows - 1) // aisle_every_y if aisle_every_y > 0 else 0
        current_depth = max_rows * space_y - (space_y - params["chair_depth"]) + num_aisles_y * AISLE_WIDTH_CM
        rem_d = effective_hall_depth - current_depth

        # 余ったスペースに椅子を追加できるか判定（通路の追加は考慮しない単純なチェック）
        if rem_w >= params["chair_width"]:
            max_cols += 1
        if rem_d >= params["chair_depth"]:
            max_rows += 1

    elif aisle_mode == 'fixed_number':
        num_aisles_x = params["num_aisles_x"]
        num_aisles_y = params["num_aisles_y"]
        chair_area_width = effective_hall_width - num_aisles_x * AISLE_WIDTH_CM
        chair_area_depth = effective_hall_depth - num_aisles_y * AISLE_WIDTH_CM
        if chair_area_width > 0 and chair_area_depth > 0:
            available_width = chair_area_width - additional_width
            max_cols = math.floor(available_width / space_x) if available_width > 0 else 0
            max_rows = math.floor(chair_area_depth / space_y)
            
            # 余ったスペースにもう1脚置けるかチェック
            rem_w = available_width - (max_cols * space_x)
            rem_d = chair_area_depth - (max_rows * space_y)

            if rem_w >= params["chair_width"]:
                max_cols += 1
            if rem_d >= params["chair_depth"]:
                max_rows += 1
        else:
            # イスが置けない場合は0を確実にする
            max_cols, max_rows = 0, 0

    else: # aisle_mode == 'none'
        available_width = effective_hall_width - additional_width
        max_cols = math.floor(available_width / space_x)
        max_rows = math.floor(effective_hall_depth / space_y)

        # 余ったスペースにもう1脚置けるかチェック
        rem_w = available_width - (max_cols * space_x)
        rem_d = effective_hall_depth - (max_rows * space_y)
        
        if rem_w >= params["chair_width"]:
            max_cols += 1
        if rem_d >= params["chair_depth"]:
            max_rows += 1

    return max_cols, max_rows

#3-0.イスの座標を計算し、リストを作成
def calculate_chair_coordinates(params, layout_info):
    coords = [] #座標の入れ物
    layout_cols, layout_rows = layout_info["cols"], layout_info["rows"]
    layout_spacing_x, layout_spacing_y = layout_info["spacing_x"], layout_info["spacing_y"]

    space_x = params["chair_width"] + layout_spacing_x
    space_y = params["chair_depth"] + layout_spacing_y

    additional_width = space_x / 2 if params["zigzag_layout"] else 0
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


#4.座標を元にMatplotlibでレイアウト画像を生成
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


#5.JSONレスポンスを組み立てる
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
    return render_template("sv10.html")
@app.route("/calculate", methods=["POST"])
def calculate():
    try:
        #1.データを受け取り、問題があるか確認
        params = parse_and_validate_input(request.json)

        #2.最適なレイアウトを探す
        best_layout, final_layout = find_optimal_layout(params)

        #イスが1脚も置けない場合は、ここで処理を終了
        if not best_layout:
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
        # 本番環境ではエラーの詳細をログに記録することが望ましい
        print(f"An unexpected error occurred: {e}") # デバッグ用
        return jsonify({"error": "サーバー内部で予期しないエラーが発生しました。"}), 500


if __name__ == "__main__":
    #FLASK_DEBUGという環境変数が '1' の時だけデバッグモードを有効にする
    debug_mode = os.environ.get("FLASK_DEBUG") == '1'
    app.run(debug=debug_mode) #デバッグモードを環境変数で制御