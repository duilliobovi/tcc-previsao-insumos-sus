"""Métricas de erro escaladas.

O MAPE da Entrega I foi substituído pelo MASE (Hyndman; Koehler, 2006): é
definido mesmo com zeros, é comparável entre séries de escalas distintas e tem
interpretação direta (MASE < 1 = melhor que o ingênuo sazonal dentro da
amostra). A perda quantílica (pinball) avalia o quantil usado na decisão de
estoque, coerente com a assimetria de custos da seção 3.1.
"""

import numpy as np


def escala_sazonal(y: np.ndarray, m: int = 12) -> float:
    """MAE do ingênuo sazonal dentro da amostra; denominador do MASE (ignora NaN)."""
    diferencas = np.abs(y[m:] - y[:-m])
    escala = np.nanmean(diferencas)
    return float(escala) if np.isfinite(escala) and escala > 0 else np.nan


def escala_sazonal_quadratica(y: np.ndarray, m: int = 12) -> float:
    """Raiz do erro quadrático médio do ingênuo sazonal; denominador do RMSSE."""
    diferencas = (y[m:] - y[:-m]) ** 2
    escala = np.sqrt(np.nanmean(diferencas))
    return float(escala) if np.isfinite(escala) and escala > 0 else np.nan


def pinball(y: np.ndarray, q: np.ndarray, tau: float) -> np.ndarray:
    """Perda quantílica: penaliza mais a subprevisão quando tau > 0,5."""
    erro = y - q
    return np.maximum(tau * erro, (tau - 1) * erro)


def wape(y: np.ndarray, previsto: np.ndarray) -> float:
    """Erro absoluto ponderado: soma |e| / soma |y|."""
    return float(np.sum(np.abs(y - previsto)) / np.sum(np.abs(y)))
