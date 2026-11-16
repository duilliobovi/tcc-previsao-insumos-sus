"""Inferência sobre diferenças de desempenho.

Estimando principal: a razão de erro entre dois modelos, medida pela média geométrica, entre
as séries, de MASE(A)/MASE(B), por horizonte. Em log, r_i = log(MASE_A,i / MASE_B,i); a razão
geométrica é exp(média de r_i), e valores abaixo de 1 indicam vantagem de A.

As 54 séries não são independentes: são 27 UFs com dois grupos cada, sujeitas a choques comuns e
a dependência espacial (achado E2). Por isso:
    * a unidade do teste é a **UF** (N = 27): r é primeiro promediado nos dois grupos da UF;
    * o intervalo vem do bootstrap por conglomerado (reamostra UFs, levando os dois grupos juntos);
    * como sensibilidade, o bootstrap em dois níveis reamostra UFs e blocos de origens ao mesmo tempo,
      e um modelo misto estima o efeito com intercepto aleatório de UF.
Testes série a série (Diebold-Mariano) não são usados: com 18 origens, o regime assintótico não vale.
"""

import warnings
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


def wilcoxon_uf(r: pd.Series, uf: pd.Series, alternativa: str = "less") -> tuple[float, float, int]:
    """Wilcoxon dos postos sinalizados sobre as médias de r por UF (unidade independente de análise)."""
    medias = r.groupby(uf.to_numpy()).mean().dropna()
    teste = stats.wilcoxon(medias.to_numpy(), alternative=alternativa)
    return float(teste.statistic), float(teste.pvalue), int(len(medias))


def bootstrap_conglomerado(r: pd.Series, uf: pd.Series, reamostragens: int, rng: np.random.Generator,
                           alfa: float = 0.05) -> tuple[float, float]:
    """IC percentil da média de r reamostrando UFs com reposição (os dois grupos da UF vão juntos)."""
    soma = r.groupby(uf.to_numpy()).sum()
    contagem = r.groupby(uf.to_numpy()).count()
    idx = rng.integers(0, len(soma), size=(reamostragens, len(soma)))
    medias = soma.to_numpy()[idx].sum(axis=1) / contagem.to_numpy()[idx].sum(axis=1)
    return tuple(np.quantile(medias, [alfa / 2, 1 - alfa / 2]))


def bootstrap_dois_niveis(erro_a: np.ndarray, erro_b: np.ndarray, uf_da_serie: np.ndarray, bloco: int,
                          reamostragens: int, rng: np.random.Generator, alfa: float = 0.05) -> tuple[float, float]:
    """IC da média de log(MASE_A/MASE_B) reamostrando UFs e blocos móveis de origens ao mesmo tempo.

    ``erro_a`` e ``erro_b``: matrizes séries × origens de erros absolutos escalados.
    """
    ufs = np.unique(uf_da_serie)
    linhas_uf = [np.flatnonzero(uf_da_serie == u) for u in ufs]
    n_origens = erro_a.shape[1]
    n_blocos = int(np.ceil(n_origens / bloco))
    medias = np.empty(reamostragens)
    for b in range(reamostragens):
        linhas = np.concatenate([linhas_uf[i] for i in rng.integers(0, len(ufs), len(ufs))])
        inicios = rng.integers(0, n_origens - bloco + 1, n_blocos)
        colunas = (inicios[:, None] + np.arange(bloco)).ravel()[:n_origens]
        with np.errstate(all="ignore"), warnings.catch_warnings():
            warnings.simplefilter("ignore", category=RuntimeWarning)  # série sem alvo observado no bloco sorteado
            a = np.nanmean(erro_a[np.ix_(linhas, colunas)], axis=1)
            e = np.nanmean(erro_b[np.ix_(linhas, colunas)], axis=1)
        medias[b] = np.nanmean(np.log(a / e))
    return tuple(np.quantile(medias, [alfa / 2, 1 - alfa / 2]))


def benjamini_hochberg(p: np.ndarray) -> np.ndarray:
    """p-valores ajustados por Benjamini-Hochberg (controle da taxa de falsas descobertas)."""
    p = np.asarray(p, dtype=float)
    n = len(p)
    ordem = np.argsort(p)
    ajustado = np.empty(n)
    acumulado = 1.0
    for posicao in range(n - 1, -1, -1):
        i = ordem[posicao]
        acumulado = min(acumulado, p[i] * n / (posicao + 1))
        ajustado[i] = acumulado
    return ajustado


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
    """Menor diferença média detectável com poder de 80% (teste pareado unilateral, aproximação normal).

    Informa a sensibilidade do desenho; não é usado para julgar, depois do fato, se o estudo tinha poder.
    """
    return float((stats.norm.ppf(1 - alfa) + stats.norm.ppf(poder)) * desvio / np.sqrt(n))


def clopper_pearson(sucessos: int, n: int, alfa: float = 0.05) -> tuple[float, float]:
    """IC exato de uma proporção binomial."""
    ic = stats.binomtest(sucessos, n).proportion_ci(confidence_level=1 - alfa, method="exact")
    return ic.low, ic.high
