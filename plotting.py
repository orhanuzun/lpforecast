#RIOT_API_KEY=RGAPI- cmd ye
#cd C:\Users\Orhan\OneDrive\Masaüstü\Flask_f


import os
from datetime import datetime

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

tier_ranges = [("I",0,400),("B",400,800), ("S", 800, 1200), ("G", 1200, 1600), ("P", 1600, 2000), ("E", 2000, 2400), ("D", 2400, 2800)] 

tier_colors = {"I":"#4A4A4A", "B": "#8c6239", "S": "#b0c4de", "G": "#d4af37", "P": "#66cccc", "E":"#4ecb8f", "D": "#66a3ff" }

def score_to_short_label(score: float) -> str:
    s = int(score)
    tier_num = s // 400
    div_num = (s % 400) // 100

    tier_map = {0: "I", 1: "B", 2: "S", 3: "G", 4: "P", 5: "E", 6: "D"}
    div_map  = {0: "IV", 1: "III", 2: "II", 3: "I"}

    tier = tier_map.get(tier_num, "")
    division = div_map.get(div_num, "")
    return f"{tier} {division}".strip()

def compute_visible_ticks(y_min: float, y_max: float):
    start = int(y_min // 100) * 100
    end = int((y_max // 100) + 1) * 100

    ticks = list(range(start, end + 1, 100))
    labels = [score_to_short_label(t) for t in ticks]
    return ticks, labels

def draw_rank_background(ax, y_min: float, y_max: float):
    # tier bands
    for tier, tmin, tmax in tier_ranges:
        if tmax <= y_min or tmin >= y_max:
            continue
        ax.axhspan(
            max(tmin, y_min),
            min(tmax, y_max),
            color=tier_colors[tier],
            alpha=0.22,
            zorder=0
        )

    # thin 100 grid lines
    for y in range(int(y_min // 100) * 100, int(y_max // 100 + 1) * 100, 100):
        ax.axhline(y, color="white", alpha=0.12, linewidth=1, zorder=1)

    # dashed 400 tier boundaries
    for y in range(0, 4000, 400):
        if y_min <= y <= y_max:
            ax.axhline(y, color="white", alpha=0.45, linewidth=2, linestyle="--", zorder=2)


def plot_future_projection(ax, future_x, sample_paths, mean_path, low_path, high_path):
    ax.plot(future_x, mean_path, linestyle="--", linewidth=2.5, alpha=0.9, zorder=6)

    ax.fill_between(
        future_x,
        low_path,
        high_path,
        alpha=0.15,
        label="Typical range",
        zorder=4
    )

    for path in sample_paths:
        ax.plot(future_x, path, alpha=0.10, linewidth=1.2, zorder=3)


def save_rank_plot_png(rank_history, forecast_plot, out_dir="static/plots", rank_window=500):
    """
    rank_history: list[dict] with keys: match_no, rank_score
    forecast_plot: dict with keys:
      games_ahead, mean_path, low_path, high_path, sample_paths
    """
    os.makedirs(out_dir, exist_ok=True)

    x_hist = [int(r["match_no"]) for r in rank_history]
    y_hist = [float(r["rank_score"]) for r in rank_history]

    current_score = y_hist[-1] if y_hist else 0.0
    y_min = current_score - rank_window
    y_max = current_score + rank_window

    fig, ax = plt.subplots(figsize=(14, 6))

    draw_rank_background(ax, y_min=y_min, y_max=y_max)
    ax.set_ylim(y_min, y_max)

    # history line
    ax.plot(x_hist, y_hist, linewidth=2.5, marker="o", markersize=6, zorder=7)

    # y labels as rank strings
    ticks, labels = compute_visible_ticks(y_min, y_max)
    ax.set_yticks(ticks)
    ax.set_yticklabels(labels)

    # future axis
    games_ahead = int(forecast_plot["games_ahead"])
    start_x = max(x_hist) + 1 if x_hist else 1
    future_x = list(range(start_x, start_x + games_ahead))

    plot_future_projection(
        ax=ax,
        future_x=future_x,
        sample_paths=forecast_plot.get("sample_paths", []),
        mean_path=forecast_plot["mean_path"],
        low_path=forecast_plot["low_path"],
        high_path=forecast_plot["high_path"]
    )

    ax.set_title("Rank Progression (Estimated LP)")
    ax.set_xlabel("Match")
    ax.set_ylabel("Rank")
    ax.legend(loc="best")

    stamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S_%f")
    filename = f"rank_plot_{stamp}.png"
    filepath = os.path.join(out_dir, filename)

    fig.tight_layout()
    fig.savefig(filepath, dpi=150)
    plt.close(fig)

    return f"/static/plots/{filename}"