"""Números citados no texto do TCC, derivados das tabelas de resultados (nunca digitados).

Gera ``resultados/numeros.json`` no formato {chave: texto já formatado em pt-BR}. O texto usa
``{{chave}}``; o montador do .docx recusa chave inexistente. As chaves seguem padrões estáveis:

    mase_<modelo>_h<h>              MASE médio no teste
    razao_h<h>, razao_ic_inf_h<h>   razão geométrica MASE(ML*)/MASE(E*) e IC 95% por conglomerado
    p_holm_<hipótese>               p-valor ajustado por Holm da família confirmatória
    estoque_<indicador>_<modelo>_L<L>_t<tau>   indicadores da simulação
    ...

Uso: python -m previsao_insumos.numeros   (também chamado ao fim do pipeline e do comportamento)
"""

import json
import re
import unicodedata
from pathlib import Path

import numpy as np
import pandas as pd

from previsao_insumos.config import RAIZ_REPOSITORIO, Configuracao

MESES = ["jan.", "fev.", "mar.", "abr.", "maio", "jun.", "jul.", "ago.", "set.", "out.", "nov.", "dez."]


def slug(texto: str) -> str:
    sem_acento = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "_", sem_acento.lower()).strip("_")


def br(x: float, casas: int = 2) -> str:
    if x is None or (isinstance(x, float) and not np.isfinite(x)):
        return "n/d"
    texto = f"{x:,.{casas}f}"
    return texto.replace(",", "X").replace(".", ",").replace("X", ".")


def p_br(p: float) -> str:
    """p-valor: três casas; abaixo de 0,001, "< 0,001"."""
    return "< 0,001" if p < 0.001 else br(p, 3)


def data_br(competencia: str) -> str:
    ano, mes = competencia.split("-")
    return f"{MESES[int(mes) - 1]} {ano}"


def pct(x: float, casas: int = 1) -> str:
    return br(100 * x, casas)


def gerar(destino: Path | None = None) -> dict[str, str]:  # noqa: PLR0915
    config = Configuracao()
    tab = config.dir_saida
    ler = lambda nome: pd.read_csv(tab / nome)  # noqa: E731
    n: dict[str, str] = {}
    resumo = json.loads((tab / "resumo_experimento.json").read_text(encoding="utf-8"))
    venc = resumo["vencedores_validacao"]
    fases = resumo["fases"]

    # Desenho e ambiente
    n["n_series"] = str(resumo["n_series"])
    n["data_val_ini"], n["data_val_fim"] = data_br(fases["validacao"][0]), data_br(fases["validacao"][-1])
    n["data_teste_ini"], n["data_teste_fim"] = data_br(fases["teste"][0]), data_br(fases["teste"][-1])
    n["pct_ausentes"] = br(resumo["pct_observacoes_ausentes"], 2)
    n["reamostragens"] = br(config.reamostragens_bootstrap, 0)
    n["versao_python"] = resumo["ambiente"]["python"]
    n["nucleos"] = str(resumo["ambiente"]["nucleos"])
    n["tempo_total_min"] = br(resumo["tempo_total_s"] / 60, 0)
    n["repositorio_url"] = "https://github.com/duilliobovi/tcc-previsao-insumos-sus"
    minuscula = lambda m: m[0].lower() + m[1:] if m.startswith(("Ingênuo", "Deriva")) else m  # noqa: E731
    for chave, v in venc.items():
        n[f"e_estrela_{chave.lower()}"] = v["E*"]
        n[f"ml_estrela_{chave.lower()}"] = v["ML*"]
        n[f"e_estrela_{chave.lower()}_txt"] = minuscula(v["E*"])
    for alg, hp in resumo["hiperparametros_escolhidos"].items():
        for k, v in hp.items():
            n[f"hp_{slug(alg)}_{k}"] = br(v, 2) if isinstance(v, float) and v < 1 else br(v, 0)

    # Caracterização
    carac = ler("caracterizacao_series.csv")
    for g, sub in carac.groupby(carac["serie"].str.split("|").str[0]):
        n[f"forca_tendencia_mediana_{slug(g)}"] = br(sub["forca_tendencia"].median(), 2)
        n[f"forca_sazonal_mediana_{slug(g)}"] = br(sub["forca_sazonal"].median(), 2)
    n["forca_tendencia_mediana"] = br(carac["forca_tendencia"].median(), 2)
    n["forca_sazonal_mediana"] = br(carac["forca_sazonal"].median(), 2)
    n["forca_sazonal_max"] = br(carac["forca_sazonal"].max(), 2)
    n["n_nao_estacionarias"] = str(int(carac["adf_kpss_concordam_nao_estacionaria"].sum()))
    for classe, qtd in carac["classe_sb"].value_counts().items():
        n[f"n_classe_{slug(classe)}"] = str(int(qtd))

    # Métricas no teste
    res = ler("resumo_metricas_teste.csv")
    for _, r in res.iterrows():
        m, h = slug(r["modelo"]), int(r["h"])
        n[f"mase_{m}_h{h}"] = br(r["mase_media"], 3)
        n[f"mase_ic_{m}_h{h}"] = f"{br(r['mase_ic_inf'], 3)} a {br(r['mase_ic_sup'], 3)}"
        n[f"mase_mediana_{m}_h{h}"] = br(r["mase_mediana"], 3)
        n[f"mase_ingenuo_{m}_h{h}"] = br(r["mase_ingenuo_media"], 3)
        n[f"wape_{m}_h{h}"] = pct(r["wape_mediana"], 1)
        n[f"rmsse_{m}_h{h}"] = br(r["rmsse_media"], 3)
        n[f"vies_{m}_h{h}"] = br(r["vies_medio"], 3)
        n[f"pinball_{m}_h{h}"] = br(r["pinball_media"], 3)
    for h, g in res.groupby("h"):
        n[f"melhor_teste_h{int(h)}"] = g.loc[g["mase_media"].idxmin(), "modelo"]
    # Global × local de cada algoritmo (exploratório; a H2 confirmatória usa só o ML* mais frequente).
    from previsao_insumos.inferencia import bootstrap_conglomerado, wilcoxon_uf
    met = ler("metricas_por_serie.csv")
    met = met[(met["fase"] == "teste") & met["h"].isin(config.horizontes)]
    largo = met.pivot_table(index=["serie", "h"], columns="modelo", values="mase")
    rng = np.random.default_rng(config.semente)
    for alg in ("Ridge", "Random Forest", "Gradient Boosting"):
        r = np.log(largo[alg] / largo[f"{alg} (local)"]).groupby(level="serie").mean().dropna()
        ufs = pd.Series([x.split("|")[1] for x in r.index], index=r.index)
        ic = bootstrap_conglomerado(r, ufs, 10_000, rng)
        k = slug(alg)
        n[f"gl_razao_{k}"] = br(float(np.exp(r.mean())), 3)
        n[f"gl_ic_{k}"] = f"{br(float(np.exp(ic[0])), 3)} a {br(float(np.exp(ic[1])), 3)}"
        n[f"gl_p_{k}"] = p_br(wilcoxon_uf(r, ufs, "two-sided")[1])
    val = ler("mase_validacao.csv")
    for _, r in val.iterrows():
        n[f"mase_val_{slug(r['modelo'])}_h{int(r['h'])}"] = br(r["mase_validacao"], 3)

    # Hipóteses confirmatórias
    hip = ler("testes_hipoteses.csv")
    for _, r in hip.iterrows():
        k = slug(r["hipotese"])
        n[f"razao_{k}"] = br(r["estimativa_razao"], 3)
        n[f"reducao_{k}"] = pct(1 - r["estimativa_razao"], 1)
        n[f"ic_{k}"] = f"{br(r['ic_inf'], 3)} a {br(r['ic_sup'], 3)}"
        if "ic2_inf" in r and pd.notna(r.get("ic2_inf")):
            n[f"ic2_{k}"] = f"{br(r['ic2_inf'], 3)} a {br(r['ic2_sup'], 3)}"
        n[f"p_{k}"] = p_br(r["p_valor"])
        n[f"p_holm_{k}"] = p_br(r["p_holm"])
        n[f"favoraveis_{k}"] = br(r["pct_series_favoraveis"], 0)
        n[f"comparacao_{k}"] = r["comparacao"]
        if "mde_razao" in r and pd.notna(r.get("mde_razao")):
            n[f"mde_{k}"] = br(r["mde_razao"], 3)
        n[f"decisao_{k}"] = "rejeitada" if r["rejeita_H0"] else "não rejeitada"
    n["n_hipoteses_rejeitadas"] = str(int(hip["rejeita_H0"].sum()))

    misto = ler("modelo_misto_razao.csv")
    for _, r in misto.iterrows():
        n[f"misto_razao_h{int(r['h'])}"] = br(r["razao"], 3)
        n[f"misto_ic_h{int(r['h'])}"] = f"{br(r['ic_inf'], 3)} a {br(r['ic_sup'], 3)}"
    n["misto_icc"] = pct(misto["var_uf"].iloc[0] / (misto["var_uf"].iloc[0] + misto["var_residual"].iloc[0]), 0)

    sens = ler("sensibilidade_ml_contra_cada_modelo.csv")
    for _, r in sens.iterrows():
        k = f"{slug(r['comparado'])}_h{int(r['h'])}"
        n[f"sens_razao_{k}"] = br(r["razao"], 3)
        n[f"sens_reducao_{k}"] = pct(1 - r["razao"], 1)
        n[f"sens_ic_{k}"] = f"{br(r['ic_inf'], 3)} a {br(r['ic_sup'], 3)}"
        n[f"sens_p_{k}"] = p_br(r["p_holm_familia"])

    postos = ler("postos_friedman.csv")
    n["friedman_melhor"] = postos["modelo"].iloc[0]
    for _, r in postos.iterrows():
        n[f"posto_{slug(r['modelo'])}"] = br(r["posto_medio"], 2)
    n["friedman_qui2"] = br(resumo["friedman_qui2"], 1)
    n["friedman_p"] = p_br(resumo["friedman_p"])
    n["kendall_mase_pinball"] = br(resumo["kendall_tau_mase_pinball_medio"], 2)

    cob = ler("calibracao_intervalos.csv")
    for _, r in cob.iterrows():
        k = f"{slug(r['modelo'])}_h{int(r['h'])}"
        n[f"cobertura_{k}"] = pct(r["cobertura"], 1)
        n[f"cobertura_ic_{k}"] = f"{pct(r['ic_inf'], 1)} a {pct(r['ic_sup'], 1)}"

    seg = ler("erro_por_segmento.csv")
    for _, r in seg.iterrows():
        n[f"seg_{slug(r['segmento'])}_{slug(str(r['nivel']))}_h{int(r['h'])}"] = br(r["razao"], 3)
        n[f"seg_n_{slug(r['segmento'])}_{slug(str(r['nivel']))}"] = str(int(r["n"]))

    sem = ler("variabilidade_sementes_resumo.csv")
    for _, r in sem.iterrows():
        k = f"{slug(r['modelo'])}_h{int(r['h'])}"
        n[f"sementes_media_{k}"] = br(r["mean"], 3)
        n[f"sementes_dp_{k}"] = br(r["std"], 4)
        n[f"sementes_amplitude_{k}"] = br(r["max"] - r["min"], 4)

    grade = ler("ajuste_hiperparametros.csv")
    for alg, g in grade.groupby("algoritmo"):
        n[f"grade_amplitude_{slug(alg)}"] = br(g["mase_validacao"].max() - g["mase_validacao"].min(), 3)
        n[f"grade_min_{slug(alg)}"] = br(g["mase_validacao"].min(), 3)
        n[f"grade_max_{slug(alg)}"] = br(g["mase_validacao"].max(), 3)

    # Tempos
    tempos = resumo["tempos"]
    for k, v in tempos.get("ajustes_por_modelo", {}).items():
        n[f"ajustes_{k}"] = br(v, 0)
    for k, v in tempos.items():
        if isinstance(v, (int, float)):
            n[f"tempo_{slug(k)}"] = br(v / 60, 1) if v >= 60 else br(v, 1)
            n[f"tempo_{slug(k)}_s"] = br(v, 1)

    # Estoque
    est = ler("simulacao_estoque_resumo.csv")
    por_serie = ler("simulacao_estoque_por_serie.csv")
    for _, r in est.iterrows():
        k = f"{slug(r['modelo'])}_l{int(r['L'])}_t{int(round(100 * r['tau']))}"
        n[f"est_atendida_{k}"] = pct(r["fill_rate_agregado"], 2)
        n[f"est_cobertura_{k}"] = br(r["cobertura_meses_mediana"], 2)
        n[f"est_valor_estoque_{k}"] = br(r["valor_estoque_medio_total"] / 1e6, 1)
        n[f"est_valor_falta_{k}"] = br(r["valor_falta_mensal_total"] / 1e6, 2)
    for (L, tau), g in est.groupby(["L", "tau"]):
        g = g.set_index("modelo")
        t = int(round(100 * tau))
        ref = g.loc[venc[f"L{L}"]["ML*"], "custo_relativo_total"]
        for modelo, v in g["custo_relativo_total"].items():
            n[f"est_custo_vs_ml_{slug(modelo)}_l{L}_t{t}"] = pct(v / ref - 1, 0)
        n[f"est_menor_custo_l{L}_t{t}"] = g["custo_relativo_total"].idxmin()
        estat = g[~g.index.isin(["Ridge", "Random Forest", "Gradient Boosting"])]["custo_relativo_total"]
        n[f"est_melhor_estat_l{L}_t{t}"] = estat.idxmin()
        n[f"est_melhor_estat_l{L}_t{t}_txt"] = minuscula(estat.idxmin())
        n[f"est_melhor_estat_pct_l{L}_t{t}"] = pct(estat.min() / ref - 1, 0)
    for L in config.prazos_compra:
        e, m = venc[f"L{L}"]["E*"], venc[f"L{L}"]["ML*"]
        for tau in config.taus_estoque:
            sub = est[(est["L"] == L) & (est["tau"] == tau)].set_index("modelo")
            t = int(round(100 * tau))
            dif_estoque = sub.loc[e, "valor_estoque_medio_total"] - sub.loc[m, "valor_estoque_medio_total"]
            dif_falta = sub.loc[e, "valor_falta_mensal_total"] - sub.loc[m, "valor_falta_mensal_total"]
            n[f"est_dif_estoque_l{L}_t{t}"] = br(dif_estoque / 1e6, 1)
            n[f"est_dif_falta_l{L}_t{t}"] = br(dif_falta / 1e6, 2)
            n[f"est_dif_falta_abs_l{L}_t{t}"] = br(abs(dif_falta) / 1e6, 2)
            n[f"est_dif_falta_anual_l{L}_t{t}"] = br(12 * dif_falta / 1e6, 1)
            n[f"est_red_estoque_pct_l{L}_t{t}"] = pct(dif_estoque / sub.loc[e, "valor_estoque_medio_total"], 0)
            if (L, t) == (3, 90):
                # Premissa rotulada (sem fonte): custo anual de implantação e operação (equipe + infraestrutura).
                for custo in (250_000, 500_000, 1_000_000):
                    n[f"impl_razao_c{custo // 1000}"] = br(dif_estoque * 0.10 / custo, 1)
                    n[f"impl_liquido_c{custo // 1000}"] = br((dif_estoque * 0.10 - custo) / 1e6, 1)
            for taxa in (0.10, 0.15, 0.20):
                # Custo anual de manutenção evitado (custo de capital sobre o estoque médio a menos).
                n[f"est_manutencao_anual_l{L}_t{t}_r{int(taxa * 100)}"] = br(dif_estoque * taxa / 1e6, 1)
            pe = por_serie[(por_serie["L"] == L) & (por_serie["tau"] == tau)].pivot(
                index="serie", columns="modelo", values="custo_relativo")
            n[f"est_custo_favoraveis_l{L}_t{t}"] = br(100 * (pe[m] < pe[e]).mean(), 0)
    painel = pd.read_csv(tab / "painel_limpo.csv")
    ultimo_ano = painel["data"].sort_values().unique()[-12:]
    n["qtd_mensal_nacional_milhoes"] = br(painel[painel["data"].isin(ultimo_ano)].groupby("data")["y"].sum().mean() / 1e6, 1)
    valor_mensal = (por_serie.drop_duplicates("serie").set_index("serie")["valor_unitario"]
                    * painel[painel["data"].isin(ultimo_ano)].groupby("serie")["y"].mean()).sum()
    n["valor_mensal_nacional_milhoes"] = br(valor_mensal / 1e6, 0)
    n["valor_unitario_mediano"] = br(por_serie.drop_duplicates("serie")["valor_unitario"].median(), 2)

    # Comportamento (se já calculado)
    if (tab / "ablacoes.csv").exists():
        abl = pd.read_csv(tab / "ablacoes.csv")
        for _, r in abl.iterrows():
            n[f"abl_{slug(r['variante'])}_h{int(r['h'])}"] = br(r["razao_vs_completo"], 3)
        curva = pd.read_csv(tab / "curvas_aprendizado.csv")
        c = curva.groupby(["dimensao", "valor", "h"])["mase_media"].mean()
        for (dim, valor, h), v in c.items():
            n[f"curva_{slug(dim)}_{slug(str(valor))}_h{int(h)}"] = br(v, 3)
        imp = pd.read_csv(tab / "importancia_permutacao.csv")
        for _, r in imp.iterrows():
            n[f"imp_{slug(r['grupo'])}_h{int(r['h'])}"] = br(r["mean"], 3)
        esc = pd.read_csv(tab / "escala_tempo.csv")
        for _, r in esc.iterrows():
            n[f"escala_cpu_{slug(r['algoritmo'])}_{int(r['series_equivalentes'])}"] = br(r["cpu_s"], 2)
        for alg, g in esc.groupby("algoritmo"):
            g = g.sort_values("series_equivalentes")
            n[f"escala_fator_{slug(alg)}"] = br(g["cpu_s"].iloc[-1] / max(g["cpu_s"].iloc[0], 1e-9), 1)
        expl = json.loads((tab / "previsao_explicada_sp.json").read_text(encoding="utf-8"))
        n["expl_origem"] = data_br(expl["origem"])
        n["expl_alvo"] = data_br(expl["alvo"])
        n["expl_nivel"] = br(expl["nivel"] / 1e6, 2)
        n["expl_previsao"] = br(expl["previsao"] / 1e6, 2)
        n["expl_observado"] = br(expl["observado"] / 1e6, 2)
        ex = pd.read_csv(tab / "previsao_explicada_sp.csv")
        for _, r in ex.iterrows():
            n[f"expl_contrib_{slug(r['atributo'])}"] = pct(np.expm1(r["contribuicao_log"]), 1)
        coef = pd.read_csv(tab / "coeficientes_ridge.csv").set_index("atributo")["coeficiente_padronizado"]
        for a, v in coef.items():
            n[f"coef_ridge_{slug(a)}"] = br(v, 3)

    # Sensibilidade da H2: Ridge local com penalização ajustada na validação (se já calculada)
    if (tab / "sensibilidade_ridge_local.json").exists():
        sl = json.loads((tab / "sensibilidade_ridge_local.json").read_text(encoding="utf-8"))
        n["rl_alfa"] = br(sl["alfa_escolhido"], 0)
        n["rl_razao"] = br(sl["razao_global_local"], 3)
        n["rl_reducao"] = pct(1 - sl["razao_global_local"], 1)
        n["rl_ic"] = f"{br(sl['ic_inf'], 3)} a {br(sl['ic_sup'], 3)}"
        n["rl_p"] = p_br(sl["p_unilateral"])
        for h, v in sl["mase_teste_local_por_h"].items():
            n[f"rl_mase_h{h}"] = br(v, 3)

    # Regional (se já calculado em R)
    if (tab / "regional_moran.csv").exists():
        moran = pd.read_csv(tab / "regional_moran.csv")
        for _, r in moran.iterrows():
            n[f"moran_i_{slug(r['variavel'])}"] = br(r["I"], 3)
            n[f"moran_p_{slug(r['variavel'])}"] = p_br(r["p_permutacao"])
            n[f"moran_p_holm_{slug(r['variavel'])}"] = p_br(r["p_holm"])
        lisa = pd.read_csv(tab / "regional_lisa.csv")
        for v, g in lisa.groupby("variavel"):
            sig = g[g["cluster"] != "Não significativo"]
            n[f"lisa_n_sig_{slug(v)}"] = str(len(sig))
            n[f"lisa_n_sig_bruto_{slug(v)}"] = str(int((g["p"] <= 0.05).sum()))
            n[f"lisa_ufs_bruto_{slug(v)}"] = ", ".join(g.loc[g["p"] <= 0.05, "sigla"]) or "nenhuma"
        reg = pd.read_csv(tab / "regional_regressao.csv")
        for _, r in reg.iterrows():
            k = f"{slug(r['medida'])}_{slug(r['termo'])}"
            n[f"reg_coef_{k}"] = br(r["estimativa"], 3)
            n[f"reg_ic_{k}"] = f"{br(r['ic_inf'], 3)} a {br(r['ic_sup'], 3)}"
            n[f"reg_p_{k}"] = p_br(r["p"])
        for medida, g in reg.groupby("medida"):
            n[f"reg_r2_{slug(medida)}"] = br(g["r2"].iloc[0], 2)
        kw = pd.read_csv(tab / "regional_kruskal_wallis.csv")
        for _, r in kw.iterrows():
            n[f"kw_p_{slug(r['medida'])}"] = p_br(r["p"])
            n[f"kw_qui2_{slug(r['medida'])}"] = br(r["qui2"], 2)
        reg_res = pd.read_csv(tab / "regional_resumo_regiao.csv")
        for _, r in reg_res.iterrows():
            n[f"regiao_razao_{slug(r['regiao'])}"] = br(r["razao_media"], 3)
            n[f"regiao_mase_{slug(r['regiao'])}"] = br(r["mase_ml_media"], 3)
        uf = pd.read_csv(tab / "regional_uf.csv")
        uf["razao"] = np.exp(uf["ganho"])
        n["n_ufs_ganho"] = str(int((uf["razao"] < 1).sum()))
        n["n_ufs_sem_ganho"] = str(int((uf["razao"] >= 1).sum()))
        sem = uf[uf["razao"] >= 1].sort_values("razao")["sigla"].tolist()
        n["ufs_sem_ganho"] = ", ".join(sem[:-1]) + " e " + sem[-1] if len(sem) > 1 else "".join(sem)
        n["uf_maior_ganho"] = uf.loc[uf["razao"].idxmin(), "sigla"]
        n["uf_menor_ganho"] = uf.loc[uf["razao"].idxmax(), "sigla"]
        n["razao_uf_min"] = br(uf["razao"].min(), 2)
        n["razao_uf_max"] = br(uf["razao"].max(), 2)

    # Versões em inglês (ponto decimal) para o abstract: chave + "_en".
    for k, v in list(n.items()):
        if re.fullmatch(r"[<\d.,\s]+", v):
            n[f"{k}_en"] = v.replace(".", "").replace(",", ".") if "," in v else v
    tabelas_texto()
    destino = destino or RAIZ_REPOSITORIO / "resultados" / "numeros.json"
    destino.write_text(json.dumps(dict(sorted(n.items())), ensure_ascii=False, indent=1), encoding="utf-8")
    return n


def tabelas_texto() -> None:
    """Tabelas do texto, já formatadas em pt-BR (resultados/tabelas/texto_*.csv), lidas pelo montador do .docx."""
    tab = Configuracao().dir_saida
    res = pd.read_csv(tab / "resumo_metricas_teste.csv")
    res = res[res["h"].isin([1, 3, 6])]
    largo = res.pivot_table(index=["familia", "modelo"], columns="h", values=["mase_media", "wape_mediana"])
    largo = largo.reset_index().sort_values(["familia", ("mase_media", 1)])
    ordem_fam = {"Estatístico": 0, "ML global": 1, "ML local": 2}
    largo = largo.iloc[np.argsort([ordem_fam[f] for f in largo["familia"]], kind="stable")]
    linhas = []
    for _, r in largo.iterrows():
        linhas.append({"Modelo": r[("modelo", "")], "Família": r[("familia", "")],
                       **{f"MASE h = {h}": br(r[("mase_media", h)], 3) for h in (1, 3, 6)},
                       "WAPE h = 1 (%)": pct(r[("wape_mediana", 1)], 1)})
    pd.DataFrame(linhas).to_csv(tab / "texto_tabela_mase.csv", index=False)

    hip = pd.read_csv(tab / "testes_hipoteses.csv")
    pd.DataFrame({"Hipótese": hip["hipotese"], "Comparação": hip["comparacao"].str.replace("×", "/", regex=False),
                  "Razão": hip["estimativa_razao"].map(lambda x: br(x, 3)),
                  "IC 95%": [f"{br(a, 3)} a {br(b, 3)}" for a, b in zip(hip["ic_inf"], hip["ic_sup"])],
                  "p de Holm": hip["p_holm"].map(p_br),
                  "Decisão": np.where(hip["rejeita_H0"], "rejeita H0", "não rejeita H0")}).to_csv(
        tab / "texto_tabela_hipoteses.csv", index=False)

    est = pd.read_csv(tab / "simulacao_estoque_resumo.csv")
    venc = json.loads((tab / "resumo_experimento.json").read_text(encoding="utf-8"))["vencedores_validacao"]
    linhas = []
    for L in (3, 6):
        for tau in (0.9, 0.95):
            sub = est[(est["L"] == L) & (est["tau"] == tau)].set_index("modelo")
            for papel in ("E*", "ML*"):
                m = venc[f"L{L}"][papel]
                linhas.append({"Prazo (meses)": str(L), "τ": br(tau, 2), "Modelo": f"{m} ({papel})",
                               "Demanda atendida (%)": pct(sub.loc[m, "fill_rate_agregado"], 2),
                               "Estoque (meses)": br(sub.loc[m, "cobertura_meses_mediana"], 2),
                               "Estoque médio (R$ mi)": br(sub.loc[m, "valor_estoque_medio_total"] / 1e6, 1),
                               "Não atendida (R$ mi/mês)": br(sub.loc[m, "valor_falta_mensal_total"] / 1e6, 2)})
    pd.DataFrame(linhas).to_csv(tab / "texto_tabela_estoque.csv", index=False)


if __name__ == "__main__":
    tabelas_texto()
    print(f"{len(gerar())} números gravados em resultados/numeros.json")
