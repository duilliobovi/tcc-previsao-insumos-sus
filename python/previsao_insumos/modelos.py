"""Modelos comparados: linhas de base estatísticas (locais) e machine learning global ou local.

Linhas de base, ajustadas série a série a cada origem (todas em log1p, exceto o ingênuo sazonal):
    * ``Ingênuo``: repete o último valor observado (passeio aleatório);
    * ``Deriva``: passeio aleatório com deriva, a taxa média de variação do histórico;
    * ``Ingênuo sazonal``: repete o valor do mesmo mês do ano anterior;
    * ``Theta``: método Theta (Assimakopoulos; Nikolopoulos, 2000);
    * ``ETS automático`` e ``ARIMA automático``: seleção por AICc entre especificações com e sem
      sazonalidade (pacote forecast do R; ver R/baselines_automaticas.R).

Machine learning: um modelo por horizonte h (estratégia direta). Na versão **global**, um único
ajuste usa todas as séries; na versão **local**, o mesmo algoritmo é ajustado série a série. Os
atributos são desvios de log1p(y) em relação ao nível local (média dos últimos 12 meses na
origem), o que torna as séries comparáveis entre UFs de portes diferentes e permite que árvores,
que não extrapolam, prevejam séries com tendência.

Alvo e perda (A6): z = log1p(y); alvo = z[t + h] − nível_t; perda quadrática (Ridge, árvores da
Random Forest e Gradient Boosting). A previsão é expm1(nível + ŷ), que estima a mediana
condicional quando o erro em log é simétrico.
"""

import warnings
from collections.abc import Callable
from dataclasses import dataclass

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor, RandomForestRegressor
from sklearn.linear_model import Ridge
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
from statsmodels.tsa.forecasting.theta import ThetaModel

M = 12
LAGS = 12

# ---------------------------------------------------------------------------
# Linhas de base locais (Python)
# ---------------------------------------------------------------------------

def _interpolar(y: np.ndarray) -> np.ndarray:
    """Preenche NA internos para o AJUSTE dos modelos estatísticos (nunca o alvo avaliado)."""
    return pd.Series(y).interpolate(limit_direction="both").to_numpy()


def ingenuo(y: np.ndarray, h_max: int) -> np.ndarray:
    return np.repeat(_interpolar(y)[-1], h_max)


def deriva(y: np.ndarray, h_max: int) -> np.ndarray:
    z = np.log1p(_interpolar(y))
    inclinacao = (z[-1] - z[0]) / (len(z) - 1)
    return np.expm1(z[-1] + inclinacao * np.arange(1, h_max + 1))


def ingenuo_sazonal(y: np.ndarray, h_max: int) -> np.ndarray:
    historico = _interpolar(y)
    return np.array([historico[len(historico) - M + ((h - 1) % M)] for h in range(1, h_max + 1)])


def theta(y: np.ndarray, h_max: int) -> np.ndarray:
    z = np.log1p(_interpolar(y))
    return np.expm1(np.asarray(ThetaModel(z, period=M).fit().forecast(h_max)))


BASELINES_PY: dict[str, Callable[[np.ndarray, int], np.ndarray]] = {
    "Ingênuo": ingenuo, "Deriva": deriva, "Ingênuo sazonal": ingenuo_sazonal, "Theta": theta,
}
BASELINES_R = ("ETS automático", "ARIMA automático")
BASELINES = tuple(BASELINES_PY) + BASELINES_R


def prever_baselines(y: np.ndarray, h_max: int) -> dict[str, np.ndarray]:
    """Previsões h = 1..h_max de cada linha de base em Python; se um ajuste falhar, cai para o ingênuo."""
    previsoes = {}
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")  # avisos de convergência do statsmodels são esperados
        for nome, funcao in BASELINES_PY.items():
            try:
                p = funcao(y, h_max)
                if not np.all(np.isfinite(p)):
                    raise ValueError("previsão não finita")
            except Exception:  # noqa: BLE001 — falha numérica não deve parar o experimento
                p = ingenuo(y, h_max)
            previsoes[nome] = np.maximum(p, 0.0)
    return previsoes


# ---------------------------------------------------------------------------
# Atributos do machine learning
# ---------------------------------------------------------------------------

GRUPOS_ATRIBUTOS: dict[str, tuple[int, ...]] = {
    "defasagens": tuple(range(LAGS)),     # desvios dos lags 0..11 em relação ao nível
    "medias_moveis": (12, 13),            # médias de 3 e 6 meses menos o nível
    "variacao_anual": (14,),              # z_t − z_{t−12}
    "calendario": (15, 16),               # seno e cosseno do mês-alvo
    "covid": (17,),                       # indicador do período 2020-03 a 2021-12 no mês-alvo
    "grupo": (18,),                       # 06 (medicamentos) ou 07 (OPM)
}
NOMES_ATRIBUTOS = [f"lag_{k}" for k in range(LAGS)] + ["media_3m", "media_6m", "variacao_anual",
                                                       "mes_seno", "mes_cosseno", "covid", "grupo"]


@dataclass(frozen=True, slots=True)
class Atributos:
    """Variante do conjunto de atributos (usada nas ablações)."""
    normalizar: bool = True
    excluir: tuple[str, ...] = ()

    @property
    def colunas(self) -> np.ndarray:
        fora = {c for g in self.excluir for c in GRUPOS_ATRIBUTOS[g]}
        return np.array([c for c in range(len(NOMES_ATRIBUTOS)) if c not in fora])


def construir_atributos(z: np.ndarray, t: np.ndarray, h: int, mes_alvo: np.ndarray, covid_alvo: np.ndarray,
                        grupo: int, variante: Atributos = Atributos()) -> tuple[np.ndarray, np.ndarray]:
    """Atributos na data t para prever t + h, usando só z[: t + 1].

    Retorna (X, âncora). ``z`` é log1p(y) com NaN nas ausências. A âncora é o nível (média dos
    últimos 12 meses) quando ``normalizar``; sem normalização, a âncora é zero e os atributos
    ficam em nível de log1p.
    """
    janelas = np.stack([z[t - k] for k in range(LAGS)], axis=1)          # lags 0..11
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", category=RuntimeWarning)        # janelas só com NaN
        nivel = np.nanmean(janelas, axis=1)
        mm3 = np.nanmean(janelas[:, :3], axis=1)
        mm6 = np.nanmean(janelas[:, :6], axis=1)
    ancora = nivel if variante.normalizar else np.zeros_like(nivel)
    angulo = 2 * np.pi * mes_alvo / 12
    X = np.column_stack([janelas - ancora[:, None], mm3 - ancora, mm6 - ancora, z[t] - z[t - M],
                         np.sin(angulo), np.cos(angulo), covid_alvo, np.full(len(t), grupo, dtype=float)])
    return X[:, variante.colunas], ancora


# ---------------------------------------------------------------------------
# Algoritmos e grades de hiperparâmetros (mesmo orçamento: 6 configurações cada)
# ---------------------------------------------------------------------------

GRADES: dict[str, list[dict]] = {
    "Ridge": [{"alpha": a} for a in (0.01, 0.1, 1.0, 10.0, 100.0, 1000.0)],
    "Random Forest": [{"min_samples_leaf": f, "max_features": m} for f in (2, 5, 10) for m in (0.33, 0.66)],
    "Gradient Boosting": [{"learning_rate": lr, "max_leaf_nodes": n} for lr in (0.03, 0.1) for n in (7, 15, 31)],
}
ALGORITMOS = tuple(GRADES)


def criar_modelo(nome: str, params: dict, semente: int, n_jobs: int = -1):
    """Instancia o algoritmo com os hiperparâmetros da grade; o restante é fixo e reportado."""
    if nome == "Ridge":
        return make_pipeline(StandardScaler(), Ridge(**params))
    if nome == "Random Forest":
        return RandomForestRegressor(n_estimators=300, n_jobs=n_jobs, random_state=semente, **params)
    if nome == "Gradient Boosting":
        # Parada antecipada: 15% do treino (só passado) monitora a perda; para após 20 iterações sem ganho.
        return HistGradientBoostingRegressor(max_iter=500, min_samples_leaf=20, l2_regularization=1.0,
                                             early_stopping=True, validation_fraction=0.15,
                                             n_iter_no_change=20, random_state=semente, **params)
    raise KeyError(nome)


def precisa_imputar(nome: str) -> bool:
    """HistGradientBoosting trata NaN nativamente; Ridge e Random Forest não."""
    return nome != "Gradient Boosting"
