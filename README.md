# LPForecast

Probabilistic ranking progression forecaster using Monte Carlo simulation.

## Overview

LPForecast is a simulation-based forecasting system that estimates future ranking progression using probabilistic modeling. 

The model simulates multiple possible match outcome paths and calculates:
- Promotion probability
- Demotion probability
- Probability of staying in the current division
- Expected LP trajectory

## Methodology

- Monte Carlo simulation
- Probabilistic win-rate estimation
- Iterative stochastic rank updates
- Confidence interval estimation

## Tech Stack

- Python (NumPy, Pandas, Matplotlib)
- Flask (API backend)
- AWS (deployment)
- Custom domain hosting

## How It Works

1. Historical match data is analyzed.
2. Win probability is estimated.
3. Thousands of future match paths are simulated.
4. Rank transition probabilities are computed from simulated outcomes.

## Disclaimer

This project is an independent forecasting model and is not affiliated with or endorsed by Riot Games.
LP Forecast system is unrelated to real MMR system that Riot Games uses.
 
