"""Executa o experimento completo e grava as tabelas de resultados em CSV.

Uso:  python -m previsao_insumos.pipeline [--processos 12]
"""

import argparse
import json
import logging
import platform
import time
from dataclasses import asdict
from importlib.metadata import version

import numpy as np
import pandas as pd
from scipy import stats

from previsao_insumos.avaliacao import (Desenho, calibrar_quantis, montar_previsoes, prever_ml,
                                        prever_todas_baselines)
from previsao_insumos.caracterizacao import caracterizar
from previsao_insumos.config import Configuracao
from previsao_insumos.dados import carregar_painel, matriz_series
from previsao_insumos.estoque import simular_politica
from previsao_insumos.inferencia import (ResultadoTeste, bootstrap_blocos, bootstrap_media, clopper_pearson,
                                         efeito_minimo_detectavel, friedman_nemenyi, holm)
from previsao_insumos.metricas import pinball
from previsao_insumos.modelos import BASELINES, fabricas_ml

logger = logging.getLogger("previsao_insumos")
TAUS = (0.05, 0.9, 0.95)
MIN_MESES_VALIDOS = 96  # ≥ 8 anos antes da validação


def selecionar_series(Y: pd.DataFrame, desenho: Desenho) -> pd.DataFrame:
    """Mantém séries com histórico suficiente antes da validação e escala do MASE definida."""
    treino = Y.iloc[: desenho.origens_validacao[0] + 1]
    validas = treino.notna().sum() >= MIN_MESES_VALIDOS
    excluidas = sorted(Y.columns[~validas])
    if excluidas:
        logger.warning("séries excluídas por histórico curto: %s", excluidas)
    return Y.loc[:, validas]


def calcular_metricas(df: pd.DataFrame, tau: float) -> pd.DataFrame:
    """Métricas por (fase, série, modelo, h) sobre as observações reais (sem imputar o alvo)."""
    d = df[df["observado"].notna()].copy()
    d["ae"] = (d["observado"] - d["previsto"]).abs()
    d["se"] = (d["observado"] - d["previsto"]) ** 2
    d["pin"] = pinball(d["observado"].to_numpy(), d[f"prev_q{tau:g}"].to_numpy(), tau)
    d["cobre"] = (d["observado"] >= d["prev_q0.05"]) & (d["observado"] <= d["prev_q0.95"])
    g = d.groupby(["fase", "serie", "modelo", "h"])
    return pd.DataFrame({
        "mase": g["ae"].mean() / g["escala"].first(),
        "rmsse": np.sqrt(g["se"].mean()) / g["escala_q"].first(),
        "wape": g["ae"].sum() / g["observado"].sum(),
        "pinball_escalado": g["pin"].mean() / g["escala"].first(),
        "cobertura_90": g["cobre"].mean(),
        "n": g["ae"].size(),
    }).reset_index()


def escolher_vencedores(metricas: pd.DataFrame) -> dict[int, dict[str, str]]:
    """E* e ML* por horizonte, escolhidos SOMENTE na validação."""
    val = metricas[metricas["fase"] == "validacao"].groupby(["h", "modelo"])["mase"].mean()
    vencedores = {}
    for h in sorted(metricas["h"].unique()):
        por_modelo = val.loc[h]
        vencedores[int(h)] = {
            "E*": por_modelo[por_modelo.index.isin(BASELINES)].idxmin(),
            "ML*": por_modelo[~por_modelo.index.isin(BASELINES)].idxmin(),
        }
    return vencedores


def testar_hipoteses(metricas: pd.DataFrame, df: pd.DataFrame, vencedores: dict, config: Configuracao,
                     rng: np.random.Generator) -> tuple[pd.DataFrame, dict]:
    teste = metricas[metricas["fase"] == "teste"]
    mase = teste.pivot_table(index=["serie", "h"], columns="modelo", values="mase")
    horizontes = sorted(vencedores)
    dif = pd.DataFrame({h: mase.xs(h, level="h")[vencedores[h]["ML*"]] - mase.xs(h, level="h")[vencedores[h]["E*"]]
                        for h in horizontes}).dropna()
    D = dif.mean(axis=1).to_numpy()
    resultados, extras = [], {}

    # H1 — superioridade do melhor ML sobre o melhor baseline (Δ < 0).
    w = stats.wilcoxon(D, alternative="less")
    ic = bootstrap_media(D, config.reamostragens_bootstrap, rng)
    razao = pd.concat([mase.xs(h, level="h")[vencedores[h]["ML*"]] / mase.xs(h, level="h")[vencedores[h]["E*"]]
                       for h in horizontes]).median()
    resultados.append(ResultadoTeste("H1", "Wilcoxon pareado unilateral (N séries) + IC bootstrap de Δ",
                                     float(w.statistic), float(w.pvalue), float(D.mean()), *ic,
                                     f"razão mediana MASE(ML*)/MASE(E*) = {razao:.3f}"))

    # Sensibilidade à dependência entre séries: bootstrap em blocos sobre as origens.
    t = df[(df["fase"] == "teste") & df["observado"].notna()].copy()
    t["ae_esc"] = (t["observado"] - t["previsto"]).abs() / t["escala"]
    por_origem = []
    for h in horizontes:
        sub = t[t["h"] == h].pivot_table(index=["origem", "serie"], columns="modelo", values="ae_esc")
        por_origem.append((sub[vencedores[h]["ML*"]] - sub[vencedores[h]["E*"]]).groupby(level="origem").mean())
    serie_origem = pd.concat(por_origem, axis=1).mean(axis=1).sort_index().to_numpy()
    extras["H1_ic_blocos_origens"] = bootstrap_blocos(serie_origem, config.bloco_bootstrap_origens,
                                                      config.reamostragens_bootstrap, rng)

    # H4 — a diferença depende do horizonte.
    fr = stats.friedmanchisquare(*[dif[h].to_numpy() for h in horizontes])
    resultados.append(ResultadoTeste("H4", "Friedman sobre d_i(h), h ∈ {1,3,6}", float(fr.statistic),
                                     float(fr.pvalue), float(dif.median().max() - dif.median().min()),
                                     observacao="efeito = amplitude das medianas de d_i(h)"))

    # H5 — a função de perda altera o ranking (MASE × pinball 0,9).
    medio = teste.groupby(["serie", "modelo"])[["mase", "pinball_escalado"]].mean().unstack("modelo")
    r_mase = medio["mase"].rank(axis=1)
    r_pin = medio["pinball_escalado"].rank(axis=1)
    taus_k = [stats.kendalltau(r_mase.loc[s], r_pin.loc[s]).statistic for s in r_mase.index]
    vencedor = r_mase.mean().idxmin()
    diferenca = (r_mase[vencedor] - r_pin[vencedor]).to_numpy()
    p_h5 = 1.0 if np.allclose(diferenca, 0) else float(stats.wilcoxon(diferenca, zero_method="zsplit").pvalue)
    resultados.append(ResultadoTeste("H5", f"Wilcoxon das posições do vencedor ({vencedor}) MASE × pinball(0,9)",
                                     np.nan, p_h5, float(np.nanmean(taus_k)),
                                     observacao="efeito = τ de Kendall médio entre rankings por série"))

    ajustados = holm({r.hipotese: r.p_valor for r in resultados})
    tabela = pd.DataFrame([asdict(r) for r in resultados])
    tabela["p_holm"] = tabela["hipotese"].map(ajustados)
    tabela["rejeita_H0"] = tabela["p_holm"] < config.alfa
    extras["efeito_minimo_detectavel"] = efeito_minimo_detectavel(len(D), float(D.std(ddof=1)), config.alfa)
    extras["N_series"] = int(len(D))
    extras["desvio_D"] = float(D.std(ddof=1))
    return tabela, extras


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--processos", type=int, default=12)
    parser.add_argument("--reusar-previsoes", action="store_true",
                        help="lê resultados/tabelas/previsoes.csv em vez de reajustar os modelos")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
    config = Configuracao()
    config.dir_saida.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(config.semente)
    inicio = time.perf_counter()

    painel = carregar_painel(config)
    Y = matriz_series(painel)
    desenho = Desenho.criar(Y.index, config)
    Y = selecionar_series(Y, desenho)
    painel = painel[painel["serie"].isin(Y.columns)]
    painel.assign(data=painel["data"].astype(str)).to_csv(config.dir_saida / "painel_limpo.csv", index=False)
    logger.info("painel: %d séries × %d meses (%s a %s); validação %s–%s; teste %s–%s",
                Y.shape[1], Y.shape[0], Y.index[0], Y.index[-1],
                Y.index[desenho.origens_validacao[0]], Y.index[desenho.origens_validacao[-1]],
                Y.index[desenho.origens_teste[0]], Y.index[desenho.origens_teste[-1]])

    # Caracterização só com o trecho anterior à validação.
    corte = desenho.origens_validacao[0] + 1
    carac = pd.DataFrame({s: caracterizar(Y[s].iloc[:corte]) for s in Y.columns}).T
    carac.index.name = "serie"
    carac.to_csv(config.dir_saida / "caracterizacao_series.csv")

    covid = painel.drop_duplicates("data").set_index("data")["covid"].reindex(Y.index).to_numpy()
    grupos = pd.Series([s.split("|")[0] for s in Y.columns], index=Y.columns)
    arquivo_previsoes = config.dir_saida / "previsoes.csv"
    t_base = time.perf_counter()
    if args.reusar_previsoes and arquivo_previsoes.exists():
        df = pd.read_csv(arquivo_previsoes)
        t_ml = t_fim = time.perf_counter()
    else:
        base = prever_todas_baselines(Y, desenho, config, args.processos)
        t_ml = time.perf_counter()
        ml = prever_ml(Y, grupos, covid, desenho, config)
        t_fim = time.perf_counter()
        df = calibrar_quantis(montar_previsoes(Y, pd.concat([base, ml]), desenho, config), TAUS)
        df.to_csv(arquivo_previsoes, index=False)
    metricas = calcular_metricas(df, config.tau_quantil)
    metricas.to_csv(config.dir_saida / "metricas_por_serie.csv", index=False)

    vencedores = escolher_vencedores(metricas)
    teste = metricas[metricas["fase"] == "teste"]
    resumo = teste.groupby(["modelo", "h"]).agg(
        mase_media=("mase", "mean"), mase_mediana=("mase", "median"), rmsse_media=("rmsse", "mean"),
        wape_mediana=("wape", "median"), pinball_media=("pinball_escalado", "mean"),
        cobertura_90=("cobertura_90", "mean"), n_series=("serie", "nunique")).reset_index()
    ics = teste.groupby(["modelo", "h"])["mase"].apply(
        lambda x: bootstrap_media(x.to_numpy(), config.reamostragens_bootstrap, rng))
    resumo["mase_ic_inf"] = [ics[(m, h)][0] for m, h in zip(resumo["modelo"], resumo["h"])]
    resumo["mase_ic_sup"] = [ics[(m, h)][1] for m, h in zip(resumo["modelo"], resumo["h"])]
    resumo["familia"] = np.where(resumo["modelo"].isin(BASELINES), "Estatístico", "Machine learning")
    resumo.to_csv(config.dir_saida / "resumo_metricas_teste.csv", index=False)
    val = metricas[metricas["fase"] == "validacao"].groupby(["modelo", "h"])["mase"].mean().rename("mase_validacao")
    val.reset_index().to_csv(config.dir_saida / "mase_validacao.csv", index=False)

    hipoteses, extras = testar_hipoteses(metricas, df, vencedores, config, rng)
    hipoteses.to_csv(config.dir_saida / "testes_hipoteses.csv", index=False)

    # Friedman/Nemenyi entre todos os modelos (MASE médio nos horizontes, teste).
    perdas = teste.groupby(["serie", "modelo"])["mase"].mean().unstack().dropna()
    postos, qui2, f_id, p_id, cd = friedman_nemenyi(perdas)
    postos.rename("posto_medio").reset_index().to_csv(config.dir_saida / "postos_friedman.csv", index=False)

    # Sanidade dos baselines: proporção de séries com MASE(E*) < 1 (H0-baseline).
    e_star = pd.concat([teste[(teste["h"] == h) & (teste["modelo"] == v["E*"])].set_index("serie")["mase"]
                        for h, v in vencedores.items()], axis=1).mean(axis=1)
    sucessos = int((e_star < 1).sum())
    ic_prop = clopper_pearson(sucessos, len(e_star))

    # Calibração dos intervalos de 90% (teste binomial exato por modelo, h = 1).
    cal = []
    t1 = df[(df["fase"] == "teste") & (df["h"] == 1) & df["observado"].notna()]
    for modelo, g in t1.groupby("modelo"):
        cobre = int(((g["observado"] >= g["prev_q0.05"]) & (g["observado"] <= g["prev_q0.95"])).sum())
        cal.append({"modelo": modelo, "cobertura": cobre / len(g), "n": len(g),
                    "p_binomial": stats.binomtest(cobre, len(g), 0.9).pvalue})
    pd.DataFrame(cal).to_csv(config.dir_saida / "calibracao_intervalos.csv", index=False)

    # Avaliação decisória (exploratória): política order-up-to com quantis 0,9 e 0,95.
    politicas = []
    for tau in (0.9, 0.95):
        p = simular_politica(df, f"prev_q{tau:g}")
        p["tau"] = tau
        politicas.append(p)
    politicas = pd.concat(politicas)
    politicas.to_csv(config.dir_saida / "simulacao_estoque_por_serie.csv", index=False)
    resumo_estoque = politicas.groupby(["tau", "modelo"])[["fill_rate", "estoque_relativo"]].median().reset_index()
    resumo_estoque.to_csv(config.dir_saida / "simulacao_estoque_resumo.csv", index=False)
    v1 = vencedores[1]
    pe = politicas[politicas["tau"] == 0.9].pivot(index="serie", columns="modelo", values="estoque_relativo")
    w_est = stats.wilcoxon(pe[v1["ML*"]] - pe[v1["E*"]])

    # Dificuldade de previsão por UF (insumo para o mapa em R): MASE do melhor modelo de cada família.
    por_uf = []
    for h, v in vencedores.items():
        for familia, modelo in v.items():
            sub = teste[(teste["h"] == h) & (teste["modelo"] == modelo)].copy()
            sub["familia"] = familia
            por_uf.append(sub)
    por_uf = pd.concat(por_uf)
    por_uf[["grupo", "uf"]] = por_uf["serie"].str.split("|", expand=True)
    por_uf.groupby(["uf", "grupo", "familia"])["mase"].mean().reset_index().to_csv(
        config.dir_saida / "mase_por_uf.csv", index=False)

    resumo_geral = {
        "n_series": int(Y.shape[1]), "periodo": [str(Y.index[0]), str(Y.index[-1])],
        "origens_validacao": [str(Y.index[o]) for o in desenho.origens_validacao],
        "origens_teste": [str(Y.index[o]) for o in desenho.origens_teste],
        "vencedores_validacao": vencedores,
        "friedman_qui2": qui2, "iman_davenport_F": f_id, "iman_davenport_p": p_id, "nemenyi_cd": cd,
        "baseline_mase_menor_1": {"sucessos": sucessos, "n": int(len(e_star)), "ic95": ic_prop},
        "estoque_wilcoxon_h1_tau09": {"estatistica": float(w_est.statistic), "p": float(w_est.pvalue),
                                      "mediana_dif": float((pe[v1["ML*"]] - pe[v1["E*"]]).median())},
        "pct_observacoes_ausentes": float(100 * painel["ausente"].mean()),
        **{k: (list(v) if isinstance(v, tuple) else v) for k, v in extras.items()},
        "tempo_s": {"baselines": t_ml - t_base, "ml": t_fim - t_ml, "total": time.perf_counter() - inicio},
        "hiperparametros_ml": {k: str(f()) for k, f in fabricas_ml(config.semente).items()},
        "ambiente": {"python": platform.python_version(),
                     **{p: version(p) for p in ("numpy", "pandas", "scipy", "scikit-learn", "statsmodels")}},
    }
    (config.dir_saida / "resumo_experimento.json").write_text(json.dumps(resumo_geral, ensure_ascii=False, indent=2,
                                                                         default=float), encoding="utf-8")
    logger.info("concluído em %.0f s", time.perf_counter() - inicio)
    print(hipoteses.to_string())
    print(resumo.pivot_table(index="modelo", columns="h", values="mase_media").round(3))


if __name__ == "__main__":
    main()
