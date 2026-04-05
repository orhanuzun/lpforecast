from flask import Flask, jsonify, request,send_from_directory, redirect
import os
import hashlib
import json
import time 
import traceback
from urllib.parse import quote
from json import JSONDecodeError
from urllib.parse import unquote

from analysis import analyze_player,get_puuid,player_rank, REGIONAL_ROUTING,PLATFORM_ROUTING
from plotting import save_rank_plot_png

print("RUNNING FILE:", __file__)
print("CWD:", os.getcwd())
API_KEY = os.getenv("RIOT_API_KEY")

if not API_KEY:
    raise RuntimeError("RIOT_API_KEY environment variable not set")



app = Flask(__name__)

CACHE_TTL_SECONDS = 150
CACHE_DIR = "cache"


def _cache_key(region: str, summoner: str, tag: str) -> str:
    raw = f"{region.lower()}|{summoner.lower()}|{tag.lower()}"
    return hashlib.md5(raw.encode("utf-8")).hexdigest()


def _cache_paths(key: str):  #To make website run faster
    os.makedirs(CACHE_DIR, exist_ok=True)          
    os.makedirs("static/plots", exist_ok=True)     
    return os.path.join(CACHE_DIR, f"{key}.json")


def _is_cache_valid(path: str, ttl: int) -> bool:
    if not os.path.exists(path):
        return False
    age = time.time() - os.path.getmtime(path)
    return age <= ttl

def movement_summary_html(result: dict) -> str:
    """
    Shows:
    Promote to X: p%
    Stay in Current: s%
    Demote to Y: d%
    """
    movement = result["forecast"]["movement_probs"]
    promo_list = movement.get("promotion", [])
    demo_list  = movement.get("demotion", [])

    # Current division label
    def score_to_short_label(score: int) -> str:
        tier_num = score // 400
        div_num = (score % 400) // 100
        tier_map = {0:"I", 1:"B", 2:"S", 3:"G", 4:"P", 5:"E", 6:"D"}
        div_map = {0:"IV", 1:"III", 2:"II", 3:"I"}
        return f"{tier_map.get(tier_num,'')} {div_map.get(div_num,'')}"

    player_score = int(result["player_score"])
    current_label = score_to_short_label(player_score)

    # Next promotion target = first (smallest) promotion score
    promo_prob = float(promo_list[0]["probability"]) if promo_list else 0.0
    promo_label = promo_list[0]["label"] if promo_list else None

    # Closest demotion target = first (highest below current) demotion score
    demo_prob = float(demo_list[0]["probability"]) if demo_list else 0.0
    demo_label = demo_list[0]["label"] if demo_list else None

    # "Stay" = not touching promotion AND not touching demotion)
    stay_prob = max(0.0, 1.0 - promo_prob - demo_prob)

    lines = []
    if promo_label:
        lines.append(f"⬆️ Promote to <b>{promo_label}</b>: <b>{promo_prob*100:.1f}%</b>")
    lines.append(f"➖ Stay in <b>{current_label}</b>: <b>{stay_prob*100:.1f}%</b>")
    if demo_label:
        lines.append(f"⬇️ Demote to <b>{demo_label}</b>: <b>{demo_prob*100:.1f}%</b>")

    return "<br>".join(lines)

@app.route("/", methods=["GET"])
def index():
    return """
    <html>
      <head>
        <title>LP Analyzer</title>
      </head>
      <body style="font-family:Arial; background:#111; color:#eee; padding:30px;">
        <h1>LP Analyzer</h1>
        <p>Enter your Riot ID and select your region.</p>

        <form action="/go" method="POST" style="margin-top:20px;">
          <label>Riot ID (name#tag):</label><br>
          <input name="riot_id" style="padding:10px; width:320px;" placeholder="Rragnatingha#EUW" required>
          <br><br>

          <label>Region:</label><br>
          <select name="region" style="padding:10px; width:340px;">
            <option value="euw1">EUW</option>
            <option value="eun1">EUNE</option>
            <option value="tr1">TR</option>
            <option value="na1">NA</option>
            <option value="kr">KR</option>
          </select>
          <br><br>

          <button type="submit" style="padding:12px 20px; font-size:16px;">
            Forecast
          </button>
        </form>

      </body>
    </html>
    """

@app.route("/riot.txt")
def riot_verification():
    return send_from_directory("static", "riot.txt")

@app.route("/go", methods=["POST"])
def go():
    riot_id = request.form.get("riot_id", "").strip()
    region = request.form.get("region", "euw1")

    if not riot_id or "#" not in riot_id:
        return redirect("/notfound?msg=invalid_id",code=302)

    summoner, tag = riot_id.split("#", 1)

    # URL-safe
    summoner_q = quote(summoner, safe="")
    tag_q = quote(tag, safe="")

    return redirect(f"/view/{region}/{summoner_q}/{tag_q}", code=302)

@app.route("/analyze/<region>/<summoner>/<tag>")
def analyze(region, summoner, tag):
    try:
        result = analyze_player(
            summoner_name=summoner,
            tag_line=tag,
            region=region,
            api_key=API_KEY
        )
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 400


from urllib.parse import unquote

@app.route("/view/<region>/<summoner>/<tag>")
def view(region, summoner, tag):
    print("VIEW HIT:", region, summoner, tag, flush=True)

    summoner = unquote(summoner)
    tag = unquote(tag)

    try:
        # Quick pre-checks (fast fail)
        region_route = REGIONAL_ROUTING.get(region)
        platform_route = PLATFORM_ROUTING.get(region)
        if not region_route or not platform_route:
            return redirect("/notfound?msg=not_found", code=302)

        account = get_puuid(summoner, tag, region_route, API_KEY)
        if not account or "puuid" not in account:
            return redirect("/notfound?msg=not_found", code=302)

        puuid = account["puuid"]

        rank_data = player_rank(puuid, platform_route, API_KEY)  # or get_rank(...)
        if not rank_data:
            return redirect("/unranked", code=302)

        # pick SOLO/DUO entry (Other ranked match options will be added)
        rank_entry = next(
            (r for r in rank_data if r.get("queueType") == "RANKED_SOLO_5x5"),None)

        # if they have no solo/duo rank, treated as unranked
        if not rank_entry:
            return redirect("/unranked", code=302)

        tier = (rank_entry.get("tier") or "").upper()
        if tier in ("MASTER", "GRANDMASTER", "CHALLENGER"):
            return redirect("/unsupported", code=302)

        # Cache setup
        key = _cache_key(region, summoner, tag)
        cache_json_path = _cache_paths(key)

        # Serve cached if fresh
        if _is_cache_valid(cache_json_path, CACHE_TTL_SECONDS):
            try:
                with open(cache_json_path, "r", encoding="utf-8") as f:
                    cached = json.load(f)
            except (JSONDecodeError, OSError) as e:
                print("CACHE READ BAD -> deleting:", cache_json_path, repr(e), flush=True)
                try:
                    os.remove(cache_json_path)
                except OSError:
                    pass
                cached = {}

            plot_url = cached.get("plot_url")
            result = cached.get("result") or {}

            if plot_url and not plot_url.startswith("/"):
                plot_url = "/" + plot_url

            if plot_url and result:
                moves_html = movement_summary_html(result)
                disk_path = plot_url[1:] if plot_url.startswith("/") else plot_url
                if os.path.exists(disk_path):
                    return f"""
                        <html>
                          <head><title>LP Analyzer</title></head>
                          <body style="font-family: Arial; background:#111; color:#eee; padding:20px;">
                            <h2>Rank Plot for {summoner}#{tag}</h2>

                            <p style="line-height:1.6;">
                              <b>Winrate:</b> {result["longterm_winrate"]:.2f}
                              <br>
                              <b>Rank move probabilities (next 20 games):</b><br>
                              {moves_html}
                              <br>
                              <small>(cached — refresh is fast for {CACHE_TTL_SECONDS}s)</small>
                            </p>

                            <img src="{plot_url}" style="max-width:100%; border:1px solid #333; border-radius:8px;">
                          </body>
                        </html>
                    """

        # Compute fresh
        result = analyze_player(
            summoner_name=summoner,
            tag_line=tag,
            region=region,
            api_key=API_KEY
        )

        if not result or not result.get("rank_history"):
            return redirect("/unranked", code=302)

        plot_url = save_rank_plot_png(
            rank_history=result["rank_history"],
            forecast_plot=result["forecast"]["plot"],
            out_dir="static/plots"
        )
        if not plot_url.startswith("/"):
            plot_url = "/" + plot_url

        payload = {
            "timestamp": time.time(),
            "plot_url": plot_url,
            "result": result
        }

        tmp_path = cache_json_path + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(payload, f)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, cache_json_path)

        moves_html = movement_summary_html(result)

        return f"""
            <html>
              <head><title>LP Analyzer</title></head>
              <body style="font-family: Arial; background:#111; color:#eee; padding:20px;">
                <h2>Rank Plot for {summoner}#{tag}</h2>

                <p style="line-height:1.6;">
                  <b>Winrate:</b> {result["longterm_winrate"]:.2f}
                  <br>
                  <b>Rank move probabilities (next 20 games):</b><br>
                  {moves_html}
                  <br>
                  <small>(cached — refresh is fast for {CACHE_TTL_SECONDS}s)</small>
                </p>

                <img src="{plot_url}" style="max-width:100%; border:1px solid #333; border-radius:8px;">
              </body>
            </html>
        """

    except Exception as e:
        print("VIEW ERROR:", repr(e), flush=True)
        traceback.print_exc()

        msg = str(e)

        if "429" in msg or "rate limit" in msg.lower():
            return "<pre>Rate limited. Please try again in ~20-30 seconds.</pre>", 429

        # player not found / bad id
        if "404" in msg or "DATA_NOT_FOUND" in msg or "not found" in msg.lower():
            return redirect("/notfound?msg=not_found", code=302)

        # unranked errors
        if "NoneType" in msg and "subscriptable" in msg:
            return redirect("/unranked", code=302)

        # default
        return f"<pre>Unexpected error:\n{msg}</pre>", 500


@app.route("/unranked")
def unranked_page():
    return """
        <html>
          <head><title>LP Analyzer</title></head>
          <body style="font-family: Arial; background:#111; color:#eee; padding:40px; text-align:center;">
            
            <div style="
              max-width:720px;
              margin:0 auto;
              background:#161b22;
              border:1px solid #30363d;
              border-radius:12px;
              padding:28px 22px;
              box-shadow: 0 10px 30px rgba(0,0,0,0.35);
            ">
              <h2 style="margin:0; font-size:26px;">🧾 No ranked data found</h2>

              <p style="margin-top:18px; font-size:16px; line-height:1.6; color:#c9d1d9;">
                This player is currently <b>unranked</b> in Solo/Duo, so we can’t generate a ranked forecast yet.
                <br><br>
                Try again after they complete placement games, or search another account.
              </p>

              <div style="margin-top:26px;">
                <a href="/" style="
                  display:inline-block;
                  padding:12px 20px;
                  background:#1f6feb;
                  color:white;
                  text-decoration:none;
                  border-radius:8px;
                  font-weight:600;
                ">⬅ Go Back</a>
              </div>
            </div>

          </body>
        </html>
        """

@app.route("/notfound")
def notfound_page():
    msg = request.args.get("msg", "")
    detail = "Check that the Riot ID is correct (name#TAG) and the region matches."
    if msg == "invalid_id":
        detail = "Please enter your Riot ID in this format: <b>name#TAG</b> (example: Faker#KR1)."

    return f"""
        <html>
          <head><title>LP Analyzer</title></head>
          <body style="font-family: Arial; background:#111; color:#eee; padding:40px; text-align:center;">

            <div style="
              max-width:720px;
              margin:0 auto;
              background:#161b22;
              border:1px solid #30363d;
              border-radius:12px;
              padding:28px 22px;
              box-shadow: 0 10px 30px rgba(0,0,0,0.35);
            ">
              <h2 style="margin:0; font-size:26px;">🔍 Player not found</h2>

              <p style="margin-top:18px; font-size:16px; line-height:1.6; color:#c9d1d9;">
                {detail}
                <br><br>
                <span style="color:#8b949e; font-size:14px;">
                  Tip: Riot IDs are case-sensitive sometimes and tags differ by region.
                </span>
              </p>

              <div style="margin-top:26px;">
                <a href="/" style="
                  display:inline-block;
                  padding:12px 20px;
                  background:#1f6feb;
                  color:white;
                  text-decoration:none;
                  border-radius:8px;
                  font-weight:600;
                ">⬅ Go Back</a>
              </div>
            </div>

          </body>
        </html>
        """

@app.route("/unsupported")
def unsupported_page():
    return """
                <html>
                  <head><title>LP Analyzer</title></head>
                  <body style="font-family: Arial; background:#111; color:#eee; padding:40px; text-align:center;">
                    
                    <h2>🚧 High Elo Forecast In Progress</h2>

                    <p style="margin-top:20px; font-size:16px;">
                      Rank forecasting for tiers above <b>Diamond I</b> is currently under development.
                      <br><br>
                      We are working on extending accurate projections to Master, Grandmaster, and Challenger tiers.
                    </p>

                    <div style="margin-top:30px;">
                      <a href="/" style="
                            padding:12px 20px;
                            background:#1f6feb;
                            color:white;
                            text-decoration:none;
                            border-radius:6px;
                      ">
                        ⬅ Go Back
                      </a>
                    </div>

                  </body>
                </html>
            """

@app.route("/_routes")
def routes_debug():
    routes = "\n".join(sorted([str(r) for r in app.url_map.iter_rules()]))
    return f"""
        <html>
          <head><title>LP Analyzer - Routes</title></head>
          <body style="font-family: Arial; background:#111; color:#eee; padding:40px;">

            <div style="
              max-width:900px;
              margin:0 auto;
              background:#161b22;
              border:1px solid #30363d;
              border-radius:12px;
              padding:22px;
              box-shadow: 0 10px 30px rgba(0,0,0,0.35);
            ">
              <h2 style="margin:0 0 14px 0; font-size:22px;">🧭 Registered Routes</h2>

              <pre style="
                margin:0;
                padding:14px;
                background:#0d1117;
                border:1px solid #30363d;
                border-radius:10px;
                overflow:auto;
                color:#c9d1d9;
                line-height:1.5;
                font-size:13px;
              ">{routes}</pre>

              <div style="margin-top:18px;">
                <a href="/" style="
                  display:inline-block;
                  padding:10px 16px;
                  background:#1f6feb;
                  color:white;
                  text-decoration:none;
                  border-radius:8px;
                  font-weight:600;
                ">⬅ Back to Home</a>
              </div>
            </div>

          </body>
        </html>
        """

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
