"""Validação com origem rolante expansiva em três trechos: validação, intervalo e teste.

Linha do tempo (índices de competência; h_max = 6):

    ... treino ... | 12 origens de VALIDAÇÃO | 5 origens de INTERVALO | 18 origens de TESTE | 6 meses
                     (escolhe hiperparâmetros,  (não avaliadas; só        (executado uma vez,
                      E* e ML*)                  alimentam a calibração)   para relatar)

A última origem de validação fica 6 meses antes da primeira de teste: os meses-alvo da validação
(até o + 6) terminam no mês da primeira origem de teste, e os do teste começam no mês seguinte
(achado A3). Em cada origem o, todos os modelos recebem exatamente a mesma informação, y[0..o], e
preveem y[o + h] para h = 1..6. Nada calculado depois da origem entra na previsão.
"""

import logging
import multiprocessing
import time
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass

import numpy as np
import pandas as pd

from previsao_insumos.config import Configuracao
from previsao_insumos.metricas import escala_ingenua, escala_sazonal, escala_sazonal_quadratica
from previsao_insumos.modelos import LAGS, M, Atributos, construir_atributos, criar_modelo, precisa_imputar, \
    prever_baselines

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class Desenho:
    """Índices das origens de validação, de intervalo e de teste sobre o eixo de datas."""
    datas: pd.PeriodIndex
    origens_validacao: tuple[int, ...]
    origens_intervalo: tuple[int, ...]
    origens_teste: tuple[int, ...]

    @classmethod
    def criar(cls, datas: pd.PeriodIndex, config: Configuracao) -> "Desenho":
        ultima = len(datas) - 1 - config.horizonte_maximo
        teste = tuple(range(ultima - config.origens_teste + 1, ultima + 1))
        fim_val = teste[0] - config.intervalo_validacao_teste
        validacao = tuple(range(fim_val - config.origens_validacao + 1, fim_val + 1))
        intervalo = tuple(range(fim_val + 1, teste[0]))
        return cls(datas, validacao, intervalo, teste)

    @property
    def todas(self) -> tuple[int, ...]:
        return self.origens_validacao + self.origens_intervalo + self.origens_teste

    def fase(self, origem: int) -> str:
        if origem in self.origens_validacao:
            return "validacao"
        return "intervalo" if origem in self.origens_intervalo else "teste"

    def como_dict(self) -> dict:
        return {fase: [str(self.datas[o]) for o in origens] for fase, origens in
                (("validacao", self.origens_validacao), ("intervalo", self.origens_intervalo),
                 ("teste", self.origens_teste))}


# ---------------------------------------------------------------------------
# Linhas de base em Python (as automáticas vêm do R)
# ---------------------------------------------------------------------------

def _baselines_uma_tarefa(args: tuple[str, int, np.ndarray, int]) -> tuple[str, int, dict[str, np.ndarray], float]:
    serie, origem, historico, h_max = args
    inicio = time.process_time()
    previsoes = prever_baselines(historico, h_max)
    return serie, origem, previsoes, time.process_time() - inicio


def prever_todas_baselines(Y: pd.DataFrame, desenho: Desenho, config: Configuracao,
                           processos: int) -> tuple[pd.DataFrame, float]:
    """Ajusta as linhas de base em todas as (série, origem), em paralelo por processo."""
    tarefas = [(s, o, Y[s].to_numpy()[: o + 1], config.horizonte_maximo) for s in Y.columns for o in desenho.todas]
    registros, cpu = [], 0.0
    with ProcessPoolExecutor(max_workers=processos) as pool:
        for serie, origem, previsoes, segundos in pool.map(_baselines_uma_tarefa, tarefas, chunksize=16):
            cpu += segundos
            for modelo, p in previsoes.items():
                registros.extend((serie, origem, h, modelo, p[h - 1]) for h in config.horizontes_previstos)
    return pd.DataFrame(registros, columns=["serie", "origem", "h", "modelo", "previsto"]), cpu


# ---------------------------------------------------------------------------
# Machine learning
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class Contexto:
    """Matriz em log1p e covariáveis comuns, montadas uma vez."""
    Z: np.ndarray                 # T × S, log1p(y), NaN nas ausências
    meses: np.ndarray
    covid: np.ndarray
    codigo_grupo: np.ndarray
    series: list[str]

    @classmethod
    def criar(cls, Y: pd.DataFrame, grupos: pd.Series, covid: np.ndarray, desenho: Desenho) -> "Contexto":
        codigo = grupos.map({g: i for i, g in enumerate(sorted(grupos.unique()))}).to_numpy()
        return cls(np.log1p(Y.to_numpy()), np.array([d.month for d in desenho.datas]), covid, codigo, list(Y.columns))

    def matrizes(self, origem: int, h: int, variante: Atributos, indices_series: np.ndarray | None = None,
                 janela_treino: int | None = None) -> tuple[list, list, list, list]:
        """Por série: (X de treino, alvo de treino, X de previsão, âncora da previsão).

        O alvo de treino t + h é conhecido na origem (t + h ≤ origem). ``janela_treino`` limita o
        treino aos últimos meses (curva de aprendizado pelo tamanho do histórico).
        """
        inicio = LAGS + M if janela_treino is None else max(LAGS + M, origem - h + 1 - janela_treino)
        t_treino = np.arange(inicio, origem - h + 1)
        t0 = np.array([origem])
        saida = ([], [], [], [])
        for s in (range(self.Z.shape[1]) if indices_series is None else indices_series):
            z = self.Z[:, s]
            X, ancora = construir_atributos(z, t_treino, h, self.meses[t_treino + h], self.covid[t_treino + h],
                                            self.codigo_grupo[s], variante)
            alvo = z[t_treino + h] - ancora
            ok = np.isfinite(alvo)
            Xp, ancora_p = construir_atributos(z, t0, h, self.meses[t0 + h], self.covid[t0 + h], self.codigo_grupo[s],
                                               variante)
            for lista, valor in zip(saida, (X[ok], alvo[ok], Xp, ancora_p)):
                lista.append(valor)
        return saida


def _ajustar_prever(nome: str, params: dict, semente: int, X: np.ndarray, alvo: np.ndarray, Xp: np.ndarray,
                    n_jobs: int = -1) -> np.ndarray:
    if precisa_imputar(nome):
        X, Xp = np.nan_to_num(X), np.nan_to_num(Xp)
    return criar_modelo(nome, params, semente, n_jobs).fit(X, alvo).predict(Xp)


def prever_ml_global(ctx: Contexto, especificacoes: dict[str, tuple[str, dict]], origens, horizontes, semente: int,
                     variante: Atributos = Atributos(), indices_treino: np.ndarray | None = None,
                     janela_treino: int | None = None) -> tuple[pd.DataFrame, dict[str, float]]:
    """Modelos globais diretos: um ajuste por (origem, h, rótulo) com as séries de treino.

    ``especificacoes``: rótulo → (algoritmo, hiperparâmetros). A previsão é feita para TODAS as
    séries, mesmo quando o treino usa só ``indices_treino`` (curva de aprendizado).
    """
    registros, cpu = [], {r: 0.0 for r in especificacoes}
    for origem in origens:
        for h in horizontes:
            Xs, alvos, _, _ = ctx.matrizes(origem, h, variante, indices_treino, janela_treino)
            _, _, Xps, ancoras = ctx.matrizes(origem, h, variante)
            X, alvo, Xp, ancora = np.vstack(Xs), np.concatenate(alvos), np.vstack(Xps), np.concatenate(ancoras)
            for rotulo, (nome, params) in especificacoes.items():
                inicio = time.process_time()
                previsto = np.maximum(np.expm1(_ajustar_prever(nome, params, semente, X, alvo, Xp) + ancora), 0.0)
                cpu[rotulo] += time.process_time() - inicio
                registros.extend((s, origem, h, rotulo, p) for s, p in zip(ctx.series, previsto))
    return pd.DataFrame(registros, columns=["serie", "origem", "h", "modelo", "previsto"]), cpu


def _local_uma_serie(args) -> tuple[list, float]:
    ctx, s, especificacoes, origens, horizontes, semente = args
    registros, inicio = [], time.process_time()
    for origem in origens:
        for h in horizontes:
            Xs, alvos, Xps, ancoras = ctx.matrizes(origem, h, Atributos(excluir=("grupo",)), np.array([s]))
            for rotulo, (nome, params) in especificacoes.items():
                p = _ajustar_prever(nome, params, semente, Xs[0], alvos[0], Xps[0], n_jobs=1)
                registros.append((ctx.series[s], origem, h, rotulo, float(np.maximum(np.expm1(p + ancoras[0]), 0)[0])))
    return registros, time.process_time() - inicio


def _um_thread() -> None:
    """Cada processo filho usa um único thread (OpenMP/BLAS), para não disputar núcleos com os demais."""
    from threadpoolctl import threadpool_limits
    threadpool_limits(1)


def prever_ml_local(ctx: Contexto, especificacoes: dict[str, tuple[str, dict]], origens, horizontes, semente: int,
                    processos: int) -> tuple[pd.DataFrame, float]:
    """O mesmo algoritmo e os mesmos hiperparâmetros, ajustados série a série (isola global × local)."""
    tarefas = [(ctx, s, especificacoes, origens, horizontes, semente) for s in range(len(ctx.series))]
    registros, cpu = [], 0.0
    # "spawn": processos novos, porque o OpenMP do scikit-learn não sobrevive a um fork do processo principal.
    with ProcessPoolExecutor(max_workers=processos, mp_context=multiprocessing.get_context("spawn"),
                             initializer=_um_thread) as pool:
        for parte, segundos in pool.map(_local_uma_serie, tarefas):
            registros.extend(parte)
            cpu += segundos
    return pd.DataFrame(registros, columns=["serie", "origem", "h", "modelo", "previsto"]), cpu


# ---------------------------------------------------------------------------
# Observado, escalas e intervalos
# ---------------------------------------------------------------------------

def montar_previsoes(Y: pd.DataFrame, previsoes: pd.DataFrame, desenho: Desenho, config: Configuracao) -> pd.DataFrame:
    """Junta o observado, a fase e as escalas (fixadas no início da validação e do teste).

    ``escala``: MAE do ingênuo sazonal dentro da amostra (MASE); ``escala_q``: RMSE do mesmo (RMSSE);
    ``escala_ing``: MAE do ingênuo simples (MASE alternativo pedido em A1). O intervalo usa a
    escala da validação.
    """
    df = previsoes.copy()
    valores = Y.to_numpy()
    col = {s: i for i, s in enumerate(Y.columns)}
    df["observado"] = [valores[o + h, col[s]] for s, o, h in zip(df["serie"], df["origem"], df["h"])]
    df["fase"] = [desenho.fase(o) for o in df["origem"]]
    inicio = {"validacao": desenho.origens_validacao[0], "teste": desenho.origens_teste[0]}
    escalas = {}
    for s in Y.columns:
        for fase, o in inicio.items():
            y = Y[s].to_numpy()[: o + 1]
            escalas[(s, fase)] = (escala_sazonal(y, config.periodo_sazonal),
                                  escala_sazonal_quadratica(y, config.periodo_sazonal), escala_ingenua(y))
        escalas[(s, "intervalo")] = escalas[(s, "validacao")]
    trio = np.array([escalas[(s, f)] for s, f in zip(df["serie"], df["fase"])])
    df["escala"], df["escala_q"], df["escala_ing"] = trio[:, 0], trio[:, 1], trio[:, 2]
    df["escala_cal"] = [escalas[(s, "validacao")][0] for s in df["serie"]]
    df["data_origem"] = [str(desenho.datas[o]) for o in df["origem"]]
    df["data_alvo"] = [str(desenho.datas[o + h]) for o, h in zip(df["origem"], df["h"])]
    return df


def calibrar_quantis_movel(df: pd.DataFrame, taus: tuple[float, ...], janela: int, minimo: int = 6) -> pd.DataFrame:
    """Conformal em janela móvel, comum a todos os modelos (achado E11).

    Na origem o e horizonte h, q_tau(modelo, h, o) é o quantil empírico dos resíduos escalados
    r = (y − ŷ)/escala_cal de todas as séries nas ``janela`` origens o' mais recentes cujo alvo já
    era conhecido em o (o' + h ≤ o). A previsão do quantil é ŷ + q_tau · escala_cal. Assim a
    calibração acompanha a tendência e nunca usa o futuro.
    """
    d = df.copy()
    d["r"] = (d["observado"] - d["previsto"]) / d["escala_cal"]
    quantis = []
    for (modelo, h), g in d.groupby(["modelo", "h"]):
        por_origem = {o: x["r"].dropna().to_numpy() for o, x in g.groupby("origem")}
        origens = sorted(por_origem)
        for o in origens:
            usadas = [oo for oo in origens if o - h - janela < oo <= o - h]
            if len(usadas) < minimo:
                continue
            r = np.concatenate([por_origem[oo] for oo in usadas])
            quantis.append({"modelo": modelo, "h": h, "origem": o, **{f"q{t:g}": np.quantile(r, t) for t in taus}})
    saida = d.drop(columns="r").merge(pd.DataFrame(quantis), on=["modelo", "h", "origem"], how="left")
    for t in taus:
        saida[f"prev_q{t:g}"] = np.maximum(saida["previsto"] + saida[f"q{t:g}"] * saida["escala_cal"], 0.0)
    return saida
