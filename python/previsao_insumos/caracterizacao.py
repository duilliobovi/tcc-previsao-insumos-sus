"""Caracterização das séries (objetivo específico 2).

Calculada **só com o trecho anterior à primeira origem de validação**, para que
nenhuma estatística descritiva use informação do teste:

* força da sazonalidade F_S e da tendência F_T pela decomposição STL robusta
  (Wang; Smith; Hyndman, 2006): F = max(0, 1 − Var(R) / Var(componente + R));
* ADF (H0: raiz unitária) e KPSS (H0: estacionariedade), usados para orientar a
  diferenciação e não como resultado inferencial (baixo poder com T < 200);
* ADI (intervalo médio entre demandas não nulas) e CV² dos tamanhos de demanda,
  com os cortes de Syntetos e Boylan (ADI = 1,32; CV² = 0,49).
"""

import warnings

import numpy as np
import pandas as pd
from statsmodels.tsa.seasonal import STL
from statsmodels.tsa.stattools import adfuller, kpss


def _forca(componente: np.ndarray, residuo: np.ndarray) -> float:
    return float(max(0.0, 1 - np.var(residuo) / np.var(componente + residuo)))


def classe_syntetos_boylan(adi: float, cv2: float) -> str:
    if adi < 1.32:
        return "suave" if cv2 < 0.49 else "errática"
    return "intermitente" if cv2 < 0.49 else "irregular (lumpy)"


def caracterizar(y: pd.Series) -> dict[str, float | str]:
    valores = y.to_numpy(dtype=float)
    serie = pd.Series(valores).interpolate(limit_direction="both").to_numpy()
    z = np.log1p(serie)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        stl = STL(z, period=12, robust=True).fit()
        p_adf = adfuller(z, autolag="AIC")[1]
        p_kpss = kpss(z, regression="c", nlags="auto")[1]
    positivos = valores[np.isfinite(valores) & (valores > 0)]
    validos = np.isfinite(valores).sum()
    adi = validos / max(len(positivos), 1)
    cv2 = float((np.std(positivos) / np.mean(positivos)) ** 2) if len(positivos) > 1 else np.nan
    return {
        "n_meses": int(len(valores)), "pct_ausentes": float(100 * np.mean(~np.isfinite(valores))),
        "pct_zeros": float(100 * np.mean(valores[np.isfinite(valores)] == 0)),
        "forca_sazonal": _forca(stl.seasonal, stl.resid), "forca_tendencia": _forca(stl.trend, stl.resid),
        "p_adf": float(p_adf), "p_kpss": float(p_kpss),
        "adf_kpss_concordam_nao_estacionaria": bool(p_adf > 0.05 and p_kpss < 0.05),
        "adi": float(adi), "cv2": cv2, "classe_sb": classe_syntetos_boylan(adi, cv2),
    }
