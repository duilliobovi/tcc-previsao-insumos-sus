"""Modelos comparados: linhas de base estatísticas (locais) e ML global direto.

Linhas de base, ajustadas série a série em log1p, a cada origem:
    * ``ingenuo_sazonal``: repete o valor do mesmo mês do ano anterior;
    * ``ets``: suavização exponencial aditiva com tendência amortecida (Holt-Winters);
    * ``sarima``: SARIMA(1,1,1)(0,1,1)12, especificação "airline" estendida;
    * ``theta``: método Theta (Assimakopoulos; Nikolopoulos, 2000), vencedor da M3.

Machine learning: um único modelo **global** por horizonte h,
treinado em todas as séries ao mesmo tempo (estratégia direta). Os atributos
são desvios de log1p(y) em relação ao nível local (média dos últimos 12 meses
na origem), o que torna as séries comparáveis entre UFs de portes diferentes e
permite que árvores, que não extrapolam, prevejam séries com tendência.
"""

import warnings
from collections.abc import Callable

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor, RandomForestRegressor
from sklearn.linear_model import Ridge
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
from statsmodels.tsa.forecasting.theta import ThetaModel
from statsmodels.tsa.holtwinters import ExponentialSmoothing
from statsmodels.tsa.statespace.sarimax import SARIMAX

M = 12
LAGS = 12

# ---------------------------------------------------------------------------
# Linhas de base locais
# ---------------------------------------------------------------------------

def _interpolar(y: np.ndarray) -> np.ndarray:
    """Preenche NA internos para o AJUSTE dos modelos estatísticos (nunca o alvo avaliado).

    Usa interpolação linear e, nas pontas, o valor válido mais próximo
    (imputação restrita ao insumo do modelo).
    """
    s = pd.Series(y)
    return s.interpolate(limit_direction="both").to_numpy()


def ingenuo_sazonal(y: np.ndarray, h_max: int) -> np.ndarray:
    historico = _interpolar(y)
    return np.array([historico[len(historico) - M + ((h - 1) % M)] for h in range(1, h_max + 1)])


def ets(y: np.ndarray, h_max: int) -> np.ndarray:
    z = np.log1p(_interpolar(y))
    modelo = ExponentialSmoothing(z, trend="add", damped_trend=True, seasonal="add",
                                  seasonal_periods=M, initialization_method="estimated")
    return np.expm1(modelo.fit(optimized=True).forecast(h_max))


def sarima(y: np.ndarray, h_max: int) -> np.ndarray:
    z = np.log1p(_interpolar(y))[-120:]  # 10 anos: estabiliza a estimação e limita o custo
    modelo = SARIMAX(z, order=(1, 1, 1), seasonal_order=(0, 1, 1, M),
                     enforce_stationarity=False, enforce_invertibility=False)
    return np.expm1(modelo.fit(disp=False, maxiter=200).forecast(h_max))


def theta(y: np.ndarray, h_max: int) -> np.ndarray:
    z = np.log1p(_interpolar(y))
    return np.expm1(np.asarray(ThetaModel(z, period=M).fit().forecast(h_max)))


BASELINES: dict[str, Callable[[np.ndarray, int], np.ndarray]] = {
    "Ingênuo sazonal": ingenuo_sazonal, "ETS": ets, "SARIMA": sarima, "Theta": theta,
}


def prever_baselines(y: np.ndarray, h_max: int) -> dict[str, np.ndarray]:
    """Previsões h = 1..h_max de cada linha de base; se um ajuste falhar, cai para o ingênuo sazonal."""
    previsoes = {}
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")  # avisos de convergência do statsmodels são esperados em séries curtas
        for nome, funcao in BASELINES.items():
            try:
                p = funcao(y, h_max)
                if not np.all(np.isfinite(p)):
                    raise ValueError("previsão não finita")
            except Exception:  # noqa: BLE001 — falha de otimização numérica não deve parar o experimento
                p = ingenuo_sazonal(y, h_max)
            previsoes[nome] = np.maximum(p, 0.0)
    return previsoes


# ---------------------------------------------------------------------------
# Machine learning global
# ---------------------------------------------------------------------------

def construir_atributos(z: np.ndarray, t: np.ndarray, h: int, mes_alvo: np.ndarray,
                        covid_alvo: np.ndarray, grupo: int) -> tuple[np.ndarray, np.ndarray]:
    """Atributos na data t para prever t + h, usando só z[: t + 1].

    Retorna (X, nivel). ``z`` é log1p(y) com NaN nas ausências. Cada linha usa:
    desvios dos lags 0..11 em relação ao nível; médias móveis de 3 e 6 meses;
    variação anual; seno/cosseno do mês-alvo; dummy COVID no mês-alvo; grupo.
    """
    janelas = np.stack([z[t - k] for k in range(LAGS)], axis=1)          # lags 0..11
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", category=RuntimeWarning)        # janelas só com NaN
        nivel = np.nanmean(janelas, axis=1)
        mm3 = np.nanmean(janelas[:, :3], axis=1) - nivel
        mm6 = np.nanmean(janelas[:, :6], axis=1) - nivel
    desvios = janelas - nivel[:, None]
    anual = z[t] - z[t - M]
    angulo = 2 * np.pi * mes_alvo / 12
    X = np.column_stack([desvios, mm3, mm6, anual, np.sin(angulo), np.cos(angulo),
                         covid_alvo, np.full(len(t), grupo, dtype=float)])
    return X, nivel


def fabricas_ml(semente: int) -> dict[str, Callable[[], object]]:
    """Hiperparâmetros fixos e reportados (orçamento de ajuste igual para todos)."""
    return {
        "Ridge": lambda: make_pipeline(StandardScaler(), Ridge(alpha=1.0)),
        "Random Forest": lambda: RandomForestRegressor(
            n_estimators=300, min_samples_leaf=5, max_features=0.5, n_jobs=-1, random_state=semente),
        "Gradient Boosting": lambda: HistGradientBoostingRegressor(
            learning_rate=0.05, max_iter=400, max_leaf_nodes=31, min_samples_leaf=20,
            l2_regularization=1.0, random_state=semente),
    }


def precisa_imputar(nome: str) -> bool:
    """HistGradientBoosting trata NaN nativamente; Ridge e Random Forest não."""
    return nome != "Gradient Boosting"
