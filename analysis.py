import requests  #api request
from urllib.parse import quote #for weird names to avoid url crashes
import getpass  #for api safety
import json 
import pandas as pd
import numpy as np
import random
from typing import Optional    #x or none
import matplotlib.pyplot as plt
from io import BytesIO
import base64
from concurrent.futures import ThreadPoolExecutor, as_completed
import time


PLATFORM_ROUTING = {
    "euw1": "euw1",
    "eun1": "eun1",
    "na1": "na1",
    "kr": "kr",
    "tr1": "tr1",
}

REGIONAL_ROUTING = {
    "euw1": "europe",
    "eun1": "europe",
    "tr1": "europe",
    "na1": "americas",
    "kr": "asia",
}

def riot_get_json(url, api_key, params=None):
    r = requests.get(url, headers=riot_headers(api_key), params=params)
    data = r.json()
    if r.status_code != 200:
        # include Riot error message in exception
        raise RuntimeError(f"Riot API error {r.status_code}: {data}")
    return data

def riot_headers(api_key: str) -> dict:
    return {"X-Riot-Token": api_key}



def get_puuid(game_name:str, tag_line:str, region_route:str, api_key:str):
    game_name = quote(game_name)
    tag_line = quote(tag_line)

    url = f"https://{region_route}.api.riotgames.com/riot/account/v1/accounts/by-riot-id/{game_name}/{tag_line}"
    return riot_get_json(url,api_key)


def get_summoner_by_puuid(puuid:str,platform_route:str, api_key:str):
    url = f"https://{platform_route}.api.riotgames.com/lol/summoner/v4/summoners/by-puuid/{puuid}"
    return riot_get_json(url, api_key)

def fetch_match(match_id, region_route, api_key, retries=3):
    url = f"https://{region_route}.api.riotgames.com/lol/match/v5/matches/{match_id}"

    for attempt in range(retries):
        try:
            r = requests.get(
                url,
                headers=riot_headers(api_key),
                timeout=10
            )

            if r.status_code == 429:
                retry_after = r.headers.get("Retry-After")
                wait = int(retry_after) if retry_after else (1 + attempt)
                time.sleep(wait)
                continue

            if r.status_code >= 400:
                return {"_error": True, "status": r.status_code}

            return r.json()

        except requests.exceptions.ConnectionError:
            # Connection reset / aborted
            time.sleep(1 + attempt)

        except requests.exceptions.Timeout:
            time.sleep(1 + attempt)

    return {"_error": True, "status": "connection_failed"}

def get_match_ids(puuid:str,region_route:str,api_key:str,count=40):
    url = f"https://{region_route}.api.riotgames.com/lol/match/v5/matches/by-puuid/{puuid}/ids"
    params = {"queue": 420, "count": count}
    r = requests.get(url, headers=riot_headers(api_key), params=params)
    return r.json()

def player_rank(puuid:str,platform_route:str,api_key:str):
    url = f"https://{platform_route}.api.riotgames.com/lol/league/v4/entries/by-puuid/{puuid}"
    r = requests.get(url, headers=riot_headers(api_key))
    return r.json()



def get_rank(puuid:str, platform_route:str, api_key:str, queue="RANKED_SOLO_5x5") -> Optional[dict]:
    rank_entries = player_rank(puuid, platform_route, api_key)

    if not rank_entries:
        return None

    for r in rank_entries:
        if r["queueType"] == queue:
            return {
                "tier": r["tier"],
                "rank": r["rank"],
                "lp": r["leaguePoints"],
                "total_games": r["wins"] + r["losses"],
                "hot_streak": r["hotStreak"]
            }

    return None #unranked


def match_results_parallel(match_ids, region_route, api_key, max_workers=5):
    """
    Fetch match details in parallel (safe for dev: 5 workers).
    Keeps ordering same as match_ids.
    """
    results_by_id = {}

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_map = {
            executor.submit(fetch_match, mid, region_route, api_key): mid
            for mid in match_ids
        }

        for fut in as_completed(future_map):
            mid = future_map[fut]
            results_by_id[mid] = fut.result()

    # return in original order
    return [results_by_id[mid] for mid in match_ids]


TIER_VALUE = {
    "IRON": 0,
    "BRONZE": 1,
    "SILVER": 2,
    "GOLD": 3,
    "PLATINUM": 4,
    "EMERALD": 5,
    "DIAMOND": 6,
}

DIVISION_VALUE = {
    "IV": 0,
    "III": 1,
    "II": 2,
    "I": 3
}

def rank_to_score(tier: str, division: str, lp: int) -> float:
    return (
        TIER_VALUE[tier] * 400 +
        DIVISION_VALUE[division] * 100 +
        lp
    )

def extract_player_and_enemies(matches, my_puuid, max_enemies=20):
    enemy_puuids = set()
    player_match_data = []

    for match in matches:
        for participant in match["info"]["participants"]:
            puuid = participant["puuid"]

            if puuid == my_puuid:
                player_match_data.append({
                    "champion": participant["championName"],
                    "kills": participant["kills"],
                    "deaths": participant["deaths"],
                    "assists": participant["assists"],
                    "win": participant["win"],
                    "team_position": participant["teamPosition"],
                    "individual_position": participant["individualPosition"],
                    "role": participant["role"],
                })
            else:
                if len(enemy_puuids) < max_enemies:
                    enemy_puuids.add(puuid)

    return player_match_data, enemy_puuids


def fetch_enemy_ranks(enemy_puuids, platform_route, api_key, max_enemies=20):  #20 to avoid rate limit
    enemy_rank_cache = {}

    for puuid in enemy_puuids:
        if len(enemy_rank_cache) >= max_enemies:
            break

        rank = get_rank(puuid, platform_route, api_key)
        if rank is not None:
            enemy_rank_cache[puuid] = rank

    return enemy_rank_cache

def compute_enemy_scores(enemy_rank_cache):
    scores = []

    for data in enemy_rank_cache.values():
        score = rank_to_score(
            data["tier"],
            data["rank"],
            data["lp"]
        )
        scores.append(score)

    return scores

def average_enemy_score(enemy_scores, player_score):
    if enemy_scores:
        return sum(enemy_scores) / len(enemy_scores)
    """
    Returns the average enemy score.
    If no valid enemy scores are available, assumes enemy MMR ~= player MMR
    and returns player_score (which is players calculated score)
    """
    return player_score


def mmr_score(
    player_score: float,
    enemy_score: float,
    L_winrate: float,
    games_played: int
) -> int:
    diff = enemy_score - player_score

    # Deadzone
    if abs(diff) < 200:
        return 0

    score = 0

    # Rank gap signal
    if diff >= 400:
        score += 2
    elif diff >= 200:
        score += 1
    elif diff <= -400:
        score -= 2
    elif diff <= -200:
        score -= 1

    # Confidence adjustments
    if L_winrate > 0.55:
        score += 1
    elif L_winrate < 0.45:
        score -= 1

    if games_played < 20:
        score += 1  # early season inflation

    return max(-3, min(3, score))
    
def mmr_lp_bias(mmr_score: int, win: bool) -> int:
    if mmr_score >= 3:
        return 4 if win else 8     # boosted
    elif mmr_score == 2:
        return 2 if win else 4
    elif mmr_score <= -3:
        return -4 if win else -8   # suppressed
    elif mmr_score == -2:
        return -2 if win else -4
    return 0


def estimate_lp_delta(
    tier: str,
    win: bool,
    winrate: float,
    win_streak: int,
    lose_streak: int,
    games_played: int,
    mmr_score: int,
    hot_streak: bool = False
) -> int:
    cfg = RANK_CONFIGS[tier]

    lp = cfg.base_win if win else cfg.base_loss

    if winrate > 0.55:
        lp += cfg.winrate_bonus
    elif winrate < 0.45:
        lp += cfg.winrate_penalty

    if win and win_streak >= 4:
        lp += cfg.streak_bonus
    if not win and lose_streak >= 4:
        lp += cfg.streak_penalty

    if hot_streak and win:
        lp += 1

    # MMR bias effect
    lp += mmr_lp_bias(mmr_score, win)

    volatility = games_played_modifier(games_played)
    lp = int(lp * volatility)

    lp = max(cfg.min_lp, min(cfg.max_lp, lp)) if win else min(-cfg.min_lp, max(-cfg.max_lp, lp))

    return lp

from dataclasses import dataclass

@dataclass
class RankConfig:
    tier: str
    base_win: int
    base_loss: int
    winrate_bonus: int
    winrate_penalty: int
    streak_bonus: int
    streak_penalty: int
    min_lp: int
    max_lp: int

RANK_CONFIGS = {
    "IRON": RankConfig(
        tier="IRON",
        base_win=22,
        base_loss=-18,
        winrate_bonus=3,
        winrate_penalty=-2,
        streak_bonus=2,
        streak_penalty=-1,
        min_lp=15,
        max_lp=30
    ),
    "BRONZE": RankConfig(
        tier="BRONZE",
        base_win=21,
        base_loss=-19,
        winrate_bonus=3,
        winrate_penalty=-2,
        streak_bonus=2,
        streak_penalty=-1,
        min_lp=15,
        max_lp=28
    ),
    "SILVER": RankConfig(
        tier="SILVER",
        base_win=20,
        base_loss=-20,
        winrate_bonus=3,
        winrate_penalty=-2,
        streak_bonus=2,
        streak_penalty=-2,
        min_lp=14,
        max_lp=26
    ),
    "GOLD": RankConfig(
        tier="GOLD",
        base_win=20,
        base_loss=-20,
        winrate_bonus=3,
        winrate_penalty=-2,
        streak_bonus=2,
        streak_penalty=-2,
        min_lp=14,
        max_lp=25
    ),
    "PLATINUM": RankConfig(
        tier="PLATINUM",
        base_win=19,
        base_loss=-21,
        winrate_bonus=2,
        winrate_penalty=-3,
        streak_bonus=1,
        streak_penalty=-2,
        min_lp=13,
        max_lp=24
    ),"EMERALD": RankConfig(
        tier="EMERALD",
        base_win=18,
        base_loss=-22,
        winrate_bonus=2,
        winrate_penalty=-4,
        streak_bonus=1,
        streak_penalty=-3,
        min_lp=12,
        max_lp=24
    ),
    "DIAMOND": RankConfig(
        tier="DIAMOND",
        base_win=17,
        base_loss=-23,
        winrate_bonus=2,
        winrate_penalty=-4,
        streak_bonus=1,
        streak_penalty=-3,
        min_lp=12,
        max_lp=22
    )
}

def games_played_modifier(games_played: int) -> float:
    """
    Returns a multiplier for LP volatility.
    """
    if games_played < 20:
        return 1.15   # very volatile
    elif games_played < 50:
        return 1.08
    elif games_played < 100:
        return 1.03
    else:
        return 1.0    # stable


def build_match_dataframe(player_match_data: list) -> pd.DataFrame:
    df = pd.DataFrame(player_match_data)

    df["Is Autofilled?"] = (
        df["individual_position"] == df["team_position"]   #Autofill func should be checked again not safe
    ).map({True: "No", False: "Yes"})

    return df

def compute_longterm_stats(df: pd.DataFrame) -> dict:
    longterm_winrate = df["win"].mean()

    first_result = df.iloc[0]["win"]
    streak_length = (df["win"] == first_result).cumprod().sum()

    return {
        "longterm_winrate": longterm_winrate,
        "initial_streak": int(streak_length)
    }


def simulate_lp_changes(
    df: pd.DataFrame,
    player_score: float,
    avg_enemy_score: float,
    player_rank: dict,
    games_played_start: int,
    max_games: int = 20
) -> list[int]:

    wins_so_far = 0
    losses_so_far = 0
    win_streak = 0
    lose_streak = 0
    games_played = games_played_start

    lp_changes = []
    prior_games = 10
    longterm_wr = df["win"].mean()

    for i, win in enumerate(df["win"]):
        if i >= max_games:
            break

        if win:
            wins_so_far += 1
            win_streak += 1
            lose_streak = 0
        else:
            losses_so_far += 1
            lose_streak += 1
            win_streak = 0

        games_played += 1

        wins = wins_so_far + (prior_games * longterm_wr)
        total = wins_so_far + losses_so_far + prior_games
        shortterm_wr = wins / total

        hot_streak = win_streak >= 3

        mmr = mmr_score(
            player_score,
            avg_enemy_score,
            longterm_wr,
            games_played
        )

        lp = estimate_lp_delta(
            tier=player_rank["tier"],
            win=win,
            winrate=shortterm_wr,
            win_streak=win_streak,
            lose_streak=lose_streak,
            games_played=games_played,
            mmr_score=mmr,
            hot_streak=hot_streak
        )

        lp_changes.append(lp)

    return lp_changes

def build_rank_score_df(df, player_score, lp_changes):
    """
    Builds historical rank score timeline used for plotting.
    """
    df_lp = df.iloc[:-19].copy()
    df_lp["match_no"] = len(df_lp) - df_lp.index

    scores = [player_score]
    for lp_delta in lp_changes:
        scores.append(scores[-1] - lp_delta)

    df_lp["rank_score"] = scores
    return df_lp


tier_ranges = [("I",0,400),("B",400,800),
    ("S", 800, 1200),
    ("G", 1200, 1600),
    ("P", 1600, 2000),
    ("E", 2000, 2400),
    ("D", 2400, 2800),
]

tier_colors = {"I":"#4A4A4A",
    "B": "#8c6239",
    "S": "#b0c4de",
    "G": "#d4af37",
    "P": "#66cccc",
    "E":"#4ecb8f",
    "D": "#66a3ff"
}

def score_to_short_label(score):
    tier_num = score // 400
    div_num = (score % 400) // 100

    tier_map = {0:"I", 1:"B", 2:"S", 3:"G", 4:"P", 5:"E",6:"D"}
    div_map = {0:"IV", 1:"III", 2:"II", 3:"I"}

    tier = tier_map.get(tier_num, "")
    division = div_map.get(div_num, "")

    return f"{tier} {division}"

"--------------------------------------------------"

"---------------------------------------------------"
def compute_ticks_from_scores(df_lp):
    min_score = df_lp["rank_score"].min()
    max_score = df_lp["rank_score"].max()

    start = (min_score // 100) * 100
    end = ((max_score // 100) + 1) * 100

    ticks = list(range(start, end + 1, 100))
    labels = [score_to_short_label(t) for t in ticks]

    return ticks, labels

def compute_y_window(player_score, window=500):
    return player_score - window, player_score + window

def render_rank_plot(
    df_lp,
    y_min,
    y_max,
    all_futures,
    tier_ranges,
    tier_colors
):
    fig, ax = plt.subplots(figsize=(14, 6))

    # background tiers
    for tier, t_min, t_max in tier_ranges:
        if t_max <= y_min or t_min >= y_max:
            continue
        ax.axhspan(
            max(t_min, y_min),
            min(t_max, y_max),
            color=tier_colors[tier],
            alpha=0.22,
            zorder=0
        )

    # grid lines
    for y in range(int(y_min//100)*100, int(y_max//100+1)*100, 100):
        ax.axhline(y, color="white", alpha=0.12)

    # rank history
    ax.plot(
        df_lp["match_no"],
        df_lp["rank_score"],
        linewidth=2.5,
        marker="o",
        zorder=3
    )

    ticks, labels = compute_ticks_from_scores(df_lp)
    ax.set_yticks(ticks)
    ax.set_yticklabels(labels)

    ax.set_title("Rank Progression (Estimated LP)")
    ax.set_xlabel("Match")
    ax.set_ylabel("Rank")

    return fig

def expected_win_prob(winrate, games_played):
    # confidence increases with games
    weight = min(games_played / 50, 1)
    return 0.5 * (1 - weight) + winrate * weight


def sample_lp_delta(win, base_lp, streak):  
    
    noise = np.random.normal(0, 6)  # for randomness sd
    # streak multiplier
    if win:
        multiplier = 1 + 0.08 * min(streak, 5)
        delta = base_lp * multiplier + noise
        return max(5, delta)
    else:
        multiplier = 1 + 0.10 * min(abs(streak), 5)
        delta = base_lp * multiplier + noise
        return -max(5, abs(delta))


def simulate_future(
    start_score,
    win_prob,
    base_lp,
    games=20
):
    f_score = start_score
    f_scores = []
    
    streak = 0
    for _ in range(games):
        win = np.random.rand() < win_prob

        if win:
            streak = max(1, streak + 1)
        else:
            streak = min(-1, streak - 1)

        lp = sample_lp_delta(win, base_lp, streak)
        f_score += lp
        f_scores.append(f_score)

    return f_scores

def simulate_all_futures(
    player_score,
    winrate,
    games_played,
    avg_lp_gain,
    sims=1000,
    games_ahead=20
):
    futures = []
    for _ in range(sims):
        futures.append(
            simulate_future(
                player_score,
                expected_win_prob(winrate, games_played),
                avg_lp_gain,
                games_ahead
            )
        )
    return np.array(futures)



def summarize_futures(
    all_futures,
    player_score: float,
    current_lp: int,
    games_ahead: int,
    fan_paths: int = 30
):
    final_scores = np.array([path[-1] for path in all_futures], dtype=float)

    expected_score = float(final_scores.mean())
    low, high = np.percentile(final_scores, [25, 75]).astype(float).tolist()

    # Prob of reaching next division (touching final >= threshold)
    current_div_base = player_score - current_lp
    next_div_score = current_div_base + 100
    rank_up_prob = float((final_scores >= next_div_score).mean())

    futures_arr = np.array(all_futures, dtype=float)
    mean_path = futures_arr.mean(axis=0).tolist()
    low_path  = np.percentile(futures_arr, 25, axis=0).tolist()
    high_path = np.percentile(futures_arr, 75, axis=0).tolist()

    k = min(fan_paths, len(all_futures))
    sample_paths = random.sample(all_futures, k=k)
    sample_paths = [[float(v) for v in path] for path in sample_paths]

    return {
        "expected_score": expected_score,
        "confidence_25_75": [float(low), float(high)],
        "rank_up_prob": rank_up_prob,
        "plot": {
            "games_ahead": int(games_ahead),
            "mean_path": [float(v) for v in mean_path],
            "low_path": [float(v) for v in low_path],
            "high_path": [float(v) for v in high_path],
            "sample_paths": sample_paths
        }
    }


def plot_future_projection(
    future_x,
    all_futures,
    mean_path,
    low_path,
    high_path
):
    plt.plot(future_x, mean_path, linestyle="--", color="white")

    plt.fill_between(
        future_x,
        low_path,
        high_path,
        color="white",
        alpha=0.15,
        label="Typical range"
    )

    for i in np.random.choice(len(all_futures), 30, replace=False):
        plt.plot(
            future_x,
            all_futures[i],
            color="white",
            alpha=0.12,
            linewidth=1.3
        )


#promotion/demotion probability

def rank_movement_probabilities(
    all_futures,
    current_score,
    steps_up=3,
    steps_down=2,
    division_size=100
):
    probs = {
        "promotion": {},
        "demotion": {}
    }

    # Promotions (touching higher divisions)
    current_div = current_score // division_size

    for i in range(1, steps_up + 1):
        target = (current_div + i) * division_size
        prob = np.mean([np.max(path) >= target for path in all_futures])
        probs["promotion"][target] = prob

    # Demotions (touching lower divisions)
    for i in range(1, steps_down + 1):
        target = (current_div - i) * division_size
        if target < 0:
            continue  # can't go below 0 LP

        prob = np.mean([np.min(path) <= target for path in all_futures])
        probs["demotion"][target] = prob

    return probs

def format_movement_probs(movement_probs):
    return {
        "promotion": [
            {
                "score": score,
                "label": score_to_short_label(score),
                "probability": float(prob)
            }
            for score, prob in sorted(movement_probs["promotion"].items())  # sorted low→high
        ],
        "demotion": [
            {
                "score": score,
                "label": score_to_short_label(score),
                "probability": float(prob)
            }
            for score, prob in sorted(movement_probs["demotion"].items(), reverse=True)  # closest demotion first
        ]
    }


def forecast_rank_progression(
    player_score,
    winrate,
    games_played,
    lp_changes,
    current_lp,         # so summarize can compute next division
    games_ahead=20,
    sims=1000
):
    avg_lp = float(sum(lp_changes) / len(lp_changes))

    all_futures = [
        simulate_future(
            start_score=player_score,
            win_prob=expected_win_prob(winrate, games_played),
            base_lp=avg_lp,
            games=games_ahead
        )
        for _ in range(sims)
    ]

    movement_probs_raw = rank_movement_probabilities(
        all_futures=all_futures,
        current_score=player_score
    )

    summary = summarize_futures(
        all_futures=all_futures,
        player_score=player_score,
        current_lp=current_lp,
        games_ahead=games_ahead
    )

    return {
        "expected_score": summary["expected_score"],
        "confidence_25_75": summary["confidence_25_75"],
        "rank_up_prob": summary["rank_up_prob"],
        "movement_probs": format_movement_probs(movement_probs_raw),
        "plot": summary["plot"]
    }

def analyze_player(
    summoner_name: str,
    tag_line: str,
    region: str,
    api_key: str
):
    platform_route = PLATFORM_ROUTING[region]
    region_route = REGIONAL_ROUTING[region]

    # FETCH CORE DATA
    account = get_puuid(summoner_name, tag_line, region_route, api_key)
    puuid = account["puuid"]

    match_ids = get_match_ids(puuid, region_route, api_key, count=40)
    rank_data = get_rank(puuid, platform_route, api_key)
    matches = match_results_parallel(match_ids, region_route, api_key, max_workers=5)

    # PLAYER & ENEMY CONTEXT
    player_match_data, enemy_puuids = extract_player_and_enemies(matches, puuid)
    enemy_rank_cache = fetch_enemy_ranks(enemy_puuids, platform_route, api_key)

    # SCORE COMPUTATION
    player_score = rank_to_score(rank_data["tier"], rank_data["rank"], rank_data["lp"])
    enemy_scores = compute_enemy_scores(enemy_rank_cache)
    avg_enemy = average_enemy_score(enemy_scores, player_score)

    # MATCH DATAFRAME + STATS
    df = build_match_dataframe(player_match_data)
    stats = compute_longterm_stats(df)

    # LP HISTORY
    lp_changes = simulate_lp_changes(
        df=df,
        player_score=player_score,
        avg_enemy_score=avg_enemy,
        player_rank=rank_data,
        games_played_start=rank_data["total_games"]
    )

    rank_history_df = build_rank_score_df(df=df, player_score=player_score, lp_changes=lp_changes)
    rank_history_records = rank_history_df.to_dict("records")

    # FORECAST
    forecast = forecast_rank_progression(
        player_score=player_score,
        winrate=stats["longterm_winrate"],
        games_played=rank_data["total_games"],
        lp_changes=lp_changes,
        current_lp=rank_data["lp"],
        games_ahead=20,
        sims=1000
    )

    forecast_public = {
        "expected_score": float(forecast["expected_score"]),
        "confidence_25_75": [float(x) for x in forecast["confidence_25_75"]],
        "movement_probs": forecast["movement_probs"],
        "plot": forecast["plot"]  
    }

    # FINAL RESPONSE
    return {
        "player_score": float(player_score),
        "avg_enemy_score": float(avg_enemy),
        "longterm_winrate": float(stats["longterm_winrate"]),
        "lp_changes": [int(x) for x in lp_changes],
        "matches": player_match_data,
        "rank_history": rank_history_records,
        "forecast": forecast_public
    }