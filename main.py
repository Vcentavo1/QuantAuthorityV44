from flask import Flask, render_template, request, jsonify, redirect, session
import websocket, json, threading, time
from datetime import datetime, timezone, timedelta
import os

app = Flask(__name__, template_folder="templates")
app.secret_key = "supersecretv44"

TOKEN_DERIV = "QVQuxsl2pJRDWCS"
signals = {}
threads = {}
lock = threading.Lock()
stats = {"wins": 0, "losses": 0, "winrate": 0}

USERS = {"12345": "senha123", "99999": "senha999"}

DATA_INICIO = datetime(2026, 3, 1)
DATA_FIM = datetime(2026, 3, 5)

def verificar_licenca():
    agora = datetime.now()
    return DATA_INICIO <= agora <= DATA_FIM

def get_time():
    now = datetime.now(timezone.utc) + timedelta(hours=2)
    weekday_map = {"Monday":"Seg","Tuesday":"Ter","Wednesday":"Qua",
                   "Thursday":"Qui","Friday":"Sex","Saturday":"Sáb","Sunday":"Dom"}
    day = now.strftime("%d/%b")
    weekday = weekday_map.get(now.strftime("%A"), now.strftime("%A"))
    return f"{weekday},{day} {now.strftime('%H:%M')}"

def deriv_ws_connect():
    ws = websocket.WebSocket()
    ws.connect("wss://ws.binaryws.com/websockets/v3?app_id=1089")
    ws.send(json.dumps({"authorize": TOKEN_DERIV}))
    ws.recv()
    return ws

def list_assets():
    ws = deriv_ws_connect()
    ws.send(json.dumps({"active_symbols": "full"}))
    while True:
        data = json.loads(ws.recv())
        if "active_symbols" in data:
            assets = [{"symbol": a["symbol"], "display": a["display_name"]} 
                      for a in data["active_symbols"]]
            assets.sort(key=lambda x: x["display"])
            return assets

def calculate_signal(candles):
    if len(candles) < 3: return {"status": "Aguardando o rompimento"}
    c0, c1, c2 = candles[-1], candles[-2], candles[-3]
    high_prev, low_prev, close = c1['high'], c1['low'], c0['close']
    direction = None
    if close > high_prev: direction = "BUY"; entry = high_prev; SL = c0['low']
    elif close < low_prev: direction = "SELL"; entry = low_prev; SL = c0['high']
    else: return {"status": "Aguardando o rompimento"}
    risk = abs(entry - SL)
    if risk == 0: return None
    if direction == "BUY":
        TP1 = entry + risk; TP2 = entry + risk*2; TP3 = entry + risk*3; re_entry = entry + risk/2
        level_150 = entry + risk*1.5; level_200 = entry + risk*2
    else:
        TP1 = entry - risk; TP2 = entry - risk*2; TP3 = entry - risk*3; re_entry = entry - risk/2
        level_150 = entry - risk*1.5; level_200 = entry - risk*2
    prob = 65
    if direction == "BUY" and c1['close'] > c1['open'] and c2['close'] > c2['open']: prob = 75
    if direction == "SELL" and c1['close'] < c1['open'] and c2['close'] < c2['open']: prob = 75
    force = 100
    return {"direction": direction, "entry": entry, "re_entry": re_entry,
            "SL": SL, "TP1": TP1, "TP2": TP2, "TP3": TP3,
            "level_150": level_150, "level_200": level_200,
            "prob": prob, "force": force, "status": "Confirmado",
            "time": get_time(), "current_price": close,
            "tp1_counted": False, "tp2_counted": False, "tp3_counted": False,
            "sl_counted": False}

def monitor_symbol(symbol, granularity):
    ws = deriv_ws_connect()
    ws.send(json.dumps({"ticks_history": symbol, "adjust_start_time": 1, "count": 20,
                        "end": "latest", "granularity": granularity, "style": "candles",
                        "subscribe": 1}))
    last_ts = None
    while True:
        try:
            data = json.loads(ws.recv())
            if "candles" in data:
                candles = data["candles"]
                ts = candles[-1]["epoch"]
                if ts != last_ts:
                    last_ts = ts
                    signal = calculate_signal(candles)
                    with lock:
                        signals[symbol] = signal
        except:
            time.sleep(2)
            ws = deriv_ws_connect()

# ====================== ROTAS ======================

@app.route("/", methods=["GET", "POST"])
def login():
    if not verificar_licenca():
        return render_template("licenca_expirada.html")
    if request.method == "POST":
        user = request.form.get("user")
        senha = request.form.get("senha")
        if user in USERS and USERS[user] == senha:
            session["user"] = user
            return redirect("/app")
        return render_template("login.html", error="ID ou Senha incorretos!")
    return render_template("login.html", error="")

@app.route("/app")
def app_main():
    if not verificar_licenca(): return render_template("licenca_expirada.html")
    if "user" not in session: return redirect("/")
    assets = list_assets()
    return render_template("index.html", assets=assets)

@app.route("/logout")
def logout():
    session.pop("user", None)
    return redirect("/")

@app.route("/start", methods=["POST"])
def start():
    if "user" not in session or not verificar_licenca():
        return jsonify({"status": "not_logged_or_expired"})
    data = request.json
    symbols = data["symbols"]
    granularity = int(data["granularity"])
    for s in symbols:
        if s and s not in threads:
            t = threading.Thread(target=monitor_symbol, args=(s, granularity))
            t.daemon = True
            t.start()
            threads[s] = t
    return jsonify({"status": "running"})

@app.route("/signals")
def get_signals():
    if "user" not in session or not verificar_licenca(): return jsonify({})
    with lock:
        return jsonify(signals)

@app.route("/clear", methods=["POST"])
def clear_signals():
    if "user" not in session or not verificar_licenca():
        return jsonify({"status": "not_logged_or_expired"})
    with lock:
        signals.clear()
    return jsonify({"status": "cleared"})

@app.route("/win")
def add_win():
    if "user" not in session or not verificar_licenca(): return jsonify({"status": "not_logged_or_expired"})
    with lock:
        stats["wins"] += 1
        stats["winrate"] = int(stats['wins'] / (stats['wins'] + stats['losses']) * 100 
                               if stats['wins'] + stats['losses'] > 0 else 0)
    return jsonify(stats)

@app.route("/loss")
def add_loss():
    if "user" not in session or not verificar_licenca(): return jsonify({"status": "not_logged_or_expired"})
    with lock:
        stats["losses"] += 1
        stats["winrate"] = int(stats['wins'] / (stats['wins'] + stats['losses']) * 100 
                               if stats['wins'] + stats['losses'] > 0 else 0)
    return jsonify(stats)

@app.route("/stats")
def get_stats():
    if "user" not in session or not verificar_licenca(): return jsonify({"status": "not_logged_or_expired"})
    with lock:
        return jsonify(stats)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5084)
