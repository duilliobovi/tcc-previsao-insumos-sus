"""Inferência sobre diferenças de desempenho.

A unidade inferencial é a **série** (N = 54): as conclusões principais vêm de
estatísticas de painel, cuja aproximação melhora com N, e não de testes por
série, cujo regime assintótico (T → ∞) não vale com 18 origens de teste.
A dependência entre séries (choques nacionais) é tratada em uma análise de
sensibilidade com bootstrap em blocos sobre as origens.
"""

from dataclasses import dataclass

import numpy as np
import pandas as pd
from scipy import stats

# Valores críticos q_alfa (alfa = 0,05) do teste de Nemenyi (Demšar, 2006, tabela 5).
Q_NEMENYI_005 = {2: 1.960, 3: 2.343, 4: 2.569, 5: 2.728, 6: 2.850, 7: 2.949, 8: 3.031, 9: 3.102, 10: 3.164}


@dataclass(frozen=True, slots=True)
class ResultadoTeste:
    hipotese: str
    teste: str
    estatistica: float
    p_valor: float
    efeito: float
    ic_inf: float = np.nan
    ic_sup: float = np.nan
    observacao: str = ""


def bootstrap_media(x: np.ndarray, reamostragens: int, rng: np.random.Generator, alfa: float = 0.05) -> tuple[float, float]:
    """IC percentil da média por reamostragem das séries (unidades independentes)."""
    idx = rng.integers(0, len(x), size=(reamostragens, len(x)))
    medias = x[idx].mean(axis=1)
    return tuple(np.quantile(medias, [alfa / 2, 1 - alfa / 2]))


def bootstrap_blocos(x: np.ndarray, bloco: int, reamostragens: int, rng: np.random.Generator,
                     alfa: float = 0.05) -> tuple[float, float]:
    """IC percentil da média por bootstrap de blocos móveis (Künsch, 1989) sobre a sequência de origens."""
    n = len(x)
    n_blocos = int(np.ceil(n / bloco))
    inicios = rng.integers(0, n - bloco + 1, size=(reamostragens, n_blocos))
    idx = (inicios[:, :, None] + np.arange(bloco)).reshape(reamostragens, -1)[:, :n]
    medias = x[idx].mean(axis=1)
    return tuple(np.quantile(medias, [alfa / 2, 1 - alfa / 2]))


def holm(p: dict[str, float]) -> dict[str, float]:
    """p-valores ajustados por Holm-Bonferroni (controle de FWER)."""
    ordem = sorted(p, key=p.get)
    m, ajustado, maximo = len(p), {}, 0.0
    for i, chave in enumerate(ordem):
        maximo = max(maximo, min(1.0, (m - i) * p[chave]))
        ajustado[chave] = maximo
    return ajustado


def friedman_nemenyi(perdas: pd.DataFrame) -> tuple[pd.Series, float, float, float, float]:
    """Postos médios, Friedman (qui-quadrado), Iman-Davenport (F), seu p e diferença crítica de Nemenyi.

    ``perdas``: linhas = séries, colunas = modelos (menor é melhor).
    """
    n, k = perdas.shape
    postos = perdas.rank(axis=1, method="average")
    medios = postos.mean()
    qui2 = 12 * n / (k * (k + 1)) * (np.sum(medios ** 2) - k * (k + 1) ** 2 / 4)
    f_id = (n - 1) * qui2 / (n * (k - 1) - qui2)
    p_id = stats.f.sf(f_id, k - 1, (k - 1) * (n - 1))
    cd = Q_NEMENYI_005[k] * np.sqrt(k * (k + 1) / (6 * n))
    return medios.sort_values(), float(qui2), float(f_id), float(p_id), float(cd)


def efeito_minimo_detectavel(n: int, desvio: float, alfa: float = 0.05, poder: float = 0.8) -> float:
    """Menor diferença média detectável (teste pareado unilateral, aproximação normal)."""
    return float((stats.norm.ppf(1 - alfa) + stats.norm.ppf(poder)) * desvio / np.sqrt(n))


def clopper_pearson(sucessos: int, n: int, alfa: float = 0.05) -> tuple[float, float]:
    """IC exato de uma proporção binomial."""
    ic = stats.binomtest(sucessos, n).proportion_ci(confidence_level=1 - alfa, method="exact")
    return ic.low, ic.high
