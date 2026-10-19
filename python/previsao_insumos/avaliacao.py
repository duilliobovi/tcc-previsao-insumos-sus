"""Validação walk-forward com origem rolante expansiva em dois níveis.

Linha do tempo (índices de competência):

    ... treino ... | 6 origens de VALIDAÇÃO | 18 origens de TESTE | h_max meses
                     (escolhe E* e ML*,       (executado uma vez,
                      calibra os quantis)      só para relatar)

Em cada origem o, todos os modelos recebem exatamente a mesma informação,
y[0..o], e preveem y[o + h] para h ∈ {1, 3, 6}. Nada calculado depois da
origem (nível, escala, atributos, hiperparâmetros) entra na previsão.
"""

import logging
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass

import numpy as np
import pandas as pd

from previsao_insumos.config import Configuracao
from previsao_insumos.metricas import escala_sazonal, escala_sazonal_quadratica
from previsao_insumos.modelos import (LAGS, M, construir_atributos, fabricas_ml, precisa_imputar,
                                      prever_baselines)

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class Desenho:
    """Índices das origens de validação e de teste sobre o eixo de datas."""
    datas: pd.PeriodIndex
    origens_validacao: tuple[int, ...]
    origens_teste: tuple[int, ...]

    @classmethod
    def criar(cls, datas: pd.PeriodIndex, config: Configuracao) -> "Desenho":
        ultima = len(datas) - 1 - config.horizonte_maximo
        teste = tuple(range(ultima - config.origens_teste + 1, ultima + 1))
        validacao = tuple(range(teste[0] - config.origens_validacao, teste[0]))
        return cls(datas, validacao, teste)

    def fase(self, origem: int) -> str:
        return "validacao" if origem in self.origens_validacao else "teste"


def _baselines_uma_tarefa(args: tuple[str, int, np.ndarray, int]) -> tuple[str, int, dict[str, np.ndarray]]:
    serie, origem, historico, h_max = args
    return serie, origem, prever_baselines(historico, h_max)


def prever_todas_baselines(Y: pd.DataFrame, desenho: Desenho, config: Configuracao, processos: int) -> pd.DataFrame:
    """Ajusta as linhas de base em todas as (série, origem), em paralelo por processo."""
    origens = desenho.origens_validacao + desenho.origens_teste
    tarefas = [(s, o, Y[s].to_numpy()[: o + 1], config.horizonte_maximo) for s in Y.columns for o in origens]
    registros = []
    with ProcessPoolExecutor(max_workers=processos) as pool:
        for n, (serie, origem, previsoes) in enumerate(pool.map(_baselines_uma_tarefa, tarefas, chunksize=8), 1):
            for modelo, p in previsoes.items():
                for h in config.horizontes:
                    registros.append((serie, origem, h, modelo, p[h - 1]))
            if n % 200 == 0:
                logger.info("linhas de base: %d/%d ajustes", n, len(tarefas))
    return pd.DataFrame(registros, columns=["serie", "origem", "h", "modelo", "previsto"])


def prever_ml(Y: pd.DataFrame, grupos: pd.Series, covid: np.ndarray, desenho: Desenho,
              config: Configuracao) -> pd.DataFrame:
    """Modelos globais diretos: um ajuste por (origem, h, algoritmo) com todas as séries."""
    Z = np.log1p(Y.to_numpy())                       # T × S, NaN nas ausências
    meses = np.array([d.month for d in desenho.datas])
    codigo_grupo = grupos.map({g: i for i, g in enumerate(sorted(grupos.unique()))}).to_numpy()
    fabricas = fabricas_ml(config.semente)
    registros = []
    for origem in desenho.origens_validacao + desenho.origens_teste:
        for h in config.horizontes:
            X_treino, alvo, X_prev, nivel_prev = [], [], [], []
            t_treino = np.arange(LAGS + M, origem - h + 1)   # alvo t + h ≤ origem: conhecido na origem
            for s in range(Z.shape[1]):
                z = Z[:, s]
                X, nivel = construir_atributos(z, t_treino, h, meses[t_treino + h], covid[t_treino + h], codigo_grupo[s])
                y_alvo = z[t_treino + h] - nivel
                ok = np.isfinite(y_alvo)
                X_treino.append(X[ok]); alvo.append(y_alvo[ok])
                t0 = np.array([origem])
                Xp, nivel_p = construir_atributos(z, t0, h, meses[t0 + h], covid[t0 + h], codigo_grupo[s])
                X_prev.append(Xp); nivel_prev.append(nivel_p)
            X_treino, alvo = np.vstack(X_treino), np.concatenate(alvo)
            X_prev, nivel_prev = np.vstack(X_prev), np.concatenate(nivel_prev)
            for nome, fabrica in fabricas.items():
                Xt, Xp = (np.nan_to_num(X_treino), np.nan_to_num(X_prev)) if precisa_imputar(nome) else (X_treino, X_prev)
                modelo = fabrica().fit(Xt, alvo)
                previsto = np.maximum(np.expm1(modelo.predict(Xp) + nivel_prev), 0.0)
                registros.extend((serie, origem, h, nome, p) for serie, p in zip(Y.columns, previsto))
        logger.info("ML: origem %s concluída", desenho.datas[origem])
    return pd.DataFrame(registros, columns=["serie", "origem", "h", "modelo", "previsto"])


def montar_previsoes(Y: pd.DataFrame, previsoes: pd.DataFrame, desenho: Desenho, config: Configuracao) -> pd.DataFrame:
    """Junta o observado, a fase e as escalas do MASE/RMSSE (fixadas no início de cada fase)."""
    df = previsoes.copy()
    valores = Y.to_numpy()
    col = {s: i for i, s in enumerate(Y.columns)}
    df["observado"] = [valores[o + h, col[s]] for s, o, h in zip(df["serie"], df["origem"], df["h"])]
    df["fase"] = [desenho.fase(o) for o in df["origem"]]
    inicio = {"validacao": desenho.origens_validacao[0], "teste": desenho.origens_teste[0]}
    escalas = {(s, f): (escala_sazonal(Y[s].to_numpy()[: o + 1], config.periodo_sazonal),
                        escala_sazonal_quadratica(Y[s].to_numpy()[: o + 1], config.periodo_sazonal))
               for s in Y.columns for f, o in inicio.items()}
    df["escala"] = [escalas[(s, f)][0] for s, f in zip(df["serie"], df["fase"])]
    df["escala_q"] = [escalas[(s, f)][1] for s, f in zip(df["serie"], df["fase"])]
    df["data_origem"] = [str(desenho.datas[o]) for o in df["origem"]]
    df["data_alvo"] = [str(desenho.datas[o + h]) for o, h in zip(df["origem"], df["h"])]
    return df


def calibrar_quantis(df: pd.DataFrame, taus: tuple[float, ...]) -> pd.DataFrame:
    """Conformal comum a todos os modelos: quantis dos resíduos escalados da validação.

    q_tau(modelo, h) = quantil empírico de (y − ŷ)/escala nas origens de validação; no teste,
    ŷ_tau = ŷ + q_tau · escala. A mesma regra vale para todos os modelos, o que torna a
    comparação de intervalos justa.
    """
    validos = df[(df["fase"] == "validacao") & df["observado"].notna()]
    residuos = (validos["observado"] - validos["previsto"]) / validos["escala"]
    quantis = (validos.assign(r=residuos).groupby(["modelo", "h"])["r"]
               .quantile(list(taus)).unstack().rename(columns=lambda t: f"q{t:g}"))
    saida = df.merge(quantis, left_on=["modelo", "h"], right_index=True, how="left")
    for t in taus:
        saida[f"prev_q{t:g}"] = np.maximum(saida["previsto"] + saida[f"q{t:g}"] * saida["escala"], 0.0)
    return saida
