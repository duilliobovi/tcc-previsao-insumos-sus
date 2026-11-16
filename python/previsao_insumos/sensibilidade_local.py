"""Sensibilidade da H2: Ridge local com a penalização ajustada na validação (mesmo orçamento da versão global).

A H2 registrada no plano compara o Ridge global com o mesmo Ridge ajustado série a série, usando os
hiperparâmetros escolhidos para a versão global. Como a escala de cada série isolada é diferente, a
penalização adequada à versão local pode ser outra. Esta análise, feita depois do teste e por isso
exploratória, dá à versão local o mesmo critério de ajuste da global: a penalização é escolhida pelo MASE
médio nas 12 origens de validação (h = 1, 3, 6) na grade da versão global estendida até 100.000, e a
comparação é refeita. A extensão existe porque o melhor valor da grade original foi o maior (1.000); com
ela, a escolha não fica presa ao limite.

Uso: python -m previsao_insumos.sensibilidade_local   (depois do pipeline)
"""

import json
import logging

import numpy as np
import pandas as pd

from previsao_insumos.avaliacao import Contexto, Desenho, montar_previsoes, prever_ml_local
from previsao_insumos.config import Configuracao
from previsao_insumos.dados import carregar_painel, matriz_series
from previsao_insumos.inferencia import bootstrap_conglomerado, wilcoxon_uf
from previsao_insumos.modelos import GRADES  # grade original + extensão
from previsao_insumos.pipeline import grupo_de, selecionar_series

logger = logging.getLogger("previsao_insumos.sensibilidade_local")


def mase_medio(Y, prev, desenho, config) -> pd.DataFrame:
    d = montar_previsoes(Y, prev, desenho, config).dropna(subset=["observado"])
    d["ae"] = (d["observado"] - d["previsto"]).abs() / d["escala"]
    return d.groupby(["modelo", "h", "serie"])["ae"].mean().reset_index(name="mase")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
    config = Configuracao()
    painel = carregar_painel(config)
    Y = matriz_series(painel)
    desenho = Desenho.criar(Y.index, config)
    Y = selecionar_series(Y, desenho)
    painel = painel[painel["serie"].isin(Y.columns)]
    covid = painel.drop_duplicates("data").set_index("data")["covid"].reindex(Y.index).to_numpy()
    ctx = Contexto.criar(Y, grupo_de(Y.columns), covid, desenho)

    # 1. Grade na validação (mesmo orçamento da versão global).
    grade = GRADES["Ridge"] + [{"alpha": a} for a in (1e4, 1e5)]
    especificacoes = {f"Ridge local alfa={p['alpha']:g}": ("Ridge", p) for p in grade}
    prev_val, _ = prever_ml_local(ctx, especificacoes, desenho.origens_validacao, config.horizontes, config.semente, 10)
    val = mase_medio(Y, prev_val, desenho, config).groupby("modelo")["mase"].mean().sort_values()
    escolhido = val.index[0]
    logger.info("validação do Ridge local: %s", val.round(3).to_dict())

    # 2. Teste com a penalização escolhida.
    prev_teste, _ = prever_ml_local(ctx, {escolhido: especificacoes[escolhido]}, desenho.origens_teste,
                                    config.horizontes, config.semente, 10)
    teste_local = mase_medio(Y, prev_teste, desenho, config)
    metricas = pd.read_csv(config.dir_saida / "metricas_por_serie.csv")
    glob = metricas[(metricas["fase"] == "teste") & (metricas["modelo"] == "Ridge")
                    & metricas["h"].isin(config.horizontes)][["serie", "h", "mase"]]
    junto = glob.merge(teste_local[["serie", "h", "mase"]], on=["serie", "h"], suffixes=("_global", "_local"))
    r = np.log(junto["mase_global"] / junto["mase_local"]).groupby(junto["serie"]).mean()
    ufs = pd.Series([s.split("|")[1] for s in r.index], index=r.index)
    rng = np.random.default_rng(config.semente)
    ic = bootstrap_conglomerado(r, ufs, config.reamostragens_bootstrap, rng)
    _, p_uni, n_uf = wilcoxon_uf(r, ufs, "less")
    _, p_bi, _ = wilcoxon_uf(r, ufs, "two-sided")
    saida = {
        "alfa_escolhido": especificacoes[escolhido][1]["alpha"],
        "mase_validacao_por_alfa": {k.split("=")[1]: float(v) for k, v in val.items()},
        "mase_teste_local_por_h": teste_local.groupby("h")["mase"].mean().to_dict(),
        "razao_global_local": float(np.exp(r.mean())), "ic_inf": float(np.exp(ic[0])), "ic_sup": float(np.exp(ic[1])),
        "p_unilateral": p_uni, "p_bilateral": p_bi, "n_uf": n_uf, "pct_series_favoraveis": float(100 * (r < 0).mean()),
    }
    (config.dir_saida / "sensibilidade_ridge_local.json").write_text(json.dumps(saida, ensure_ascii=False, indent=2),
                                                                    encoding="utf-8")
    print(json.dumps(saida, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
