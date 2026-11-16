"""Executa o experimento completo e grava as tabelas de resultados em CSV e os números do texto.

Uso:  python -m previsao_insumos.pipeline [--processos 10] [--reusar-previsoes]

Ordem: dados → caracterização → linhas de base (Python e R) → ajuste de hiperparâmetros do ML
(só validação) → ML global e local → intervalos conformais → métricas → escolha de E* e ML* (só
validação) → hipóteses confirmatórias (plano em docs/plano_analise.md) → análises exploratórias →
simulação de estoque → tabelas para o R → numeros.json.
"""

import argparse
import json
import logging
import os
import platform
import subprocess
import time
from importlib.metadata import version

import numpy as np
import pandas as pd
from scipy import stats
from statsmodels.formula.api import mixedlm

from previsao_insumos.avaliacao import (Contexto, Desenho, calibrar_quantis_movel, montar_previsoes, prever_ml_global,
                                        prever_ml_local, prever_todas_baselines)
from previsao_insumos.caracterizacao import caracterizar
from previsao_insumos.config import RAIZ_REPOSITORIO, Configuracao
from previsao_insumos.dados import carregar_painel, matriz_series, valor_unitario_medio
from previsao_insumos.estoque import simular_politicas
from previsao_insumos.inferencia import (bootstrap_conglomerado, bootstrap_dois_niveis,
                                         efeito_minimo_detectavel, holm, wilcoxon_uf)
from previsao_insumos.metricas import pinball
from previsao_insumos.modelos import ALGORITMOS, BASELINES, GRADES

logger = logging.getLogger("previsao_insumos")
TAUS = (0.05, 0.9, 0.95)
MIN_MESES_VALIDOS = 96  # ≥ 8 anos antes da validação
GLOBAIS = ALGORITMOS
LOCAIS = tuple(f"{a} (local)" for a in ALGORITMOS)


def selecionar_series(Y: pd.DataFrame, desenho: Desenho) -> pd.DataFrame:
    """Mantém séries com histórico suficiente antes da validação."""
    treino = Y.iloc[: desenho.origens_validacao[0] + 1]
    validas = treino.notna().sum() >= MIN_MESES_VALIDOS
    if (~validas).any():
        logger.warning("séries excluídas por histórico curto: %s", sorted(Y.columns[~validas]))
    return Y.loc[:, validas]


def calcular_metricas(df: pd.DataFrame, tau: float = 0.9) -> pd.DataFrame:
    """Métricas por (fase, série, modelo, h) sobre as observações reais (sem imputar o alvo)."""
    d = df[df["observado"].notna() & (df["fase"] != "intervalo")].copy()
    d["ae"] = (d["observado"] - d["previsto"]).abs()
    d["se"] = (d["observado"] - d["previsto"]) ** 2
    d["vies"] = (d["previsto"] - d["observado"]) / d["escala"]
    d["pin"] = pinball(d["observado"].to_numpy(), d[f"prev_q{tau:g}"].to_numpy(), tau)
    d["cobre"] = ((d["observado"] >= d["prev_q0.05"]) & (d["observado"] <= d["prev_q0.95"])).astype(float)
    d.loc[d["prev_q0.05"].isna(), "cobre"] = np.nan
    g = d.groupby(["fase", "serie", "modelo", "h"])
    return pd.DataFrame({
        "mase": g["ae"].mean() / g["escala"].first(),
        "mase_ingenuo": g["ae"].mean() / g["escala_ing"].first(),
        "rmsse": np.sqrt(g["se"].mean()) / g["escala_q"].first(),
        "wape": g["ae"].sum() / g["observado"].sum(),
        "pinball_escalado": g["pin"].mean() / g["escala"].first(),
        "vies_escalado": g["vies"].mean(),
        "cobertura_90": g["cobre"].mean(),
        "n": g["ae"].size(),
    }).reset_index()


def uf_de(series) -> pd.Series:
    return pd.Series([s.split("|")[1] for s in series], index=series)


def grupo_de(series) -> pd.Series:
    return pd.Series([s.split("|")[0] for s in series], index=series)


def razao_log(mase: pd.DataFrame, a: str, b: str) -> pd.Series:
    """r_i = log(MASE_a / MASE_b) por série (linhas de ``mase``: séries; colunas: modelos)."""
    return np.log(mase[a] / mase[b]).replace([np.inf, -np.inf], np.nan).dropna()


def ajustar_hiperparametros(ctx: Contexto, Y: pd.DataFrame, desenho: Desenho, config: Configuracao,
                            ) -> tuple[dict[str, dict], pd.DataFrame]:
    """Grade de 6 configurações por algoritmo, avaliada SÓ nas origens de validação (h = 1, 3, 6)."""
    especificacoes = {f"{nome}#{i}": (nome, p) for nome, grade in GRADES.items() for i, p in enumerate(grade)}
    prev, _ = prever_ml_global(ctx, especificacoes, desenho.origens_validacao, config.horizontes, config.semente)
    d = montar_previsoes(Y, prev, desenho, config).dropna(subset=["observado"])
    d["ae"] = (d["observado"] - d["previsto"]).abs() / d["escala"]
    mase = d.groupby(["modelo", "serie", "h"])["ae"].mean().groupby("modelo").mean()
    tabela = pd.DataFrame({"rotulo": mase.index, "mase_validacao": mase.to_numpy()})
    tabela["algoritmo"] = tabela["rotulo"].str.split("#").str[0]
    tabela["hiperparametros"] = [json.dumps(especificacoes[r][1]) for r in tabela["rotulo"]]
    escolhidos = {}
    for nome, g in tabela.groupby("algoritmo"):
        melhor = g.loc[g["mase_validacao"].idxmin(), "rotulo"]
        escolhidos[nome] = especificacoes[melhor][1]
    tabela["escolhido"] = [especificacoes[r][1] == escolhidos[a] for r, a in zip(tabela["rotulo"], tabela["algoritmo"])]
    return escolhidos, tabela


def escolher_vencedores(metricas: pd.DataFrame, horizontes) -> dict[str, dict[str, str]]:
    """E* (estatístico) e ML* (global) por horizonte e por prazo de compra, SÓ na validação."""
    val = metricas[metricas["fase"] == "validacao"].groupby(["h", "modelo"])["mase"].mean()

    def melhores(hs):
        media = val.loc[list(hs)].groupby("modelo").mean()
        return {"E*": media[media.index.isin(BASELINES)].idxmin(), "ML*": media[media.index.isin(GLOBAIS)].idxmin()}

    saida = {f"h{h}": melhores([h]) for h in horizontes}
    saida.update({f"L{L}": melhores(range(1, L + 1)) for L in (3, 6)})
    return saida


def main() -> None:  # noqa: PLR0915 — roteiro linear do experimento, mais legível em uma função
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--processos", type=int, default=max(1, (os.cpu_count() or 2) - 2))
    parser.add_argument("--reusar-previsoes", action="store_true",
                        help="lê resultados/tabelas/previsoes.csv em vez de reajustar os modelos")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
    config = Configuracao()
    saida = config.dir_saida
    saida.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(config.semente)
    inicio = time.perf_counter()

    # 1. Dados ---------------------------------------------------------------
    painel = carregar_painel(config)
    Y = matriz_series(painel)
    desenho = Desenho.criar(Y.index, config)
    Y = selecionar_series(Y, desenho)
    painel = painel[painel["serie"].isin(Y.columns)]
    painel.assign(data=painel["data"].astype(str)).to_csv(saida / "painel_limpo.csv", index=False)
    (saida / "desenho.json").write_text(json.dumps({
        "datas": [str(d) for d in Y.index], "origens_indice": list(desenho.todas),
        "horizonte_maximo": config.horizonte_maximo, **desenho.como_dict()}, ensure_ascii=False), encoding="utf-8")
    fases = desenho.como_dict()
    logger.info("painel: %d séries × %d meses (%s a %s); validação %s–%s; teste %s–%s", Y.shape[1], Y.shape[0],
                Y.index[0], Y.index[-1], fases["validacao"][0], fases["validacao"][-1], fases["teste"][0],
                fases["teste"][-1])
    ufs, grupos = uf_de(Y.columns), grupo_de(Y.columns)

    # 2. Caracterização (só antes da validação) -----------------------------
    corte = desenho.origens_validacao[0] + 1
    carac = pd.DataFrame({s: caracterizar(Y[s].iloc[:corte]) for s in Y.columns}).T
    carac.index.name = "serie"
    carac.to_csv(saida / "caracterizacao_series.csv")

    # 3–5. Previsões -----------------------------------------------------------
    covid = painel.drop_duplicates("data").set_index("data")["covid"].reindex(Y.index).to_numpy()
    ctx = Contexto.criar(Y, grupos, covid, desenho)
    arquivo_previsoes = saida / "previsoes.csv"
    tempos = {}
    if args.reusar_previsoes and arquivo_previsoes.exists():
        df = pd.read_csv(arquivo_previsoes)
        hp = json.loads((saida / "hiperparametros_escolhidos.json").read_text(encoding="utf-8"))
        tempos = json.loads((saida / "tempos_cpu.json").read_text(encoding="utf-8"))
    else:
        t0 = time.perf_counter()
        base_py, tempos["baselines_python_cpu_s"] = prever_todas_baselines(Y, desenho, config, args.processos)
        tempos["baselines_python_parede_s"] = time.perf_counter() - t0
        t0 = time.perf_counter()
        subprocess.run(["Rscript", "R/baselines_automaticas.R", str(args.processos)], check=True,
                       cwd=RAIZ_REPOSITORIO)
        tempos["baselines_r_parede_s"] = time.perf_counter() - t0
        base_r = pd.read_csv(saida / "previsoes_r.csv")
        tempos.update({f"{m}_cpu_s": float(g["cpu_s"].sum()) for m, g in base_r.groupby("modelo")})
        base_r = base_r.drop(columns="cpu_s")

        hp, grade = ajustar_hiperparametros(ctx, Y, desenho, config)
        grade.to_csv(saida / "ajuste_hiperparametros.csv", index=False)
        (saida / "hiperparametros_escolhidos.json").write_text(json.dumps(hp, ensure_ascii=False), encoding="utf-8")
        logger.info("hiperparâmetros escolhidos na validação: %s", hp)
        especificacoes = {nome: (nome, hp[nome]) for nome in ALGORITMOS}
        t0 = time.perf_counter()
        ml, cpu_ml = prever_ml_global(ctx, especificacoes, desenho.todas, config.horizontes_previstos, config.semente)
        tempos["ml_global_parede_s"] = time.perf_counter() - t0
        tempos.update({f"{k} (global)_cpu_s": v for k, v in cpu_ml.items()})
        t0 = time.perf_counter()
        locais = {f"{nome} (local)": (nome, hp[nome]) for nome in ALGORITMOS}
        ml_local, tempos["ml_local_cpu_s"] = prever_ml_local(ctx, locais, desenho.origens_teste, config.horizontes,
                                                             config.semente, args.processos)
        tempos["ml_local_parede_s"] = time.perf_counter() - t0
        tempos["ajustes_por_modelo"] = {
            "baseline": len(Y.columns) * len(desenho.todas),
            "ml_global": len(desenho.todas) * len(config.horizontes_previstos),
            "ml_local": len(Y.columns) * len(desenho.origens_teste) * len(config.horizontes)}
        df = calibrar_quantis_movel(montar_previsoes(Y, pd.concat([base_py, base_r, ml, ml_local]), desenho, config),
                                    TAUS, config.janela_conformal)
        df.to_csv(arquivo_previsoes, index=False)
        (saida / "tempos_cpu.json").write_text(json.dumps(tempos, ensure_ascii=False, indent=2), encoding="utf-8")

    metricas = calcular_metricas(df)
    metricas.to_csv(saida / "metricas_por_serie.csv", index=False)
    vencedores = escolher_vencedores(metricas, config.horizontes)
    teste = metricas[metricas["fase"] == "teste"]

    resumo = teste.groupby(["modelo", "h"]).agg(
        mase_media=("mase", "mean"), mase_mediana=("mase", "median"), mase_ingenuo_media=("mase_ingenuo", "mean"),
        rmsse_media=("rmsse", "mean"), wape_mediana=("wape", "median"), pinball_media=("pinball_escalado", "mean"),
        vies_medio=("vies_escalado", "mean"), cobertura_90=("cobertura_90", "mean"),
        n_series=("serie", "nunique")).reset_index()
    resumo["familia"] = np.select([resumo["modelo"].isin(BASELINES), resumo["modelo"].isin(GLOBAIS)],
                                  ["Estatístico", "ML global"], "ML local")
    # IC 95% do MASE médio por bootstrap por conglomerado de UF.
    ics = {}
    for (modelo, h), g in teste.groupby(["modelo", "h"]):
        g = g.set_index("serie")["mase"]
        ics[(modelo, h)] = bootstrap_conglomerado(g, ufs.loc[g.index], 2000, rng)
    resumo["mase_ic_inf"] = [ics[(m, h)][0] for m, h in zip(resumo["modelo"], resumo["h"])]
    resumo["mase_ic_sup"] = [ics[(m, h)][1] for m, h in zip(resumo["modelo"], resumo["h"])]
    resumo.to_csv(saida / "resumo_metricas_teste.csv", index=False)
    val = metricas[metricas["fase"] == "validacao"].groupby(["modelo", "h"])["mase"].mean().rename("mase_validacao")
    val.reset_index().to_csv(saida / "mase_validacao.csv", index=False)

    # 6. Hipóteses confirmatórias (família de Holm) ---------------------------
    mase_h = {h: teste[teste["h"] == h].pivot(index="serie", columns="modelo", values="mase") for h in config.horizontes}
    confirmatorias, razoes_serie = [], []
    t = df[(df["fase"] == "teste") & df["observado"].notna()].copy()
    t["ae_esc"] = (t["observado"] - t["previsto"]).abs() / t["escala"]
    for h in config.horizontes:
        e, m = vencedores[f"h{h}"]["E*"], vencedores[f"h{h}"]["ML*"]
        r = razao_log(mase_h[h], m, e)
        est, p, n_uf = wilcoxon_uf(r, ufs.loc[r.index])
        ic = bootstrap_conglomerado(r, ufs.loc[r.index], config.reamostragens_bootstrap, rng)
        sub = t[t["h"] == h]
        ea = sub[sub["modelo"] == m].pivot(index="serie", columns="origem", values="ae_esc").loc[r.index].to_numpy()
        eb = sub[sub["modelo"] == e].pivot(index="serie", columns="origem", values="ae_esc").loc[r.index].to_numpy()
        ic2 = bootstrap_dois_niveis(ea, eb, ufs.loc[r.index].to_numpy(), config.bloco_bootstrap_origens, 2000, rng)
        medias_uf = r.groupby(ufs.loc[r.index].to_numpy()).mean()
        confirmatorias.append({
            "hipotese": f"H1 (h = {h})", "comparacao": f"{m} × {e}", "estimativa_razao": float(np.exp(r.mean())),
            "ic_inf": float(np.exp(ic[0])), "ic_sup": float(np.exp(ic[1])),
            "ic2_inf": float(np.exp(ic2[0])), "ic2_sup": float(np.exp(ic2[1])), "estatistica": est, "p_valor": p,
            "n_uf": n_uf, "pct_series_favoraveis": float(100 * (r < 0).mean()),
            "mde_razao": float(np.exp(-efeito_minimo_detectavel(n_uf, float(medias_uf.std(ddof=1)), config.alfa)))})
        razoes_serie.append(pd.DataFrame({"serie": r.index, "h": h, "log_razao": r.to_numpy(), "ml": m, "e": e}))
    razoes_serie = pd.concat(razoes_serie)
    razoes_serie.to_csv(saida / "razao_ml_estatistico_por_serie.csv", index=False)

    # H2 — global × local do mesmo algoritmo (o ML* mais frequente entre os horizontes).
    alg = pd.Series([vencedores[f"h{h}"]["ML*"] for h in config.horizontes]).mode()[0]
    r2 = pd.concat([razao_log(mase_h[h], alg, f"{alg} (local)") for h in config.horizontes], axis=1).mean(axis=1)
    est, p, n_uf = wilcoxon_uf(r2, ufs.loc[r2.index])
    ic = bootstrap_conglomerado(r2, ufs.loc[r2.index], config.reamostragens_bootstrap, rng)
    confirmatorias.append({"hipotese": "H2", "comparacao": f"{alg} global × local", "estimativa_razao": float(np.exp(r2.mean())),
                           "ic_inf": float(np.exp(ic[0])), "ic_sup": float(np.exp(ic[1])), "estatistica": est,
                           "p_valor": p, "n_uf": n_uf, "pct_series_favoraveis": float(100 * (r2 < 0).mean())})

    # 7. Simulação de estoque (H3) -------------------------------------------
    valor_unit = valor_unitario_medio(config, desenho.datas[desenho.origens_teste[0]])
    sim = simular_politicas(df[df["modelo"].isin(BASELINES + GLOBAIS)], Y, desenho.origens_teste, config.prazos_compra,
                            config.taus_estoque, config.janela_conformal, valor_unit)
    sim.to_csv(saida / "simulacao_estoque_por_serie.csv", index=False)
    for L in config.prazos_compra:
        e, m = vencedores[f"L{L}"]["E*"], vencedores[f"L{L}"]["ML*"]
        s = sim[(sim["L"] == L) & (sim["tau"] == 0.9)].pivot(index="serie", columns="modelo", values="custo_relativo")
        r3 = razao_log(s, m, e)
        est, p, n_uf = wilcoxon_uf(r3, ufs.loc[r3.index])
        ic = bootstrap_conglomerado(r3, ufs.loc[r3.index], config.reamostragens_bootstrap, rng)
        confirmatorias.append({"hipotese": f"H3 (L = {L})", "comparacao": f"custo {m} × {e}",
                               "estimativa_razao": float(np.exp(r3.mean())), "ic_inf": float(np.exp(ic[0])),
                               "ic_sup": float(np.exp(ic[1])), "estatistica": est, "p_valor": p, "n_uf": n_uf,
                               "pct_series_favoraveis": float(100 * (r3 < 0).mean())})
    hip = pd.DataFrame(confirmatorias)
    ajust = holm(dict(zip(hip["hipotese"], hip["p_valor"])))
    hip["p_holm"] = hip["hipotese"].map(ajust)
    hip["rejeita_H0"] = hip["p_holm"] < config.alfa
    hip.to_csv(saida / "testes_hipoteses.csv", index=False)

    resumo_est = sim.groupby(["L", "tau", "modelo"]).agg(
        fill_rate_mediana=("fill_rate", "median"), fill_rate_agregado=("falta_total", "sum"),
        cobertura_meses_mediana=("cobertura_meses", "median"), valor_estoque_medio_total=("valor_estoque_medio", "sum"),
        valor_falta_mensal_total=("valor_falta_mensal", "sum"), custo_relativo_total=("custo_relativo", "sum")).reset_index()
    demanda_total = sim.groupby(["L", "tau", "modelo"]).apply(lambda g: (g["demanda_media"] * g["meses"]).sum(),
                                                              include_groups=False).to_numpy()
    resumo_est["fill_rate_agregado"] = 1 - resumo_est["fill_rate_agregado"] / demanda_total
    resumo_est.to_csv(saida / "simulacao_estoque_resumo.csv", index=False)

    # 8. Análises exploratórias -------------------------------------------------
    # 8a. ML* contra cada linha de base, por horizonte (sensibilidade de E1).
    sens = []
    for h in config.horizontes:
        m = vencedores[f"h{h}"]["ML*"]
        for b in BASELINES + tuple(x for x in GLOBAIS if x != m):
            r = razao_log(mase_h[h], m, b)
            ic = bootstrap_conglomerado(r, ufs.loc[r.index], 2000, rng)
            p = wilcoxon_uf(r, ufs.loc[r.index], "two-sided")[1]
            sens.append({"h": h, "ml": m, "comparado": b, "razao": float(np.exp(r.mean())),
                         "ic_inf": float(np.exp(ic[0])), "ic_sup": float(np.exp(ic[1])), "p_bilateral": p})
    sens = pd.DataFrame(sens)
    sens["p_holm_familia"] = np.nan
    for h, g in sens.groupby("h"):
        aj = holm(dict(zip(g["comparado"], g["p_bilateral"])))
        sens.loc[g.index, "p_holm_familia"] = g["comparado"].map(aj).to_numpy()
    sens.to_csv(saida / "sensibilidade_ml_contra_cada_modelo.csv", index=False)

    # 8b. Modelo misto: log da razão ML*/E* com intercepto aleatório de UF.
    mm = razoes_serie.assign(uf=lambda d: d["serie"].str.split("|").str[1],
                             grupo=lambda d: d["serie"].str.split("|").str[0])
    ajuste_mm = mixedlm("log_razao ~ 0 + C(h)", mm, groups=mm["uf"]).fit(reml=True)
    ic_mm = ajuste_mm.conf_int()
    misto = pd.DataFrame({"h": config.horizontes,
                          "razao": np.exp(ajuste_mm.fe_params.to_numpy()),
                          "ic_inf": np.exp(ic_mm.iloc[: len(config.horizontes), 0].to_numpy()),
                          "ic_sup": np.exp(ic_mm.iloc[: len(config.horizontes), 1].to_numpy()),
                          "var_uf": float(ajuste_mm.cov_re.iloc[0, 0]), "var_residual": float(ajuste_mm.scale)})
    misto.to_csv(saida / "modelo_misto_razao.csv", index=False)

    # 8c. Friedman entre os modelos (UF como bloco) e Wilcoxon par a par contra o melhor, com Holm (A8).
    globais_base = list(BASELINES + GLOBAIS)
    perdas = (teste[teste["modelo"].isin(globais_base)].assign(uf=lambda d: d["serie"].str.split("|").str[1])
              .groupby(["uf", "modelo"])["mase"].mean().unstack())
    postos = perdas.rank(axis=1).mean().sort_values()
    fr = stats.friedmanchisquare(*[perdas[c].to_numpy() for c in perdas.columns])
    melhor = postos.index[0]
    par = {c: stats.wilcoxon(perdas[melhor], perdas[c]).pvalue for c in perdas.columns if c != melhor}
    aj = holm(par)
    pd.DataFrame({"modelo": postos.index, "posto_medio": postos.to_numpy(),
                  "p_holm_contra_melhor": [aj.get(c, np.nan) for c in postos.index]}).to_csv(
        saida / "postos_friedman.csv", index=False)

    # 8d. Concordância de rankings MASE × pinball (descritiva, E10).
    medio = teste[teste["modelo"].isin(globais_base)].groupby(["serie", "modelo"])[["mase", "pinball_escalado"]].mean()
    r_mase = medio["mase"].unstack().rank(axis=1)
    r_pin = medio["pinball_escalado"].unstack().rank(axis=1)
    taus_k = np.array([stats.kendalltau(r_mase.loc[s], r_pin.loc[s]).statistic for s in r_mase.index])

    # 8e. Cobertura dos intervalos de 90% (conformal móvel), com IC por conglomerado de UF.
    cob = []
    for (modelo, h), g in teste[teste["modelo"].isin(globais_base)].groupby(["modelo", "h"]):
        g = g.set_index("serie")["cobertura_90"].dropna()
        ic = bootstrap_conglomerado(g, ufs.loc[g.index], 2000, rng)
        cob.append({"modelo": modelo, "h": h, "cobertura": g.mean(), "ic_inf": ic[0], "ic_sup": ic[1]})
    pd.DataFrame(cob).to_csv(saida / "calibracao_intervalos.csv", index=False)

    # 8f. Erro por segmento (A7): grupo, classe de Syntetos-Boylan, faixa de volume.
    volume = Y.iloc[: desenho.origens_teste[0] + 1].tail(12).mean()
    seg = razoes_serie.merge(carac[["classe_sb", "forca_tendencia", "forca_sazonal", "pct_ausentes"]],
                             left_on="serie", right_index=True)
    seg["grupo"] = seg["serie"].str.split("|").str[0]
    seg["faixa_volume"] = pd.qcut(seg["serie"].map(np.log(volume)), 3, labels=["baixo", "médio", "alto"])
    segmentos = pd.concat([seg.groupby(["h", c], observed=True)["log_razao"].agg(["mean", "size"])
                           .rename(columns={"mean": "log_razao_media", "size": "n"}).reset_index()
                           .rename(columns={c: "nivel"}).assign(segmento=c)
                           for c in ("grupo", "classe_sb", "faixa_volume")])
    segmentos["razao"] = np.exp(segmentos["log_razao_media"])
    segmentos.to_csv(saida / "erro_por_segmento.csv", index=False)

    # 8g. Variabilidade entre sementes (A5): RF e GB globais no teste.
    sementes = []
    for semente in config.sementes_variabilidade:
        esp = {n: (n, hp[n]) for n in ("Random Forest", "Gradient Boosting")}
        prev, _ = prever_ml_global(ctx, esp, desenho.origens_teste, config.horizontes, semente)
        d = montar_previsoes(Y, prev, desenho, config).dropna(subset=["observado"])
        d["ae"] = (d["observado"] - d["previsto"]).abs() / d["escala"]
        m = d.groupby(["modelo", "h", "serie"])["ae"].mean().groupby(["modelo", "h"]).mean()
        sementes.extend({"semente": semente, "modelo": a, "h": h, "mase_media": v} for (a, h), v in m.items())
    sementes = pd.DataFrame(sementes)
    sementes.to_csv(saida / "variabilidade_sementes.csv", index=False)
    var_sem = sementes.groupby(["modelo", "h"])["mase_media"].agg(["mean", "std", "min", "max"]).reset_index()
    var_sem.to_csv(saida / "variabilidade_sementes_resumo.csv", index=False)

    # 9. Tabela por série para a análise regional em R -------------------------
    regional = (razoes_serie.groupby("serie")["log_razao"].mean().rename("log_razao_media").to_frame()
                .join(teste[teste["modelo"] == vencedores["h1"]["ML*"]].groupby("serie")["mase"].mean().rename("mase_ml"))
                .join(teste[teste["modelo"] == vencedores["h1"]["E*"]].groupby("serie")["mase"].mean().rename("mase_e"))
                .join(np.log(volume).rename("log_volume")).join(carac[["pct_ausentes", "forca_tendencia", "classe_sb"]])
                .join(valor_unit))
    regional["uf"], regional["grupo"] = ufs.loc[regional.index], grupos.loc[regional.index]
    regional.to_csv(saida / "regional_por_serie.csv")

    # 10. Resumo e números do texto --------------------------------------------
    resumo_geral = {
        "n_series": int(Y.shape[1]), "periodo": [str(Y.index[0]), str(Y.index[-1])], "fases": fases,
        "vencedores_validacao": vencedores, "hiperparametros_escolhidos": hp,
        "friedman_qui2": float(fr.statistic), "friedman_p": float(fr.pvalue),
        "kendall_tau_mase_pinball_medio": float(np.nanmean(taus_k)),
        "pct_observacoes_ausentes": float(100 * painel["ausente"].mean()), "tempos": tempos,
        "tempo_total_s": time.perf_counter() - inicio,
        "ambiente": {"python": platform.python_version(), "processador": platform.processor() or platform.machine(),
                     "nucleos": os.cpu_count(), "sistema": platform.platform(),
                     **{p: version(p) for p in ("numpy", "pandas", "scipy", "scikit-learn", "statsmodels")}},
    }
    (saida / "resumo_experimento.json").write_text(json.dumps(resumo_geral, ensure_ascii=False, indent=2, default=float),
                                                   encoding="utf-8")
    try:  # os números do texto são derivados das tabelas acima por um módulo à parte
        from previsao_insumos import numeros
        numeros.gerar()
    except ImportError:
        logger.warning("módulo numeros ausente: numeros.json não atualizado")
    logger.info("concluído em %.0f s", time.perf_counter() - inicio)
    print(hip.to_string())
    print(resumo.pivot_table(index="modelo", columns="h", values="mase_media").round(3))


if __name__ == "__main__":
    main()
